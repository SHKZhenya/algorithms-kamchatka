import math
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx
from shapely import make_valid
from shapely.affinity import affine_transform, rotate
from shapely.geometry import LineString, Polygon, box
from shapely.ops import polygonize, unary_union

from .city_cache import cached_elements

RADIUS = 6378137
BUFFER = 360
OVERPASS_URLS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
)


class OverpassError(Exception):
    pass


def fetch_overpass(query):
    urls = (override,) if (override := os.getenv("OVERPASS_URL")) else OVERPASS_URLS
    errors = []
    for url in urls:
        try:
            response = httpx.post(
                url, data={"data": query}, headers={"User-Agent": "KamchaSnowMap/0.1"},
                timeout=httpx.Timeout(25, connect=5),
            )
            response.raise_for_status()
            data = response.json()
            if data.get("remark") or not isinstance(data.get("elements"), list):
                raise ValueError("Overpass не завершил запрос или вернул неполный ответ")
            return data["elements"]
        except httpx.HTTPStatusError as error:
            status = error.response.status_code
            errors.append(f"HTTP {status}")
            if status == 400:
                break
        except httpx.ConnectTimeout:
            errors.append("сервер не принял подключение за 5 с")
        except httpx.ReadTimeout:
            errors.append("сервер не прислал ответ за 25 с")
        except httpx.TimeoutException:
            errors.append("истекло время ожидания при передаче запроса")
        except httpx.RequestError:
            errors.append("ошибка подключения или DNS")
        except (ValueError, AttributeError):
            errors.append("некорректный или неполный ответ")
    raise OverpassError(", ".join(dict.fromkeys(errors)))


@dataclass(frozen=True)
class Building:
    footprint: Polygon
    height: float
    height_source: str


def tile_bounds(z, x, y):
    n = 2**z

    def latitude(row):
        return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * row / n))))

    return (x / n * 360 - 180, latitude(y + 1),
            (x + 1) / n * 360 - 180, latitude(y))


def projection(bounds):
    west, south, east, north = bounds
    scale_y = math.pi * RADIUS / 180
    scale_x = scale_y * math.cos(math.radians((south + north) / 2))
    return scale_x, scale_y, (east - west) * scale_x, (north - south) * scale_y


def height_of(tags):
    for key, multiplier in (("height", 1), ("building:levels", 3)):
        try:
            value = float(tags.get(key, "").strip().removesuffix("m").replace(",", "."))
            height = value * multiplier
            if math.isfinite(height) and height > 0:
                return height, key
        except ValueError:
            pass
    return 9.0, "default"


def polygons(geometry):
    if geometry.geom_type == "Polygon":
        yield geometry
    elif hasattr(geometry, "geoms"):
        for part in geometry.geoms:
            yield from polygons(part)


def parse_buildings(elements, bounds):
    west, south, *_ = bounds
    sx, sy, width, height = projection(bounds)
    region = box(-BUFFER, -BUFFER, width + BUFFER, height + BUFFER)
    buildings = []
    used_members = set()

    def coords(element):
        return [(point["lon"], point["lat"]) for point in element.get("geometry", [])]

    def rings(members):
        return list(polygonize([LineString(points) for member in members
                                if len(points := coords(member)) >= 2]))

    def add(geometry, tags):
        metric = affine_transform(geometry, (sx, 0, 0, sy, -west*sx, -south*sy))
        building_height, source = height_of(tags)
        for part in polygons(make_valid(metric)):
            if part.area >= 4 and region.intersects(part):
                buildings.append(Building(part, building_height, source))

    for element in elements:
        if element["type"] == "relation":
            members = [m for m in element.get("members", []) if m["type"] == "way"]
            outer = rings([m for m in members if m["role"] in ("outer", "")])
            inner = rings([m for m in members if m["role"] == "inner"])
            if outer:
                add(unary_union(outer).difference(unary_union(inner)), element["tags"])
                used_members.update(m["ref"] for m in members)
    for element in elements:
        if element["type"] == "way" and element["id"] not in used_members:
            points = coords(element)
            if len(points) >= 4 and points[0] == points[-1]:
                add(Polygon(points), element["tags"])
    return tuple(buildings)


