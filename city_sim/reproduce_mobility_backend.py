"""Offline facade holdout, width audit, real-geometry cleaning and equipment.

python -m city_sim.reproduce_mobility_backend --output out/mobility-backend-2026-11-22/reproduce
"""
import argparse
from collections import Counter,defaultdict
from datetime import datetime
from hashlib import sha256,sha1
import importlib.metadata
import json
from pathlib import Path
import time
import numpy as np
from fastapi.testclient import TestClient
from shapely.geometry import shape,box,mapping,LineString,Point
from shapely.ops import transform
from weather_coverage.geography import TO_METRIC,TO_WGS84
from .api import make_app
from .object_registry import ObjectRegistry
from .equipment_access import EquipmentAccess,RouteLeg,RouteRequest,WayEvidence,PROFILES
from .facade_inference import FacadeInference
from .reproduce_dynamic_backend import choose_pilot,result,control_experiments
from .dynamic_api import dynamic_rankings


def bootstrap(validation, inference):
    groups=defaultdict(list)
    for r in validation['predictions']:
        groups[inference.blocks[r['building_id']]].append(r)
    groups=list(groups.values());rng=np.random.default_rng(20261122)
    differences=[]
    for _ in range(2000):
        rows=[r for i in rng.integers(0,len(groups),len(groups)) for r in groups[i]]
        differences.append(np.mean([int(r['methods']['model']['top1_hit'])-int(r['methods']['longest_facade']['top1_hit']) for r in rows]))
    return dict(blocks=len(groups),replicates=2000,seed=20261122,
        paired_top1_model_minus_longest_95pct=np.quantile(differences,[.025,.975]).tolist())


