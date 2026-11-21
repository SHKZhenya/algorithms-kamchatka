"""Unified building/access inventory. Spatial proximity never creates a graph edge."""
from collections import Counter, defaultdict
import csv
import math
from pathlib import Path
from hashlib import file_digest

from shapely.geometry import Point, LineString, shape
from shapely.ops import transform, unary_union
from shapely import STRtree

from weather_coverage.geography import TO_METRIC, TO_WGS84, tags
from .access import category, osm_id, PEDESTRIAN
from .geodata import feature, collection, polygons


def facade(building, point):
    """Nearest exterior wall and outward normal, robust to ring winding."""
    best = None
    for polygon in polygons(building):
        for ring_object in (polygon.exterior, *polygon.interiors):
            ring = list(ring_object.coords)
            for a, b in zip(ring, ring[1:]):
                edge = LineString([a, b])
                length = edge.length
                if not length:
                    continue
                wall = edge.interpolate(edge.project(point))
                nx, ny = (b[1]-a[1])/length, -(b[0]-a[0])/length
                if polygon.covers(Point(wall.x+nx*.1, wall.y+ny*.1)):
                    nx, ny = -nx, -ny
                choice = (point.distance(edge), wall, nx, ny)
                if best is None or choice[0] < best[0]:
                    best = choice
    return best


