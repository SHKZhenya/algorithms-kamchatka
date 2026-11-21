"""Independent spatial holdout and equipment/snow causality contracts."""
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import numpy as np
from fastapi.testclient import TestClient
from shapely.geometry import LineString, box, mapping
from city_sim.api import make_app
from city_sim.object_registry import ObjectRegistry
from city_sim.equipment_access import (EquipmentAccess,RouteRequest,RouteLeg,WayEvidence,PROFILES,
    metres,width_evidence,snow_width)
from city_sim.facade_inference import FacadeInference,fold_of
from city_sim.snow_state import SnowState
from city_sim.tests.test_access import OSM


AT=datetime(2026,1,1,tzinfo=timezone.utc)


def evidence(way='osm:way:20',width=4,**kw):
    return WayEvidence(way_id=way,source='explicit test corridor scenario',kind='scenario',
        valid_from='2025-12-31T00:00:00Z',valid_to='2026-01-02T00:00:00Z',
        width_min_m=width,width_max_m=width,grade_pct=0,legal_access=True,
        work_allowed=True,obstacles_reviewed=True,**kw)


class WidthAndSnowTests(unittest.TestCase):
    def test_units_and_ambiguous_width_are_not_guessed(self):
        self.assertAlmostEqual(metres('6 ft'),1.8288)
        self.assertAlmostEqual(metres('6\' 6"'),1.9812)
        self.assertEqual(metres('2,5 m'),2.5)
        for raw in ('narrow','3;4','3-4','nan','-2','0','1 km'):
            self.assertIsNone(metres(raw))
        self.assertEqual(width_evidence({'highway':'residential'})['status'],'unknown')
        self.assertEqual(width_evidence({'highway':'residential','lanes':'2'})['status'],'estimated')
        self.assertIsNone(width_evidence({'highway':'path','lanes':'2'})['interval_m'])

    def test_full_causal_snowfall_bank_narrowing_dry_transport(self):
        n=100;area=np.ones((n,n))
        s=SnowState((0,0,100,100),np.zeros_like(area),np.zeros_like(area),area,np.ones_like(area),np.full_like(area,np.nan),200,200,200)
        s.loose[:]=10;s.ledger['initial_kg']=s.total_kg()
        s.weather(1800,12,270,4)
        path=LineString([(25,50),(70,50)])
        before=snow_width(path,4,s,.2)
        self.assertEqual(before['available_width_range_m'],[4,4])
        clean=s.clean(box(30,0,50,100),box(50,0,55,100),bank_mobile_fraction=1)
        after=snow_width(path,4,s,.2)
        self.assertEqual(after['available_width_range_m'],[0,0])
        old=s.mass.copy();precip=s.ledger['precipitation_kg']
        s.weather(3600,14,90)
        self.assertEqual(s.ledger['precipitation_kg'],precip)
        self.assertGreater(float(np.sum(abs(s.mass-old))),0)
        self.assertGreater(float(s.mass[:,30:50].mean()),0)
        self.assertGreater(clean['bank_deposited_kg'],0)
        self.assertLess(abs(s.balance()['error_kg']),1e-6)

    def test_density_uncertainty_missing_coverage_and_single_cell_bottleneck(self):
        a=np.ones((10,10));s=SnowState((0,0,10,10),a*30,a*0,a,a,np.full_like(a,np.nan),200,100,400)
        line=LineString([(1,5),(9,5)])
        self.assertEqual(snow_width(line,4,s,.2)['available_width_range_m'],[0,4])
        self.assertLess(snow_width(LineString([(-1,5),(9,5)]),4,s,.2)['coverage_fraction'],1)
        s.loose[:]=0;s.loose[:,5]=100
        self.assertEqual(snow_width(line,4,s,.2)['available_width_range_m'],[0,0])


