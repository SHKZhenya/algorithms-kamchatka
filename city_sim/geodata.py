"""Spatial views of the shared OSM snapshots and canonical snowops graph."""
from collections import Counter
from functools import cached_property
from hashlib import file_digest, sha256
import gzip
import json
import os
from pathlib import Path
import re
import xml.etree.ElementTree as ET

from shapely import STRtree, make_valid
from shapely.geometry import LineString, Point, Polygon, box, mapping, shape
from shapely.ops import unary_union
from shapely.geometry.polygon import orient

from snowops.osm import load_osm, load_osm_snapshots
from weather_coverage.geography import read_osm, relation_polygon, tags
from .building_evidence import BuildingEvidence, public_address
from .terrain import BOUNDS, DATA

ROOT = Path(__file__).resolve().parent.parent
PILOT = (158.628, 53.018, 158.658, 53.047)
PARKS = {"wood", "scrub", "grassland"}


def collection(features, **metadata):
    return {"type": "FeatureCollection", "features": features, "metadata": metadata}


def feature(geometry, identifier, **properties):
    return {"type": "Feature", "id": identifier,
            "geometry": mapping(geometry), "properties": properties}


def number(value, metres=False):
    if value is None:
        return None
    suffix = r"\s*m?" if metres else ""
    match = re.fullmatch(r"\s*(\d+(?:[.,]\d+)?)" + suffix + r"\s*", value, re.I)
    return float(match[1].replace(",", ".")) if match else None


def building_height(meta):
    height = number(meta.get("height"), metres=True)
    levels = number(meta.get("building:levels"))
    issues = []
    if meta.get("height") is not None and not (height and height <= 300):
        issues.append("invalid_osm_height")
        height = None
    if meta.get("building:levels") is not None and not (levels and levels <= 100):
        issues.append("invalid_osm_levels")
        levels = None
    estimated = 3 * levels if levels else None
    if height is not None and estimated and not 2 <= height / levels <= 6:
        issues.append("height_levels_disagreement")
    source = ("osm_height" if height is not None else
              "estimated_from_levels_3m_per_level" if estimated is not None else "unknown")
    visual_default = 3 if meta["building"] in {"garage", "garages", "shed", "roof"} else 6
    return dict(height_m=height if height is not None else estimated,
                physical_height_m=height, osm_height_raw=meta.get("height"),
                levels=levels, height_source=source, height_verified=False,
                render_height_m=height or estimated or visual_default,
                render_height_is_placeholder=source == "unknown",
                height_flags=issues)


def polygons(geom):
    if geom.is_empty:
        return []
    if geom.geom_type == "Polygon":
        return [orient(geom, sign=1)]
    if hasattr(geom, "geoms"):
        return [polygon for part in geom.geoms for polygon in polygons(part)]
    return []


class Layer:
    def __init__(self, features):
        self.features = features
        self.index = STRtree([shape(item["geometry"]) for item in features])

    def query(self, bbox):
        return [self.features[i] for i in sorted(self.index.query(box(*bbox), predicate="intersects"))]


