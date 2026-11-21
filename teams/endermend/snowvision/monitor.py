"""Independent camera scheduling and last-successful-frame storage."""
import asyncio
import hashlib
import json
import logging
import random
import sqlite3
import time
from pathlib import Path
import cv2
from sources import fetch_frame, fetch_burst
from vision import frame_quality
from scene_model import analyze_scene


def prepare(frame):
    if frame is None or frame.ndim != 3 or min(frame.shape[:2]) < 16:
        raise ValueError('Недопустимый размер изображения')
    version = hashlib.sha256(frame.tobytes()).hexdigest()
    frame = cv2.resize(frame, (640, 360))
    ok, encoded = cv2.imencode('.jpg', frame)
    if not ok:
        raise ValueError('Ошибка кодирования JPEG')
    return frame, encoded.tobytes(), version


class CameraMonitor:
    def __init__(self, camera, client, acquisition, processing, analyze, demo, database='/data/history.db', source_lock=None, journal=None, scene_client=None, scene_url=""):
        self.camera = dict(camera)
        self.journal = journal
        self.scene_client = scene_client
        self.scene_url = scene_url
        self.client = client
        self.acquisition = acquisition
        self.processing = processing
        self.analyze = analyze
        self.demo = demo
        self.database = database
        self.lock = asyncio.Lock()
        self.source_lock = source_lock or asyncio.Lock()
        self.frames = []
        self.image = None
        self.tick = 0
        self.changed_at = time.time()
        self.state = dict(camera_id=camera['id'], snow_probability=None, confidence=0,
                          status='unknown', timestamp=None, frame_timestamp=None,
                          source_timestamp=None, source_status='offline',
                          analysis_method=('frame_quality' if camera.get('interval_seconds', 0.35) > 2 and camera['type'] != 'hls' else 'opencv_motion') if camera.get('motion_analysis', True) else 'snapshot_only',
                          is_demo=camera['type'] == 'demo', error=None,
                          last_success_at=None, last_attempt_at=None, next_attempt_at=0,
                          frame_version=None, consecutive_failures=0, has_cached_frame=False,
                          is_stale=False, is_fetching=False, last_attempt_status='pending', revision=0, last_analysis_at=None, analysis_reason='insufficient_frames', features={})

    def observation(self):
        state = dict(self.state)
        frame_time = state.get('frame_timestamp')
        if frame_time is not None and time.time()-frame_time > self.camera.get('stale_after_seconds', 120):
            state.update(is_stale=True, snow_probability=None, confidence=0, status='unknown')
            if state['source_status'] == 'online':
                state['source_status'] = 'stale'
        state['unchanged_since'] = self.changed_at if state.get('frame_version') else None
        state['frame_age_seconds'] = max(0, time.time()-self.changed_at) if state.get('frame_version') else None
        reason = None
        if state.get('is_stale'):
            threshold = self.camera.get('stale_after_seconds',120)
            if state.get('source_status') in ('offline','degraded'): reason = 'source_unavailable'
            elif (state.get('last_success_at') or 0)-self.changed_at > threshold: reason = 'unchanged_frame'
            elif state.get('source_timestamp') is not None and time.time()-state['source_timestamp'] > threshold: reason = 'old_source_timestamp'
            else: reason = 'frame_expired'
        state['freshness_reason'] = reason
        return state

    def restore(self):
        with sqlite3.connect(self.database) as db:
            row = db.execute('SELECT image, payload FROM frame_cache WHERE camera=?', (self.camera['id'],)).fetchone()
        if row:
            self.image, payload = row
            method = self.state['analysis_method']
            self.state.update(json.loads(payload))
            self.state.update(analysis_method=method, revision=time.time_ns()//1000)
            self.changed_at = self.state.get('frame_timestamp') or time.time()
            threshold = self.camera.get('stale_after_seconds', 120)
            source_time = self.state.get('source_timestamp')
            stale = time.time()-self.changed_at > threshold or (source_time is not None and time.time()-source_time > threshold)
            source_status = 'degraded' if self.state.get('error') else 'stale' if stale else 'online'
            self.state.update(source_status=source_status, is_fetching=False,
                              snow_probability=None, confidence=0, status='unknown', is_stale=stale)

    def persist(self):
        state = dict(self.state)
        with sqlite3.connect(self.database, timeout=15) as db:
            db.execute('INSERT INTO observations VALUES (?,?,?)', (self.camera['id'], time.time(), json.dumps(state)))
            if self.image:
                db.execute('INSERT OR REPLACE INTO frame_cache VALUES (?,?,?)',
                           (self.camera['id'], self.image, json.dumps(state)))
            db.execute('DELETE FROM observations WHERE ts < ?', (time.time()-7*86400,))

    async def attempt(self):
        # Check and acquire without yielding, so manual and automatic attempts cannot overlap.
        if self.lock.locked():
            return 'in_progress'
        if time.time() < self.state['next_attempt_at']:
            return 'rate_limited'
        async with self.lock:
            self.state.update(is_fetching=True, last_attempt_at=time.time(), last_attempt_status='fetching', revision=time.time_ns()//1000)
            error = None
            burst = None
            try:
                if self.camera['type'] == 'demo':
                    frame = await asyncio.to_thread(self.demo, self.tick)
                    self.tick += 1
                else:
                    async with self.source_lock, self.acquisition:
                        if self.camera['type'] == 'hls':
                            burst = await fetch_burst(self.camera)
                            frame = burst[-1]
                        else:
                            frame = await fetch_frame(self.camera, self.client)
                async with self.processing:
                    frame, encoded, version = await asyncio.to_thread(prepare, frame)
                    quality = await asyncio.to_thread(frame_quality, frame)
                now = time.time()
                changed = version != self.state['frame_version']
                source_time = self.camera.get('source_timestamp')
                previous_time = self.state.get('source_timestamp')
                if source_time is not None and previous_time is not None and source_time < previous_time:
                    raise ValueError('Источник вернул более старый снимок')
                if changed:
                    self.changed_at = now
                stale_after = self.camera.get('stale_after_seconds', 120)
                stale = now-self.changed_at > stale_after or (source_time is not None and now-source_time > stale_after)
                # Publish the validated frame and its metadata together, before analysis.
                self.image = encoded
                self.state.update(revision=time.time_ns()//1000, last_success_at=now, has_cached_frame=True,
                                  source_timestamp=source_time, download_attempts=self.camera.get('download_attempts'), http_cache_age=self.camera.get('http_cache_age'), consecutive_failures=0, features=quality,
                                  is_stale=stale, source_status='stale' if stale else 'online', error=None)
                if changed:
                    self.state.update(frame_version=version, frame_timestamp=now)
                score, confidence, status = None, 0, 'unknown'
                if self.camera.get('motion_analysis', True) and not stale:
                    cutoff = now-stale_after
                    self.frames = [(t, f) for t, f in self.frames if t >= cutoff]
                    if changed:
                        self.frames.append((now, frame))
                        self.frames = self.frames[-16:]
                        async with self.processing:
                            gaps = [b[0]-a[0] for a,b in zip(self.frames,self.frames[1:])]
                            if burst is not None:
                                self.frames = [(now-(len(burst)-1-index)*.5, item) for index,item in enumerate(burst)]
                                gaps = [.5]
                            repeated_burst = burst is not None and len({hashlib.sha256(item.tobytes()).digest() for item in burst}) < 8
                            if repeated_burst:
                                self.state['analysis_reason'] = 'insufficient_distinct_frames'
                            elif burst is None and (self.camera.get('interval_seconds', 0.35) > 2 or (gaps and max(gaps) > 2)):
                                self.state['analysis_reason'] = 'sampling_too_sparse'
                            else:
                                result = await asyncio.to_thread(self.analyze, [f for _, f in self.frames])
                                if isinstance(result, dict):
                                    score, confidence, status = result['snow_probability'], result['confidence'], result['status']
                                    self.state.update(analysis_reason=result['reason'], features=result['features'])
                                else:
                                    score, confidence, status = result
                            self.state['last_analysis_at'] = time.time()
                    else:
                        score = self.state['snow_probability']
                        confidence = self.state['confidence']
                        status = self.state['status']
                self.state.update(snow_probability=score, confidence=confidence, status=status)
                if changed and score is None and self.scene_url and self.camera.get('city_observation') and self.camera['type']!='demo':
                    try:
                        scene = await analyze_scene(self.scene_client, self.scene_url, encoded)
                        self.state.update(scene=scene, scene_frame_version=version, scene_error=None)
                    except Exception as exc:
                        self.state.update(scene=None, scene_frame_version=None, scene_error=type(exc).__name__)
                self.state['last_attempt_status'] = 'success'
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                error = f'{type(exc).__name__}: {exc}'
                self.frames.clear()
                self.state.update(error=error, snow_probability=None, confidence=0, status='unknown',
                                  source_status='degraded' if self.image else 'offline',
                                  consecutive_failures=self.state['consecutive_failures']+1,
                                  last_attempt_status='error')
            finally:
                interval = self.camera.get('interval_seconds', 0.35 if self.camera['type']=='demo' else 30)
                if self.camera['type'] == 'hls':
                    interval = max(.5, min(interval, self.camera.get('hls_burst_pause_seconds', 2)))
                if error:
                    interval = min(300, 30 * 2**min(self.state['consecutive_failures']-1, 3))
                self.state.update(is_fetching=False, timestamp=time.time(), revision=time.time_ns()//1000,
                                  next_attempt_at=time.time()+interval+random.uniform(0, interval*0.08))
            try:
                async with self.processing:
                    if self.journal is not None:
                        await asyncio.to_thread(self.journal.observe, self.camera, self.observation())
                    await asyncio.to_thread(self.persist)
            except Exception:
                logging.exception('Failed to persist camera %s', self.camera['id'])
            return 'error' if error else 'updated'

    async def run(self):
        await asyncio.sleep(random.uniform(0, 2))
        while True:
            await self.attempt()
            await asyncio.sleep(max(0.05, self.state['next_attempt_at']-time.time()))
