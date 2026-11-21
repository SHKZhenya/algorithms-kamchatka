"""Independent saved-state replay, current spatial assessment and evidence export."""
import argparse
from collections import Counter
from hashlib import sha256,sha1
import importlib.metadata
import json
from pathlib import Path
import sqlite3

from .api import make_app
from .geodata import CityData,BOUNDS
from .object_registry import ObjectRegistry
from .dynamic_api import dynamic_rankings
from .coverage_diagnostics import diagnose_old_raster


def verify(output,evidence):
    evidence.mkdir(parents=True,exist_ok=True)
    report=json.loads((output/'report.json').read_text())
    app=make_app(db_path=output/'state.sqlite');city=app.state.city;store=app.state.dynamic_snow
    registry=ObjectRegistry(city)
    assert city.data_revision==report['city_revision'],'Source changed since experiment; run again on the new snapshot'
    print('Replaying all saved territories',flush=True)
    with sqlite3.connect(output/'state.sqlite') as db:
        ids=[r[0] for r in db.execute('SELECT id FROM snow_territories')]
    balances=[]
    for id in ids:
        result=store.replay(id)
        assert result['replay_identical'],id
        b=result['balance'];tolerance=1e-7+1e-12*(b['initial_kg']+b['precipitation_kg']+b['boundary_in_kg'])
        assert abs(b['error_kg'])<=tolerance,(id,b)
        balances.append({'territory_id':id,'state_id':result['state_id'],'replay_identical':True,'balance':b})
    p=report['pilot'];history=store.history(p['territory_id'])
    selectors={'before':'snowfall','after_clean':'road-pass','after_wind_ranking':'dry-wind'}
    for phase,event_id in selectors.items():
        frame=next(f['frame'] for f in history['frames'] if f.get('event_id')==event_id)
        state,_=store.state(p['territory_id'],frame)
        data=dynamic_rankings(state,registry,p['bounds'],20,10000)
        selected={f['id']:f['properties']['physical'] for f in data['features'] if f['id'] in {p['road_id'],p['entrance_id']}}
        for id,phys in selected.items():
            assert abs(phys['p90_kg_m2']-p[phase]['selected'][id]['p90_kg_m2'])<1e-8
        p[phase]={'order':[f['id'] for f in data['features']],'selected':selected,
            'unknown':sum(f['properties']['physical']['p90_kg_m2'] is None for f in data['features']),'total':data['metadata']['total']}
    p['ranking_order_changed_after_cleaning']=p['before']['order']!=p['after_clean']['order']
    p['ranking_order_changed_after_wind']=p['after_clean']['order']!=p['after_wind_ranking']['order']
    assert p['ranking_order_changed_after_cleaning'] and p['ranking_order_changed_after_wind']
    assert p['after_wind_ranking']['selected'][p['road_id']]['mean_kg_m2']>p['after_clean']['selected'][p['road_id']]['mean_kg_m2']
    state,meta=store.state(report['city']['territory_id']);report['city']['state']=meta
    print('Checking city object coverage',flush=True)
    for kind in ('road','building','entrance'):
        data=dynamic_rankings(state,registry,BOUNDS,20,100000,kind)
        stats=[f['properties']['physical'] for f in data['features']]
        report['city']['coverage'][kind].update(objects=data['metadata']['total'],evaluated=len(stats),
            unknown=sum(x['p90_kg_m2'] is None for x in stats),covered_support=sum(x['covered'] for x in stats),
            total_open_support=sum(x['total'] for x in stats),original_geometry_measure=sum(x['original_geometry_measure'] for x in stats),
            excluded_support_measure=sum(x['excluded_non_ground_or_outside_domain'] for x in stats),
            unknown_object_ids=[f['id'] for f in data['features'] if f['properties']['physical']['p90_kg_m2'] is None])
        print(kind,report['city']['coverage'][kind]['unknown'],flush=True)
    record=app.state.city_snow.status(report['legacy_city_run_id']);fields=app.state.city_snow.probe_fields(report['legacy_city_run_id'])
    print('Classifying legacy missing support',flush=True)
    report['missing_before']={k:diagnose_old_raster(registry,fields['snow_cm'],record['bounds'],city,k) for k in ('road','building','entrance')}
    old=CityData(city.buildings_path,city.graph_path,city.supplements,city.terrain,True)
    old.supplements=tuple(p for p in old.supplements if p.name!='access-supplement.osm.gz')
    baseline=ObjectRegistry(old)
    entrance_ids=lambda r:{f['id'] for f in r.objects.values() if f['properties']['kind']=='entrance'}
    buildings=lambda r:{f['properties']['building_id'] for f in r.objects.values() if f['properties']['kind']=='entrance' and f['properties'].get('building_id')}
    proof={'baseline_report_buildings':483,'baseline_report_entrances':1331,'old_snapshot_with_new_matching':baseline.audit,
        'new_snapshot_with_new_matching':registry.audit,'additional_real_entrance_ids':sorted(entrance_ids(registry)-entrance_ids(baseline)),
        'newly_covered_building_ids_from_snapshot':sorted(buildings(registry)-buildings(baseline)),
        'candidate_entrances':registry.candidates,'candidate_methods':dict(Counter(f['properties']['method'] for f in registry.candidates)),
        'empty_entrance_areas':[id for id,a in registry.areas.items() if a.is_empty and registry.objects[id]['properties']['kind']=='entrance'],
        'facility_containment_links':sum(f['properties'].get('building_match_method')=='facility_point_inside_building_not_an_entrance' for f in registry.objects.values()),
        'geometry_fallbacks':[{id:f['properties'].get('assessment_area_method')} for id,f in registry.objects.items() if 'fallback' in f['properties'].get('assessment_area_method','')]}
    report['inventory']=registry.audit
    report['geometry_input_issues']={k:v for k,v in city.osm_layers[2].items() if 'unresolved' in k}
    report['independent_replays_verified']=len(balances)
    (evidence/'entrance-evidence.json').write_text(json.dumps(proof,ensure_ascii=False,allow_nan=False,indent=2))
    (evidence/'results.json').write_text(json.dumps(report,ensure_ascii=False,allow_nan=False,indent=2))
    ui=json.loads(Path('research/object-backend-2026-11-22/ui-rollback-verification.json').read_text())
    assert all(sha1(b'blob '+str(len((d:=Path(f['path']).read_bytes()))).encode()+b'\0'+d).hexdigest()==f['expected'] for f in ui['checks'])
    paths=['snow_state.py','snow_domain.py','dynamic_schema.py','dynamic_store.py','dynamic_api.py','entrance_enrichment.py','entrance_reviews.py',
           'object_registry.py','object_api.py','object_links.py','coverage_diagnostics.py','geodata.py','spatial.py','api.py',
           'reproduce_dynamic_backend.py','verify_dynamic_backend.py','prepare_access_sources.py','tests/test_dynamic_snow.py']
    check={'date':'2026-11-22','replays':balances,'ui_unchanged_files':len(ui['checks']),
        'versions':{p:importlib.metadata.version(p) for p in ('numpy','shapely','fastapi','pydantic')},
        'source_sha256':{str(p):sha256(p.read_bytes()).hexdigest() for name in paths for p in [Path('city_sim')/name]},
        'data_sha256':sha256(Path('city_sim/data/access-supplement.osm.gz').read_bytes()).hexdigest()}
    (evidence/'verification.json').write_text(json.dumps(check,ensure_ascii=False,allow_nan=False,indent=2))
    print(json.dumps({'new_real':len(proof['additional_real_entrance_ids']),'candidates':len(registry.candidates),'city':report['city']['coverage'],
                      'replays':len(balances)},ensure_ascii=False),flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--evidence',type=Path,default=Path('research/dynamic-backend-2026-11-22'))
    a=p.parse_args();verify(a.output,a.evidence)
