"""Facade uncertainty and equipment integration in the existing backend."""
from functools import lru_cache
from fastapi import HTTPException, Query
from shapely.geometry import box, shape
from .facade_inference import FacadeInference
from .equipment_access import EquipmentAccess, RouteRequest, PROFILES
from .geodata import PILOT
from .road_evidence import RoadEvidence
from hashlib import sha256
from pathlib import Path
import os


def register_mobility_api(api, registry_factory, parse_bbox):
    @lru_cache(maxsize=2)
    def external_roads(path,checksum,revision):
        return RoadEvidence(path,revision)

    @api.get('/api/v1/registry/road-evidence',tags=['Equipment and entrances'])
    def road_evidence(bbox: str=Query(','.join(map(str,PILOT))),way_id: str|None=None):
        bounds=parse_bbox(bbox);path=os.getenv('CITY_SIM_ROAD_EVIDENCE')
        if not path:
            return {'type':'FeatureCollection','features':[],'metadata':{'status':'not_configured',
                'canonical_graph_modified':False,'operational_access_inferred':False}}
        try:
            checksum=sha256(Path(path).read_bytes()).hexdigest()
            result=external_roads(path,checksum,registry_factory().revision).query(bounds,way_id)
        except OSError as e:
            raise HTTPException(503,'Configured road evidence unavailable') from e
        except (ValueError,KeyError,TypeError) as e:
            raise HTTPException(422,'Invalid road evidence file') from e
        except RuntimeError as e:
            raise HTTPException(409,str(e)) from e
        result['metadata']['sha256']=checksum
        return result

    @lru_cache(maxsize=1)
    def inference(registry):
        return FacadeInference(registry)

    def call(fn,*args):
        try:
            return fn(*args)
        except LookupError as e:
            raise HTTPException(404,str(e)) from e
        except ValueError as e:
            raise HTTPException(422,str(e)) from e
        except RuntimeError as e:
            raise HTTPException(409,str(e)) from e

    @api.get('/api/v1/registry/facade-hypotheses',tags=['Equipment and entrances'])
    def facades(building_id: str, territory_id: str|None=None,frame: int|None=Query(None,ge=0)):
        if frame is not None and territory_id is None:
            raise HTTPException(422,'frame requires territory_id')
        state=meta=None
        if territory_id:
            state,meta=call(api.state.dynamic_snow.state,territory_id,frame)
            if meta['city_revision']!=registry_factory().revision:
                raise HTTPException(409,'Snow state and registry revisions differ')
        result=call(inference(registry_factory()).predict,building_id,state)
        if meta:
            result['metadata'].update(state_id=meta['state_id'],valid_at=meta['valid_at'])
        return result

    @api.get('/api/v1/registry/road-widths',tags=['Equipment and entrances'])
    def widths(bbox: str=Query(','.join(map(str,PILOT)))):
        bounds=parse_bbox(bbox);extent=box(*bounds)
        result=EquipmentAccess(registry_factory()).inventory()
        result['features']=[f for f in result['features'] if extent.intersects(shape(f['geometry']))]
        result['metadata']['returned_ways']=len(result['features'])
        result['metadata']['coverage_scope']='full_snapshot; features filtered by bbox'
        return result

    @api.get('/api/v1/equipment/profiles',tags=['Equipment and entrances'])
    def profiles():
        return {'profiles':[p.model_dump() for p in PROFILES],'status':'scenario_examples_not_real_fleet'}

    @api.post('/api/v1/equipment/route-assessment',tags=['Equipment and entrances'])
    def assess(payload: RouteRequest):
        state=meta=None
        reg=registry_factory()
        if payload.territory_id:
            state,meta=call(api.state.dynamic_snow.state,payload.territory_id,payload.frame)
            if meta['city_revision']!=reg.revision:
                raise HTTPException(409,'Snow state and registry revisions differ')
            from datetime import datetime
            if datetime.fromisoformat(meta['valid_at'].replace('Z','+00:00'))!=payload.valid_at:
                raise HTTPException(422,'Route time must match selected snow frame')
        result=call(EquipmentAccess(reg).route,payload,state)
        if meta:
            result.update(state_id=meta['state_id'],territory_id=payload.territory_id)
        return result