class CityData:
    def __init__(self, buildings_path, graph_path, supplements=(), terrain=None, building_evidence=True):
        self.buildings_path = Path(buildings_path)
        self.graph_path = Path(graph_path)
        self.supplements = tuple(supplements)
        access_extra = DATA / 'access-supplement.osm.gz'
        if building_evidence and access_extra.exists() and os.getenv('CITY_SIM_ACCESS_SUPPLEMENT', '1') != '0':
            self.supplements += (access_extra,)
        self.terrain = terrain
        self.use_building_evidence = building_evidence
        self.grade_path = DATA / "road-grades.json.gz"
        self._revision_cache = None

    @property
    def data_revision(self):
        paths = [self.buildings_path, self.graph_path, *self.supplements, self.grade_path, DATA / "pkc_bus_stops.csv"]
        if self.use_building_evidence:
            paths.extend(BuildingEvidence.paths)
        if self.terrain:
            paths.extend((self.terrain.path, self.terrain.path.with_suffix(".json")))
        signature = tuple((p.stat().st_size, p.stat().st_mtime_ns) if p.is_file() else None for p in paths)
        if self._revision_cache is None or self._revision_cache[0] != signature:
            if self._revision_cache:
                for key in ("osm_elements", "access_catalog", "osm_layers", "graph_layers", "extended_graph_layers", "building_evidence_metadata", "building_candidate_layer"):
                    self.__dict__.pop(key, None)
                if self.terrain:
                    self.terrain.tiles.cache_clear()
                    self.terrain.png.cache_clear()
                    self.terrain.__dict__.pop("metadata", None)
            hashes = []
            for path in paths:
                if path.is_file():
                    with path.open("rb") as handle:
                        hashes.append(file_digest(handle, "sha256").hexdigest())
                else:
                    hashes.append(f"missing:{path.name}")
            revision = sha256(("city-v1|" + "|".join(hashes)).encode()).hexdigest()[:20]
            self._revision_cache = (signature, revision)
        return self._revision_cache[1]

    @cached_property
    def osm_elements(self):
        paths = list(dict.fromkeys(p for p in (self.buildings_path, self.graph_path, *self.supplements) if p.exists()))
        elements = read_osm([p for p in paths if p.suffix == ".gz"])
        for path in paths:
            if path.suffix != ".gz":
                elements.update({e.tag[0] + e.get("id"): e for e in ET.parse(path).getroot()
                                 if e.tag in {"node", "way", "relation"}})
        return elements

    @cached_property
    def access_catalog(self):
        from .access import AccessCatalog
        return AccessCatalog(self)

    @cached_property
    def osm_layers(self):
        elements = self.osm_elements
        nodes = {key: (float(elem.get("lon")), float(elem.get("lat")))
                 for key, elem in elements.items() if key.startswith("n")}
        lines = {}
        for key, elem in elements.items():
            if key.startswith("w"):
                refs = ["n" + node.get("ref") for node in elem.findall("nd")]
                if len(refs) >= 2 and all(ref in nodes for ref in refs):
                    lines[key] = LineString([nodes[ref] for ref in refs])
        stats = Counter()
        buildings, base = [], []
        covered_ways = set()
        clip = box(*BOUNDS)

        def add_building(key, geom, meta):
            parts = polygons(geom)
            if not parts:
                return False
            geom = unary_union(parts)
            identifier = f"osm:{'way' if key[0] == 'w' else 'relation'}:{key[1:]}"
            heights = building_height(meta)
            key, address = public_address(meta)
            start = re.match(r"^(1[89]\d{2}|20[0-2]\d)(?:$|-)", meta.get("start_date", ""))
            buildings.append(feature(geom, identifier, asset_id=identifier, **heights,
                                     address_key=key, address=address,
                                     construction_year=int(start[1]) if start else None,
                                     construction_year_source="osm_start_date" if start else None,
                                     name=meta.get("name"), building_type=meta["building"],
                                     data_kind="osm_geometry"))
            return True

        water = []
        for key, elem in elements.items():
            if not key.startswith("r"):
                continue
            meta = tags(elem)
            is_building = meta.get("building") not in (None, "no") and meta.get("type") == "multipolygon"
            is_water = key == "r1763103" or meta.get("natural") == "water"
            if not (is_building or is_water):
                continue
            geom = relation_polygon(elem, lines)
            if is_building:
                if geom is not None and add_building(key, geom, meta):
                    stats["multipolygons"] += 1
                    covered_ways.update("w" + m.get("ref") for m in elem.findall("member")
                                        if m.get("type") == "way")
                else:
                    stats["unresolved_multipolygons"] += 1
            if is_water and geom is not None:
                water.extend(polygons(geom.intersection(clip)))
        for key, elem in elements.items():
            if not key.startswith("w"):
                continue
            line = lines.get(key)
            meta = tags(elem)
            is_building = meta.get("building") not in (None, "no") and key not in covered_ways
            semantic = ("water" if meta.get("natural") in {"water", "bay"} or
                        meta.get("waterway") == "riverbank" else "green" if
                        meta.get("natural") in PARKS or meta.get("landuse") in {"forest", "grass"} else None)
            if (is_building or semantic) and line is not None and line.is_closed and len(line.coords) >= 4:
                geom = make_valid(Polygon(line))
                if is_building and add_building(key, geom, meta):
                    stats["ways"] += 1
                if semantic:
                    for i, polygon in enumerate(polygons(geom.intersection(clip))):
                        if semantic == "water":
                            water.append(polygon)
                        else:
                            base.append(feature(polygon, f"{key}-{i}", kind=semantic))
            elif is_building:
                stats["unresolved_ways"] += 1
            highway = meta.get("highway")
            if line and highway in {"trunk", "primary", "secondary", "tertiary"}:
                base.append(feature(line.simplify(.000015), key, kind="arterial", name=meta.get("name")))
            elif line and highway in {"residential", "living_street", "service", "unclassified"}:
                base.append(feature(line.simplify(.000012), key, kind="street", name=meta.get("name")))
        base.extend(feature(part, f"water-{i}", kind="water")
                    for i, part in enumerate(polygons(unary_union(water))))
        self.building_evidence_metadata = {}
        if self.use_building_evidence:
            evidence = BuildingEvidence()
            buildings.extend(evidence.supplement["features"])
            node_addresses = []
            for key, coords in nodes.items():
                address, _ = public_address(tags(elements[key]))
                if address:
                    node_addresses.append((address, Point(coords)))
            self.building_evidence_metadata = evidence.enrich(buildings, node_addresses)
        for item in buildings:
            attributes = item["properties"]
            stats[attributes["height_source"]] += 1
            stats.update(attributes["height_flags"])
            attributes.pop("address_key", None)
        return Layer(buildings), collection(base, source="OpenStreetMap"), dict(stats)

    @cached_property
    def building_candidate_layer(self):
        candidates = BuildingEvidence().checks.get("candidates", []) if self.use_building_evidence else []
        return Layer(candidates)

    def building_candidates(self, bbox):
        self.data_revision
        return collection(self.building_candidate_layer.query(bbox), source="GlobalBuildingAtlas",
                          status="unverified_candidate_outlines")

    @cached_property
    def graph_layers(self):
        if not self.graph_path.is_file():
            return None, Layer([]), Layer([])
        return self._graph_layers(load_osm(self.graph_path))

    @cached_property
    def extended_graph_layers(self):
        baseline = self.graph_layers[0]
        if baseline is None or not self.supplements:
            return self.graph_layers
        paths = (self.graph_path, *(path for path in self.supplements if path.is_file()))
        return self._graph_layers(load_osm_snapshots(paths), baseline)

    def _graph_layers(self, graph, baseline=None):
        grades = {}
        if self.grade_path.exists():
            with gzip.open(self.grade_path, "rt") as stream:
                audit = json.load(stream)
            if audit["graph_version"] == (baseline or graph).version:
                grades = audit["edges"]
        roads = []
        for edge in graph.edges.values():
            original = baseline.edges.get(edge.edge_id) if baseline else edge
            audited_geometry = original is not None and all(
                graph.nodes[node] == (baseline or graph).nodes[node] for node in (edge.from_node, edge.to_node)
            )
            audit = grades.get(edge.edge_id, {}) if audited_geometry else {}
            roads.append(feature(LineString([graph.nodes[edge.from_node], graph.nodes[edge.to_node]]),
                                 edge.edge_id, edge_id=edge.edge_id, physical_road_id=edge.physical_road_id,
                                 road_class=edge.road_class, road_name=edge.name, length_m=edge.length_m,
                                 slope_pct=audit.get("grade_pct", edge.slope_pct),
                                 relief_quality=audit.get("quality"),
                                 surface_start_m=audit.get("start_m"), surface_end_m=audit.get("end_m"),
                                 graph_version=graph.version, network_extension=not audited_geometry))
        assets = [feature(Point(site.lon_lat), site.site_id,
                          object_id=site.site_id, kind=site.kind, name=site.name)
                  for site in graph.facilities]
        return graph, Layer(roads), Layer(assets)

    def buildings(self, bbox):
        revision = self.data_revision
        return collection(self.osm_layers[0].query(bbox), source="OpenStreetMap", revision=revision, building_evidence=self.building_evidence_metadata)

    def roads(self, bbox, extended=False):
        self.data_revision
        graph, roads, _ = self.extended_graph_layers if extended else self.graph_layers
        return collection(roads.query(bbox), source="snowops.osm.load_osm",
                          graph_version=graph.version if graph else None, extended=extended,
                          status="ok" if graph else "source_missing")

    def infrastructure(self, bbox):
        self.data_revision
        return collection(self.graph_layers[2].query(bbox), source="snowops.osm.load_osm")

    def metadata(self):
        revision = self.data_revision
        buildings, _, height_audit = self.osm_layers
        graph, roads, infrastructure = self.graph_layers
        return dict(data_revision=revision, bbox=BOUNDS, pilot_bbox=PILOT,
                    building_count_total=len(buildings.features), road_count_total=len(roads.features),
                    infrastructure_count_total=len(infrastructure.features), heights=height_audit,
                    graph_version=graph.version if graph else None, crs_geometry="EPSG:4326",
                    crs_solver="EPSG:32657", building_evidence=self.building_evidence_metadata, terrain=self.terrain.metadata if self.terrain else None,
                    physical_dem_loaded=bool(self.terrain and self.terrain.metadata))
