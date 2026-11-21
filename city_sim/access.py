"""OSM access inventory and snow exposure, independent of the web client.

Adapted concepts: NDaVS/kamcha-snow-sim 75bc8ecf clearance.py/map.js.
No synthetic entrances, links or operational clearance guarantees are generated.
"""
from collections import Counter, defaultdict
import csv
from functools import lru_cache
import math

import numpy as np
from shapely import STRtree
from shapely.geometry import LineString, Point, box, shape
from shapely.ops import transform

from weather_coverage.geography import TO_METRIC, TO_WGS84, tags
from .geodata import BOUNDS, DATA, Layer, collection, feature, polygons

PEDESTRIAN = {"footway", "path", "pedestrian", "steps", "cycleway", "bridleway"}
VEHICLE = {"trunk", "primary", "secondary", "tertiary", "residential", "living_street",
           "service", "unclassified", "track", "road", "trunk_link", "primary_link",
           "secondary_link", "tertiary_link"}
MAIN = {"trunk", "primary", "secondary", "tertiary", "residential", "unclassified", "living_street"}
SIDES = ("С", "СВ", "В", "ЮВ", "Ю", "ЮЗ", "З", "СЗ")


def category(meta):
    use = {meta.get("building"), meta.get("amenity"), meta.get("healthcare")}
    if use & {"hospital", "clinic", "doctors", "doctor"}:
        return "medical"
    if use & {"fire_station", "police", "ambulance_station", "rescue_station"}:
        return "emergency"
    if use & {"school", "kindergarten", "university", "college"}:
        return "education"
    if use & {"apartments", "house", "residential", "dormitory", "detached", "terrace"}:
        return "residential"
    return "other"


def address(meta):
    return meta.get("addr:full") or ", ".join(filter(None, [meta.get("addr:street"), meta.get("addr:housenumber")]))


def osm_id(key):
    return f"osm:{ {'n': 'node', 'w': 'way', 'r': 'relation'}[key[0]] }:{key[1:]}"


