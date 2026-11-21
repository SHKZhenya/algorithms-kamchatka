from contextlib import asynccontextmanager
from functools import lru_cache, partial
import logging
import os
from pathlib import Path
from typing import Literal
import httpx

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import AwareDatetime, ValidationError
from starlette.concurrency import run_in_threadpool

from .city_snow import CitySnow, CitySnowRequest
from .geodata import BOUNDS, DATA, PILOT, ROOT, CityData
from .live_weather import ForecastRevisionError
from .physics import HttpWindSolver, SolverNotConnected, UnavailableWindSolver, display_field, sample_assets, solver_request
from .schema import (AssetFieldResult, CitySnapshot, Region, RunRecord, ScenarioCreate, ScenarioRecord, WeatherSample,
                     WeatherSource, WindFieldResult, WindRequest)
from .storage import Store
from .terrain import Terrain
from .weather import WeatherArchive
from .weather_atlas import WeatherAtlas
from weather_coverage.heatmap_fields import FIELDS
from .operations import PERIODS, inspection_network, weather_benchmark, weather_controls, weather_replay
from .snow_experiment import SnowStudy, SnowStudyRequest

log = logging.getLogger(__name__)
PILOT_QUERY = ",".join(map(str, PILOT))


def make_app(db_path=None, building_path=None, graph_path=None, solver=None, weather_path=None):
    atlas = ROOT / "weather_coverage"
    osm = atlas / "osm"
    supplements = () if building_path else (
        ROOT / "data/pkc/depot-east.osm.gz",
        *(p for p in sorted(osm.glob("*.osm.gz")) if p.name not in {"city-boundary.osm.gz", "stops.osm.gz"}),
    )
    archive = WeatherArchive(Path(weather_path or os.getenv("CITY_SIM_WEATHER", atlas / "heatmaps")))
    fallback_weather = WeatherArchive(archive.root.parent if archive.root.name == "heatmaps" else atlas)
    city = CityData(
        building_path or os.getenv("CITY_SIM_BUILDINGS", ROOT / "data/pkc/city.osm.gz"),
        graph_path or os.getenv("CITY_SIM_GRAPH", ROOT / "data/pkc/city.osm.gz"),
        supplements=supplements,
        terrain=Terrain(),
        building_evidence=building_path is None,
    )
    store = Store(Path(db_path or os.getenv("CITY_SIM_DB", ROOT / "city_sim/.state/city_sim.sqlite3")))

    @asynccontextmanager
    async def lifespan(api):
        await run_in_threadpool(city.metadata)
        yield

    api = FastAPI(title="Алгоритмы Камчатки · 3D", version="1.0.0", lifespan=lifespan)
    api.add_middleware(GZipMiddleware, minimum_size=1200)
    url = os.getenv("CITY_SIM_SOLVER_URL")
    api.state.wind_solver = solver or (HttpWindSolver(url) if url else UnavailableWindSolver())
    api.state.city = city
    api.state.store = store
    snow_study = SnowStudy(city, archive)
    city_snow = CitySnow(snow_study, store.path.parent / "citywide")
    atlas_view = WeatherAtlas(archive.root, lambda: city_snow.land, lambda: city_snow.storms)
    city_snow.atlas = atlas_view
    api.state.city_snow = city_snow

    @api.get("/api/v1/health")
    def health():
        try:
            snow_study.engine
            model_ready = True
        except (FileNotFoundError, ImportError):
            model_ready = False
        return {"status": "ok", "solver_connected": not isinstance(api.state.wind_solver, UnavailableWindSolver),
                "terrain_loaded": bool(city.terrain.metadata), "snow_solver_connected": model_ready}

    @api.get("/api/v1/operations/weather")
    def operation_weather(period: Literal["evening", "night"] = "evening"):
        return {**weather_replay(archive, PERIODS[period]), "controls": weather_controls(),
                "benchmark": weather_benchmark()}

    @lru_cache(maxsize=1)
    def current_network(revision):
        return inspection_network(city)

    @api.get("/api/v1/operations/network")
    def operation_network():
        return current_network(city.data_revision)

    @api.post("/api/v1/operations/snow")
    def operation_snow(payload: SnowStudyRequest):
        try:
            return snow_study.run(payload)
        except FileNotFoundError as error:
            raise HTTPException(503, str(error)) from error
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @api.get("/api/v1/operations/city/catalog")
    def city_snow_catalog():
        return city_snow.catalog()

    @api.get("/api/v1/operations/atlas")
    def atlas_catalog():
        return atlas_view.catalog()

    @api.get("/api/v1/operations/atlas/frame")
    def atlas_frame(dataset: Literal["forecast", "history", "live", "storm"], valid_at: AwareDatetime,
                    field: str = "snow", method: Literal["idw", "nearest"] = "idw", event_id: str | None = None,
                    revision: str | None = None, haze: bool = False):
        if field not in FIELDS:
            raise HTTPException(422, "Неизвестный слой погоды.")
        if haze and field != "clouds":
            raise HTTPException(422, "Дымка использует поле облачности.")
        try:
            return atlas_view.frame(dataset, valid_at, field, method, event_id, revision, haze)
        except ForecastRevisionError as error:
            raise HTTPException(409, str(error)) from error
        except ValueError as error:
            raise HTTPException(404, str(error)) from error
        except httpx.HTTPError as error:
            raise HTTPException(503, "Поставщик свежего прогноза сейчас недоступен.") from error

    @api.get("/api/v1/operations/atlas/live")
    def live_atlas_catalog(refresh: bool = False):
        try:
            return atlas_view.live_catalog(refresh)
        except (httpx.HTTPError, ValueError, KeyError) as error:
            raise HTTPException(503, "Поставщик свежего прогноза сейчас недоступен.") from error

    @api.post("/api/v1/operations/city/runs", status_code=202)
    def submit_city_snow(payload: CitySnowRequest):
        try:
            return city_snow.submit(payload)
        except FileNotFoundError as error:
            raise HTTPException(503, str(error)) from error
        except httpx.HTTPError as error:
            raise HTTPException(503, "Поставщик свежего прогноза сейчас недоступен.") from error
        except ForecastRevisionError as error:
            raise HTTPException(409, str(error)) from error
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        except RuntimeError as error:
            raise HTTPException(429, str(error)) from error

    @api.get("/api/v1/operations/city/runs/{run_id}")
    def city_snow_status(run_id: str):
        try:
            result = city_snow.status(run_id)
        except ValueError as error:
            raise HTTPException(404, "Расчёт не найден.") from error
        if result is None:
            raise HTTPException(404, "Расчёт не найден.")
        return result

    @api.get("/api/v1/operations/city/runs/{run_id}/replay")
    def download_city_snow(run_id: str):
        try:
            path = city_snow.replay_path(run_id)
        except ValueError as error:
            raise HTTPException(404, "Файл расчёта не найден.") from error
        if not path.is_file():
            raise HTTPException(404, "Файл расчёта не найден.")
        return FileResponse(path, media_type="application/gzip", filename=f"city-snow-{run_id}.json.gz")

    @api.get("/api/v1/operations/city/runs/{run_id}/surface")
    def city_snow_surface(run_id: str, frame: int | None = Query(default=None, ge=0, le=64)):
        try:
            result = city_snow.surface(run_id, frame)
        except (ValueError, FileNotFoundError) as error:
            raise HTTPException(404, "Слой снега недоступен.") from error
        if result is None:
            raise HTTPException(404, "Расчёт не найден.")
        return result

    @api.get("/api/v1/operations/city/runs/{run_id}/sample")
    def sample_city_snow(run_id: str, lon: float = Query(ge=-180, le=180),
                        lat: float = Query(ge=-90, le=90), frame: int | None = Query(default=None, ge=0, le=64),
                        nearby_m: int = Query(default=0, ge=0, le=30)):
        try:
            result = city_snow.probe(run_id, lon, lat, frame, nearby_m)
        except (ValueError, FileNotFoundError) as error:
            raise HTTPException(404, "Данные расчёта недоступны.") from error
        if result is None:
            raise HTTPException(404, "Расчёт не найден.")
        return result

    @api.get("/api/v1/city/metadata", response_model=CitySnapshot)
    def metadata():
        return city.metadata()

    def parse_bbox(bbox):
        try:
            west, south, east, north = (float(value) for value in bbox.split(","))
            region = Region(west=west, south=south, east=east, north=north)
        except (ValueError, ValidationError) as error:
            raise HTTPException(422, "Ожидаются west,south,east,north и размер до 0.08°.") from error
        return region.west, region.south, region.east, region.north

    def parse_inventory_bbox(bbox):
        import math
        try:
            w, s, e, n = map(float, bbox.split(','))
            if not all(map(math.isfinite, (w, s, e, n))) or not BOUNDS[0] <= w < e <= BOUNDS[2] or not BOUNDS[1] <= s < n <= BOUNDS[3]:
                raise ValueError('Outside available city extent')
            return w, s, e, n
        except ValueError as error:
            raise HTTPException(422, 'Expected a finite bbox within the available city extent') from error

    @api.get("/api/v1/city/base")
    def base(bbox: str | None = None):
        city.data_revision
        data = city.osm_layers[1]
        if bbox is None:
            return data
        from shapely.geometry import box, shape
        extent = box(*parse_bbox(bbox))
        return {**data, "features": [f for f in data["features"] if shape(f["geometry"]).intersects(extent)]}

    @api.get("/api/v1/city/buildings")
    def buildings(bbox: str = Query(PILOT_QUERY)):
        return city.buildings(parse_bbox(bbox))

    @api.get("/api/v1/city/building-candidates")
    def building_candidates(bbox: str = Query(PILOT_QUERY)):
        return city.building_candidates(parse_bbox(bbox))

    @api.get("/api/v1/city/roads")
    def roads(bbox: str = Query(PILOT_QUERY), network: Literal["audit", "city"] = "audit"):
        return city.roads(parse_bbox(bbox), extended=network == "city")

    @api.get("/api/v1/city/infrastructure")
    def infrastructure(bbox: str = Query(PILOT_QUERY)):
        return city.infrastructure(parse_bbox(bbox))

    def access_catalog():
        city.data_revision
        return city.access_catalog

    from .object_api import register_object_api
    object_registry = register_object_api(api, city, city_snow, parse_inventory_bbox)
    from .dynamic_api import register_dynamic_api
    register_dynamic_api(api, city, store.path, parse_inventory_bbox, object_registry)
    from .mobility_api import register_mobility_api
    register_mobility_api(api, object_registry, parse_inventory_bbox)
    from .procurement import register_procurement_api
    register_procurement_api(api, object_registry)

    @api.get("/api/v1/city/access-roads", tags=["Access"])
    def access_roads(bbox: str = Query(PILOT_QUERY), mode: Literal["all", "vehicle", "pedestrian"] = "all"):
        """Real OSM lines; vehicle_candidate is not an equipment admission."""
        catalog = access_catalog()
        rows = catalog.roads.query(parse_bbox(bbox))
        if mode != "all":
            rows = [f for f in rows if f["properties"]["mode"] == mode]
        return {"type": "FeatureCollection", "features": rows,
                "metadata": {"revision": catalog.revision, "source": "OpenStreetMap", "synthetic_links": False}}

    @api.get("/api/v1/city/objects", tags=["Access"])
    def access_objects(bbox: str = Query(PILOT_QUERY), kind: Literal["all", "entrance", "bus_stop", "medical"] = "all"):
        """Mapped entrances, stops and medical facilities. Missing entrances are not inferred."""
        catalog = access_catalog()
        rows = catalog.objects.query(parse_bbox(bbox))
        if kind != "all":
            rows = [f for f in rows if f["properties"]["kind"] == kind]
        return {"type": "FeatureCollection", "features": rows, "metadata": {"revision": catalog.revision}}

    @api.get("/api/v1/city/access-quality", tags=["Access"])
    def access_quality():
        """Snapshot coverage, shared-node components and missing links; no field validation."""
        return access_catalog().audit

    @api.get("/api/v1/operations/city/runs/{run_id}/objects", tags=["Access"])
    def ranked_objects(run_id: str, bbox: str = Query(PILOT_QUERY),
                       frame: int | None = Query(default=None, ge=0, le=64),
                       threshold_cm: float = Query(default=20, gt=0, le=1000, allow_inf_nan=False),
                       kind: Literal["all", "entrance", "bus_stop", "medical"] = "all",
                       limit: int = Query(default=100, ge=1, le=1000), offset: int = Query(default=0, ge=0)):
        """Rank by modeled fresh snow for this run/frame. Administrative priority is separate."""
        bounds = parse_bbox(bbox)
        try:
            return access_catalog().ranking(city_snow, run_id, bounds, frame, threshold_cm, kind, limit, offset)
        except (LookupError, FileNotFoundError) as error:
            raise HTTPException(404, str(error)) from error
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        except RuntimeError as error:
            raise HTTPException(409, str(error)) from error

    @api.get("/api/v1/city/height-audit")
    def height_audit():
        return FileResponse(DATA / "topo-audit.geojson", media_type="application/geo+json")

    @api.get("/api/v1/city/elevation")
    def elevation(lon: float = Query(ge=-180, le=180), lat: float = Query(ge=-90, le=90)):
        city.data_revision
        sample = float(city.terrain.sample(lon, lat))
        return {"elevation_m": round(sample, 2) if sample == sample else None, "vertical_datum": "EGM2008"}

    @api.get("/api/v1/tiles/{kind}/{z}/{x}/{y}.png")
    def tile(kind: Literal["dem", "relief"], z: int, x: int, y: int):
        city.data_revision
        if not (8 <= z <= 15 and 0 <= x < 2**z and 0 <= y < 2**z and city.terrain.metadata):
            raise HTTPException(404, "Тайл рельефа отсутствует.")
        return Response(city.terrain.png(z, x, y, color=kind == "relief"), media_type="image/png",
                        headers={"Cache-Control": "public, max-age=3600"})

    @api.get("/api/v1/weather", response_model=list[WeatherSample])
    def weather(lon: float = Query(ge=-180, le=180), lat: float = Query(ge=-90, le=90),
                kind: Literal["forecast", "observation"] = "forecast"):
        samples = archive.nearest(lon, lat, kind)
        return samples or fallback_weather.nearest(lon, lat, kind)

    @api.post("/api/v1/scenarios", status_code=201, response_model=ScenarioRecord)
    def create_scenario(payload: ScenarioCreate):
        region = payload.region
        if not (BOUNDS[0] <= region.west < region.east <= BOUNDS[2]
                and BOUNDS[1] <= region.south < region.north <= BOUNDS[3]):
            raise HTTPException(422, "Расчётная область выходит за пределы городской сцены.")
        if payload.weather.kind == "manual_scenario":
            payload = payload.model_copy(update={"weather": WeatherSource()})
        else:
            key = payload.weather.sample_id
            sample = archive.samples.get(key) or fallback_weather.samples.get(key)
            if not sample or sample.weather.kind != payload.weather.kind or sample.wind != payload.wind:
                raise HTTPException(422, "Параметры ветра должны совпадать с выбранной записью архива.")
            payload = payload.model_copy(update={"weather": sample.weather})
        return store.create_scenario(payload, city.data_revision)

    @api.get("/api/v1/scenarios", response_model=list[ScenarioRecord])
    def scenarios():
        return store.list_scenarios()

    @api.get("/api/v1/scenarios/{scenario_id}", response_model=ScenarioRecord)
    def scenario(scenario_id: str):
        item = store.get_scenario(scenario_id)
        if item is None:
            raise HTTPException(404, "Сценарий не найден.")
        return item

    def current_scenario(scenario_id):
        item = scenario(scenario_id)
        if item.data_revision != city.data_revision:
            raise HTTPException(409, "Снимок города изменился. Сохраните новый сценарий.")
        return item

    @api.get("/api/v1/scenarios/{scenario_id}/solver-input", response_model=WindRequest)
    def inputs(scenario_id: str):
        return solver_request(current_scenario(scenario_id), city)

    @api.post("/api/v1/scenarios/{scenario_id}/runs", status_code=201, response_model=RunRecord)
    def run(scenario_id: str):
        obj = current_scenario(scenario_id)
        wind_solver = api.state.wind_solver
        try:
            if isinstance(wind_solver, UnavailableWindSolver):
                raise SolverNotConnected("Аэродинамический решатель пока не подключён.")
            request = solver_request(obj, city)
            field = WindFieldResult.model_validate(wind_solver.calculate(request))
            return store.create_run(scenario_id, "completed", result=field.model_dump(mode="json"))
        except SolverNotConnected as error:
            return store.create_run(scenario_id, "solver_not_connected", error=str(error))
        except Exception:
            log.exception("Wind run failed for %s", scenario_id)
            return store.create_run(scenario_id, "failed", error="Ошибка решателя. Подробности в журнале сервера.")

    @api.get("/api/v1/runs/{run_id}", response_model=RunRecord)
    def get_run(run_id: str):
        obj = store.get_run(run_id)
        if obj is None:
            raise HTTPException(404, "Расчёт не найден.")
        return obj

    def physical_result(run_id):
        record = get_run(run_id)
        if record.status != "completed" or not record.result:
            raise HTTPException(404, "Расчётное поле отсутствует.")
        field = WindFieldResult.model_validate(record.result)
        if field.source_kind != "physical_solver":
            raise HTTPException(404, "Доступны поля физического решателя.")
        return record, field

    @api.get("/api/v1/runs/{run_id}/field")
    def field_view(run_id: str):
        _, field = physical_result(run_id)
        return display_field(field)

    @api.get("/api/v1/runs/{run_id}/assets", response_model=AssetFieldResult)
    def asset_fields(run_id: str):
        record, field = physical_result(run_id)
        obj = current_scenario(record.scenario_id)
        graph_version, samples = sample_assets(city, obj.region, field)
        return AssetFieldResult(
            run_id=run_id, scenario_id=record.scenario_id, graph_version=graph_version,
            reference_height_m=field.grid.reference_height_m, pressure_reference=field.pressure_reference,
            samples=samples)

    @api.get("/api/v1/scenarios/{scenario_id}/runs", response_model=list[RunRecord])
    def runs(scenario_id: str):
        scenario(scenario_id)
        return store.list_runs(scenario_id)

    web = Path(__file__).resolve().parent / "web"

    if web.is_dir():
        @api.get("/", include_in_schema=False)
        def index():
            return FileResponse(web / "index.html")

        api.mount("/static", StaticFiles(directory=web), name="static")
    return api


app = make_app()