class MobilityAPI(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory();self.root=Path(self.tmp.name)
        self.osm=self.root/'osm.xml';self.osm.write_text(OSM)
        self.app=make_app(db_path=self.root/'state.sqlite',building_path=self.osm,graph_path=self.osm)
        self.client=TestClient(self.app);self.reg=ObjectRegistry(self.app.state.city)
        self.engine=EquipmentAccess(self.reg)

    def tearDown(self):
        self.tmp.cleanup()

    def request(self,way='osm:way:20',a='osm:node:5',b='osm:node:6',**kwargs):
        return RouteRequest(profile=PROFILES[0],valid_at=AT,legs=[RouteLeg(way_id=way,from_node=a,to_node=b)],**kwargs)

    def test_three_states_and_different_machine_sizes(self):
        req=self.request()
        self.assertEqual(self.engine.route(req)['status'],'unknown')
        req.evidence=[evidence(width=2)]
        self.assertEqual(self.engine.route(req)['status'],'passable')
        req.profile=PROFILES[2]
        self.assertEqual(self.engine.route(req)['status'],'blocked')
        req.profile=PROFILES[0];req.operation='clear';req.evidence=[evidence(width=1.5)]
        self.assertEqual(self.engine.route(req)['status'],'blocked')

    def test_steps_and_no_synthetic_route_edges(self):
        req=self.request('osm:way:23','osm:node:8','osm:node:9',evidence=[evidence('osm:way:23')])
        self.assertEqual(self.engine.route(req)['status'],'blocked')
        bad=self.request(a='osm:node:1')
        with self.assertRaises(ValueError):self.engine.route(bad)
        response=self.client.post('/api/v1/equipment/route-assessment',json=bad.model_dump(mode='json'))
        self.assertEqual(response.status_code,422)

    def test_validity_duplicates_and_api(self):
        req=self.request(evidence=[evidence()])
        self.assertEqual(self.client.post('/api/v1/equipment/route-assessment',json=req.model_dump(mode='json')).json()['status'],'passable')
        expired=req.model_dump(mode='json');expired['valid_at']='2027-01-01T00:00:00Z'
        self.assertEqual(self.client.post('/api/v1/equipment/route-assessment',json=expired).status_code,422)
        duplicate=req.model_dump(mode='json');duplicate['evidence']*=2
        self.assertEqual(self.client.post('/api/v1/equipment/route-assessment',json=duplicate).status_code,422)
        self.assertEqual(len(self.client.get('/api/v1/equipment/profiles').json()['profiles']),3)
        data=self.client.get('/api/v1/registry/road-widths').json()
        self.assertEqual(data['metadata']['surveyed_widths'],0)
        self.assertEqual(len(data['features']),4)

    def test_stale_snow_frame_time_rejected(self):
        initial=dict(bounds=[158.636,53.027,158.643,53.034],rows=10,cols=10,loose_kg_m2=0,
                     valid_at=AT.isoformat(),source='scenario',city_geometry=False)
        tid=self.client.post('/api/v1/snow-state/territories',json=initial).json()['territory_id']
        req=self.request(territory_id=tid,evidence=[evidence()]).model_dump(mode='json')
        self.assertEqual(self.client.post('/api/v1/equipment/route-assessment',json=req).status_code,200)
        req['valid_at']='2026-01-01T01:00:00Z'
        self.assertEqual(self.client.post('/api/v1/equipment/route-assessment',json=req).status_code,422)

    def test_facade_hypotheses_leave_counts_unknown_and_can_sample_snow(self):
        result=self.client.get('/api/v1/registry/facade-hypotheses?building_id=osm:way:10')
        self.assertEqual(result.status_code,200,result.text)
        data=result.json()
        self.assertIsNone(data['metadata']['predicted_entrance_count'])
        self.assertEqual(len(data['metadata']['probable_facade_ids']),2)
        self.assertTrue(all(f['properties']['entrance_status']=='hypothesis_not_observed' for f in data['features']))
        self.assertEqual(self.client.get('/api/v1/registry/facade-hypotheses?building_id=missing').status_code,404)
        self.assertEqual(self.client.get('/api/v1/registry/facade-hypotheses?building_id=osm:way:10&frame=0').status_code,422)
        initial=dict(bounds=[158.636,53.027,158.643,53.034],rows=10,cols=10,loose_kg_m2=10,
                     valid_at=AT.isoformat(),source='scenario',city_geometry=False)
        tid=self.client.post('/api/v1/snow-state/territories',json=initial).json()['territory_id']
        data=self.client.get('/api/v1/registry/facade-hypotheses',params={'building_id':'osm:way:10','territory_id':tid}).json()
        self.assertTrue(all(f['properties']['physical']['p90_kg_m2']==10 for f in data['features']))

    def test_spatial_validation_never_uses_test_labels(self):
        infer=FacadeInference(self.reg)
        # Fixture has a corner door which cannot identify the labelled wall.
        self.assertEqual(len(infer.labels),0)
        result=infer.validate()
        for fold in result['folds']:
            self.assertFalse(set(fold['training_building_ids'])&set(fold['test_building_ids']))
        self.assertIsNone(result['metrics']['model']['top1_hit'])

    def test_width_restrictions_on_nodes_and_grade(self):
        self.osm.write_text(OSM.replace('<node id="6" lon="158.638" lat="53.030"/>',
            '<node id="6" lon="158.638" lat="53.030"><tag k="barrier" v="bollard"/><tag k="maxwidth:physical" v="0.9"/></node>'))
        app=make_app(db_path=self.root/'second.sqlite',building_path=self.osm,graph_path=self.osm)
        engine=EquipmentAccess(ObjectRegistry(app.state.city))
        self.assertEqual(engine.route(self.request(evidence=[evidence()]))['status'],'blocked')
        e=evidence();e.grade_pct=20
        self.assertEqual(self.engine.route(self.request(evidence=[e]))['status'],'blocked')

    def test_turn_corridor_and_restriction(self):
        extra='<way id="25"><nd ref="6"/><nd ref="7"/><tag k="highway" v="residential"/></way>'
        text=OSM.replace('</osm>',extra+'</osm>')
        self.osm.write_text(text)
        app=make_app(db_path=self.root/'turn.sqlite',building_path=self.osm,graph_path=self.osm)
        engine=EquipmentAccess(ObjectRegistry(app.state.city))
        req=self.request(evidence=[evidence(),evidence('osm:way:25')])
        req.legs.append(RouteLeg(way_id='osm:way:25',from_node='osm:node:6',to_node='osm:node:7'))
        self.assertEqual(engine.route(req)['turns'][0]['status'],'unknown')
        for e in req.evidence:e.corridor=mapping(box(158.635,53.025,158.643,53.034))
        self.assertEqual(engine.route(req)['turns'][0]['status'],'passable')
        relation='<relation id="100"><member type="way" ref="20" role="from"/><member type="node" ref="6" role="via"/><member type="way" ref="25" role="to"/><tag k="type" v="restriction"/><tag k="restriction" v="no_left_turn"/></relation>'
        self.osm.write_text(text.replace('</osm>',relation+'</osm>'))
        app=make_app(db_path=self.root/'restriction.sqlite',building_path=self.osm,graph_path=self.osm)
        engine=EquipmentAccess(ObjectRegistry(app.state.city))
        self.assertEqual(engine.route(req)['status'],'blocked')
        conditional=relation.replace('k="restriction" v="no_left_turn"','k="restriction:conditional" v="no_left_turn @ (snow)"')
        self.osm.write_text(text.replace('</osm>',conditional+'</osm>'))
        app=make_app(db_path=self.root/'conditional.sqlite',building_path=self.osm,graph_path=self.osm)
        engine=EquipmentAccess(ObjectRegistry(app.state.city))
        self.assertEqual(engine.route(req)['status'],'unknown')

    def test_entrance_connection_requires_real_terminal_node_and_door_clearance(self):
        request=self.request('osm:way:22','osm:node:5','osm:node:1',
            evidence=[evidence('osm:way:22')],entrance_id='osm:node:1')
        result=self.engine.route(request)
        self.assertTrue(result['entrance_connection']['topologically_connected'])
        self.assertEqual(result['entrance_connection']['status'],'unknown')
        request.entrance_id='osm:node:99'
        self.assertFalse(self.engine.route(request)['entrance_connection']['topologically_connected'])

    def test_graph_file_is_included_when_buildings_are_separate(self):
        from city_sim.geodata import CityData
        b=self.root/'buildings.osm';b.write_text('<osm version="0.6"/>')
        city=CityData(b,self.osm,building_evidence=False)
        self.assertIn('w20',city.osm_elements)

    def test_clear_side_strips_do_not_certify_obstructed_centerline(self):
        from shapely.geometry import shape,Point
        from shapely.ops import transform
        from weather_coverage.geography import TO_METRIC
        line=transform(TO_METRIC,shape(self.engine.roads['osm:way:20']['geometry']))
        x0,y0,x1,y1=line.bounds;n=100
        bounds=(x0-10,y0-10,x1+10,y1+10)
        area=np.full((n,n),(bounds[2]-bounds[0])*(bounds[3]-bounds[1])/n**2)
        s=SnowState(bounds,np.zeros_like(area),np.zeros_like(area),area,np.ones_like(area),np.full_like(area,np.nan))
        for j in range(n):
            for i in range(n):
                p=Point(bounds[0]+(i+.5)*s.dx,bounds[3]-(j+.5)*s.dy)
                if line.distance(p)<.5:s.bound[j,i]=400
        req=self.request(evidence=[evidence(width=6)])
        result=self.engine.route(req,s)
        self.assertGreater(result['legs'][0]['available_width_range_m'][0],result['legs'][0]['required_width_m'])
        self.assertEqual(result['status'],'unknown')
        self.assertIn('continuous_centerline_snow_clearance_not_established',result['legs'][0]['reasons'])


if __name__=='__main__':unittest.main()
