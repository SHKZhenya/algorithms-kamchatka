"""Durable onset episodes. Confirmation policy adapted from Eazyan's scene.py.

Keeps first evidence separate from repeated confirmation and operator acknowledgement.
"""
import json
import sqlite3
import time
import uuid
from integrations.team_scene import event_signals
from datetime import datetime, timezone

class AlertJournal:
    def __init__(self, database, delivery_camera_ids=()):
        self.database = database
        self.delivery_camera_ids = frozenset(delivery_camera_ids)
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS onset_state (camera TEXT PRIMARY KEY, payload TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS onset_events (id TEXT PRIMARY KEY, camera TEXT, opened REAL, payload TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS alert_outbox (id TEXT PRIMARY KEY, event_id TEXT, stage TEXT, created REAL, payload TEXT, status TEXT, attempts INTEGER, next_attempt REAL, error TEXT)')

            db.execute('CREATE INDEX IF NOT EXISTS outbox_event ON alert_outbox(event_id)')
            db.execute('CREATE INDEX IF NOT EXISTS outbox_due ON alert_outbox(status,next_attempt)')

    def connect(self):
        return sqlite3.connect(self.database, timeout=15)

    def observe(self, camera, observation):
        now = time.time()
        version = observation.get('frame_version')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT payload FROM onset_state WHERE camera=?', (camera['id'],)).fetchone()
            state = json.loads(row[0]) if row else dict(version=None, hits=0, clears=0, event_id=None, last_valid=0)
            score = observation.get('snow_probability')
            scene = observation.get('scene') or {}
            scene_valid = (scene.get('format_valid') is True and scene.get('air_view') in ('good','limited')
                           and observation.get('scene_frame_version') == version
                           and scene.get('falling_snow') in ('yes','no'))
            cv_valid = score is not None and observation.get('confidence',0) >= .25
            valid = (bool(version) and observation.get('source_status') == 'online' and not observation.get('is_stale')
                     and (cv_valid or scene_valid))
            if not valid:
                state.update(hits=0, clears=0)
            elif version not in state.get('versions', [state['version']]):
                state['versions'] = (state.get('versions', []) + [version])[-32:]
                if now-state['last_valid'] > max(120, camera.get('interval_seconds', 30)*3):
                    state.update(hits=0, clears=0)
                state.update(version=version, last_valid=now)
                features = observation.get('features') or {}
                weak_motion = features.get('temporal_persistence', 0) >= .6 and features.get('spatial_coverage', 0) >= .4
                cv_yes = cv_valid and (score >= .5 or (score >= .2 and weak_motion))
                scene_yes = scene_valid and scene['falling_snow']=='yes'
                conflict = scene_valid and cv_valid and ((scene_yes and score <= .1) or (not scene_yes and score >= .5))
                positive = cv_yes or scene_yes
                strong = not conflict and ((cv_valid and score >= .5) or scene_yes)
                negative = not positive and ((cv_valid and score <= .1) or (scene_valid and not scene_yes))
                state['hits'] = state['hits']+1 if strong else 0
                state['positive_times'] = (state.get('positive_times',[])+[now])[-3:] if strong else []
                state['clears'] = state['clears']+1 if negative else 0
                event = None
                if state['event_id']:
                    stored = db.execute('SELECT payload FROM onset_events WHERE id=?',(state['event_id'],)).fetchone()
                    event = json.loads(stored[0]) if stored else None
                previous_stage = event['stage'] if event else None
                if event and event.get('confirmed_at') is not None:
                    previous_stage = 'confirmed'
                if positive:
                    if event is None:
                        event = dict(id=str(uuid.uuid4()), camera_id=camera['id'], name=camera['name'],
                                     location=camera.get('location',''), is_demo=camera['type']=='demo',
                                     opened_at=now, acknowledged_at=None, stage='early', ended_at=None)
                        state['event_id'] = event['id']
                    event.update(last_seen=now, score=score if cv_valid else None, confidence=observation.get('confidence') if cv_valid else None,
                                 conflict=conflict, scene=scene if scene_valid else None,
                                 frame_version=version, frame_timestamp=observation.get('frame_timestamp'),
                                 pipeline_delay_seconds=max(0,now-(observation.get('last_attempt_at') or observation.get('frame_timestamp') or now)),
                                 source_timestamp=observation.get('source_timestamp'),
                                 method='weather_vlm_v2' if scene_valid else observation.get('analysis_method'), confirmations=state['hits'])
                    if event.get('confirmed_at') is not None:
                        event['stage'] = 'confirmed'
                    scenes = [dict(observed_at=datetime.fromtimestamp(timestamp,timezone.utc),
                                   scene=dict(format_valid=True, view_quality='limited', falling_snow='yes',
                                              intensity='uncertain', visibility='uncertain'))
                              for timestamp in reversed(state['positive_times'][-state['hits']:])]
                    if event_signals(scenes, False, max(180, camera.get('interval_seconds',30)*4)).get('snowfall') == 'watch':
                        event['stage'] = 'confirmed'
                        event.setdefault('confirmed_at',now)
                    db.execute('INSERT OR REPLACE INTO onset_events VALUES (?,?,?,?)',
                               (event['id'],camera['id'],event['opened_at'],json.dumps(event)))
                    if (not event['is_demo'] and camera['id'] in self.delivery_camera_ids
                            and (previous_stage is None or previous_stage != event['stage'])):
                        if event['stage'] == 'confirmed':
                            db.execute("UPDATE alert_outbox SET status='suppressed' WHERE event_id=? AND stage='early' AND status='pending'", (event['id'],))
                        delivery_id = event['id'] + ':' + event['stage']
                        db.execute('INSERT OR IGNORE INTO alert_outbox VALUES (?,?,?,?,?,?,?,?,?)',
                                   (delivery_id, event['id'], event['stage'], now, json.dumps(event), 'pending', 0, now, None))
                elif state['clears'] >= 3 and event:
                    event.update(ended_at=now, stage='ended')
                    db.execute('UPDATE onset_events SET payload=? WHERE id=?',(json.dumps(event),event['id']))
                    state['event_id'] = None
            db.execute('INSERT OR REPLACE INTO onset_state VALUES (?,?)',(camera['id'],json.dumps(state)))

    def events(self):
        with self.connect() as db:
            rows=db.execute('SELECT payload FROM onset_events ORDER BY opened DESC LIMIT 100').fetchall()
        events=[json.loads(row[0]) for row in rows]
        with self.connect() as db:
            deliveries = db.execute('SELECT event_id, stage, status, attempts, error FROM alert_outbox WHERE event_id IN (SELECT id FROM onset_events ORDER BY opened DESC LIMIT 100) ORDER BY created').fetchall()
        by_event = {}
        for event_id, stage, status, attempts, error in deliveries:
            by_event.setdefault(event_id, []).append(dict(stage=stage, status=status, attempts=attempts, error=error))
        for event in events:
            event['delivery'] = by_event.get(event['id'], [])
            event['evidence_stale'] = time.time()-event['last_seen'] > 120
            if event['stage'] == 'early' and event.get('confirmed_at') is not None:
                event['stage'] = 'confirmed'
        return events

    def acknowledge(self, id):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT payload FROM onset_events WHERE id=?',(id,)).fetchone()
            if row is None: return None
            event=json.loads(row[0])
            event['acknowledged_at']=event.get('acknowledged_at') or time.time()
            db.execute('UPDATE onset_events SET payload=? WHERE id=?',(json.dumps(event),id))
        return event

    def next_delivery(self):
        """Single worker. Old/handled episodes cannot page an operator later."""
        now = time.time()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            rows = db.execute("SELECT o.id,o.payload,o.created,e.payload FROM alert_outbox o JOIN onset_events e ON e.id=o.event_id WHERE o.status='pending' AND o.next_attempt<=? ORDER BY o.created", (now,)).fetchall()
            for id, payload, created, current in rows:
                event = json.loads(current)
                if (event.get('is_demo') or event['camera_id'] not in self.delivery_camera_ids
                        or now-created > 120 or event.get('ended_at') or event.get('acknowledged_at')):
                    db.execute("UPDATE alert_outbox SET status='suppressed' WHERE id=?", (id,))
                    continue
                return dict(id=id, event=json.loads(payload))
        return None

    def delivery_result(self, id, error=None):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            attempts=db.execute('SELECT attempts FROM alert_outbox WHERE id=?',(id,)).fetchone()[0]+1
            status='delivered' if error is None else 'failed' if attempts >= 5 else 'pending'
            db.execute('UPDATE alert_outbox SET status=?, attempts=?, next_attempt=?, error=? WHERE id=?',
                       (status, attempts, time.time()+min(60, 5*2**(attempts-1)), error, id))