class Cache:
    def __init__(self, ttl, limit):
        self.ttl = ttl
        self.limit = limit
        self.entries = {}
        self.lock = threading.Lock()

    def get(self, key, load):
        with self.lock:
            entry = self.entries.get(key)
            if entry and entry[0] > time.monotonic():
                return entry[1]
        result = load()
        with self.lock:
            self.entries.pop(key, None)
            self.entries[key] = (time.monotonic() + self.ttl, result)
            if len(self.entries) > self.limit:
                del self.entries[next(iter(self.entries))]
        return result


building_cache = Cache(3600, 64)
weather_cache = Cache(300, 128)


def osm_buildings(bounds, *, cache_only=False):
    west, south, east, north = bounds
    sx, sy, *_ = projection(bounds)
    west, east = west - BUFFER/sx, east + BUFFER/sx
    cached = cached_elements(west, south-BUFFER/sy, east, north+BUFFER/sy, required=cache_only)
    if cached is not None:
        return parse_buildings(cached, bounds)
    if cache_only:
        return None

    def load():
        longitude_ranges = [(max(-180, west), min(180, east))]
        if west < -180:
            longitude_ranges.append((west + 360, 180))
        if east > 180:
            longitude_ranges.append((-180, east - 360))
        selectors = []
        for left, right in longitude_ranges:
            bbox = f"({south-BUFFER/sy},{left},{north+BUFFER/sy},{right})"
            selectors.extend((f'way["building"]["building"!="no"]{bbox};',
                              f'relation["type"="multipolygon"]["building"]["building"!="no"]{bbox};'))
        elements = fetch_overpass(f"[out:json][timeout:20];({''.join(selectors)});out geom;")
        if len(longitude_ranges) > 1:
            center = (bounds[0] + bounds[2]) / 2
            for element in elements:
                for item in [element, *element.get("members", [])]:
                    for point in item.get("geometry", []):
                        point["lon"] = center + (point["lon"] - center + 180) % 360 - 180
        return parse_buildings(elements, bounds)
    return building_cache.get(bounds, load)


def weather(bounds, hours):
    hour = int(datetime.now(timezone.utc).timestamp()) // 3600 * 3600

    def load():
        west, south, east, north = bounds
        response = httpx.get("https://api.open-meteo.com/v1/forecast", params={
            "latitude": (south + north)/2, "longitude": (west + east)/2,
            "hourly": "wind_speed_10m,wind_direction_10m,snowfall,precipitation",
            "wind_speed_unit": "ms", "timeformat": "unixtime", "timezone": "GMT",
            "forecast_hours": hours + 1,
        }, timeout=12)
        response.raise_for_status()
        data = response.json()["hourly"]
        rows = []
        for index, timestamp in enumerate(data["time"]):
            if not hour < timestamp <= hour + hours*3600:
                continue
            rows.append({
                "end_utc": datetime.fromtimestamp(timestamp, timezone.utc).isoformat(),
                "wind_speed_ms": float(data["wind_speed_10m"][index]),
                "wind_from_deg": float(data["wind_direction_10m"][index]),
                "snowfall_cm": float(data["snowfall"][index]),
                "precipitation_mm": float(data["precipitation"][index]),
            })
        if len(rows) != hours or any(
            not math.isfinite(value) for row in rows for key, value in row.items() if key != "end_utc"
        ):
            raise ValueError("Неполный почасовой прогноз")
        return rows

    return weather_cache.get((bounds, hours, hour), load)


def demo_tile():
    tile = (17, 123298, 42669)
    bounds = tile_bounds(*tile)
    _, _, width, height = projection(bounds)
    samples = [(0.30, 0.64, 30, 18, 15, 0), (0.61, 0.64, 30, 18, 15, 0),
               (0.36, 0.31, 24, 17, 9, 20), (0.73, 0.29, 25, 17, 9, 20)]
    buildings = []
    for x, y, length, depth, h, angle in samples:
        cx, cy = width*x, height*y
        shape = box(cx-length/2, cy-depth/2, cx+length/2, cy+depth/2)
        buildings.append(Building(rotate(shape, angle), h, "demo"))
    return tile, bounds, tuple(buildings)
