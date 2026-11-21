import logging
import subprocess
from threading import BoundedSemaphore
import time
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field, model_validator

from .model import estimate
from .city_cache import CityCacheUnavailable
from .clearance import osm_project, points
from .sources import OverpassError, demo_tile, osm_buildings, tile_bounds, weather

app = FastAPI(title="Карта снежных заносов", version="0.1.0")
log = logging.getLogger(__name__)
foam_slot = BoundedSemaphore(1)


class Tile(BaseModel):
    z: int = Field(ge=15, le=20)
    x: int = Field(ge=0)
    y: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_range(self):
        if max(self.x, self.y) >= 2**self.z:
            raise ValueError("x и y должны быть меньше 2**z")
        return self


class ManualWeather(BaseModel):
    wind_from_deg: float = Field(default=270, ge=0, lt=360)
    wind_speed_ms: float = Field(default=8, ge=0, le=70)
    snowfall_cm_h: float = Field(default=2, ge=0, le=50)


class SnowRequest(BaseModel):
    tile: Tile | None = None
    manual_weather: ManualWeather | None = None
    hours: int = Field(default=3, ge=1, le=24)
    resolution: int = Field(default=120, ge=24, le=240)
    use_foam: bool = True
    city_cache_only: bool = False
    run_foam: bool = False

    @model_validator(mode="after")
    def validate_foam_weather(self):
        if self.run_foam and self.tile is not None and self.manual_weather is None:
            raise ValueError("Для расчёта тайла в OpenFOAM задайте погоду вручную")
        return self


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(Path(__file__).parent/"static"/"index.html")


@app.get("/clearance/points")
def clearance_points():
    return points()


@app.get("/clearance/map.js", include_in_schema=False)
def clearance_script():
    return FileResponse(Path(__file__).parent / "static/clearance_map.js", media_type="text/javascript")


@app.get("/clearance/vendor/{asset}", include_in_schema=False)
def leaflet_asset(asset: str):
    vendor = osm_project() / "shared/snowsite/apps/web/vendor"
    if asset not in ("leaflet.js", "leaflet.css") or not (vendor / asset).is_file():
        raise HTTPException(404)
    return FileResponse(vendor / asset)


@app.post("/api/snow")
def calculate(request: SnowRequest):
    started = time.perf_counter()
    demo = request.tile is None
    try:
        if demo:
            tile, bounds, buildings = demo_tile()
        else:
            tile = (request.tile.z, request.tile.x, request.tile.y)
            bounds = tile_bounds(*tile)
            if request.city_cache_only:
                buildings = osm_buildings(bounds, cache_only=True)
                if buildings is None:
                    return Response(status_code=204)
            else:
                buildings = osm_buildings(bounds)
    except CityCacheUnavailable as error:
        raise HTTPException(503, str(error)) from error
    except OverpassError as error:
        log.warning("Overpass failed for tile %s: %s", tile, error)
        raise HTTPException(502, f"Не удалось получить здания из Overpass ({error})") from error
    except (ValueError, KeyError, TypeError) as error:
        log.warning("Overpass failed: %s", error)
        raise HTTPException(502, "Не удалось получить здания из Overpass") from error
    if request.manual_weather is not None or demo:
        manual = request.manual_weather or ManualWeather()
        hourly = [{
            "wind_from_deg": manual.wind_from_deg,
            "wind_speed_ms": manual.wind_speed_ms,
            "snowfall_cm": manual.snowfall_cm_h,
            "precipitation_mm": None,
        }]*request.hours
        weather_source = "manual" if request.manual_weather else "demo"
    else:
        try:
            hourly = weather(bounds, request.hours)
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as error:
            log.warning("Open-Meteo failed: %s", error)
            raise HTTPException(502, "Не удалось получить почасовой прогноз Open-Meteo") from error
        weather_source = "Open-Meteo"

    calc_start = time.perf_counter()
    if request.run_foam:
        if not foam_slot.acquire(blocking=False):
            raise HTTPException(429, "OpenFOAM уже рассчитывает другой тайл")
        try:
            result = estimate(bounds, buildings, hourly, request.resolution,
                              request.use_foam, run_foam=True)
        except subprocess.TimeoutExpired as error:
            log.warning("OpenFOAM timed out for tile %s", tile)
            raise HTTPException(504, "Истекло время ожидания OpenFOAM") from error
        except (OSError, RuntimeError, ValueError) as error:
            log.warning("OpenFOAM failed for tile %s: %s", tile, error)
            raise HTTPException(503, "OpenFOAM не завершил расчёт. Подробности в журнале сервера.") from error
        finally:
            foam_slot.release()
    else:
        result = estimate(bounds, buildings, hourly, request.resolution, request.use_foam)
    end = time.perf_counter()
    return {
        "tile": dict(zip(("z", "x", "y"), tile)),
        "bbox_wgs84": bounds,
        "geometry_source": "demo" if demo else "OpenStreetMap",
        "weather_source": weather_source,
        "weather": hourly,
        "forecast_snowfall_cm": round(sum(hour["snowfall_cm"] for hour in hourly), 3),
        "compute_ms": round((end-calc_start)*1000, 2),
        "total_ms": round((end-started)*1000, 2),
        **result,
    }
