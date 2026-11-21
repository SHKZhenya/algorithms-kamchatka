"""Evidence-backed candidate review, preserving predicted and reported coordinates separately."""
import json
import sqlite3
from datetime import datetime,timezone
from hashlib import sha256
from pydantic import Field,model_validator
from typing import Literal
from .dynamic_schema import Strict
from shapely.geometry import shape,Point
from shapely.ops import transform
from weather_coverage.geography import TO_METRIC

class EntranceReview(Strict):
    candidate_id:str
    verdict:Literal['confirmed','rejected']
    source:str=Field(min_length=1,max_length=2000)
    coordinates:tuple[float,float]|None=None
    uncertainty_m:float|None=Field(None,gt=0,le=100)
    @model_validator(mode='after')
    def check(self):
        if self.verdict=='confirmed' and (self.coordinates is None or self.uncertainty_m is None):
            raise ValueError('Confirmation requires independently reported coordinates and uncertainty, not just the predicted point')
        return self

class EntranceReviews:
    def __init__(self,path):
        self.path=path
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE IF NOT EXISTS entrance_reviews(candidate_id TEXT PRIMARY KEY,body TEXT NOT NULL)')
    def all(self):
        with sqlite3.connect(self.path) as db:
            return [json.loads(row[0]) for row in db.execute('SELECT body FROM entrance_reviews ORDER BY candidate_id')]
    def save(self,review,registry):
        candidate=next((f for f in registry.candidates if f['id']==review.candidate_id),None)
        if candidate is None:
            previous=next((r for r in self.all() if r['candidate_id']==review.candidate_id),None)
            if previous:
                candidate={'geometry':{'coordinates':previous['predicted_coordinates']},'properties':{'building_id':previous['building_id']}}
        if not candidate:raise LookupError('Candidate not found or already superseded by a confirmed entrance')
        if review.coordinates:
            lon,lat=review.coordinates
            if not -180<=lon<=180 or not -90<=lat<=90:raise ValueError('Invalid coordinate')
            geom=transform(TO_METRIC,shape(registry.buildings[candidate['properties']['building_id']]['geometry']))
            if geom.boundary.distance(Point(TO_METRIC(lon,lat)))>max(5,review.uncertainty_m or 0):
                raise ValueError('Reported coordinate does not match the building facade')
        body={**review.model_dump(mode='json'),'building_id':candidate['properties']['building_id'],
              'predicted_coordinates':candidate['geometry']['coordinates'],'reviewed_at':datetime.now(timezone.utc).isoformat()}
        with sqlite3.connect(self.path) as db:
            db.execute('INSERT INTO entrance_reviews VALUES(?,?) ON CONFLICT(candidate_id) DO UPDATE SET body=excluded.body',
                       (review.candidate_id,json.dumps(body,ensure_ascii=False,allow_nan=False)))
        return body