def scenario(app,reg,client):
    rid,oid,bounds,line,bank,wind=choose_pilot(reg)
    initial=dict(bounds=bounds,rows=128,cols=128,valid_at='2026-01-01T00:00:00Z',loose_kg_m2=10,
                 source='Explicit controlled old cover, not observed',city_geometry=True)
    tid=result(client.post('/api/v1/snow-state/territories',json=initial))['territory_id']
    path='/api/v1/snow-state/territories/'+tid
    snow=dict(event_id='snow-and-wind',start_at=initial['valid_at'],end_at='2026-01-01T01:00:00Z',
              wind_speed_m_s=12,wind_from_deg=270,snowfall_kg_m2_h=5,source='Controlled snowfall and drifting',shelter=True,unknown_building_height_m=9)
    result(client.post(path+'/weather',json=snow))
    before,before_meta=app.state.dynamic_snow.state(tid)
    cleaning=dict(event_id='road-pass',valid_at=snow['end_at'],operation='road_plow',
        service_line=mapping(transform(TO_WGS84,line)),width_m=6,bank_width_m=4,bank_side='left',
        fraction=1,bank_mobile_fraction=1,source='Scenario geometry, width not measured',confirmation='scenario',related_object_ids=[rid,oid])
    clean=result(client.post(path+'/cleaning',json=cleaning))
    after,after_meta=app.state.dynamic_snow.state(tid)
    dry=dict(event_id='dry-wind',start_at=snow['end_at'],end_at='2026-01-01T02:00:00Z',
             wind_speed_m_s=14,wind_from_deg=wind,source='Controlled dry wind toward cleared road',shelter=True,unknown_building_height_m=9)
    result(client.post(path+'/weather',json=dry))
    blown,blown_meta=app.state.dynamic_snow.state(tid)
    engine=EquipmentAccess(reg);rows=[]
    for road,geom in zip(reg.catalog.roads.features,reg.catalog.metric_roads):
        if not geom.intersects(bank.buffer(10)):
            continue
        element=reg.catalog_city_elements['w'+road['id'].split(':')[-1]]
        refs=['osm:node:'+n.get('ref') for n in element.findall('nd')]
        for a,b in zip(refs,refs[1:]):
            ea,eb=[reg.catalog_city_elements['n'+n.split(':')[-1]] for n in (a,b)]
            legline=transform(TO_METRIC,LineString([(float(e.get('lon')),float(e.get('lat'))) for e in (ea,eb)]))
            if not legline.intersects(bank.buffer(3)) or legline.length<.1:
                continue
            leg=RouteLeg(way_id=road['id'],from_node=a,to_node=b)
            width=6 if road['properties']['mode']=='vehicle' else 3
            evidence=WayEvidence(way_id=road['id'],source='Scenario width/grade/authorization; OSM coordinates are real',kind='scenario',
                valid_from=initial['valid_at'],valid_to='2026-01-02T00:00:00Z',width_min_m=width,width_max_m=width,
                grade_pct=0,legal_access=True,work_allowed=True,obstacles_reviewed=True)
            for profile in PROFILES:
                results=[engine.assess_leg(leg,profile,'transit',evidence,state) for state in (before,after,blown)]
                rows.append(dict(leg=leg.model_dump(),profile_id=profile.id,evidence=evidence.model_dump(mode='json'),
                    before=results[0],after_clean=results[1],after_dry_wind=results[2]))
    selected=[r for r in rows if r['before']['status']!=r['after_clean']['status']]
    checks=[]
    for r in selected[:3]:
        profile=next(p for p in PROFILES if p.id==r['profile_id'])
        request=RouteRequest(profile=profile,valid_at=datetime.fromisoformat(blown_meta['valid_at']),
            legs=[RouteLeg(**r['leg'])],evidence=[WayEvidence(**r['evidence'])],territory_id=tid)
        response=result(client.post('/api/v1/equipment/route-assessment',json=request.model_dump(mode='json')))
        checks.append(dict(request=request.model_dump(mode='json'),response=response))
    assert clean['event_effects'][0]['bank_deposited_kg']>0
    assert blown.ledger['precipitation_kg']==after.ledger['precipitation_kg']
    assert abs(blown.balance()['error_kg'])<1e-6
    assert np.sum(abs(after.mass-blown.mass)*after.area)>0
    replay=result(client.post(path+'/replay'))
    assert replay['replay_identical']
    entrance={name:dynamic_rankings(state,reg,bounds,20,100,'entrance',object_id=oid)['features'][0]['properties']['physical']
              for name,state in (('before',before),('after_clean',after),('after_dry_wind',blown))}
    road={name:dynamic_rankings(state,reg,bounds,20,100,'road',object_id=rid)['features'][0]['properties']['physical']
          for name,state in (('before',before),('after_clean',after),('after_dry_wind',blown))}
    # A full connected approach in this snapshot: residential -> service ->
    # mapped entrance node. There is no separately mapped footway on this chain.
    chain_legs=[RouteLeg(way_id=rid,from_node='osm:node:1426077148',to_node='osm:node:1426077150'),
                RouteLeg(way_id='osm:way:781741060',from_node='osm:node:1426077150',to_node=oid)]
    chain_evidence=[WayEvidence(way_id=way,source='Explicit scenario width, grade, authorization; real shared-node topology',
        kind='scenario',valid_from=initial['valid_at'],valid_to='2026-01-02T00:00:00Z',
        width_min_m=width,width_max_m=width,grade_pct=0,legal_access=True,work_allowed=True,obstacles_reviewed=True)
        for way,width in ((rid,6),('osm:way:781741060',3))]
    chain=[]
    for profile in PROFILES:
        stages={}
        for name,state,meta in (('before',before,before_meta),('after_clean',after,after_meta),('after_dry_wind',blown,blown_meta)):
            request=RouteRequest(profile=profile,valid_at=datetime.fromisoformat(meta['valid_at']),legs=chain_legs,
                                 evidence=chain_evidence,entrance_id=oid)
            stages[name]=engine.route(request,state)
            assert stages[name]['entrance_connection']['topologically_connected']
        chain.append(dict(profile_id=profile.id,stages=stages))
    return dict(territory_id=tid,road_id=rid,entrance_id=oid,initial=initial,snow=snow,cleaning=cleaning,dry=dry,
        before=before_meta,after_clean=after_meta,after_dry_wind=blown_meta,cleaning_effect=clean,
        road_to_bank_to_equipment=rows,changed_leg_profile_pairs=len(selected),api_checks=checks,
        status_counts={stage:dict(Counter(r[stage]['status'] for r in rows)) for stage in ('before','after_clean','after_dry_wind')},
        entrance_assessments=entrance,road_assessments=road,connected_road_service_entrance_routes=chain,
        dry_transport_l1_kg=float(np.sum(abs(after.mass-blown.mass)*after.area)),replay_identical=replay['replay_identical'])


