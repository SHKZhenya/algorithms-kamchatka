"""Independent saved-data validation against the unchanged OSM topology."""
import json
from hashlib import sha256,sha1
from pathlib import Path
from .api import make_app
from .object_registry import ObjectRegistry
from .procurement import ROOT
RESEARCH=Path(__file__).resolve().parents[1]/'research/procurement-0903-2026-11-22'
def main():
 app=make_app(db_path='/tmp/0903-verification.sqlite');reg=ObjectRegistry(app.state.city)
 data=json.loads((ROOT/'route-hypotheses.geojson').read_text());assert data['metadata']['city_revision']==reg.revision
 count=0
 for f in data['features']:
  pr=f['properties'];assert not pr['service_boundary_confirmed']
  for l in pr['legs']:
   e=reg.catalog_city_elements['w'+l['way_id'].split(':')[-1]];refs=['osm:node:'+x.get('ref') for x in e.findall('nd')];pairs=list(zip(refs,refs[1:]))
   pair=(l['from_node'],l['to_node']);assert pair in pairs or pair[::-1] in pairs
   count+=1
  if pr['route_status']=='all_anchors_candidate':assert all(a['to_node']==b['from_node'] for a,b in zip(pr['legs'],pr['legs'][1:]))
 exp=json.loads((RESEARCH/'experiment.json').read_text());assert exp['city_revision']==reg.revision
 assert all(r['replay_identical'] and r['dry_precipitation_kg']==0 for r in exp['runs'])
 assert all(abs(s['balance']['error_kg'])<1e-5 for r in exp['runs'] for s in r['snapshots'].values())
 assert all(a['status']=='unknown' for r in exp['runs'] for s in r['snapshots'].values() for a in s['equipment'])
 manifest=json.loads((RESEARCH.parent/'object-backend-2026-11-22/ui-rollback-verification.json').read_text());ui=[]
 for f in manifest['checks']:
  data=Path(f['path']).read_bytes();assert sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()==f['expected'];ui.append(f['path'])
 files=['procurement.py','reproduce_procurement.py','verify_procurement.py','tests/test_procurement.py','api.py','snow_state.py','equipment_access.py']
 audit=dict(city_revision=reg.revision,verified_existing_node_legs=count,route_variants=len(json.loads((ROOT/'route-hypotheses.geojson').read_text())['features']),mass_tolerance_kg=1e-5,ui_unchanged_files=len(ui),source_sha256={p:sha256((Path('city_sim')/p).read_bytes()).hexdigest() for p in files},checks='passed')
 (RESEARCH/'verification.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2));print(audit)
if __name__=='__main__':main()
