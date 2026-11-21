"""Local entrances, bus stops and healthcare buildings for the interactive map."""

import csv
import gzip
from functools import lru_cache
import json
import math
import os
from pathlib import Path
from xml.etree import ElementTree

from shapely.geometry import Polygon
from shapely.ops import unary_union

from .city_cache import CITY_CACHE
from .sources import parse_buildings, polygons, projection

ROOT = Path(__file__).resolve().parent.parent
HEALTHCARE = "Больницы и поликлиники"
HEALTHCARE_DISTANCE_M = 5


def address(tags):
    return tags.get("addr:full") or ", ".join(filter(None, (
        tags.get("addr:street") or tags.get("addr:place"), tags.get("addr:housenumber"),
    )))


def entrance_csv():
    configured = os.environ.get("KAMCHATKA_ENTRANCE_CSV")
    if configured:
        return Path(configured).expanduser()
    local = ROOT / "data/building-entrance-snow.csv"
    return local if local.is_file() else ROOT.parent / "stats/building-entrance-snow.csv"


def osm_project():
    configured = os.environ.get("KAMCHATKA_OSM_DIR")
    return Path(configured).expanduser() if configured else ROOT.parent / "algorithms-kamchatka"


def category(tags, building):
    use = {building, tags.get("building"), tags.get("amenity")}
    if use & {"hospital", "clinic", "doctors"} or tags.get("healthcare") in {
        "hospital", "clinic", "doctor", "doctors"
    }:
        return HEALTHCARE
    if use & {"townhall", "courthouse", "government", "public_building"} or (
        tags.get("office") in {"government", "administrative"}
    ):
        return "Администрация и госучреждения"
    if use & {"fire_station", "rescue_station", "police", "ambulance_station"}:
        return "Экстренные службы"
    if use & {"school", "kindergarten", "college", "university"}:
        return "Школы, детсады и вузы"
    if use & {"apartments", "house", "residential", "detached", "dormitory",
              "semidetached_house", "terrace"}:
        return "Жилые дома"
    if use & {"retail", "commercial", "supermarket", "office"} or tags.get("shop"):
        return "Магазины и офисы"
    return "Другие здания"


def stops(project):
    directories = (project / "data/pkc", project / "weather_coverage/osm")
    nodes = {}
    for directory in directories:
        for path in sorted(directory.glob("*.osm.gz")):
            with gzip.open(path, "rb") as stream:
                for element in ElementTree.parse(stream).getroot().iterfind("node"):
                    tags = {tag.attrib["k"]: tag.attrib["v"] for tag in element.iterfind("tag")}
                    identifier = int(element.attrib["id"])
                    version = int(element.attrib.get("version", 0))
                    is_stop = tags.get("highway") == "bus_stop" or (
                        tags.get("public_transport") == "platform" and tags.get("bus") == "yes"
                    )
                    if identifier in nodes and version < nodes[identifier][0]:
                        continue
                    point = None
                    if is_stop:
                        point = dict(
                            id=f"node/{identifier}", name=tags.get("name") or "Остановка без названия",
                            lat=float(element.attrib["lat"]), lon=float(element.attrib["lon"]),
                            address=address(tags),
                            category="Автобусные остановки",
                        )
                    nodes[identifier] = version, point
    return [point for _, point in nodes.values() if point is not None]


def healthcare_buildings(elements):
    result = []
    for element in elements:
        tags = element.get("tags", {})
        if category(tags, None) != HEALTHCARE:
            continue
        geometry = element.get("geometry", []) + [
            coord for member in element.get("members", []) for coord in member.get("geometry", [])
        ]
        if not geometry:
            continue
        lon = [coord["lon"] for coord in geometry]
        lat = [coord["lat"] for coord in geometry]
        west, south = min(lon), min(lat)
        bounds = west, south, max(lon), max(lat)
        sx, sy, _, _ = projection(bounds)
        parts = parse_buildings([element], bounds)
        if not parts:
            continue
        footprint = unary_union([part.footprint for part in parts])
        marker = footprint.representative_point()
        # Offset exterior walls in metres. Exclude inner courtyards.
        outside = unary_union([Polygon(part.exterior) for part in polygons(footprint)])
        samples = []
        for part in polygons(outside.buffer(HEALTHCARE_DISTANCE_M)):
            ring = part.exterior
            count = math.ceil(ring.length / 3)
            for i in range(count):
                sample = ring.interpolate(i / count, normalized=True)
                samples.append((south + sample.y / sy, west + sample.x / sx))
        identifier = f"{element['type']}/{element['id']}"
        result.append(dict(
            id=identifier, name=tags.get("name") or f"Медучреждение {identifier}",
            address=address(tags), category=HEALTHCARE,
            lat=south + marker.y / sy, lon=west + marker.x / sx, samples=samples,
        ))
    return result


def doors(path, elements):
    if not path.is_file():
        if os.environ.get("KAMCHATKA_ENTRANCE_CSV"):
            raise FileNotFoundError(path)
        return []
    tags_by_id = {f"{e['type']}/{e['id']}": e.get("tags", {}) for e in elements}
    result = []
    with path.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            if row["point_source"] != "osm_node":
                continue
            tags = tags_by_id.get(row["building_id"], {})
            result.append(dict(
                id=f"node/{row['entrance_id']}", building=row["building_id"],
                kind=row["building_type"], address=row["address"] or address(tags), name=tags.get("name"),
                category=category(tags, row["building_type"]),
                lat=float(row["lat"]), lon=float(row["lon"]), side=row["side"],
                bearing=float(row["bearing_deg"]) if row["bearing_deg"] else None,
                role=row["entrance"], exit=row["exit"],
            ))
    return result


@lru_cache(maxsize=1)
def points():
    try:
        elements = json.loads(CITY_CACHE.read_text(encoding="utf-8"))["elements"]
    except FileNotFoundError:
        elements = []
    return {
        "doors": doors(entrance_csv(), elements),
        "stops": stops(osm_project()),
        "healthcare": healthcare_buildings(elements),
    }
