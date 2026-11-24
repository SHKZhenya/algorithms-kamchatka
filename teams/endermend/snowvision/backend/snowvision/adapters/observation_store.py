"""SQLite persistence for camera observations and the last usable frame."""

import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path


HISTORY_DATABASE = Path('/data/history.db')


def initialize(database):
    Path(database).parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute(
            'CREATE TABLE IF NOT EXISTS observations (camera TEXT, ts REAL, payload TEXT)'
        )
        connection.execute(
            'CREATE TABLE IF NOT EXISTS frame_cache (camera TEXT PRIMARY KEY, image BLOB, payload TEXT)'
        )


def load_frame(database, camera_id):
    with closing(sqlite3.connect(database)) as connection:
        row = connection.execute(
            'SELECT image, payload FROM frame_cache WHERE camera=?', (camera_id,)
        ).fetchone()
    if row is None:
        return None
    image, payload = row
    return image, json.loads(payload)


def load_history(database, camera_id):
    with closing(sqlite3.connect(database)) as connection:
        rows = connection.execute(
            'SELECT payload FROM observations WHERE camera=? ORDER BY ts DESC LIMIT 120',
            (camera_id,),
        ).fetchall()
    return [json.loads(payload) for (payload,) in reversed(rows)]


def save_observation(database, camera_id, state, image):
    payload = json.dumps(state)
    now = time.time()
    with closing(sqlite3.connect(database, timeout=15)) as connection, connection:
        connection.execute('INSERT INTO observations VALUES (?,?,?)', (camera_id, now, payload))
        if image:
            connection.execute(
                'INSERT OR REPLACE INTO frame_cache VALUES (?,?,?)', (camera_id, image, payload)
            )
        connection.execute('DELETE FROM observations WHERE ts < ?', (now - 7 * 86400,))
