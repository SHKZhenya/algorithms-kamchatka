"""Integration API independent of planners and vehicle monitoring vendors."""
import math
from typing import Literal
from fastapi import HTTPException, Query
from pydantic import Field
import numpy as np
from shapely.geometry import box, shape, mapping, MultiLineString, Polygon
from shapely.ops import transform
from weather_coverage.geography import TO_METRIC, TO_WGS84
from .dynamic_schema import InitialState, WeatherEvent, CleaningEvent, Strict
from .dynamic_store import DynamicStore
from .object_registry import ObjectRegistry
from .spatial import RasterAssessment
from .geodata import collection, BOUNDS

class Confirmation(Strict):
    source: str=Field(min_length=1,max_length=1000)
    expected_revision: int|None=Field(None,ge=0)


def dynamic_rankings(state,registry,bounds,threshold_cm=20,limit=100,kind='all',offset=0,object_id=None):
    depth=np.where(state.area>0,state.mass/state.density*100,np.nan)
    mass=np.where(state.area>0,state.mass,np.nan)
    reference=state.cleared_reference
    reacc=np.where(np.isfinite(reference)&(state.area>0),np.maximum(state.mass-reference,0),np.nan)
    depth_sampler=RasterAssessment(depth,state.bounds,metric_coordinates=True)
    mass_sampler=RasterAssessment(mass,state.bounds,metric_coordinates=True)
    re_sampler=RasterAssessment(reacc,state.bounds,metric_coordinates=True)
    candidates=[]
    if kind in {'all','road'}: candidates.extend((f,shape(f['geometry'])) for f in registry.catalog.roads.query(bounds))
    if kind in {'all','building'}:
        for f in registry.building_records:
            geom=shape(f['geometry'])
            if box(*bounds).intersects(geom):
                g=transform(TO_METRIC,geom);area=g.buffer(8).difference(g)
                candidates.append((f,transform(TO_WGS84,area)))
    for f in registry.objects.values():
        if kind in {'all',f['properties']['kind']} and box(*bounds).intersects(shape(f['geometry'])):
            candidates.append((f,registry.areas[f['id']]))
    for f in getattr(registry,'candidates',[]):
        if kind in {'all','candidate_entrance'} and box(*bounds).intersects(shape(f['geometry'])):
            candidates.append((f,shape(f['geometry'])))
    rows=[]
    for f,geom in candidates:
        if object_id is not None and f['id'] != object_id:
            continue
        metric=transform(TO_METRIC,geom)
        # Do not evaluate snow through obstacles; retain original support as denominator.
        support=metric.intersection(state.ground) if hasattr(state,'ground') else metric
        if ('LineString' in metric.geom_type and support.geom_type not in {'LineString', 'MultiLineString'}) or ('Polygon' in metric.geom_type and support.geom_type not in {'Polygon', 'MultiPolygon'}):
            def parts(g):
                if hasattr(g, 'geoms'):
                    return [p for child in g.geoms for p in parts(child)]
                return [g]
            if 'LineString' in metric.geom_type:
                support = MultiLineString([g for g in parts(support) if g.geom_type == 'LineString' and not g.is_empty])
            elif 'Polygon' in metric.geom_type:
                from shapely.ops import unary_union
                selected = [g for g in parts(support) if g.geom_type == 'Polygon']
                support = unary_union(selected) if selected else Polygon()
        physical=depth_sampler.assess(support,threshold_cm,False)
        original_measure = metric.area if 'Polygon' in metric.geom_type else 1. if metric.geom_type == 'Point' else metric.length
        physical['original_geometry_measure'] = original_measure
        physical['excluded_non_ground_or_outside_domain'] = max(0., original_measure-physical['total'])
        physical['coverage_original_geometry_fraction'] = physical['covered']/original_measure if original_measure else 0.
        kg=mass_sampler.assess(support,threshold_cm*state.density/100,False)
        again=re_sampler.assess(support,0,False)
        p90=kg['p90_cm'] # sampler statistic in supplied units; rename at this boundary
        physical.update(quantity='total_surface_snow_depth_scenario_density',p90_kg_m2=p90,
            mean_kg_m2=kg['mean_cm'],max_kg_m2=kg['max_cm'],
            p90_depth_range_cm=[p90/state.density_max*100,p90/state.density_min*100] if p90 is not None else None,
            reaccumulated_p90_kg_m2=again['p90_cm'],model='snow-mass-fv-1',actual_accessibility='unknown',
            old_cover_included=True,threshold_status='research_not_normative',
            density_kg_m3=state.density,density_range_kg_m3=[state.density_min,state.density_max],
            support_geometry=mapping(transform(TO_WGS84,support)))
        physical['support_excluded_reason']='no_open_ground_at_geometry' if support.is_empty else None
        rows.append({**f,'properties':{**f['properties'],'physical':physical,
            'significance':{'category':f['properties'].get('category','other'),'verified':False},
            'quality':{'coverage_fraction':physical['coverage_fraction'],'field_validated':False,
                       'location_status':f['properties'].get('entrance_status','mapped_or_snapshot'),
                       'geometry_source':f['properties'].get('source',f['properties'].get('data_kind'))},
            'reasons':[f'Computed P90 mass: {p90} kg/m²',f'Reaccumulated after cleaning: {again["p90_cm"]} kg/m²',
                       'Density uncertainty is separate from mass','Research transport closures require field validation']}})
    rows.sort(key=lambda f:(f['properties']['physical']['p90_kg_m2'] is None,-(f['properties']['physical']['p90_kg_m2'] or 0),f['id']))
    return collection(rows[offset:offset+limit],total=len(rows),offset=offset,limit=limit,threshold_cm=threshold_cm,model='snow-mass-fv-1',
                      ranking='computed_p90_mass_desc',field_validated=False)


