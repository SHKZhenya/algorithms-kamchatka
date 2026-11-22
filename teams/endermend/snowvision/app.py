import asyncio, json, sqlite3, time, hashlib, os
from contextlib import asynccontextmanager
from pathlib import Path
import cv2
import numpy as np
import httpx
from monitor import CameraMonitor
from vision import analyze, analyze_frames
from city import aggregate, CITY, distance_km
from alerts import AlertJournal
from delivery import run_delivery
from yandex_weather import YandexWeather
from storm_warnings import StormWarnings
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response, FileResponse, StreamingResponse

ROOT=Path(__file__).parent
def demo(t,kind='snow'):
    img=np.full((360,640,3),35,np.uint8)
    for x in range(20,640,90): cv2.rectangle(img,(x,130),(x+60,310),(65,75,85),-1)
    if kind=='snow':
        rng=np.random.default_rng(42)
        for x,y in rng.integers([0,0],[640,360],(100,2)):
            cv2.circle(img,((int(x)+t*2)%640,(int(y)+t*7)%360),2,(240,240,240),-1)
    if kind=='car': cv2.rectangle(img,((t*7)%550,270),((t*7)%550+80,310),(200,200,200),-1)
    return img

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
    yandex_weather = YandexWeather(os.getenv('X-Yandex-Weather-Key', '').strip())
    storm_warnings = StormWarnings()
    Path('/data').mkdir(exist_ok=True)
    with sqlite3.connect('/data/history.db') as db:
        db.execute('CREATE TABLE IF NOT EXISTS observations (camera TEXT, ts REAL, payload TEXT)')
        db.execute('CREATE TABLE IF NOT EXISTS frame_cache (camera TEXT PRIMARY KEY, image BLOB, payload TEXT)')
    journal = AlertJournal('/data/history.db', delivery_camera_ids())
    acquisition = asyncio.Semaphore(4)
    processing = asyncio.Semaphore(2)
    async with httpx.AsyncClient(timeout=15, follow_redirects=True, limits=httpx.Limits(max_connections=4)) as client, httpx.AsyncClient(timeout=20, limits=httpx.Limits(max_connections=1)) as scene_client, httpx.AsyncClient(timeout=10, limits=httpx.Limits(max_connections=1)) as delivery_client:
        monitors.clear()
        source_locks = {}
        for config in cameras:
            monitor = CameraMonitor(config, client, acquisition, processing, analyze_frames, demo, journal=journal, scene_client=scene_client, scene_url=os.getenv('SCENE_MODEL_URL',''), source_lock=source_locks.setdefault(config.get('url', config['id']), asyncio.Lock()))
            await asyncio.to_thread(monitor.restore)
            monitors[config['id']] = monitor
        tasks = [asyncio.create_task(m.run()) for m in monitors.values()]
        tasks.append(asyncio.create_task(storm_warnings.run()))
        if yandex_weather.key:
            tasks.append(asyncio.create_task(yandex_weather.run()))
        if os.getenv('ALERT_WEBHOOK_URL'):
            tasks.append(asyncio.create_task(run_delivery(journal, delivery_client, os.environ['ALERT_WEBHOOK_URL'], os.getenv('ALERT_WEBHOOK_TOKEN',''))))
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

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
    version = monitor.state['frame_version']
    if v is not None and v != version:
        raise HTTPException(409, 'Версия кадра изменилась, дождитесь метаданных')
    headers = {'ETag': f'"{version}"', 'Cache-Control': 'private, no-cache'}
    if request.headers.get('if-none-match') == headers['ETag']:
        return Response(status_code=304, headers=headers)
    return Response(monitor.image, media_type='image/jpeg', headers=headers)

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
    with sqlite3.connect('/data/history.db') as db: return [json.loads(r[0]) for r in db.execute('SELECT payload FROM observations WHERE camera=? ORDER BY ts DESC LIMIT 120',(id,))][::-1]
@app.get('/api/summary')
def summary(): return {'total':len(cameras),'online':sum(s['source_status']=='online' for s in (m.observation() for m in monitors.values())),'snowing_real':sum(s['status']=='snowing' and not s['is_demo'] for s in (m.observation() for m in monitors.values()))}
@app.get('/api/city/outlook')
def city_outlook():
    return {
        'observations': aggregate(listing()),
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
        usable_cv = observation.get('snow_probability') is not None and observation.get('confidence',0) >= .25
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
