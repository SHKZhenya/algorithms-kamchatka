"""Reproduce pilot, city inventory/scaling and unchanged-engine physical diagnostics.

python -m city_sim.reproduce_object_backend --output out/object-backend --city
No live network, forecast substitution or field truth is required or fabricated.
"""
import argparse
from hashlib import file_digest
import json
from pathlib import Path
import time

import numpy as np
from shapely.geometry import box
from .api import make_app
from .city_snow import CitySnowRequest
from .geodata import BOUNDS
from .object_registry import ObjectRegistry
from .object_assessment import ObjectAssessment
from .snow_experiment import ENGINE_LOCK

BLOCK = (158.640, 53.031, 158.647, 53.036)


def diagnostics(study):
    model, sources = study.engine
    bounds=(158.64,53.03,158.644,53.032)
    _,_,width,height=sources.projection(bounds)
    def estimate(direction=270,speed=8,snow=6,resolution=64,building_height=15):
        b=sources.Building(box(width/2-6,height/2-12,width/2+6,height/2+12),building_height,'control')
        data=model.estimate(bounds,[b],[{'wind_from_deg':direction,'wind_speed_ms':speed,'snowfall_cm':snow}],resolution,True)
        return np.asarray(data['snow_depth_cm'],dtype=float)
    base=estimate()
    reverse=estimate(direction=90)
    calm=estimate(speed=0)
    dry=estimate(snow=0)
    short=estimate(building_height=11.25)
    fast=estimate(speed=12)
    double=estimate(snow=12)
    repeat=estimate()
    metrics={
        'directional_control':study.directional_control,
        'calm_uniform_6cm':bool(np.all(calm[np.isfinite(calm)]==6)),
        'dry_zero':bool(np.all(dry[np.isfinite(dry)]==0)),
        'repeat_identical':bool(np.array_equal(base,repeat,equal_nan=True)),
        'reverse_mae_cm':float(np.nanmean(abs(base-reverse))),
        'height_minus25percent_mae_cm':float(np.nanmean(abs(base-short))),
        'speed_8_to_12_mae_cm':float(np.nanmean(abs(base-fast))),
        'double_snow_linearity_max_error_cm':float(np.nanmax(abs(double-2*base))),
        'resolution_mean_cm':{str(r):float(np.nanmean(estimate(resolution=r))) for r in (40,64,96,128)},
        'uniform_baseline_cm':6., 'model_open_cell_mean_cm':float(np.nanmean(base)),
        'open_cell_depth_sum_ratio_to_uniform':float(np.nansum(base)/(6*np.isfinite(base).sum())),
        'mass_conservation_claimed':False, 'mass_balance_status':'not_applicable_empirical_deposition_no_mass_or_boundary_flux_state',
        'existing_snow_transport_supported':False, 'field_error_model_and_baseline':'not_testable_without_observations'}
    assert metrics['calm_uniform_6cm'] and metrics['dry_zero'] and metrics['repeat_identical']
    assert metrics['directional_control']['passes']
    return metrics


def calculate(runner,spec):
    select,source=runner.weather(spec)
    run_id=runner.identity(spec,source)
    runner._calculate(run_id,spec,select,source)
    result=runner.status(run_id)
    if result['status']!='completed':
        raise RuntimeError(result)
    return run_id


def summarize(engine,bounds,kind):
    started=time.perf_counter()
    data=engine.ranking(bounds,kind,20,limit=1000,fragments=False)
    rows=data['features']
    all_physical=[engine.raster.assess(g,20,False) for _,g in engine.assets(bounds,kind)]
    return {'metadata':data['metadata'],'assessments':len(all_physical),
            'covered_measure':sum(p['covered'] for p in all_physical),
            'total_measure':sum(p['total'] for p in all_physical),
            'fully_covered':sum(p['coverage_fraction']>=.999999 for p in all_physical),
            'unknown':sum(not p['covered'] for p in all_physical),
            'p90_differs_from_midpoint':sum(p['p90_cm']!=p['midpoint_baseline_cm'] for p in all_physical),
            'mean_differs_from_midpoint':sum(p['mean_cm'] is not None and p['midpoint_baseline_cm'] is not None and
                                            abs(p['mean_cm']-p['midpoint_baseline_cm'])>.01 for p in all_physical),
            'top10': [{'id':f['id'],'p90_cm':f['properties']['physical']['p90_cm'],
                       'coverage':f['properties']['quality']['coverage_fraction']} for f in rows[:10]],
            'wall_seconds':round(time.perf_counter()-started,3)}


def reproduce(output,city_scale=False):
    output.mkdir(parents=True,exist_ok=True)
    app=make_app(db_path=output/'state.sqlite')
    city,runner=app.state.city,app.state.city_snow
    started=time.perf_counter(); registry=ObjectRegistry(city)
    report={'date':'2026-11-22','engine':ENGINE_LOCK,'city_revision':city.data_revision,
            'inventory_seconds':round(time.perf_counter()-started,3),'coverage':registry.audit,
            'physical_diagnostics':diagnostics(runner.study)}
    spec=CitySnowRequest(source='manual',hours=1,zone=[(BLOCK[0],BLOCK[1]),(BLOCK[2],BLOCK[1]),(BLOCK[2],BLOCK[3]),(BLOCK[0],BLOCK[3])])
    run_id=calculate(runner,spec)
    engine=ObjectAssessment(registry,runner,run_id)
    report['pilot']={'bounds':BLOCK,'run_id':run_id,
                     'roads':summarize(engine,BLOCK,'road'),
                     'objects':engine.ranking(BLOCK,'all',20,limit=5,fragments=False)}
    report['pilot']['assessment_repeat_identical'] = (report['pilot']['objects'] ==
        ObjectAssessment(registry,runner,run_id).ranking(BLOCK,'all',20,limit=5,fragments=False))
    assert report['pilot']['assessment_repeat_identical']
    if city_scale:
        started=time.perf_counter()
        run_id=calculate(runner,CitySnowRequest(source='manual',hours=1))
        engine=ObjectAssessment(registry,runner,run_id)
        report['city']={'run_id':run_id,'compute_seconds':round(time.perf_counter()-started,3),
                        'roads':summarize(engine,BOUNDS,'road'),
                        'buildings':summarize(engine,BOUNDS,'building'),
                        'entrances':summarize(engine,BOUNDS,'entrance')}
    def deterministic(value):
        if isinstance(value,dict):
            return {k:deterministic(v) for k,v in value.items() if k not in {'wall_seconds','compute_seconds','inventory_seconds'}}
        if isinstance(value,list):
            return [deterministic(v) for v in value]
        return value
    (output/'deterministic-report.json').write_text(json.dumps(deterministic(report),ensure_ascii=False,allow_nan=False,sort_keys=True,indent=2))
    target=output/'report.json'
    target.write_text(json.dumps(report,ensure_ascii=False,allow_nan=False,indent=2))
    with target.open('rb') as f:
        checksum=file_digest(f,'sha256').hexdigest()
    (output/'manifest.json').write_text(json.dumps({'report.json':checksum,'engine_commit':ENGINE_LOCK['commit']},indent=2))
    print(json.dumps({'report':str(target),'coverage':registry.audit,'pilot_roads':report['pilot']['roads'],
                      'city':report.get('city')},ensure_ascii=False))

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=Path('out/object-backend'))
    parser.add_argument('--city',action='store_true')
    args=parser.parse_args()
    reproduce(args.output,args.city)
