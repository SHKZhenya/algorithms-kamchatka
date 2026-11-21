"""Transactional event journal and deterministic reconstruction from initial mass."""
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import uuid

import numpy as np
from .snow_state import SnowState, operation_geometry
from .snow_domain import create_state, attach_ground, shelter
from .dynamic_schema import InitialState, WeatherEvent, CleaningEvent


def canonical(value): return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)
def stamp(value): return datetime.fromisoformat(value).astimezone(timezone.utc)


def state_hash(state,valid_at):
    h=sha256(canonical({'state':state.metadata(),'valid_at':valid_at}).encode())
    for a in (state.loose,state.bound,state.area,state.exposure,state.cleared_reference): h.update(a.astype('<f8').tobytes())
    return h.hexdigest()


class DynamicStore:
    def __init__(self,path,city):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True);self.city=city
        with self.connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS snow_territories(id TEXT PRIMARY KEY, initial_spec TEXT NOT NULL, initial_meta TEXT NOT NULL,
                initial_blob BLOB NOT NULL,current_meta TEXT NOT NULL,current_blob BLOB NOT NULL,valid_at TEXT NOT NULL,revision INTEGER NOT NULL,city_revision TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS snow_events(territory TEXT NOT NULL,event_id TEXT NOT NULL,payload TEXT NOT NULL,
                sequence INTEGER NOT NULL,confirmation TEXT,PRIMARY KEY(territory,event_id));
            CREATE TABLE IF NOT EXISTS snow_history(territory TEXT NOT NULL,frame INTEGER NOT NULL,meta TEXT NOT NULL,blob BLOB NOT NULL,
                PRIMARY KEY(territory,frame));
            ''')

    def connect(self):
        db=sqlite3.connect(self.path,timeout=60);db.row_factory=sqlite3.Row;return db

    def create(self,spec):
        state=create_state(spec.bounds,spec.rows,spec.cols,spec.loose_kg_m2,spec.bound_kg_m2,
            spec.density_kg_m3,spec.density_min_kg_m3,spec.density_max_kg_m3,self.city if spec.city_geometry else None,
            spec.parameters.model_dump(),spec.patches)
        id=uuid.uuid4().hex;valid=spec.valid_at.astimezone(timezone.utc).isoformat()
        meta=state.metadata();meta.update(valid_at=valid,state_id=state_hash(state,valid),source=spec.source,confirmation=spec.confirmation)
        with self.connect() as db:
            db.execute('INSERT INTO snow_territories VALUES(?,?,?,?,?,?,?,?,?)',(id,canonical(spec.model_dump(mode='json')),canonical(state.metadata()),state.pack(),canonical(meta),state.pack(),valid,0,self.city.data_revision))
            db.execute('INSERT INTO snow_history VALUES(?,?,?,?)',(id,0,canonical(meta),state.pack()))
        return {'territory_id':id,'revision':0,**meta}

    def _row(self,db,id):
        row=db.execute('SELECT * FROM snow_territories WHERE id=?',(id,)).fetchone()
        if not row: raise LookupError('Territory not found')
        if row['city_revision']!=self.city.data_revision: raise RuntimeError('City geometry revision changed; create a new territory or explicit migration')
        return row

    def state(self,id,frame=None):
        with self.connect() as db:
            row=self._row(db,id)
            if frame is None: meta=json.loads(row['current_meta']);blob=row['current_blob']
            else:
                h=db.execute('SELECT * FROM snow_history WHERE territory=? AND frame=?',(id,frame)).fetchone()
                if not h: raise LookupError('State history frame not found')
                meta=json.loads(h['meta']);blob=h['blob']
            state=SnowState.unpack(blob,meta)
            spec=json.loads(row['initial_spec'])
            if spec['city_geometry']: attach_ground(state,spec['bounds'],self.city)
            else:
                from shapely.geometry import box
                from shapely.ops import transform
                from weather_coverage.geography import TO_METRIC
                state.ground=transform(TO_METRIC,box(*spec['bounds']))
            return state,{**meta,'territory_id':id,'revision':row['revision'],'city_revision':row['city_revision']}

    def history(self,id):
        with self.connect() as db:
            self._row(db,id)
            return {'territory_id':id,'frames':[{'frame':r['frame'],**json.loads(r['meta'])} for r in db.execute('SELECT * FROM snow_history WHERE territory=? ORDER BY frame',(id,))],
                    'events':[json.loads(r['payload']) for r in db.execute('SELECT * FROM snow_events WHERE territory=? ORDER BY sequence',(id,))]}

    def append(self,id,event,expected_revision=None,allow_replay=False):
        body=event.model_dump(mode='json');encoded=canonical(body)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE');row=self._row(db,id)
            old=db.execute('SELECT payload FROM snow_events WHERE territory=? AND event_id=?',(id,event.event_id)).fetchone()
            if old:
                if old['payload']!=encoded: raise RuntimeError('Event ID already exists with different payload')
                return {'territory_id':id,'revision':row['revision'],'deduplicated':True,**json.loads(row['current_meta'])}
            if expected_revision is not None and expected_revision!=row['revision']: raise RuntimeError('Stale expected_revision')
            at=event.start_at if isinstance(event,WeatherEvent) else event.valid_at
            if at<stamp(row['valid_at']) and not allow_replay: raise RuntimeError('Past event requires allow_replay=true; no silent history rewriting')
            initial=InitialState.model_validate_json(row['initial_spec'])
            if at<initial.valid_at: raise ValueError('Event precedes initial state')
            seq=db.execute('SELECT COALESCE(MAX(sequence),0)+1 FROM snow_events WHERE territory=?',(id,)).fetchone()[0]
            db.execute('INSERT INTO snow_events VALUES(?,?,?,?,?)',(id,event.event_id,encoded,seq,None))
            return self._replay(db,row)

    def confirm(self,id,event_id,source,expected_revision=None):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE');row=self._row(db,id)
            if expected_revision is not None and row['revision']!=expected_revision: raise RuntimeError('Stale expected_revision')
            event=db.execute('SELECT * FROM snow_events WHERE territory=? AND event_id=?',(id,event_id)).fetchone()
            if not event: raise LookupError('Event not found')
            payload=json.loads(event['payload'])
            if payload['kind']!='cleaning': raise ValueError('Only cleaning requires confirmation')
            if event['confirmation']: return {'deduplicated':True,**json.loads(row['current_meta'])}
            db.execute('UPDATE snow_events SET confirmation=? WHERE territory=? AND event_id=?',
                (canonical({'source':source,'confirmed_at':datetime.now(timezone.utc).isoformat()}),id,event_id))
            return self._replay(db,row)

    def replay(self,id):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE');row=self._row(db,id)
            before=json.loads(row['current_meta'])['state_id'];result=self._replay(db,row,increment=False)
            result['replay_identical']=before==result['state_id'];return result

    def _replay(self,db,row,increment=True):
        spec=InitialState.model_validate_json(row['initial_spec'])
        state=SnowState.unpack(row['initial_blob'],json.loads(row['initial_meta']))
        if spec.city_geometry: attach_ground(state,spec.bounds,self.city)
        else:
            from shapely.geometry import box
            from shapely.ops import transform
            from weather_coverage.geography import TO_METRIC
            state.ground=transform(TO_METRIC,box(*spec.bounds))
        entries=list(db.execute('SELECT * FROM snow_events WHERE territory=? ORDER BY sequence',(row['id'],)))
        weather=[];clean=[]
        for entry in entries:
            body=json.loads(entry['payload'])
            if body['kind']=='weather': weather.append(WeatherEvent.model_validate(body))
            else:
                obj=CleaningEvent.model_validate(body)
                clean.append((obj,entry['confirmation'],entry['sequence']))
        weather.sort(key=lambda e:(e.start_at,e.event_id))
        clean.sort(key=lambda x:(x[0].valid_at,x[0].order,x[2]))
        histories=[];applied=set();effects=[];now=spec.valid_at
        def snapshot(at,event,effect):
            meta=state.metadata();valid=at.astimezone(timezone.utc).isoformat()
            meta.update(valid_at=valid,state_id=state_hash(state,valid),event_id=event,effect=effect,
                        source=spec.source,confirmation=spec.confirmation)
            histories.append((meta,state.pack()))
        def cleanup(at):
            for event,confirmation,seq in clean:
                if event.event_id in applied or event.valid_at>at: continue
                applied.add(event.event_id)
                if event.confirmation=='unconfirmed' and not confirmation:
                    effects.append({'event_id':event.event_id,'status':'awaiting_confirmation','mass_changed':False});continue
                if event.valid_at<now: raise ValueError('Cleaning timestamp not covered by continuous weather')
                source,target=operation_geometry(event.model_dump(mode='json'))
                effect=state.clean(source,target,event.fraction,event.residual_kg_m2,
                    1. if event.operation=='load_export' else event.export_fraction,event.bank_mobile_fraction)
                from shapely.geometry import mapping
                from shapely.ops import transform
                from weather_coverage.geography import TO_WGS84, TO_METRIC
                from .object_registry import ObjectRegistry
                if not hasattr(self, '_object_registry'):
                    self._object_registry = ObjectRegistry(self.city)
                effect['related_object_ids'] = event.related_object_ids
                effect['source_geometry'] = mapping(transform(TO_WGS84, source))
                effect['destination_geometry'] = mapping(transform(TO_WGS84, target)) if target is not None else None
                effect['bank_overlap_object_ids'] = [oid for oid, area in self._object_registry.areas.items()
                    if target is not None and target.intersects(transform(TO_METRIC, area))]
                effect.update(operation=event.operation,confirmation=event.confirmation if not confirmation else 'confirmed',
                              confirmation_evidence=json.loads(confirmation) if confirmation else None)
                effects.append({'event_id':event.event_id,'status':'applied',**effect})
                snapshot(event.valid_at,event.event_id,effect)
        snapshot(now,'initial',{})
        cleanup(now)
        for event in weather:
            if event.start_at!=now: raise ValueError('Weather must be contiguous and non-overlapping from initial valid_at')
            cuts=sorted({e.valid_at for e,c,s in clean if now<e.valid_at<=event.end_at and (e.confirmation!='unconfirmed' or c)}|{event.end_at})
            closure=shelter(state,self.city,event.wind_from_deg,event.unknown_building_height_m) if event.shelter and spec.city_geometry else {'method':'uniform_prescribed_wind'}
            if closure['method']=='uniform_prescribed_wind': state.exposure[:]=1.
            for end in cuts:
                result=state.weather((end-now).total_seconds(),event.wind_speed_m_s,event.wind_from_deg,
                    event.snowfall_kg_m2_h,event.boundary_inflow_kg_m_s,event.explicit_loss_kg_m2_h)
                now=end
                snapshot(now,event.event_id,{'weather':event.model_dump(mode='json'),'wind_closure':closure,**result})
                cleanup(now)
        for event,confirmation,seq in clean:
            if event.event_id not in applied: effects.append({'event_id':event.event_id,'status':'pending_future'})
        meta=histories[-1][0]
        meta.update(event_effects=effects)
        revision=row['revision']+int(increment)
        db.execute('UPDATE snow_territories SET current_meta=?,current_blob=?,valid_at=?,revision=? WHERE id=?',
            (canonical(meta),state.pack(),now.astimezone(timezone.utc).isoformat(),revision,row['id']))
        db.execute('DELETE FROM snow_history WHERE territory=?',(row['id'],))
        db.executemany('INSERT INTO snow_history VALUES(?,?,?,?)',[(row['id'],i,canonical(m),blob) for i,(m,blob) in enumerate(histories)])
        return {'territory_id':row['id'],'revision':revision,'deduplicated':False,**meta}
