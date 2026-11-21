"""Offline procurement mapping and causal snow experiment. No external downloads."""
import argparse,json,shutil,time,re
from collections import Counter
from pathlib import Path
from .api import make_app
from .object_registry import ObjectRegistry
from .procurement import Matcher,Procurement,ROOT,norm,ANCHORS
RESEARCH=Path(__file__).resolve().parents[1]/'research/procurement-0903-2026-11-22'
def save(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False))
def build():
 ROOT.mkdir(exist_ok=True,parents=True)
 for name in ('objects.json','works.json','appendices.json','estimate-analysis.json','extraction-audit.json'):
  shutil.copyfile(RESEARCH/name,ROOT/name)
 app=make_app(db_path='/tmp/0903-mapping.sqlite');reg=ObjectRegistry(app.state.city);matcher=Matcher(reg)
 routes,audit=matcher.all();save(RESEARCH/'route-hypotheses.geojson',routes);save(RESEARCH/'route-anchor-audit.json',audit)
 # Name and address evidence for every listed object, without asserting extents.
 objects=Procurement().read('objects.json')
 for o in objects:
  desc=o['description']+' '+o['address_context'];mentions=[]
  for street in ('Чубарова','Победы','Вулканная','Кавказская','Ломоносова','Дальневосточная','Ларина','Королева','Таранца','Вольского','Савченко','Тушканова','Маркса','Топоркова','Восточное','Халактырское','Березовая','Кручины','Фролова','70 лет Победы','Авиационная','Невского'):
   if street.replace('ё','е').lower() in desc.replace('ё','е').lower():
    matches=[rid for rid,r in matcher.roads.items() if norm(street) in norm(r['tags'].get('name',''))]
    mentions.append(dict(street=street,candidate_way_ids=matches,extent_status='named_street_only_not_object_boundary'))
  o['street_mentions']=mentions
  o['address_mentions']=[dict(street=m.group(1),house=m.group(2),status='textual_anchor_not_entrance') for m in re.finditer(r'(Ларина|Савченко|Дальневосточная|Таранца|Вольского|Кручины|Фролова|Корол[её]ва|Чубарова)[,.\s]+(?:д\.?\s*)?(\d+(?:/\d+)?)',desc)]
  if o['kind']=='intraquarter':
   o['anchor_audit_id']=o['id']
   spec=ANCHORS[int(o['number'])-1]
   o['interpreted_route_anchors']=spec
   for a in spec:
    entries=a['addresses'] if a['kind']=='between_addresses' else [a]
    for entry in entries:
     if entry['kind']=='address' and not any(m['street']==entry['street'] and m['house']==entry['house'] for m in o['address_mentions']):
      o['address_mentions'].append(dict(street=entry['street'],house=entry['house'],status='textual_anchor_not_entrance'))
   o['geometry_status']='route_hypothesis_available' if any(f['properties']['object_id']==o['id'] for f in routes['features']) else 'anchors_only'
  elif any(m['candidate_way_ids'] for m in mentions):o['geometry_status']='named_way_candidates_only'
  o['text_qualifiers']={k:bool(re.search(pattern,desc,re.I)) for k,pattern in dict(behind_buildings='за домами',gate='ворот',slope='спуск|подъ[её]м',exit='выезд',container_access='контейнер',traffic_light='светофор',bus_terminal='конечн|разворот').items()}
  if o['kind']=='individual_housing':o['listed_subsections']=4
  chain=re.search(r'ПК(\d+\+\d+(?:[.,]\d+)?) до ПК(\d+\+\d+(?:[.,]\d+)?)',desc)
  if chain:o['project_chainage']=dict(start=chain.group(1),end=chain.group(2),survey_alignment_unknown=True)
 save(RESEARCH/'objects.json',objects);save(ROOT/'objects.json',objects)
 for name in ('route-hypotheses.geojson','route-anchor-audit.json'):shutil.copyfile(RESEARCH/name,ROOT/name)
 print('mapped',len(routes['features']),'variants',flush=True)
 return app,reg,routes,audit

