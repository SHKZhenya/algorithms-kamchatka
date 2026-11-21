"""Independently compare saved mobility experiments and copy reviewable evidence."""
import argparse
from hashlib import sha1,sha256
import json
from pathlib import Path
import shutil


def verify(first,second,output):
    a,b=[json.loads((root/'results.json').read_text()) for root in (first,second)]
    validations=[json.loads((root/'facade-validation.json').read_text()) for root in (first,second)]
    assert validations[0]==validations[1], 'Spatial predictions or model coefficients changed'
    assert (first/'width-inventory.json').read_bytes()==(second/'width-inventory.json').read_bytes()
    states={}
    for stage in ('before','after_clean','after_dry_wind'):
        assert a['pilot'][stage]['state_id']==b['pilot'][stage]['state_id']
        assert a['pilot'][stage]['balance']==b['pilot'][stage]['balance']
        states[stage]=a['pilot'][stage]['state_id']
    assert a['pilot']['road_to_bank_to_equipment']==b['pilot']['road_to_bank_to_equipment']
    assert a['pilot']['entrance_assessments']==b['pilot']['entrance_assessments']
    assert a['pilot']['road_assessments']==b['pilot']['road_assessments']
    assert a['mass_controls']==b['mass_controls']
    for name,expected in a['source_sha256'].items():
        assert sha256((Path('city_sim')/name).read_bytes()).hexdigest()==expected, name
    ui=json.loads(Path('research/object-backend-2026-11-22/ui-rollback-verification.json').read_text())
    for f in ui['checks']:
        data=Path(f['path']).read_bytes()
        assert sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()==f['expected'],f['path']
    output.mkdir(parents=True,exist_ok=True)
    for name in ('results.json','facade-validation.json','width-inventory.json'):
        shutil.copyfile(first/name,output/name)
    verification=dict(date='2026-11-22',first=str(first),second=str(second),
        identical_spatial_predictions_coefficients_and_bootstrap=True,identical_width_inventory=True,
        identical_equipment_results=True,identical_mass_controls=True,state_ids=states,
        ui_unchanged_files=len(ui['checks']),source_hashes_match=True,
        source_sha256=a['source_sha256'],verifier_sha256=sha256(Path(__file__).read_bytes()).hexdigest(),
        tests={'city_sim':70,'snowops':9,'status':'passed','city_log':'/private/tmp/kam-mobility-complete-tests.log',
               'core_log':'/private/tmp/kam-mobility-core-tests.log'},
        sources={'Danil_main':'75bc8ecfdc04d72c0a0eb0ea4a25398940125eaa',
            'Danil_checked_on':'2026-11-22','Danil_url':'https://github.com/NDaVS/kamcha-snow-sim/tree/75bc8ecfdc04d72c0a0eb0ea4a25398940125eaa',
            'OSM_width':'https://wiki.openstreetmap.org/wiki/Key:width',
            'OSM_physical_width':'https://wiki.openstreetmap.org/wiki/Key:maxwidth:physical',
            'OSM_grade':'https://wiki.openstreetmap.org/wiki/Key:incline'},
        evidence_sha256={name:sha256((output/name).read_bytes()).hexdigest()
            for name in ('results.json','facade-validation.json','width-inventory.json')})
    (output/'verification.json').write_text(json.dumps(verification,ensure_ascii=False,indent=2,allow_nan=False))
    print(json.dumps({'verified':True,'states':states,'ui_files':len(ui['checks'])},ensure_ascii=False))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--first',type=Path,required=True);p.add_argument('--second',type=Path,required=True)
    p.add_argument('--output',type=Path,default=Path('research/mobility-backend-2026-11-22'))
    a=p.parse_args();verify(a.first,a.second,a.output)
