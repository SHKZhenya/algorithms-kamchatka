"""Scenario-specific physical exposure, independent importance, and data quality."""
from functools import lru_cache
import gzip
import json
from pathlib import Path

import numpy as np
from shapely.geometry import shape, box, mapping
from shapely import STRtree
from shapely.ops import transform
from weather_coverage.geography import TO_METRIC, TO_WGS84
from .spatial import RasterAssessment
from .geodata import collection
from .city_snow import checkpoints


@lru_cache(maxsize=4)
def archived_tiles(path):
    path = Path(path)
    if not path.exists():
        return []
    with gzip.open(path, 'rt') as stream:
        return json.load(stream).get('tiles', [])


class ObjectAssessment:
    def __init__(self, registry, runner, run_id, frame=None):
        self.registry, self.runner, self.run_id, self.frame = registry, runner, run_id, frame
        self.result = runner.status(run_id)
        if not self.result:
            raise LookupError('Расчёт не найден')
        if self.result['status'] != 'completed':
            raise RuntimeError('Расчёт ещё не завершён')
        if self.result['provenance']['city_revision'] != registry.revision:
            raise RuntimeError('Геоданные расчёта и реестра различаются')
        fields = runner.probe_fields(run_id)
        timeline = fields.get('timeline', [])
        if frame is not None and not 0 <= frame < len(timeline):
            raise ValueError('Кадр отсутствует в расчёте')
        self.fields = fields
        self.raster = RasterAssessment(timeline[frame] if frame is not None else fields['snow_cm'], self.result['bounds'])
        self.baseline = RasterAssessment(np.where(np.isfinite(self.raster.grid), fields['weather_snow'], np.nan), self.result['bounds']) if frame is None and 'weather_snow' in fields else None
        self.tiles = archived_tiles(str(runner.replay_path(run_id)))
        self.tile_geometries = []
        w, s, e, n = self.result['bounds']
        cells = self.result.get('tile_cells')
        if cells:
            rows, cols = self.raster.grid.shape
            nx, ny = cols/cells, rows/cells
            for tile in self.tiles:
                i, j = tile['i'], tile['j']
                self.tile_geometries.append(box(w+i*(e-w)/nx, n-(j+1)*(n-s)/ny,
                                                w+(i+1)*(e-w)/nx, n-j*(n-s)/ny))
        self.tile_index = STRtree(self.tile_geometries)
        if frame is not None and cells and self.tiles:
            # One baseline grid per frame, shared by all objects in this request.
            blank = np.full_like(self.raster.grid, np.nan)
            for tile in self.tiles:
                hours = tile.get('hours', [])
                indices = checkpoints(len(hours)) if hours else []
                if frame >= len(indices):
                    continue
                included = hours[:indices[frame]+1]
                if all(h.get('snowfall_cm') is not None for h in included):
                    i, j = tile['i'], tile['j']
                    blank[j*cells:(j+1)*cells, i*cells:(i+1)*cells] = sum(h['snowfall_cm'] for h in included)
            blank[~np.isfinite(self.raster.grid)] = np.nan
            self.baseline = RasterAssessment(blank, self.result['bounds'])

    def weather(self, geom):
        w, s, e, n = self.result['bounds']
        cells = self.result.get('tile_cells')
        rows, cols = self.raster.grid.shape
        contexts = []
        source = self.result.get('source', {})
        if cells:
            nx, ny = cols/cells, rows/cells
            for index in sorted(self.tile_index.query(geom, predicate='intersects')):
                tile = self.tiles[index]
                i, j = tile['i'], tile['j']
                extent = box(w+i*(e-w)/nx, n-(j+1)*(n-s)/ny, w+(i+1)*(e-w)/nx, n-j*(n-s)/ny)
                if not extent.intersects(geom):
                    continue
                hours = tile.get('hours', [])
                if self.frame is not None and hours:
                    indices = checkpoints(len(hours))
                    hours = hours[:indices[self.frame]+1] if self.frame < len(indices) else []
                coordinates = next((h.get('provider_coordinates') or h.get('coordinates') for h in hours
                                    if h.get('provider_coordinates') or h.get('coordinates')), None)
                if coordinates is None and source.get('locations'):
                    from winter_audit.relief import distance
                    center = extent.centroid
                    selected = min(source['locations'], key=lambda p: distance((center.x, center.y), p['coordinates']))
                    coordinates = selected['coordinates']
                object_distance = None
                if coordinates and not geom.is_empty:
                    from winter_audit.relief import distance
                    p = geom.representative_point()
                    object_distance = distance((p.x, p.y), coordinates)
                contexts.append({'tile': [i, j], 'distance_to_source_m': tile.get('distance_to_source_m'),
                    'distance_reference': 'simulation_tile_center', 'source_coordinates': coordinates,
                    'object_representative_point_distance_m': object_distance,
                    'extrapolated': tile.get('extrapolated'), 'hours': hours,
                    'valid_at': [h.get('valid_at') for h in hours],
                    'retrieved_at': [h.get('source_time') for h in hours],
                    'units': {'wind_from_deg': 'degree meteorological from north', 'wind_speed_ms': 'm/s',
                              'snowfall_cm': 'cm fresh snow per input step', 'snow_mm_3h': 'mm water equivalent / 3h',
                              'gust_mps': 'm/s', 'gust_ms': 'm/s', 'temperature_c': 'degree Celsius',
                              'precipitation_mm': 'mm water equivalent per input step'}})
        return {'source': {k: v for k, v in source.items() if k != 'locations'},
                'source_resolution_m': source.get('nominal_resolution_m', source.get('resolution_m')),
                'resolution_status': 'reported' if source.get('nominal_resolution_m', source.get('resolution_m')) else 'unknown',
                'retrieved_at': source.get('retrieved_at'), 'contexts': contexts,
                'missing': not bool(contexts), 'street_resolution_verified': False,
                'limitations': ['Raster cell size is not weather resolution',
                                'Missing timestamps/resolution remain unknown',
                                'Nearest weather input is selected per simulation tile, not per object']}

    def assets(self, bounds, kind='all'):
        extent = box(*bounds)
        if kind in {'all', 'road'}:
            yield from ((f, shape(f['geometry'])) for f in self.registry.catalog.roads.query(bounds))
        if kind in {'all', 'building'}:
            for f in self.registry.building_records:
                geom = shape(f['geometry'])
                if extent.intersects(geom):
                    # Exterior ring band only; no fabricated door coordinates.
                    metric = transform(TO_METRIC, geom)
                    area = metric.buffer(8).difference(metric)
                    indices = self.registry.catalog.building_index.query(area, predicate='intersects')
                    if len(indices):
                        from shapely.ops import unary_union
                        area = area.difference(unary_union([self.registry.catalog.metric_buildings[i] for i in indices]))
                    yield f, transform(TO_WGS84, area)
        for f in self.registry.objects.values():
            if kind not in {'all', f['properties']['kind']} or not extent.intersects(shape(f['geometry'])):
                continue
            yield f, self.registry.areas[f['id']]

    def evaluate(self, f, geom, threshold, fragments=True):
        props = f['properties']
        physical = self.raster.assess(geom, threshold, fragments)
        baseline = self.baseline.assess(geom, threshold, False) if self.baseline else None
        weather = self.weather(geom)
        cat = props.get('category', 'other')
        significance = {'category': cat, 'declared_priority': props.get('operational_priority', 'standard'),
                        'consequence': 'potential_access_disruption_not_observed', 'verified': False}
        coverage = physical['coverage_fraction']
        quality = {'coverage_fraction': coverage, 'status': 'missing' if not physical['covered'] else 'partial' if coverage < .999999 else 'covered_unvalidated',
                   'coordinate_source': props.get('coordinate_source', props.get('source', props.get('data_kind', 'unknown'))),
                   'entrance_status': props.get('entrance_status', props.get('entrance_coverage')),
                   'pedestrian_link_status': props.get('pedestrian_link_status'),
                   'field_validated': False, 'accessibility_known': False}
        reasons = [f'Weighted P90 of modeled new snow: {physical["p90_cm"]} cm',
                   f'Coverage: {coverage:.1%}', f'Category: {cat} (independent of snow)']
        if quality['status'] in {'missing', 'partial'}:
            reasons.append('Missing cells require verification; missing does not mean zero snow')
        physical.update(quantity='modeled_new_snow_accumulation', old_cover_cm=None,
                        existing_snow_transport_supported=False, actual_accessibility='unknown',
                        uniform_weather_baseline=baseline,
                        mean_difference_from_uniform_cm=physical['mean_cm']-baseline['mean_cm']
                        if baseline and physical['mean_cm'] is not None and baseline['mean_cm'] is not None else None,
                        wind_context=weather,
                        nearby_buildings_effect='already_in_model_not_added_as_a_risk_weight',
                        assessment_geometry=mapping(geom),
                        geometry_policy={'status': 'experimental', 'building_exterior_band_m': 8,
                                         'entrance_outward_m': [1, 8], 'entrance_half_width_m': 2,
                                         'point_buffer_radius_m': 5})
        road_map = {r['id']: r for r in self.registry.catalog.roads.features} if self.registry.approaches.get(f['id']) else {}
        approaches = [{'road_id': rid, 'physical': self.raster.assess(shape(road_map[rid]['geometry']), threshold, fragments)}
                      for rid in self.registry.approaches.get(f['id'], [])]
        return {**f, 'properties': {**props, 'physical': physical, 'significance': significance,
                'quality': quality, 'approaches': approaches, 'reasons': reasons}}

    def ranking(self, bounds, kind, threshold, limit=100, offset=0, order='physical', fragments=True):
        rows = [self.evaluate(f, geom, threshold, fragments) for f, geom in self.assets(bounds, kind)]
        def key(f):
            p = f['properties']; v = p['physical']['p90_cm']
            # No weighted composite score. Importance-first policy is explicit and experimental.
            important = p['significance']['category'] in {'medical', 'emergency'}
            return ((not important,) if order == 'importance_then_physical' else ()) + (v is None, -(v or 0), -p['quality']['coverage_fraction'], f['id'])
        rows.sort(key=key)
        return collection(rows[offset:offset+limit], run_id=self.run_id, frame=self.frame,
                          revision=self.registry.revision, total=len(rows), offset=offset, limit=limit,
                          unknown=sum(f['properties']['physical']['p90_cm'] is None for f in rows),
                          order=order, policy_status='experimental_not_normative',
                          tie_break='coverage_desc_then_stable_id',
                          threshold_cm=threshold, actual_accessibility='unknown',
                          field_validation='not_testable_without_matched_observations')
