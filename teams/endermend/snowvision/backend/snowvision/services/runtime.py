"""Manage the resources and background workers used by the HTTP application."""

import asyncio
import os
from contextlib import asynccontextmanager

import cv2
import httpx
import numpy as np

from snowvision.adapters.alerts import AlertJournal
from snowvision.services.delivery import run_delivery
from snowvision.services.monitor import CameraMonitor
from snowvision.adapters.observation_store import HISTORY_DATABASE, initialize
from snowvision.adapters.storm_warnings import StormWarnings
from snowvision.domain.vision import analyze_frames
from snowvision.adapters.yandex_weather import YandexWeather


def demo(t,kind='snow'):
    img=np.full((360,640,3),35,np.uint8)
    for x in range(20,640,90): cv2.rectangle(img,(x,130),(x+60,310),(65,75,85),-1)
    if kind=='snow':
        rng=np.random.default_rng(42)
        for x,y in rng.integers([0,0],[640,360],(100,2)):
            cv2.circle(img,((int(x)+t*2)%640,(int(y)+t*7)%360),2,(240,240,240),-1)
    if kind=='car': cv2.rectangle(img,((t*7)%550,270),((t*7)%550+80,310),(200,200,200),-1)
    return img


@asynccontextmanager
async def run_services(cameras, monitors, delivery_ids):
    yandex_weather = YandexWeather(os.getenv('X-Yandex-Weather-Key', '').strip())
    storm_warnings = StormWarnings()
    initialize(HISTORY_DATABASE)
    journal = AlertJournal(str(HISTORY_DATABASE), delivery_ids)
    acquisition = asyncio.Semaphore(4)
    processing = asyncio.Semaphore(2)
    async with (
        httpx.AsyncClient(timeout=15, follow_redirects=True, limits=httpx.Limits(max_connections=4)) as client,
        httpx.AsyncClient(timeout=20, limits=httpx.Limits(max_connections=1)) as scene_client,
        httpx.AsyncClient(timeout=10, limits=httpx.Limits(max_connections=1)) as delivery_client,
    ):
        monitors.clear()
        source_locks = {}
        for config in cameras:
            source = config.get('url', config['id'])
            monitor = CameraMonitor(
                config, client, acquisition, processing, analyze_frames, demo,
                journal=journal, scene_client=scene_client,
                scene_url=os.getenv('SCENE_MODEL_URL', ''),
                source_lock=source_locks.setdefault(source, asyncio.Lock()),
            )
            await asyncio.to_thread(monitor.restore)
            monitors[config['id']] = monitor
        tasks = [asyncio.create_task(m.run()) for m in monitors.values()]
        tasks.append(asyncio.create_task(storm_warnings.run()))
        if yandex_weather.key:
            tasks.append(asyncio.create_task(yandex_weather.run()))
        if os.getenv('ALERT_WEBHOOK_URL'):
            tasks.append(asyncio.create_task(run_delivery(journal, delivery_client, os.environ['ALERT_WEBHOOK_URL'], os.getenv('ALERT_WEBHOOK_TOKEN',''))))
        try:
            yield yandex_weather, storm_warnings, journal
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
