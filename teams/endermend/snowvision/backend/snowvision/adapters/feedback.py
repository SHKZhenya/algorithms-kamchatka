"""Operator corrections tied to a camera and a specific observed frame."""
import json
import sqlite3
import time
from contextlib import closing


def save(database, camera, version, labels, observation):
    with closing(sqlite3.connect(database, timeout=15)) as db, db:
        db.execute('''CREATE TABLE IF NOT EXISTS frame_feedback (
            camera TEXT NOT NULL, version TEXT NOT NULL, created_at REAL NOT NULL,
            labels TEXT NOT NULL, model_observation TEXT NOT NULL,
            PRIMARY KEY (camera, version))''')
        db.execute('INSERT OR REPLACE INTO frame_feedback VALUES (?,?,?,?,?)',
                   (camera, version, time.time(), json.dumps(labels), json.dumps(observation)))


def load(database, camera, version):
    with closing(sqlite3.connect(database, timeout=15)) as db:
        exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='frame_feedback'").fetchone()
        if not exists:
            return None
        row = db.execute('SELECT labels, created_at FROM frame_feedback WHERE camera=? AND version=?',
                         (camera, version)).fetchone()
    return dict(labels=json.loads(row[0]), created_at=row[1]) if row else None