class AccessCatalog:
    def __init__(self, city):
        self.revision = city.data_revision
        elements = city.osm_elements
        nodes = {k: Point(float(e.get("lon")), float(e.get("lat"))) for k, e in elements.items() if k.startswith("n")}
        metric = {k: transform(TO_METRIC, p) for k, p in nodes.items()}
        roads, road_nodes, incomplete = [], [], 0
        parent = {}

        def find(k):
            parent.setdefault(k, k)
            while parent[k] != k:
                parent[k] = parent[parent[k]]
                k = parent[k]
            return k

        def union(a, b):
            a, b = find(a), find(b)
            if a != b:
                parent[max(a, b)] = min(a, b)

        for key, elem in elements.items():
            meta = tags(elem)
            highway = meta.get("highway")
            if not key.startswith("w") or highway not in VEHICLE | PEDESTRIAN:
                continue
            refs = ["n" + n.get("ref") for n in elem.findall("nd")]
            if len(refs) < 2 or any(k not in nodes for k in refs):
                incomplete += 1
                continue
            for a, b in zip(refs, refs[1:]):
                union(a, b)
            pedestrian = highway in PEDESTRIAN
            denied = meta.get("motor_vehicle", meta.get("vehicle", meta.get("access"))) in {"no", "private"}
            line = LineString([nodes[k] for k in refs])
            roads.append(feature(line, osm_id(key), highway=highway, name=meta.get("name"),
                                 mode="pedestrian" if pedestrian else "vehicle", service=meta.get("service"),
                                 access=meta.get("access"), motor_vehicle=meta.get("motor_vehicle"),
                                 foot=meta.get("foot"), surface=meta.get("surface"), width=meta.get("width"),
                                 vehicle_candidate=not pedestrian and not denied,
                                 special_equipment_verified=False, source="OpenStreetMap",
                                 source_url=f"https://www.openstreetmap.org/way/{key[1:]}",
                                 length_m=round(transform(TO_METRIC, line).length, 1)))
            road_nodes.append(refs)
        components = defaultdict(list)
        for i, refs in enumerate(road_nodes):
            components[find(refs[0])].append(i)
        anchored = {root for root, ids in components.items() if any(roads[i]["properties"]["highway"] in MAIN for i in ids)}
        for road, refs in zip(roads, road_nodes):
            road["properties"].update(component_id=find(refs[0]), connected_to_street=find(refs[0]) in anchored)
        self.roads = Layer(roads)
        self.metric_roads = [transform(TO_METRIC, shape(f["geometry"])) for f in roads]
        self.road_index = STRtree(self.metric_roads)
        self.membership = defaultdict(list)
        for road, refs in zip(roads, road_nodes):
            for k in refs:
                self.membership[k].append(road["id"])

        buildings = city.osm_layers[0].features
        self.buildings = {f["id"]: f for f in buildings}
        self.metric_buildings = [transform(TO_METRIC, shape(f["geometry"])) for f in buildings]
        self.building_index = STRtree(self.metric_buildings)
        self.building_ids = [f["id"] for f in buildings]
        self.object_samples = {}
        objects = []
        for key, elem in elements.items():
            meta = tags(elem)
            is_door = meta.get("entrance") not in {None, "no"} or meta.get("exit") is not None
            stop = meta.get("highway") == "bus_stop" or (meta.get("public_transport") in {"platform", "stop_position"} and meta.get("bus") == "yes")
            medical = category(meta) == "medical"
            if key.startswith("n") and (is_door or stop or medical):
                p = metric[key]
                bid = None
                if is_door and len(buildings):
                    candidates = self.building_index.query(p.buffer(3), predicate="intersects")
                    candidates = sorted(candidates, key=lambda i: self.metric_buildings[i].boundary.distance(p))
                    if len(candidates) and self.metric_buildings[candidates[0]].boundary.distance(p) <= 3:
                        bid = self.building_ids[candidates[0]]
                b = self.buildings.get(bid, {}).get("properties", {})
                bk = None if not bid or not bid.startswith("osm:") else bid.split(":")[1][0] + bid.split(":")[2]
                bmeta = tags(elements[bk]) if bk in elements else {"building": b.get("building_type")}
                item = self._object(nodes[key], osm_id(key), "entrance" if is_door else "bus_stop" if stop else "medical",
                                    category(bmeta) if is_door else "transport" if stop else "medical",
                                    meta.get("name") or b.get("name"), address(meta) or b.get("address"), bid,
                                    source="OpenStreetMap", source_url=f"https://www.openstreetmap.org/node/{key[1:]}",
                                    role=meta.get("entrance"), stop_type=meta.get("public_transport"),
                                    connection_ids=self.membership.get(key, []))
                objects.append(item)
        # Healthcare polygons also have perimeter exposure even without mapped entrances.
        for bid, geom in zip(self.building_ids, self.metric_buildings):
            key = bid.split(":")[1][0] + bid.split(":")[2] if bid.startswith("osm:") else None
            meta = tags(elements[key]) if key in elements else {}
            if category(meta) == "medical":
                b = self.buildings[bid]["properties"]
                objects.append(self._object(transform(TO_WGS84, geom.representative_point()), bid + ":medical",
                                            "medical", "medical", meta.get("name"), b.get("address"), bid,
                                            source="OpenStreetMap", source_url=f"https://www.openstreetmap.org/{bid.split(':')[1]}/{bid.split(':')[2]}"))
        csv_file = DATA / "pkc_bus_stops.csv"
        imported = matched = invalid = 0
        by_id = {f["id"]: f for f in objects}
        if city.use_building_evidence and csv_file.exists():
            with csv_file.open(encoding="utf-8-sig", newline="") as stream:
                for row in csv.DictReader(stream):
                    try:
                        lon, lat = float(row["longitude"]), float(row["latitude"])
                        if not all(map(math.isfinite, (lon, lat))) or not box(*BOUNDS).covers(Point(lon, lat)):
                            raise ValueError("outside city extent")
                        identifier = "osm:node:" + str(int(row["osm_id"]))
                    except (ValueError, KeyError):
                        invalid += 1
                        continue
                    if identifier in by_id:
                        by_id[identifier]["properties"]["additional_source"] = "user_csv:pkc_bus_stops.csv"
                        by_id[identifier]["properties"]["csv_distance_m"] = round(transform(TO_METRIC, shape(by_id[identifier]["geometry"])).distance(Point(TO_METRIC(lon, lat))), 1)
                        matched += 1
                        continue
                    item = self._object(Point(lon, lat), identifier, "bus_stop", "transport", row.get("name"), None, None,
                                        source="user_csv:pkc_bus_stops.csv", source_url=f"https://www.openstreetmap.org/node/{row['osm_id']}",
                                        stop_type=row.get("type"), connection_ids=self.membership.get("n" + row["osm_id"], []))
                    objects.append(item)
                    by_id[identifier] = item
                    imported += 1
        self.objects = Layer(objects)
        counts = Counter(f["properties"]["kind"] for f in objects)
        with_entrance = {f["properties"]["building_id"] for f in objects if f["properties"]["kind"] == "entrance"}
        self.audit = {"source": "OSM local snapshots + user CSV", "revision": self.revision,
                      "roads": len(roads), "by_mode": dict(Counter(f["properties"]["mode"] for f in roads)),
                      "components": len(components), "components_without_street": len(set(components)-anchored),
                      "incomplete_ways": incomplete, "objects": dict(counts), "buildings": len(buildings),
                      "buildings_with_mapped_entrance": len(with_entrance - {None}),
                      "entrances_without_building": sum(f["properties"]["kind"] == "entrance" and not f["properties"]["building_id"] for f in objects),
                      "csv": {"imported": imported, "matched_osm_id": matched, "invalid": invalid},
                      "limitations": ["Нет полевой проверки проходов и допуска спецтехники", "Ближайшая линия не доказывает доступ", "Отсутствующие входы и связи не синтезируются", "Дата актуальности пользовательского CSV неизвестна"]}

    def _object(self, point, identifier, kind, cat, name, addr, bid, **extra):
        metric = transform(TO_METRIC, point)
        nearest = self.road_index.nearest(metric) if self.metric_roads else None
        gap = metric.distance(self.metric_roads[nearest]) if nearest is not None else None
        samples = [(point.x, point.y, None)]
        sides = []
        if bid:
            geom = transform(TO_METRIC, shape(self.buildings[bid]["geometry"]))
            # Exterior samples 5 m outside each wall, with a 3 m sampling step.
            candidates = []
            for part in polygons(geom):
                ring = list(part.exterior.coords)
                for a, b in zip(ring, ring[1:]):
                    dx, dy = b[0]-a[0], b[1]-a[1]
                    length = math.hypot(dx, dy)
                    if not length:
                        continue
                    nx, ny = dy/length, -dx/length  # CCW exterior: outward to right
                    side = SIDES[int((math.degrees(math.atan2(nx, ny)) % 360 + 22.5)//45) % 8]
                    for i in range(max(1, math.ceil(length/3))):
                        f = (i+.5)/max(1, math.ceil(length/3))
                        q = Point(a[0]+f*dx+5*nx, a[1]+f*dy+5*ny)
                        if not geom.covers(q):
                            candidates.append((*TO_WGS84(q.x, q.y), side))
                if kind == "entrance":
                    nearest_wall = part.exterior.interpolate(part.exterior.project(metric))
                    # Only sample along this entrance's nearest wall exterior normal.
                    for a, b in zip(ring, ring[1:]):
                        edge = LineString([a, b])
                        if edge.distance(nearest_wall) > .01:
                            continue
                        dx, dy = b[0]-a[0], b[1]-a[1]
                        length = math.hypot(dx, dy)
                        if not length:
                            continue
                        nx, ny = dy/length, -dx/length
                        side = SIDES[int((math.degrees(math.atan2(nx, ny)) % 360 + 22.5)//45) % 8]
                        samples = [(*TO_WGS84(nearest_wall.x+d*nx, nearest_wall.y+d*ny), side) for d in (1, 2, 3, 4, 5)]
                        break
            sides = candidates
            if kind == "medical":
                samples = sides
        self.object_samples[identifier] = (samples, sides)
        return feature(point, identifier, object_id=identifier, kind=kind, category=cat, name=name, address=addr,
                       building_id=bid, nearest_access_id=self.roads.features[nearest]["id"] if nearest is not None and gap <= 30 else None,
                       access_distance_m=round(gap, 1) if gap is not None and gap <= 30 else None,
                       link_status="shared_osm_node" if extra.get("connection_ids") else "nearby_only" if gap is not None and gap <= 30 else "missing",
                       operational_priority="high" if cat in {"medical", "emergency"} else "standard",
                       location_verified=False, **extra)

    @lru_cache(maxsize=16)
    def exposure(self, runner, run_id, frame):
        result = runner.status(run_id)
        if not result:
            raise LookupError("Расчёт не найден")
        if result["status"] != "completed":
            raise RuntimeError("Расчёт ещё не завершён")
        if result["provenance"]["city_revision"] != self.revision:
            raise RuntimeError("Геоданные изменились: выполните новый расчёт")
        fields = runner.probe_fields(run_id)
        timeline = fields.get("timeline", [])
        if frame is not None and not 0 <= frame < len(timeline):
            raise ValueError("Кадр отсутствует в расчёте")
        grid = timeline[frame] if frame is not None else fields["snow_cm"]
        w, s, e, n = result["bounds"]
        rows, cols = grid.shape

        def sample(candidates, first=False):
            valid = []
            for lon, lat, side in candidates:
                if not (w <= lon < e and s < lat <= n):
                    continue
                i, j = int((lon-w)/(e-w)*cols), int((n-lat)/(n-s)*rows)
                value = float(grid[j, i])
                if math.isfinite(value):
                    valid.append((value, lon, lat, side))
                    if first:
                        break
            return max(valid, default=None, key=lambda x: x[0])

        output = []
        for obj in self.objects.query((w, s, e, n)):
            props = obj["properties"]
            points, sides = self.object_samples[obj["id"]]
            hit = sample(points, first=props["kind"] == "entrance")
            worst = sample(sides)
            selected = hit
            snow = round(selected[0], 4) if selected else None
            where = list(selected[1:3]) if selected else None
            output.append({**obj, "properties": {**props, "snow_cm": snow,
                "sample_coordinates": where, "sample_side": selected[3] if selected else None,
                "sample_distance_m": round(transform(TO_METRIC, shape(obj["geometry"])).distance(Point(TO_METRIC(*where))), 1) if where else None,
                "cell_width_m": round((e-w)/cols*111195*math.cos(math.radians(shape(obj["geometry"]).y)), 1),
                "cell_height_m": round((n-s)/rows*111195, 1),
                "worst_side": worst[3] if worst else None, "worst_side_snow_cm": round(worst[0], 4) if worst else None,
                "worst_side_coordinates": list(worst[1:3]) if worst else None,
                "confidence": "not_field_validated", "data_kind": "model_result",
                "explanation": "Первая доступная ячейка в 1–5 м снаружи входа" if props["kind"] == "entrance" and props["building_id"] else
                               "Максимум в 5 м от наружных стен" if props["kind"] == "medical" and props["building_id"] else "Ячейка в координате OSM/CSV",
                "missing_reason": None if selected else "Нет доступной расчётной ячейки у объекта"}})
        return output

    def ranking(self, runner, run_id, bbox, frame, threshold, kind, limit, offset):
        rows = self.exposure(runner, run_id, frame)
        extent = box(*bbox)
        rows = [f for f in rows if extent.intersects(shape(f["geometry"])) and (kind == "all" or f["properties"]["kind"] == kind)]
        rows = sorted(rows, key=lambda f: (f["properties"]["snow_cm"] is None, -(f["properties"]["snow_cm"] or 0), f["id"]))
        count = len(rows)
        above = sum(f["properties"]["snow_cm"] is not None and f["properties"]["snow_cm"] >= threshold for f in rows)
        unknown = sum(f["properties"]["snow_cm"] is None for f in rows)
        rows = [{**f, "properties": {**f["properties"], "risk": "unknown" if f["properties"]["snow_cm"] is None else
                                  "above_threshold" if f["properties"]["snow_cm"] >= threshold else "below_threshold"}} for f in rows[offset:offset+limit]]
        return collection(rows, run_id=run_id, frame=frame, revision=self.revision, total=count, unknown=unknown,
                          above_threshold=above, threshold_cm=threshold, threshold_status="experimental_not_normative",
                          offset=offset, limit=limit, source=runner.status(run_id)["source"], field_validated=False)
