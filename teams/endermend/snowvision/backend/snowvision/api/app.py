import asyncio, json, os
from contextlib import asynccontextmanager
from pathlib import Path
from snowvision.adapters.observation_store import HISTORY_DATABASE, load_history
from snowvision.domain.vision import SNAPSHOT_METHOD, analyze
from snowvision.services.runtime import demo, run_services
from snowvision.domain.city import aggregate, CITY, distance_km
from snowvision.adapters.yandex_weather import YandexWeather
from snowvision.adapters.storm_warnings import StormWarnings
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
from typing import Literal
from snowvision.adapters import feedback
from fastapi.responses import Response, FileResponse, StreamingResponse

ROOT=Path(__file__).resolve().parents[1] / "resources"

monitors = {}
yandex_weather = None
storm_warnings = None
journal = None

def delivery_camera_ids():
    return {c['id'] for c in cameras if c['type'] != 'demo'
            and c.get('city_observation') is True and c.get('alert_delivery') is True}

@asynccontextmanager
async def lifespan(app):
    global yandex_weather, storm_warnings, journal
    async with run_services(cameras, monitors, delivery_camera_ids()) as services:
        yandex_weather, storm_warnings, journal = services
        yield

cameras=json.loads((ROOT/'cameras.json').read_text())
app=FastAPI(lifespan=lifespan)
def camera(id):
    for c in cameras:
        if c['id']==id: return c
    raise HTTPException(404,'Камера не найдена')
@app.get('/api/health')
def health(): return {'status':'ok'}
@app.get('/api/cameras')
def listing(): return [dict(c,observation=monitors[c['id']].observation() if c['id'] in monitors else None) for c in cameras]
@app.get('/api/cameras/{id}')
def detail(id:str): return dict(camera(id),observation=monitors[id].observation() if id in monitors else None)
@app.get('/api/cameras/{id}/status')
def status(id:str):
    camera(id); return monitors[id].observation() if id in monitors else {'status':'unknown'}
@app.get('/api/cameras/{id}/snapshot')
async def snapshot(id: str, request: Request, v: str | None = None):
    camera(id)
    monitor = monitors.get(id)
    if monitor is None or monitor.image is None:
        raise HTTPException(404, 'Кадр ещё не получен')
    version = v or monitor.state['frame_version']
    image = monitor.recent_images.get(version) if version else monitor.image
    if image is None:
        raise HTTPException(409, 'Версия кадра больше не хранится в краткой истории')
    headers = {'ETag': f'"{version}"', 'Cache-Control': 'private, no-cache'}
    if request.headers.get('if-none-match') == headers['ETag']:
        return Response(status_code=304, headers=headers)
    return Response(image, media_type='image/jpeg', headers=headers)

class FrameFeedback(BaseModel):
    version: str
    snow_on_ground: Literal['yes', 'no', 'uncertain']
    possible_icing: Literal['yes', 'no', 'uncertain']
    falling_snow: Literal['yes', 'no', 'uncertain']


@app.get('/api/cameras/{id}/feedback')
def get_feedback(id: str, version: str):
    camera(id)
    return feedback.load(HISTORY_DATABASE, id, version)


@app.post('/api/cameras/{id}/feedback')
def post_feedback(id: str, body: FrameFeedback):
    config = camera(id)
    monitor = monitors.get(id)
    if config['type'] == 'demo':
        raise HTTPException(400, 'Демо-кадры не принимаются для разметки')
    if monitor is None or body.version != monitor.state.get('frame_version') or body.version not in monitor.recent_images:
        raise HTTPException(409, 'Кадр изменился. Откройте разметку заново')
    observation = monitor.observation()
    labels = body.model_dump(exclude={'version'})
    feedback.save(HISTORY_DATABASE, id, body.version, labels, observation)
    return feedback.load(HISTORY_DATABASE, id, body.version)

@app.post('/api/cameras/{id}/refresh')
async def refresh_camera(id: str):
    camera(id)
    monitor = monitors.get(id)
    if monitor is None:
        raise HTTPException(503, 'Мониторинг запускается')
    result = await monitor.attempt()
    return {'result': result, 'observation': monitor.observation()}