def experiment(app,reg,routes,output):
 import numpy as np
 from datetime import datetime
 from fastapi.testclient import TestClient
 from shapely.geometry import shape,LineString,mapping,box
 from shapely.ops import transform
 from weather_coverage.geography import TO_METRIC,TO_WGS84
 from .equipment_access import EquipmentAccess,RouteLeg,RouteRequest,PROFILES
 from .dynamic_api import dynamic_rankings
 from .reproduce_dynamic_backend import result
 from .spatial import RasterAssessment
 from .facade_inference import FacadeInference
 client=TestClient(app)
 # A two-anchor street-to-house description; all route variants remain candidates.
 selected=next(f for f in routes['features'] if f['properties']['object_id']=='0903:intraquarter:03' and f['properties']['route_status']=='all_anchors_candidate')
 prop=selected['properties'];legdict=prop['legs'];line=transform(TO_METRIC,shape(selected['geometry']));minx,miny,maxx,maxy=line.bounds
 bounds=transform(TO_WGS84,box(minx-60,miny-60,maxx+60,maxy+60)).bounds
 initials=dict(bounds=bounds,rows=128,cols=128,valid_at='2026-01-01T00:00:00Z',loose_kg_m2=10,source='Controlled old cover; no observations',city_geometry=True)
 snow=dict(event_id='snow',start_at=initials['valid_at'],end_at='2026-01-01T01:00:00Z',wind_speed_m_s=0,wind_from_deg=270,snowfall_kg_m2_h=5,source='Controlled 5 kg/m2 scenario',shelter=False)
 dry=dict(event_id='dry',start_at=snow['end_at'],end_at='2026-01-01T02:00:00Z',wind_speed_m_s=14,wind_from_deg=270,source='Controlled dry wind; not a forecast',shelter=True,unknown_building_height_m=9)
 engine=EquipmentAccess(reg);allruns=[];facades=FacadeInference(reg)
 for n in (128,256):
  for policy in ('no_cleaning','plow_bank','plow_export') if n==128 else ('plow_bank',):
   created=result(client.post('/api/v1/snow-state/territories',json={**initials,'rows':n,'cols':n}));tid=created['territory_id'];path='/api/v1/snow-state/territories/'+tid
   result(client.post(path+'/weather',json=snow));before,bmeta=app.state.dynamic_snow.state(tid)
   clean=None
   if policy!='no_cleaning':
    # Width and bank location remain explicit scenario assumptions.
    for i,part in enumerate(line.geoms):
     clean=result(client.post(path+'/cleaning',json=dict(event_id='pass-'+str(i),valid_at=snow['end_at'],operation='road_plow',service_line=mapping(transform(TO_WGS84,part)),width_m=3,bank_width_m=2,bank_side='left',fraction=1,export_fraction=1 if policy=='plow_export' else 0,bank_mobile_fraction=1,source='Scenario 3m swath/2m bank; not survey or customer order',confirmation='scenario',related_object_ids=[prop['object_id']]+sorted({l['way_id'] for l in legdict}))))
   after,ameta=app.state.dynamic_snow.state(tid);result(client.post(path+'/weather',json=dry));blown,meta=app.state.dynamic_snow.state(tid)
   building_ids=[]
   extent=box(*bounds)
   for bid,b in reg.buildings.items():
    if extent.intersects(shape(b['geometry'])):building_ids.append(bid)
   entrance_ids=[oid for oid,o in reg.objects.items() if o['properties'].get('kind')=='entrance' and extent.intersects(shape(o['geometry']))]
   snapshots={}
   history=app.state.dynamic_snow.history(tid)['frames']
   for label,state,m in [('before',before,bmeta),('after_clean',after,ameta),('after_wind',blown,meta)]:
    assessments=[]
    for profile in PROFILES:
     request=RouteRequest(profile=profile,valid_at=datetime.fromisoformat(m['valid_at']),legs=[RouteLeg(**l) for l in legdict])
     api_result=result(client.post('/api/v1/procurement/0903/candidate-assessment',json=dict(candidate_id=selected['id'],profile=profile.model_dump(),valid_at=m['valid_at'],territory_id=tid,frame=next(h['frame'] for h in reversed(history) if h['state_id']==m['state_id']))))
     assessments.append(api_result['assessment'])
     assert api_result['service_boundary_confirmed'] is False
    physical={kind:dynamic_rankings(state,reg,bounds,20,10000,kind) for kind in ('road','building','entrance')}
    candidate_snow=RasterAssessment(np.where(state.area>0,state.mass,np.nan),state.bounds,metric_coordinates=True).assess(line,20,False)
    candidate_snow={k.replace('_cm','_kg_m2'):v for k,v in candidate_snow.items()}
    candidate_snow.update(quantity='surface_snow_mass',unit='kg/m2')
    building_access=[dict(building_id=bid,known_entrance_ids=reg.by_building.get(bid,[]),actual_accessibility='unknown',reason='no_mapped_entrance' if not reg.by_building.get(bid) else 'door_dimensions_and_continuous_route_unverified') for bid in building_ids]
    target_facade=facades.predict('osm:way:106795313',state)
    snapshots[label]=dict(balance=state.balance(),equipment=assessments,physical=physical,candidate_line_mass_statistics=candidate_snow,building_accessibility=building_access,target_building_facade_hypotheses=target_facade)
   replay=result(client.post(path+'/replay'));assert replay['replay_identical'];assert abs(blown.balance()['error_kg'])<1e-5
   allruns.append(dict(grid=n,policy=policy,territory_id=tid,building_ids=building_ids,known_entrance_ids=entrance_ids,snapshots=snapshots,cleaning=clean,replay_identical=True,dry_transport_l1_kg=float(np.sum(abs(after.mass-blown.mass)*after.area)),dry_precipitation_kg=blown.ledger['precipitation_kg']-after.ledger['precipitation_kg']))
 checks={url:client.get(url).status_code for url in ['/api/v1/procurement/0903/objects','/api/v1/procurement/0903/works','/api/v1/procurement/0903/routes','/api/v1/procurement/0903/anchors']};assert set(checks.values())=={200}
 summary=dict(date='2026-11-22',city_revision=reg.revision,procurement_id='0138300000426000903',selected_candidate=selected,initial=initials,snow=snow,dry=dry,runs=allruns,api_checks=checks,field_accuracy='not_testable',actual_accessibility='unknown',service_boundary_confirmed=False,fleet_status='scenario_profiles_not_actual_equipment',cost_comparison='not_claimed_different_quality_and_unknown_ordered_volume')
 save(output/'experiment.json',summary)
 print('experiment complete',[(r['grid'],r['policy'],r['snapshots']['after_wind']['balance'],r['dry_transport_l1_kg']) for r in allruns],flush=True)

if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,default=RESEARCH);args=parser.parse_args();args.output.mkdir(exist_ok=True,parents=True)
 started=time.monotonic();app,reg,routes,audit=build();experiment(app,reg,routes,args.output);print('elapsed',time.monotonic()-started)