def register_dynamic_api(api,city,db_path,parse_bbox,registry_factory=None):
    store=DynamicStore(db_path,city);api.state.dynamic_snow=store
    def call(fn,*args,**kwargs):
        try: return fn(*args,**kwargs)
        except LookupError as e: raise HTTPException(404,str(e)) from e
        except RuntimeError as e: raise HTTPException(409,str(e)) from e
        except (ValueError,TypeError) as e: raise HTTPException(422,str(e)) from e

    @api.post('/api/v1/snow-state/territories',tags=['Dynamic snow'])
    def create(payload:InitialState):
        """Load explicit initial mass in kg/m², density range and source. No implicit zero for unknown snow."""
        return call(store.create,payload)

    @api.get('/api/v1/snow-state/territories/{id}',tags=['Dynamic snow'])
    def state(id:str,frame:int|None=Query(None,ge=0),grid:bool=False):
        snow,meta=call(store.state,id,frame)
        if grid:
            def values(a): return np.where(snow.area>0,a,np.nan).astype(object)
            def rows(a):
                return [[float(v) if math.isfinite(v) else None for v in row] for row in a]
            meta['grids']={'loose_kg_m2':rows(values(snow.loose)),'bound_kg_m2':rows(values(snow.bound)),
                           'depth_cm':rows(values(snow.mass/snow.density*100)),'open_area_m2':snow.area.tolist()}
        return meta

    @api.post('/api/v1/snow-state/territories/{id}/weather',tags=['Dynamic snow'])
    def weather(id:str,payload:WeatherEvent,expected_revision:int|None=Query(None,ge=0)):
        """Advance continuous weather interval; scheduled cleanings split it at their exact timestamps."""
        return call(store.append,id,payload,expected_revision)

    @api.post('/api/v1/snow-state/territories/{id}/cleaning',tags=['Dynamic snow'])
    def cleaning(id:str,payload:CleaningEvent,expected_revision:int|None=Query(None,ge=0),allow_replay:bool=False):
        """Register confirmed/scenario/pending cleaning. Past changes require explicit replay authorization."""
        return call(store.append,id,payload,expected_revision,allow_replay)

    @api.post('/api/v1/snow-state/territories/{id}/cleaning/{event_id}/confirm',tags=['Dynamic snow'])
    def confirm(id:str,event_id:str,payload:Confirmation):
        """Explicit evidence confirmation and deterministic history reconstruction."""
        return call(store.confirm,id,event_id,payload.source,payload.expected_revision)

    @api.post('/api/v1/snow-state/territories/{id}/replay',tags=['Dynamic snow'])
    def replay(id:str): return call(store.replay,id)

    @api.get('/api/v1/snow-state/territories/{id}/history',tags=['Dynamic snow'])
    def history(id:str): return call(store.history,id)

    @api.get('/api/v1/snow-state/territories/{id}/rankings',tags=['Dynamic snow'])
    def rankings(id:str,bbox:str=Query(','.join(map(str,BOUNDS))),frame:int|None=Query(None,ge=0),
                 kind:Literal['all','road','building','entrance','candidate_entrance','bus_stop','medical','facility']='all',
                 threshold_cm:float=Query(20,ge=0,le=1000,allow_inf_nan=False),limit:int=Query(100,ge=1,le=10000),
                 offset:int=Query(0,ge=0),object_id:str|None=None):
        snow,meta=call(store.state,id,frame)
        registry = registry_factory() if registry_factory else ObjectRegistry(city)
        result=call(dynamic_rankings,snow,registry,parse_bbox(bbox),threshold_cm,limit,kind,offset,object_id)
        result['metadata'].update(territory_id=id,state_id=meta['state_id'],valid_at=meta['valid_at'],revision=meta['revision'])
        return result