def run(output):
    output.mkdir(parents=True,exist_ok=True)
    app=make_app(db_path=output/'state.sqlite');client=TestClient(app);reg=ObjectRegistry(app.state.city)
    started=time.perf_counter();engine=EquipmentAccess(reg)
    inventory=engine.inventory();(output/'width-inventory.json').write_text(json.dumps(inventory,ensure_ascii=False,allow_nan=False))
    print('width inventory',inventory['metadata'],flush=True)
    inference=FacadeInference(reg);validation=inference.validate()
    validation['bootstrap']=bootstrap(validation,inference)
    # Verify every training centroid against the actual test block guard geometry.
    for fold in validation['folds']:
        blocks={inference.blocks[b] for b in fold['test_building_ids']}
        for b in fold['training_building_ids']:
            assert all(not box(x*500-100,y*500-100,(x+1)*500+100,(y+1)*500+100).intersects(Point(*inference.centers[b])) for x,y in blocks)
    (output/'facade-validation.json').write_text(json.dumps(validation,ensure_ascii=False,allow_nan=False,indent=2))
    print('spatial validation',validation['metrics'],validation['bootstrap'],flush=True)
    pilot=scenario(app,reg,client)
    print('real geometry causal test',pilot['status_counts'],'changed',pilot['changed_leg_profile_pairs'],flush=True)
    unknown=[b for b in sorted(inference.rows) if not reg.by_building.get(b)]
    sample=[inference.predict(b) for b in unknown[:3]]
    controls=control_experiments()
    # Preserve reproducibility fingerprints of the UI and pinned static baseline.
    manifest=json.loads(Path('research/object-backend-2026-11-22/ui-rollback-verification.json').read_text())
    assert all(sha1(b'blob '+str(len((data:=Path(f['path']).read_bytes()))).encode()+b'\0'+data).hexdigest()==f['expected'] for f in manifest['checks'])
    sources=['facade_inference.py','equipment_access.py','mobility_api.py','reproduce_mobility_backend.py',
             'tests/test_mobility_backend.py','api.py','geodata.py','snow_state.py','snow_domain.py']
    report=dict(date='2026-11-22',city_revision=reg.revision,inventory=reg.audit,widths=inventory['metadata'],
        facades=dict(buildings_with_supported_walls=len(inference.rows),labelled_buildings=len(inference.labels),
                     unknown_buildings_with_facade_candidates=len(unknown),metrics=validation['metrics'],bootstrap=validation['bootstrap'],samples=sample),
        pilot=pilot,mass_controls=controls,ui_unchanged_files=len(manifest['checks']),
        static_baseline_lock=json.loads(Path('city_sim/data/snow-engine-lock.json').read_text()),
        source_sha256={name:sha256((Path('city_sim')/name).read_bytes()).hexdigest() for name in sources},
        versions={p:importlib.metadata.version(p) for p in ('numpy','shapely','fastapi','pydantic')},
        elapsed_seconds=time.perf_counter()-started)
    (output/'results.json').write_text(json.dumps(report,ensure_ascii=False,allow_nan=False,indent=2))
    print('finished',output,flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    run(parser.parse_args().output)
