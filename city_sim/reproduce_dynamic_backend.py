"""End-to-end dynamic snow, cleaning/reburial, source coverage and city scaling.

python -m city_sim.reproduce_dynamic_backend --output out/dynamic-backend --city
Uses pinned legacy solver and bundled OSM snapshots; no network calls.
"""
import argparse
from datetime import datetime,timezone
from hashlib import sha256
import json
import math
from pathlib import Path
import time

import numpy as np
from fastapi.testclient import TestClient
from shapely.geometry import box,shape,mapping,Point
from shapely.ops import transform
from weather_coverage.geography import TO_METRIC,TO_WGS84
from .api import make_app
from .object_registry import ObjectRegistry
from .dynamic_api import dynamic_rankings
from .dynamic_schema import InitialState,WeatherEvent
from .snow_state import SnowState
from .snow_domain import create_state
from .geodata import BOUNDS
from .reproduce_object_backend import calculate, diagnostics
from .city_snow import CitySnowRequest
from .coverage_diagnostics import diagnose_old_raster


def control_experiments():
    def state(n=40,boundary='closed'):
        a=np.full((n,n),(100/n)**2)
        s=SnowState((0,0,100,100),np.zeros((n,n)),np.zeros((n,n)),a,np.ones_like(a),np.full_like(a,np.nan))
        s.parameters['boundary']=boundary;return s
    resolution=[]
    profiles=[]
    for n in (20,40,80):
        s=state(n);s.loose[:,:n//4]=20;s.ledger['initial_kg']=s.total_kg()
        before=s.loose.copy();s.weather(1800,12,270)
        profiles.append(s.loose.copy())
        resolution.append({'n':n,'dx_m':s.dx,'downstream_mass_kg':float(np.sum(s.loose[:,n//4:]*s.area[:,n//4:])),
                           'l1_change_kg':float(np.sum(abs(s.loose-before)*s.area)),**s.balance()})
    s=state();s.loose[:]=10;s.ledger['initial_kg']=s.total_kg()
    before=s.total_kg();clean=s.clean(box(30,0,50,100),box(50,0,55,100),bank_mobile_fraction=1)
    after_clear=float(s.loose[:,12:20].mean());bank=float(s.loose[:,20:22].mean())
    s.weather(3600,14,90)
    reburied=float(s.loose[:,12:20].mean());after_wind=s.balance()
    exported=s.clean(box(50,0,55,100),export_fraction=1.)
    open_state=state(boundary='open');open_state.loose[:]=5;open_state.ledger['initial_kg']=open_state.total_kg()
    open_state.weather(3600,12,270,2,{'west':.003},.2)
    convergence=[]
    for coarse,fine in zip(profiles,profiles[1:]):
        n=coarse.shape[0];restricted=fine.reshape(n,2,n,2).mean(axis=(1,3))
        convergence.append(float(np.mean(abs(restricted-coarse))))
    return {'resolution':resolution,'coarsened_profile_mae_kg_m2':convergence,'cleaning':clean,'road_after_clean_kg_m2':after_clear,
        'bank_after_clean_kg_m2':bank,'road_after_dry_wind_kg_m2':reburied,
        'after_wind_balance':after_wind,'export':exported,'open_boundary_precip_loss':open_state.balance(),
        'no_transport_baseline_downstream_mass_kg':0.,'legacy_no_precip_increment_cm':0.,
        'reburial_pass':reburied>after_clear,'mass_tolerance':'1e-7 kg + 1e-12 of total input'}


def result(response):
    if response.status_code>=400: raise RuntimeError(f'{response.status_code}: {response.text[:2000]}')
    return response.json()


def choose_pilot(registry):
    # A real OSM access road where an explicitly scenario-sized bank overlaps an entrance approach.
    rid='osm:way:129214932';oid='osm:node:7298702221'
    road=next(f for f in registry.catalog.roads.features if f['id']==rid)
    door=registry.objects[oid];p=transform(TO_METRIC,shape(door['geometry']))
    line=transform(TO_METRIC,shape(road['geometry'])).intersection(box(p.x-90,p.y-90,p.x+90,p.y+90))
    if line.geom_type=='MultiLineString':line=max(line.geoms,key=lambda g:g.length)
    source=line.buffer(3,cap_style=2);bank=line.offset_curve(5).buffer(2,cap_style=2)
    lon,lat=door['geometry']['coordinates'];bounds=(lon-.003,lat-.002,lon+.003,lat+.002)
    a,b=line.coords[0],line.coords[-1];nx,ny=-(b[1]-a[1]),b[0]-a[0]
    wind_from=math.degrees(math.atan2(nx,ny))%360
    return rid,oid,bounds,line,bank,wind_from


def run(output,city_scale):
    output.mkdir(parents=True,exist_ok=True);app=make_app(db_path=output/'state.sqlite');client=TestClient(app)
    city=app.state.city;started=time.perf_counter();registry=ObjectRegistry(city)
    report={'date':'2026-11-22','model':'snow-mass-fv-1','city_revision':city.data_revision,'inventory':registry.audit,
            'inventory_seconds':time.perf_counter()-started,'controls':control_experiments()}
    rid,oid,bounds,line,bank,wind=choose_pilot(registry)
    initial={'bounds':bounds,'rows':128,'cols':128,'valid_at':'2026-01-01T00:00:00Z',
             'loose_kg_m2':10,'source':'Controlled old loose snow scenario; not observed','city_geometry':True}
    territory=result(client.post('/api/v1/snow-state/territories',json=initial))['territory_id'];path='/api/v1/snow-state/territories/'+territory
    snow={'event_id':'snowfall','start_at':'2026-01-01T00:00:00Z','end_at':'2026-01-01T01:00:00Z',
          'wind_speed_m_s':0,'wind_from_deg':wind,'snowfall_kg_m2_h':5,'source':'Controlled 5 kg/m² snowfall','shelter':False}
    result(client.post(path+'/weather',json=snow))
    state,meta=app.state.dynamic_snow.state(territory)
    pre=dynamic_rankings(state,registry,bounds,20,10000)
    clean={'event_id':'road-pass','valid_at':'2026-01-01T01:00:00Z','operation':'road_plow',
        'service_line':mapping(transform(TO_WGS84,line)),'width_m':6,'bank_width_m':4,'bank_side':'left',
        'fraction':1,'bank_mobile_fraction':1,'source':'Explicit scenario pass; widths not measured',
        'confirmation':'scenario','related_object_ids':[rid,oid]}
    cleaned=result(client.post(path+'/cleaning',json=clean))
    state,meta=app.state.dynamic_snow.state(territory);post=dynamic_rankings(state,registry,bounds,20,10000)
    dry={'event_id':'dry-wind','start_at':'2026-01-01T01:00:00Z','end_at':'2026-01-01T02:00:00Z',
         'wind_speed_m_s':14,'wind_from_deg':wind,'source':'Dry wind toward cleared road','shelter':True,'unknown_building_height_m':9}
    blown=result(client.post(path+'/weather',json=dry))
    state,meta=app.state.dynamic_snow.state(territory);after=dynamic_rankings(state,registry,bounds,20,10000)
    replay=result(client.post(path+'/replay'))
    assert cleaned['event_effects'][0]['bank_deposited_kg']>0
    assert oid in cleaned['event_effects'][0]['bank_overlap_object_ids']
    def summary(data):
        return {'order':[f['id'] for f in data['features']],
            'selected':{f['id']:f['properties']['physical'] for f in data['features'] if f['id'] in {rid,oid}},
            'unknown':sum(f['properties']['physical']['p90_kg_m2'] is None for f in data['features']),
            'total':data['metadata']['total']}
    report['pilot']={'territory_id':territory,'bounds':bounds,'road_id':rid,'entrance_id':oid,
        'initial':initial,'snow':snow,'cleaning_event':clean,'dry_wind':dry,'cleaning':cleaned,'after_wind':blown,
        'before':summary(pre),'after_clean':summary(post),'after_wind_ranking':summary(after),'replay_identical':replay['replay_identical'],
        'ranking_order_changed_after_cleaning':summary(pre)['order']!=summary(post)['order'],
        'ranking_order_changed_after_wind':summary(post)['order']!=summary(after)['order']}
    assert replay['replay_identical'] and report['controls']['reburial_pass']
    # Same weather and pass geometry, but the pass occurs AFTER the dry wind.
    alternative=result(client.post('/api/v1/snow-state/territories',json=initial))['territory_id']
    alt='/api/v1/snow-state/territories/'+alternative
    result(client.post(alt+'/weather',json=snow));result(client.post(alt+'/weather',json=dry))
    result(client.post(alt+'/cleaning',json={**clean,'valid_at':'2026-01-01T02:00:00Z'}))
    alt_state,_=app.state.dynamic_snow.state(alternative)
    report['pilot']['order_effect_l1_mass_kg']=float(np.sum(abs(state.mass-alt_state.mass)*state.area))
    assert report['pilot']['order_effect_l1_mass_kg']>0
    # Refine the actual pilot geometry and repeat the complete journal.
    fine_id=result(client.post('/api/v1/snow-state/territories',json={**initial,'rows':256,'cols':256}))['territory_id']
    fp='/api/v1/snow-state/territories/'+fine_id
    result(client.post(fp+'/weather',json=snow));result(client.post(fp+'/cleaning',json=clean));result(client.post(fp+'/weather',json=dry))
    fine,_=app.state.dynamic_snow.state(fine_id)
    fine_rank=dynamic_rankings(fine,registry,bounds,20,10000)
    report['pilot']['resolution_comparison']={'coarse_grid':[128,128],'fine_grid':[256,256],
        'fine_selected':summary(fine_rank)['selected'],'fine_balance':fine.balance()}
    # Legacy physics remains independently executable with the current object snapshot.
    report['legacy_controls']=diagnostics(app.state.city_snow.study)
    legacy_run=calculate(app.state.city_snow,CitySnowRequest(source='manual',hours=1))
    record=app.state.city_snow.status(legacy_run);fields=app.state.city_snow.probe_fields(legacy_run)
    report['legacy_city_run_id']=legacy_run
    report['missing_before']={k:diagnose_old_raster(registry,fields['snow_cm'],record['bounds'],city,k) for k in ('road','building','entrance')}
    if city_scale:
        start=time.perf_counter()
        spec=InitialState(bounds=BOUNDS,rows=256,cols=256,valid_at='2026-01-01T00:00:00Z',loose_kg_m2=10,bound_kg_m2=5,
            source='Explicit uniform city scenario, no field initial observations',city_geometry=True)
        created=app.state.dynamic_snow.create(spec);id=created['territory_id']
        event=WeatherEvent(event_id='city-weather',start_at='2026-01-01T00:00:00Z',end_at='2026-01-01T01:00:00Z',
            wind_speed_m_s=12,wind_from_deg=270,snowfall_kg_m2_h=2,source='City scaling scenario',unknown_building_height_m=9)
        app.state.dynamic_snow.append(id,event)
        state,meta=app.state.dynamic_snow.state(id)
        coverage={}
        for kind in ('road','building','entrance'):
            data=dynamic_rankings(state,registry,BOUNDS,20,100000,kind)
            p=[f['properties']['physical'] for f in data['features']]
            coverage[kind]={'objects':data['metadata']['total'],'evaluated':len(p),
                'unknown':sum(x['p90_kg_m2'] is None for x in p),'covered_support':sum(x['covered'] for x in p),
                'original_geometry_measure':sum(x['original_geometry_measure'] for x in p),
                'excluded_support_measure':sum(x['excluded_non_ground_or_outside_domain'] for x in p),
                'unknown_object_ids':[f['id'] for f in data['features'] if f['properties']['physical']['p90_kg_m2'] is None],
                'total_open_support':sum(x['total'] for x in p),'coverage_denominator':'open support inside declared bbox, buildings/water excluded',
                'min_cell_width_m':state.dx,'max_cell_height_m':state.dy}
        report['city']={'territory_id':id,'state':meta,'coverage':coverage,'wall_seconds':time.perf_counter()-start,
            'replay_identical':app.state.dynamic_snow.replay(id)['replay_identical'],
            'domain_note':'Declared bounding box instead of legacy municipal/urban tile mask; cut-cell open area retained'}
    report['observational_accuracy']='not_testable_without_synchronous_mass_density_wind_and_cleaning_observations'
    (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,allow_nan=False,indent=2))
    print(json.dumps({'report':str(output/'report.json'),'inventory':registry.audit,'pilot_replay':replay['replay_identical'],
                      'balance':report.get('city',{}).get('state',{}).get('balance')},ensure_ascii=False))

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,default=Path('out/dynamic-backend'))
    p.add_argument('--city',action='store_true');args=p.parse_args();run(args.output,args.city)
