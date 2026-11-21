"""Procurement source consistency, uncertain topology and API integration contracts."""
import json,unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from fastapi.testclient import TestClient
from city_sim.procurement import Procurement,ROOT,norm,ANCHORS
from city_sim.api import make_app
from city_sim.equipment_access import PROFILES
from city_sim.tests.test_access import OSM

class ProcurementTests(unittest.TestCase):
 def test_complete_inventory_and_source_discrepancies_preserved(self):
  p=Procurement();objs=p.query()['objects'];self.assertEqual(len(objs),128)
  self.assertEqual(sum(o['kind']=='intraquarter' for o in objs),25)
  self.assertTrue(all(o['service_boundary_confirmed'] is False for o in objs))
  self.assertTrue(all(o['source'] and o['contract_source'] for o in objs))
  works=p.read('works.json');self.assertEqual(len(works),1042)
  self.assertTrue(all(w['quantity'] is None for w in works))
  self.assertAlmostEqual(sum(w['unit_price_rub'] for w in works),7798428.16,places=2)
  audit=p.read('estimate-analysis.json');self.assertEqual(len(audit['price_differences']),6)
  self.assertEqual(len(audit['year_text_conflicts']),2)
 def test_between_is_not_two_sequential_house_visits(self):
  self.assertEqual(ANCHORS[1][1]['kind'],'between_addresses')
  self.assertEqual(ANCHORS[8][1]['kind'],'between_addresses')
  self.assertEqual(norm('улица Королёва'),'улицакоролева')
 def test_variants_keep_gaps_and_revision_mismatch_fails(self):
  p=Procurement();data=p.read('route-hypotheses.geojson')
  self.assertTrue(data['features']);self.assertFalse(data['metadata']['canonical_graph_modified'])
  with self.assertRaises(RuntimeError):p.routes('different_graph')
  for f in data['features']:
   pr=f['properties'];self.assertFalse(pr['service_boundary_confirmed']);self.assertFalse(pr['operational_authorization'])
   if pr['route_status']=='all_anchors_candidate':
    self.assertFalse(pr['gaps']);self.assertEqual(pr['anchor_coverage'],1)
    self.assertTrue(all(a['to_node']==b['from_node'] for a,b in zip(pr['legs'],pr['legs'][1:])))
 def test_api_refuses_stale_graph_and_keeps_unit_rates(self):
  with TemporaryDirectory() as d:
   p=Path(d);osm=p/'map.osm';osm.write_text(OSM)
   client=TestClient(make_app(db_path=p/'state.sqlite',building_path=osm,graph_path=osm))
   self.assertEqual(client.get('/api/v1/procurement/0903/routes').status_code,409)
   self.assertEqual(client.get('/api/v1/procurement/0903/objects').status_code,409)
   works=client.get('/api/v1/procurement/0903/works');self.assertEqual(works.status_code,200);self.assertFalse(works.json()['ordered_volumes_known'])
   payload=dict(candidate_id='none',profile=PROFILES[0].model_dump(),valid_at='2026-01-01T00:00:00Z')
   self.assertEqual(client.post('/api/v1/procurement/0903/candidate-assessment',json=payload).status_code,409)

if __name__=='__main__':unittest.main()