@app.get('/api/cameras/{id}/history')
def history(id:str):
    camera(id)
    return load_history(HISTORY_DATABASE, id)
@app.get('/api/summary')
def summary(): return {'total':len(cameras),'online':sum(s['source_status']=='online' for s in (m.observation() for m in monitors.values())),'snowing_real':sum(s['status']=='snowing' and not s['is_demo'] for s in (m.observation() for m in monitors.values()))}
@app.get('/api/city/outlook')
def city_outlook():
    return {
        'observations': aggregate(listing(), events=journal.events() if journal else []),
        'weather': (yandex_weather or YandexWeather('')).observation(),
        'forecast': (yandex_weather or YandexWeather('')).forecast(),
        'warnings': (storm_warnings or StormWarnings()).observation(),
    }

@app.get('/api/monitoring')
def monitoring():
    real = [c for c in cameras if c['type'] != 'demo']
    capable = [c for c in real if (c.get('motion_analysis', True) and (c['type']=='hls' or c.get('interval_seconds', 30)<=2)) or (os.getenv('SCENE_MODEL_URL') and c.get('city_observation'))]
    def ready(config):
        if config['id'] not in monitors:
            return False
        observation = monitors[config['id']].observation()
        scene = observation.get('scene') or {}
        usable_model = (scene.get('format_valid') is True and scene.get('air_view') in ('good','limited')
                        and scene.get('falling_snow') in ('yes','no')
                        and observation.get('scene_frame_version') == observation.get('frame_version'))
        usable_cv = (observation.get('analysis_method') != SNAPSHOT_METHOD
                     and observation.get('snow_probability') is not None and observation.get('confidence',0) >= .25)
        return bool(observation.get('frame_version')) and observation['source_status']=='online' and not observation.get('is_stale') and (usable_cv or usable_model)
    ready_ids = {c['id'] for c in capable if ready(c)}
    delivery_ids = delivery_camera_ids()
    return {'real_cameras':len(real), 'onset_capable':len(capable), 'onset_online':len(ready_ids),
            'stream_interval_seconds':1, 'webhook_configured':bool(os.getenv('ALERT_WEBHOOK_URL')),
            'delivery_cameras':len(delivery_ids), 'delivery_cameras_ready':len(delivery_ids & ready_ids),
            'yandex_weather':yandex_weather.observation() if yandex_weather else {'status':'disabled'},
            'limitation':'JPEG раз в 30 секунд ограничивают обнаружение первых снежинок. Для него требуется доступная видеосерия.'}

@app.get('/api/alerts')
def alerts():
    events = journal.events() if journal else []
    context = yandex_weather.observation() if yandex_weather else {}
    if context.get('status') != 'available':
        return events
    reference = CITY['latitude'], CITY['longitude']
    nearby = {c['id'] for c in cameras if c.get('city_observation') is True
              and c.get('coordinates_lat_lon') and distance_km(c['coordinates_lat_lon'], reference) <= 1}
    for event in events:
        if (not event['is_demo'] and not event['ended_at'] and not event['evidence_stale']
                and event['camera_id'] in nearby):
            event['weather_context'] = context
    return events

@app.post('/api/alerts/{id}/acknowledge')
def acknowledge(id: str):
    result = journal.acknowledge(id) if journal else None
    if result is None:
        raise HTTPException(404, 'Сигнал не найден')
    return result

@app.get('/api/stream/status')
async def stream():
    async def events():
        previous_alerts = None
        while True:
            yield 'data: '+json.dumps(listing())+'\n\n'
            payload = json.dumps(await asyncio.to_thread(alerts))
            if payload != previous_alerts:
                yield 'event: alerts\ndata: '+payload+'\n\n'
                previous_alerts = payload
            await asyncio.sleep(1)
    return StreamingResponse(events(),media_type='text/event-stream')
@app.get('/')
def index(): return FileResponse(ROOT/'index.html')
