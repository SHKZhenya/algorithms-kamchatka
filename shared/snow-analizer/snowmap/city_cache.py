"""Download one Overpass snapshot of Petropavlovsk-Kamchatsky."""
import json
import os
import tempfile
from functools import lru_cache
from pathlib import Path

import httpx
from shapely.geometry import LineString, box
from shapely.ops import polygonize, unary_union
from shapely.strtree import STRtree

CITY_ID = 4139922
CITY_CACHE = Path(__file__).resolve().parent.parent / "data/petropavlovsk-buildings.json"
VK_OVERPASS = "https://maps.mail.ru/osm/tools/overpass/api/interpreter"
CITY_QUERY = f"""
[out:json][timeout:90];
rel({CITY_ID})->.city;
.city map_to_area ->.cityArea;
(
  .city;
  way["building"]["building"!="no"](area.cityArea);
  relation["type"="multipolygon"]["building"]["building"!="no"](area.cityArea);
);
out geom;
"""


def build_snapshot(data):
    if data.get("remark"):
        raise ValueError(data["remark"])
    elements = data["elements"]
    boundary = next(
        (item for item in elements if item["type"] == "relation" and item["id"] == CITY_ID), None
    )
    if boundary is None:
        raise ValueError("В ответе Overpass отсутствует граница города")

    def points(item):
        return [(p["lon"], p["lat"]) for p in item.get("geometry", [])]

    rings = {"outer": [], "inner": []}
    for member in boundary.get("members", []):
        if member.get("role") in rings and len(coords := points(member)) >= 2:
            rings[member["role"]].append(LineString(coords))
    city = unary_union(list(polygonize(rings["outer"]))).difference(
        unary_union(list(polygonize(rings["inner"])))
    )
    buildings, extents = [], []
    for element in elements:
        if element is boundary:
            continue
        coords = points(element)
        for member in element.get("members", []):
            coords.extend(points(member))
        if coords:
            xs, ys = zip(*coords)
            buildings.append(element)
            extents.append(box(min(xs), min(ys), max(xs), max(ys)))
    if city.is_empty or not city.is_valid or not buildings:
        raise ValueError("Overpass не вернул корректную границу и здания города")
    return city, buildings, STRtree(extents)


@lru_cache(maxsize=1)
def read_snapshot(path, _modified_ns):
    return build_snapshot(json.loads(path.read_text(encoding="utf-8")))


class CityCacheUnavailable(Exception):
    pass


def cached_elements(west, south, east, north, *, required=False):
    try:
        modified = CITY_CACHE.stat().st_mtime_ns
    except FileNotFoundError as error:
        if required:
            raise CityCacheUnavailable("Нет городского кеша зданий. Запустите python -m snowmap.city_cache") from error
        return None
    try:
        city, buildings, index = read_snapshot(CITY_CACHE, modified)
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise CityCacheUnavailable("Не удалось прочитать городской кеш зданий") from error
    region = box(west, south, east, north)
    if not city.intersects(region):
        return None
    return [buildings[i] for i in sorted(index.query(region))]


def main():
    endpoint = os.getenv("OVERPASS_URL") or VK_OVERPASS
    try:
        response = httpx.post(
            endpoint,
            data={"data": CITY_QUERY},
            headers={"User-Agent": "KamchaSnowMap/0.1"},
            timeout=httpx.Timeout(120, connect=8),
        )
        response.raise_for_status()
        data = response.json()
        _, buildings, _ = build_snapshot(data)
        CITY_CACHE.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("wb", dir=CITY_CACHE.parent, delete=False) as stream:
            temporary = Path(stream.name)
            try:
                stream.write(response.content)
                stream.close()
                temporary.replace(CITY_CACHE)
            finally:
                temporary.unlink(missing_ok=True)
    except (httpx.HTTPError, ValueError, KeyError, OSError) as error:
        raise SystemExit(f"Не удалось обновить кеш зданий: {error}") from error
    print(f"Сохранено объектов зданий: {len(buildings)}. Файл: {CITY_CACHE}")


if __name__ == "__main__":
    main()
