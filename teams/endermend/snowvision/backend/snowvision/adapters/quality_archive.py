"""Optional, separate archive for evaluating camera evidence and forecast releases."""

import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path

import cv2


class QualityArchive:
    def __init__(self, path, *, media_limit_bytes=0, pipeline=None):
        self.path = Path(path).resolve()
        self.media_limit_bytes = media_limit_bytes
        self.pipeline = pipeline or {}
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if media_limit_bytes and self.path.parent.stat().st_mode & 0o077:
            raise PermissionError('The pilot media directory must be private (0700)')
        self.path.touch(mode=0o600, exist_ok=True)
        if media_limit_bytes and self.path.stat().st_mode & 0o077:
            raise PermissionError('The pilot media archive must be private (0600)')
        with closing(self.connect()) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS samples (
                    id INTEGER PRIMARY KEY, available_at REAL NOT NULL,
                    camera TEXT NOT NULL, payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS samples_camera_time
                    ON samples (camera, available_at);
                CREATE TABLE IF NOT EXISTS media (
                    digest TEXT PRIMARY KEY, image BLOB NOT NULL
                );
                CREATE TABLE IF NOT EXISTS releases (
                    available_at REAL PRIMARY KEY, hours TEXT NOT NULL
                );
            """)

    def connect(self):
        return sqlite3.connect(self.path, timeout=10)

    def record_sample(self, camera, observation, frames=(), acquisition=None):
        fields = (
            'frame_version', 'frame_timestamp', 'source_timestamp', 'source_status',
            'last_attempt_at', 'last_success_at', 'last_analysis_at', 'last_attempt_status',
            'analysis_method', 'analysis_reason', 'features', 'status', 'snow_probability',
            'confidence', 'is_stale', 'is_demo', 'scene', 'scene_frame_version',
            'http_cache_age', 'frame_age_seconds',
        )
        state = {key: observation.get(key) for key in fields}
        state['acquisition'] = acquisition
        error = observation.get('error')
        state['error_kind'] = error.split(':', 1)[0] if error else None
        state['source_type'] = camera['type']
        state['interval_seconds'] = camera.get('interval_seconds', 30)
        state['zone'] = camera.get('quality_zone', camera['id'])
        state['view_revision'] = camera.get('view_revision', 'unverified')
        state['city_observation'] = camera.get('city_observation', False)
        state['coordinates_lat_lon'] = camera.get('coordinates_lat_lon')
        state['media'] = []
        state['pipeline'] = self.pipeline
        encoded = []
        if self.media_limit_bytes:
            for frame in frames:
                ok, image = cv2.imencode('.png', frame)
                if not ok:
                    raise ValueError('Could not encode pilot frame')
                data = image.tobytes()
                encoded.append((hashlib.sha256(data).hexdigest(), data))
        with closing(self.connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            if encoded:
                used = db.execute('SELECT COALESCE(SUM(length(image)), 0) FROM media').fetchone()[0]
                pending = {
                    digest: data for digest, data in encoded
                    if db.execute('SELECT 1 FROM media WHERE digest=?', (digest,)).fetchone() is None
                }
                if used + sum(map(len, pending.values())) <= self.media_limit_bytes:
                    db.executemany('INSERT INTO media VALUES (?, ?)', pending.items())
                    state['media'] = [digest for digest, _ in encoded]
                else:
                    state['media_reason'] = 'media_budget'
            db.execute(
                'INSERT INTO samples (available_at, camera, payload) VALUES (?, ?, ?)',
                (observation['timestamp'], camera['id'], json.dumps(state, allow_nan=False)),
            )

    def record_release(self, available_at, hours):
        if hours:
            with closing(self.connect()) as db, db:
                db.execute(
                    'INSERT OR IGNORE INTO releases VALUES (?, ?)',
                    (available_at, json.dumps(hours, allow_nan=False)),
                )