class ObjectRegistry:
    def __init__(self, city, entrance_csv=None, reviewed_entrances=()):
        self.catalog = city.access_catalog
        self.catalog_city_elements = city.osm_elements
        self.revision = self.catalog.revision
        self.buildings = self.catalog.buildings
        self.building_categories = {}
        self.objects = {f['id']: {**f, 'properties': dict(f['properties'])} for f in self.catalog.objects.features}
        for review in reviewed_entrances:
            if review['verdict'] != 'confirmed' or review['building_id'] not in self.buildings:
                continue
            oid = 'review:' + review['candidate_id']
            self.objects[oid] = feature(Point(*review['coordinates']), oid, kind='entrance',
                building_id=review['building_id'], source='reported_confirmation',
                source_evidence=review['source'], uncertainty_m=review['uncertainty_m'],
                predicted_coordinates=review['predicted_coordinates'], entrance_status='review_confirmed',
                category=category({'building': self.buildings[review['building_id']]['properties'].get('building_type')}),
                location_verified=False, connection_ids=[])
        self.import_audit = Counter()
        self.csv_sha256 = None
        if entrance_csv:
            with Path(entrance_csv).open('rb') as stream:
                self.csv_sha256 = file_digest(stream, 'sha256').hexdigest()
            self._import(Path(entrance_csv))
        from .entrance_enrichment import enrich_entrances
        enrichment = enrich_entrances(self, city)
        elements = city.osm_elements
        for bid, building in self.buildings.items():
            key = bid.split(':')[1][0]+bid.split(':')[2] if bid.startswith('osm:') else ''
            meta = tags(elements[key]) if key in elements else {}
            cat = category(meta)
            self.building_categories[bid] = cat
            if cat in {'education', 'emergency'}:
                p = shape(building['geometry']).representative_point()
                self.objects[bid+':facility'] = feature(p, bid+':facility', kind='facility', category=cat,
                    building_id=bid, name=meta.get('name'), source='OpenStreetMap', location_verified=False)
        self.by_building = defaultdict(list)
        self.areas = {}
        self.approaches = {}
        foot_ids = [i for i, f in enumerate(self.catalog.roads.features)
                    if f['properties'].get('foot') not in {'no', 'private'} and
                    f['properties'].get('access') not in {'no', 'private'} and
                    f['properties'].get('highway') not in {'motorway', 'trunk', 'trunk_link'}]
        foot_geoms = [self.catalog.metric_roads[i] for i in foot_ids]
        permitted_foot_ids = {self.catalog.roads.features[i]['id'] for i in foot_ids}
        foot_tree = STRtree(foot_geoms)
        metric_buildings = self.catalog.metric_buildings
        building_tree = self.catalog.building_index
        for oid, obj in self.objects.items():
            p = obj['properties']
            if p['kind'] in {'medical', 'facility'} and not p.get('building_id'):
                marker = transform(TO_METRIC, shape(obj['geometry']))
                containing = building_tree.query(marker, predicate='intersects')
                if len(containing) == 1:
                    p['building_id'] = self.catalog.building_ids[containing[0]]
                    p['building_match_method'] = 'facility_point_inside_building_not_an_entrance'
            bid = p.get('building_id')
            point = transform(TO_METRIC, shape(obj['geometry']))
            p.update(object_id=oid, coordinate_source=p.get('source'),
                     coordinate_confidence='osm_mapped_not_field_verified' if p.get('source') == 'OpenStreetMap' else 'imported_unverified',
                     entrance_id=oid if p['kind'] == 'entrance' else None,
                     entrance_status=p.get('entrance_status', 'mapped') if p['kind'] == 'entrance' else None,
                     orientation_from_north_deg=None, orientation_source=None)
            key = 'n'+oid.split(':')[-1] if oid.startswith('osm:node:') else ''
            p['source_timestamp'] = elements[key].get('timestamp') if key in elements else None
            p['source_osm_version'] = elements[key].get('version') if key in elements else None
            if bid and p['kind'] == 'entrance':
                self.by_building[bid].append(oid)
                candidates = building_tree.query(point.buffer(3), predicate='intersects')
                p['candidate_building_ids'] = [self.catalog.building_ids[i] for i in candidates
                    if metric_buildings[i].boundary.distance(point) <= 3]
                bk = bid.split(':')[1][0]+bid.split(':')[2] if bid.startswith('osm:') else ''
                shares_node = bk in elements and key and any(nd.get('ref') == key[1:] for nd in elements[bk].findall('nd'))
                p['building_link_status'] = 'shared_osm_boundary_node' if shares_node else 'spatial_proximity'
                p['building_link_ambiguous'] = len(p['candidate_building_ids']) > 1
                wall = facade(transform(TO_METRIC, shape(self.buildings[bid]['geometry'])), point)
                if wall:
                    _, anchor, nx, ny = wall
                    p.update(orientation_from_north_deg=math.degrees(math.atan2(nx, ny)) % 360,
                             orientation_source='nearest_osm_facade_inferred', facade_distance_m=wall[0],
                             building_link_confidence='boundary_proximity_not_field_verified')
                    # Exterior local approach assessment, not an asserted traversable route.
                    area = LineString([(anchor.x+nx, anchor.y+ny), (anchor.x+nx*8, anchor.y+ny*8)]).buffer(2, cap_style=2)
                else:
                    area = point.buffer(5)
            elif bid:
                area = transform(TO_METRIC, shape(self.buildings[bid]['geometry'])).buffer(8)
            else:
                area = point.buffer(5)
            # Exclude all known buildings, including neighbouring footprints.
            blockers = building_tree.query(area, predicate='intersects')
            if len(blockers):
                area = area.difference(unary_union([metric_buildings[i] for i in blockers]))
            p['assessment_area_method'] = 'exterior_facade_or_point_buffer'
            if area.is_empty and p['kind'] == 'entrance':
                # At a concave corner a single inferred normal can face another wall.
                # Preserve the reported point; assess nearby open ground with an explicit fallback.
                area = point.buffer(8)
                blockers = building_tree.query(area, predicate='intersects')
                if len(blockers):
                    area = area.difference(unary_union([metric_buildings[i] for i in blockers]))
                p['assessment_area_method'] = 'open_ground_within_8m_fallback_normal_unresolved'
            self.areas[oid] = transform(TO_WGS84, area)
            shared = [rid for rid in p.get('connection_ids', []) if rid in permitted_foot_ids]
            nearest = foot_tree.nearest(point) if foot_geoms else None
            distance = point.distance(foot_geoms[nearest]) if nearest is not None else None
            nearest_id = self.catalog.roads.features[foot_ids[nearest]]['id'] if nearest is not None else None
            p.update(pedestrian_connection_ids=shared,
                     nearest_pedestrian_candidate_id=nearest_id if distance is not None and distance <= 30 else None,
                     pedestrian_candidate_distance_m=distance if distance is not None and distance <= 30 else None,
                     pedestrian_link_status='shared_osm_node' if shared else 'nearby_only' if distance is not None and distance <= 30 else 'missing',
                     actual_accessibility='unknown')
            self.approaches[oid] = shared
        self.building_records = []
        for bid, b in self.buildings.items():
            ids = sorted(self.by_building[bid])
            self.building_records.append({**b, 'properties': {**b['properties'], 'building_id': bid,
                'category': self.building_categories[bid],
                'entrance_ids': ids, 'entrance_coverage': 'mapped' if ids else 'unknown',
                'entrance_location_unknown': not bool(ids), 'fallback_assessment': 'exterior_perimeter_only',
                'coverage_scope': 'available_building_snapshot_not_city_census'}})
        self.audit = {**self.catalog.audit, 'registry_objects': dict(Counter(p['properties']['kind'] for p in self.objects.values())),
                      'buildings_with_mapped_entrance': sum(bool(v) for v in self.by_building.values()),
                      'buildings_without_mapped_entrance': sum(not self.by_building[k] for k in self.buildings),
                      'csv_import': dict(self.import_audit), 'entrance_csv_sha256': self.csv_sha256,
                      'fabricated_connections': 0,
                      'city_census_complete': False}
        from .entrance_enrichment import entrance_candidates
        self.candidates = entrance_candidates(self)
        for candidate in self.candidates:
            review = next((r for r in reviewed_entrances if r['candidate_id'] == candidate['id']), None)
            if review:
                candidate['properties'].update(review_verdict=review['verdict'], review_source=review['source'])
        self.audit.update(entrance_enrichment=enrichment, candidate_entrances=len(self.candidates),
                          candidate_method='pedestrian_endpoints_preferred_then_service_driveway_endpoints',
                          candidates_counted_as_real=False)
        self.audit['entrances_without_building'] = sum(f['properties']['kind'] == 'entrance' and not f['properties'].get('building_id') for f in self.objects.values())

    def _import(self, path):
        with path.open(encoding='utf-8-sig', newline='') as stream:
            for row in csv.DictReader(stream):
                try:
                    if row.get('point_source') != 'osm_node':
                        self.import_audit['skipped_unconfirmed'] += 1
                        continue
                    lon, lat = float(row['lon']), float(row['lat'])
                    if not math.isfinite(lon+lat) or not -180 <= lon <= 180 or not -90 <= lat <= 90:
                        raise ValueError('invalid coordinates')
                    bid = 'osm:'+row['building_id'].replace('/', ':')
                    oid = 'osm:node:'+str(int(row['entrance_id']))
                    if bid not in self.buildings:
                        self.import_audit['unknown_building'] += 1
                        continue
                    if transform(TO_METRIC, shape(self.buildings[bid]['geometry'])).boundary.distance(Point(TO_METRIC(lon, lat))) > 5:
                        self.import_audit['off_facade'] += 1
                        continue
                    if oid in self.objects:
                        self.import_audit['duplicate_osm_id'] += 1
                        continue
                    self.objects[oid] = feature(Point(lon, lat), oid, kind='entrance', building_id=bid,
                        category=category({'building': row.get('building_type')}), role=row.get('entrance'),
                        source='danil_csv:osm_node', source_file=path.name,
                        source_url=f'https://www.openstreetmap.org/node/{int(row["entrance_id"])}',
                        connection_ids=self.catalog.membership.get('n'+str(int(row['entrance_id'])), []),
                        location_verified=False)
                    self.import_audit['imported'] += 1
                except (KeyError, ValueError):
                    self.import_audit['invalid'] += 1

    def graph(self, bounds):
        """Mode-specific directed edges between consecutive actual OSM node refs.

        Unknown foot permission is recorded. No geometrical snapping or crossing join.
        Turn restrictions and live closures remain in the canonical routing graph.
        """
        edges = []
        elements = self.catalog_city_elements
        for road in self.catalog.roads.query(bounds):
            key = 'w'+road['id'].split(':')[-1]
            e = elements[key]
            meta = tags(e)
            refs = ['n'+nd.get('ref') for nd in e.findall('nd')]
            vehicle = road['properties']['vehicle_candidate']
            foot = meta.get('foot', meta.get('access')) not in {'no', 'private'} and meta.get('highway') not in {'motorway', 'trunk', 'trunk_link'}
            one = meta.get('oneway', 'yes' if meta.get('junction') == 'roundabout' else 'no')
            for a, b in zip(refs, refs[1:]):
                for mode, allowed in (('pedestrian', foot), ('vehicle', vehicle)):
                    if not allowed:
                        continue
                    directions = [(a, b), (b, a)] if mode == 'pedestrian' or one not in {'yes', '1', 'true', '-1'} else [(b, a)] if one == '-1' else [(a, b)]
                    for start, end in directions:
                        edges.append({'from': osm_id(start), 'to': osm_id(end), 'way_id': road['id'], 'mode': mode,
                                      'access_status': 'tag_permits_or_unspecified', 'field_verified': False})
        return {'revision': self.revision, 'edges': edges, 'synthetic_connections': False,
                'nodes': [{'id': osm_id(k), 'coordinates': [float(elements[k].get('lon')), float(elements[k].get('lat'))]}
                          for k in sorted({e[endpoint].replace('osm:node:', 'n') for e in edges for endpoint in ('from', 'to')})],
                'limitations': ['Topological inventory, not operational routing', 'No turn restrictions or live closures applied']}
