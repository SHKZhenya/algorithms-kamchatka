"""Backend-only registry and scenario assessment routes in the existing FastAPI app."""
from functools import lru_cache
from hashlib import file_digest
import os
from pathlib import Path
from typing import Literal

from fastapi import HTTPException, Query
from shapely.geometry import shape, box, mapping
from shapely.ops import transform, unary_union
from weather_coverage.geography import TO_METRIC, TO_WGS84
from .geodata import PILOT, BOUNDS, collection
from .object_registry import ObjectRegistry
from .object_assessment import ObjectAssessment


def register_object_api(api, city, runner, parse_bbox):
    import json
    from .entrance_reviews import EntranceReviews, EntranceReview
    reviews = EntranceReviews(api.state.store.path)
    @lru_cache(maxsize=2)
    def cached_registry(revision, csv_path, csv_hash, reviewed):
        return ObjectRegistry(city, csv_path or None, json.loads(reviewed))

    def registry():
        path = os.getenv('CITY_SIM_ENTRANCE_CSV', '')
        checksum = ''
        if path:
            try:
                with Path(path).open('rb') as stream:
                    checksum = file_digest(stream, 'sha256').hexdigest()
            except OSError as e:
                raise HTTPException(503, 'Configured entrance CSV unavailable') from e
        return cached_registry(city.data_revision, path, checksum, json.dumps(reviews.all(), sort_keys=True))

    @api.post('/api/v1/registry/entrance-reviews', tags=['Object backend'])
    def review_entrance(payload: EntranceReview):
        """Confirm with reported coordinates/evidence or reject a hypothesis; no OSM write."""
        try:
            return reviews.save(payload, registry())
        except LookupError as e:
            raise HTTPException(404, str(e)) from e
        except ValueError as e:
            raise HTTPException(422, str(e)) from e

    @api.get('/api/v1/registry/entrance-reviews', tags=['Object backend'])
    def entrance_reviews():
        return reviews.all()

    def assessment(run_id, frame):
        try:
            return ObjectAssessment(registry(), runner, run_id, frame)
        except (LookupError, FileNotFoundError) as e:
            raise HTTPException(404, str(e)) from e
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        except RuntimeError as e:
            raise HTTPException(409, str(e)) from e

    @api.get('/api/v1/registry/buildings', tags=['Object backend'])
    def buildings(bbox: str = Query(','.join(map(str, PILOT)))):
        """All buildings in the available snapshot, including unknown entrance coverage."""
        reg = registry(); extent = box(*parse_bbox(bbox))
        return collection([f for f in reg.building_records if extent.intersects(shape(f['geometry']))], revision=reg.revision)

    @api.get('/api/v1/registry/objects', tags=['Object backend'])
    def objects(bbox: str = Query(','.join(map(str, PILOT))),
                kind: Literal['all', 'entrance', 'bus_stop', 'medical', 'facility'] = 'all'):
        """Real mapped/imported entrances and facilities; nearby candidates are not connections."""
        reg = registry(); extent = box(*parse_bbox(bbox))
        return collection([f for f in reg.objects.values() if extent.intersects(shape(f['geometry'])) and
                           (kind == 'all' or f['properties']['kind'] == kind)], revision=reg.revision)

    @api.get('/api/v1/registry/graph', tags=['Object backend'])
    def graph(bbox: str = Query(','.join(map(str, PILOT)))):
        """Directed shared-node topology, separated by travel mode; not operational admission."""
        return registry().graph(parse_bbox(bbox))

    @api.get('/api/v1/registry/coverage', tags=['Object backend'])
    def coverage():
        return registry().audit

    @api.get('/api/v1/registry/entrance-candidates', tags=['Object backend'])
    def entrance_candidates(bbox: str = Query(','.join(map(str, PILOT)))):
        """Hypotheses based on real path endpoints; never included in mapped entrance counts."""
        reg = registry(); extent = box(*parse_bbox(bbox))
        return collection([f for f in reg.candidates if extent.intersects(shape(f['geometry']))],
                          revision=reg.revision, observed=False, graph_connections_created=False)

    @api.get('/api/v1/registry/relationships', tags=['Object backend'])
    def relationships(bbox: str = Query(','.join(map(str, PILOT)))):
        """Evidence for roads, access ways, approaches, entrances and stops; hypotheses stay separate."""
        from .object_links import relationships
        return relationships(registry(), parse_bbox(bbox))

    @api.get('/api/v1/operations/city/runs/{run_id}/assessments', tags=['Object backend'])
    def assessments(run_id: str, bbox: str = Query(','.join(map(str, PILOT))),
                    frame: int | None = Query(None, ge=0, le=64),
                    kind: Literal['all', 'road', 'building', 'entrance', 'bus_stop', 'medical', 'facility'] = 'all',
                    threshold_cm: float = Query(20, ge=0, le=1000, allow_inf_nan=False),
                    order: Literal['physical', 'importance_then_physical'] = 'physical',
                    fragments: bool = True, limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0)):
        """Weighted physical impact, separate significance/quality, problem geometry and weather.

        Physical ordering uses P90 descending, then coverage and stable ID. Importance-first puts medical/emergency
        categories first, then P90. Both policies are research candidates for checking.
        Neither infers passability, old snow, or equipment clearance requirements.
        """
        bounds = parse_bbox(bbox)
        return assessment(run_id, frame).ranking(bounds, kind, threshold_cm, limit, offset, order, fragments)

    def find_asset(engine, object_id):
        reg = engine.registry
        obj = reg.objects.get(object_id)
        if obj:
            return obj, reg.areas[object_id]
        b = reg.buildings.get(object_id)
        if b:
            metric = transform(TO_METRIC, shape(b['geometry']))
            area = metric.buffer(8).difference(metric)
            indices = reg.catalog.building_index.query(area, predicate='intersects')
            if len(indices):
                area = area.difference(unary_union([reg.catalog.metric_buildings[i] for i in indices]))
            return next(f for f in reg.building_records if f['id'] == object_id), transform(TO_WGS84, area)
        road = next((f for f in reg.catalog.roads.features if f['id'] == object_id), None)
        if road:
            return road, shape(road['geometry'])
        raise HTTPException(404, 'Object not found')

    @api.get('/api/v1/operations/city/runs/{run_id}/assessment', tags=['Object backend'])
    def single(run_id: str, object_id: str, frame: int | None = Query(None, ge=0, le=64),
               threshold_cm: float = Query(20, ge=0, le=1000, allow_inf_nan=False)):
        """Assessment of one stable OSM object ID, including linked mapped approaches."""
        engine = assessment(run_id, frame)
        obj, geom = find_asset(engine, object_id)
        return engine.evaluate(obj, geom, threshold_cm)

    @api.get('/api/v1/operations/city/runs/{run_id}/object-weather', tags=['Object backend'])
    def weather(run_id: str, object_id: str, frame: int | None = Query(None, ge=0, le=64)):
        """Exact archived simulation inputs; no substitution with today's forecast."""
        engine = assessment(run_id, frame)
        _, geom = find_asset(engine, object_id)
        return engine.weather(geom)

    return registry
