"""Conservation, event causality, API journal and spatial domain contracts."""
from datetime import datetime,timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest
import numpy as np
from shapely.geometry import box,mapping
from fastapi.testclient import TestClient
from city_sim.snow_state import SnowState
from city_sim.api import make_app
from city_sim.tests.test_access import OSM


def fixture(n=20,boundary='closed'):
    area=np.full((n,n),(100/n)**2)
    s=SnowState((0,0,100,100),np.zeros((n,n)),np.zeros((n,n)),area,np.ones((n,n)),np.full((n,n),np.nan))
    s.parameters['boundary']=boundary
    return s

class MassTests(unittest.TestCase):
    def test_old_loose_snow_moves_without_precipitation_and_mass_closes(self):
        s=fixture();s.loose[:,:5]=20;s.ledger['initial_kg']=s.total_kg()
        s.weather(3600,12,270)
        self.assertGreater(s.loose[:,5:].sum(),0)
        self.assertLess(abs(s.balance()['error_kg']),1e-7)
        self.assertEqual(s.ledger['precipitation_kg'],0)
        self.assertGreaterEqual(s.loose.min(),0)

    def test_bound_snow_calm_and_no_inventory_cannot_produce_snow(self):
        for wind in (0,12):
            s=fixture();s.bound[:]=10;s.ledger['initial_kg']=s.total_kg()
            s.weather(3600,wind,270)
            np.testing.assert_array_equal(s.bound,10)
            np.testing.assert_array_equal(s.loose,0)
        empty=fixture();empty.weather(3600,20,0)
        self.assertEqual(empty.total_kg(),0)

    def test_open_boundary_inflow_outflow_losses_and_ageing_accounted(self):
        s=fixture(boundary='open');s.loose[:]=5;s.parameters['ageing_per_hour']=.1;s.ledger['initial_kg']=s.total_kg()
        s.weather(3600,12,270,2,{'west':.003},.2)
        self.assertGreater(s.ledger['boundary_out_kg'],0)
        self.assertGreater(s.ledger['boundary_in_kg'],0)
        self.assertGreater(s.bound.sum(),0)
        self.assertGreater(s.ledger['explicit_loss_kg'],0)
        self.assertLess(abs(s.balance()['error_kg']),1e-6)

    def test_plow_bank_blocks_approach_and_reburial_is_computed(self):
        s=fixture();s.loose[:]=10;s.ledger['initial_kg']=s.total_kg()
        road=box(30,0,50,100);bank=box(50,0,55,100)
        effect=s.clean(road,bank,fraction=1,bank_mobile_fraction=1)
        self.assertAlmostEqual(effect['removed_kg'],20000.)
        self.assertGreater(s.loose[:,10].mean(),10)
        # Reverse wind drives the created bank back onto the cleared road.
        before=s.loose[:,6:10].mean()
        s.weather(3600,14,90)
        self.assertGreater(s.loose[:,6:10].mean(),before)
        self.assertLess(abs(s.balance()['error_kg']),1e-6)
        exported=s.clean(bank,export_fraction=1)
        self.assertGreater(exported['exported_kg'],0)
        self.assertLess(abs(s.balance()['error_kg']),1e-6)

    def test_failed_bank_does_not_mutate_and_event_order_matters(self):
        a=fixture();a.loose[:]=5;a.ledger['initial_kg']=a.total_kg()
        before=a.pack()
        with self.assertRaises(ValueError):a.clean(box(20,0,40,100))
        with self.assertRaises(ValueError):a.clean(box(20,0,40,100),box(95,0,105,100))
        np.testing.assert_array_equal(a.loose,5)
        b=fixture();b.loose[:]=5;b.ledger['initial_kg']=b.total_kg()
        a.clean(box(20,0,40,100),box(40,0,45,100),bank_mobile_fraction=1)
        a.weather(3600,12,270,2)
        b.weather(3600,12,270,2)
        b.clean(box(20,0,40,100),box(40,0,45,100),bank_mobile_fraction=1)
        self.assertFalse(np.allclose(a.mass,b.mass))

    def test_resolution_convergence_with_independent_mass_and_patch_region(self):
        region=[]
        for n in (20,40,80):
            s=fixture(n);s.loose[:,:n//4]=20;s.ledger['initial_kg']=s.total_kg()
            s.weather(1800,12,270)
            region.append(float(np.sum(s.loose[:,n//4:]*s.area[:,n//4:])))
            self.assertLess(abs(s.balance()['error_kg']),1e-6)
        self.assertLess(abs(region[-1]-region[-2]),abs(region[1]-region[0])+1e-5)

class JournalTests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory();self.root=Path(self.tmp.name)
        osm=self.root/'osm.xml';osm.write_text(OSM)
        self.client=TestClient(make_app(db_path=self.root/'state.sqlite',building_path=osm,graph_path=osm))
        self.initial={'bounds':[158.638,53.028,158.642,53.032],'rows':20,'cols':20,
            'valid_at':'2026-01-01T00:00:00Z','loose_kg_m2':10,'source':'controlled initial scenario','city_geometry':False}
        self.t=self.client.post('/api/v1/snow-state/territories',json=self.initial).json()['territory_id']
        self.path='/api/v1/snow-state/territories/'+self.t
    def tearDown(self):self.tmp.cleanup()
    def weather(self,id='wind',start='2026-01-01T00:00:00Z',end='2026-01-01T01:00:00Z'):
        return {'event_id':id,'start_at':start,'end_at':end,'wind_speed_m_s':12,'wind_from_deg':90,'source':'scenario','shelter':False}
    def cleaning(self,confirmation='scenario'):
        return {'event_id':'plow','valid_at':'2026-01-01T00:30:00Z','operation':'road_plow',
            'geometry':mapping(box(158.639,53.028,158.640,53.032)),
            'destination':mapping(box(158.640,53.028,158.6404,53.032)),
            'source':'test plow plan','confirmation':confirmation,'bank_mobile_fraction':1}
    def test_scheduled_cleaning_weather_replay_deduplication_and_history(self):
        pending=self.client.post(self.path+'/cleaning',json=self.cleaning()).json()
        self.assertEqual(pending['event_effects'][0]['status'],'pending_future')
        weather=self.weather();result=self.client.post(self.path+'/weather',json=weather)
        self.assertEqual(result.status_code,200,result.text)
        self.assertLess(abs(result.json()['balance']['error_kg']),1e-6)
        duplicate=self.client.post(self.path+'/weather',json=weather).json()
        self.assertTrue(duplicate['deduplicated'])
        self.assertEqual(duplicate['state_id'],result.json()['state_id'])
        conflicting={**weather,'wind_speed_m_s':14}
        self.assertEqual(self.client.post(self.path+'/weather',json=conflicting).status_code,409)
        replay=self.client.post(self.path+'/replay').json()
        self.assertTrue(replay['replay_identical'])
        history=self.client.get(self.path+'/history').json()
        self.assertGreaterEqual(len(history['frames']),4)
        self.assertEqual(len(history['events']),2)
        self.assertEqual(self.client.post(self.path+'/cleaning',json={**self.cleaning(),'event_id':'retro'}).status_code,409)
        self.assertEqual(self.client.post(self.path+'/weather?expected_revision=0',json=self.weather('next','2026-01-01T01:00:00Z','2026-01-01T02:00:00Z')).status_code,409)
        self.assertEqual(self.client.get(self.path+'?grid=true').status_code,200)
        ranking=self.client.get(self.path+'/rankings?kind=road').json()
        self.assertGreater(ranking['metadata']['total'],0)
    def test_unconfirmed_gps_event_does_not_clean_until_confirmation(self):
        self.client.post(self.path+'/cleaning',json=self.cleaning('unconfirmed'))
        unconfirmed=self.client.post(self.path+'/weather',json=self.weather()).json()
        self.assertEqual(unconfirmed['ledger']['internal_cleaning_moved_kg'],0)
        confirmed=self.client.post(self.path+'/cleaning/plow/confirm',json={'source':'signed work confirmation'}).json()
        self.assertGreater(confirmed['ledger']['internal_cleaning_moved_kg'],0)
        self.assertNotEqual(unconfirmed['state_id'],confirmed['state_id'])
        self.assertTrue(self.client.post(self.path+'/replay').json()['replay_identical'])
    def test_invalid_initial_unknown_mass_and_weather_gap_are_rejected(self):
        bad={**self.initial,'loose_kg_m2':None}
        self.assertEqual(self.client.post('/api/v1/snow-state/territories',json=bad).status_code,422)
        self.assertEqual(self.client.post(self.path+'/weather',json=self.weather(start='2026-01-01T00:01:00Z')).status_code,422)
        self.assertEqual(len(self.client.get(self.path+'/history').json()['events']),0)

if __name__=='__main__': unittest.main()

class EntranceEvidenceTests(unittest.TestCase):
    def test_candidate_confirmation_requires_reported_coordinate_and_preserves_origin(self):
        with TemporaryDirectory() as folder:
            root=Path(folder);osm=root/'osm.xml'
            text=OSM.replace('<tag k="entrance" v="staircase"/>','')
            text=text.replace('<way id="22"><nd ref="5"/><nd ref="1"/>','<node id="101" lon="158.63998" lat="53.0305"/><way id="22"><nd ref="5"/><nd ref="101"/>')
            osm.write_text(text)
            client=TestClient(make_app(db_path=root/'state.sqlite',building_path=osm,graph_path=osm))
            candidates=client.get('/api/v1/registry/entrance-candidates').json()['features']
            self.assertEqual(len(candidates),1)
            cid=candidates[0]['id']
            self.assertFalse(candidates[0]['properties']['graph_connection_created'])
            bad={'candidate_id':cid,'verdict':'confirmed','source':'fixture survey'}
            self.assertEqual(client.post('/api/v1/registry/entrance-reviews',json=bad).status_code,422)
            good={**bad,'coordinates':[158.640,53.0305],'uncertainty_m':1}
            response=client.post('/api/v1/registry/entrance-reviews',json=good)
            self.assertEqual(response.status_code,200,response.text)
            buildings=client.get('/api/v1/registry/buildings').json()['features']
            self.assertTrue(any(f['properties']['entrance_coverage']=='mapped' for f in buildings))
            reviewed=[f for f in client.get('/api/v1/registry/objects').json()['features'] if f['id'].startswith('review:')]
            self.assertEqual(len(reviewed),1)
            self.assertEqual(reviewed[0]['properties']['entrance_status'],'review_confirmed')
            self.assertNotEqual(list(reviewed[0]['geometry']['coordinates']),list(reviewed[0]['properties']['predicted_coordinates']))
            self.assertEqual(client.post('/api/v1/registry/entrance-reviews',json={**bad,'verdict':'rejected'}).status_code,200)
            self.assertTrue(client.get('/api/v1/registry/entrance-candidates').json()['features'])

    def test_courtyard_facade_normal_points_into_open_courtyard(self):
        from city_sim.object_registry import facade
        from shapely.geometry import Polygon,Point
        building=Polygon([(0,0),(20,0),(20,20),(0,20)],holes=[[(5,5),(5,15),(15,15),(15,5)]])
        _,p,nx,ny=facade(building,Point(5,10))
        self.assertGreater(nx,0)
        self.assertFalse(building.contains(Point(p.x+nx,p.y+ny)))

    def test_explicit_machine_width_and_direction_determine_bank_side(self):
        from city_sim.snow_state import operation_geometry
        from shapely.geometry import LineString
        line=LineString([(158.64,53.03),(158.641,53.03)])
        a,b=operation_geometry({'service_line':mapping(line),'width_m':6,'bank_width_m':4,'bank_side':'left'})
        c,d=operation_geometry({'service_line':mapping(LineString(list(line.coords)[::-1])),'width_m':6,'bank_width_m':4,'bank_side':'left'})
        self.assertGreater(b.centroid.y,a.centroid.y)
        self.assertLess(d.centroid.y,c.centroid.y)
        with self.assertRaises(ValueError):operation_geometry({'service_line':mapping(line),'bank_width_m':4,'bank_side':'left'})
