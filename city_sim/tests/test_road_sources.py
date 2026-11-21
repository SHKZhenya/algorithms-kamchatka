"""Import scope, provenance, no area-derived widths, public API and label blindness."""
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest
from unittest.mock import patch
import numpy as np
from shapely.geometry import LineString,mapping,shape
from shapely.ops import transform
from pyproj import Transformer
from fastapi.testclient import TestClient
from weather_coverage.geography import TO_METRIC,TO_WGS84
from city_sim.api import make_app
from city_sim.object_registry import ObjectRegistry
from city_sim.facade_inference import FacadeInference
from city_sim.facade_access_experiment import AccessFeatures
from city_sim.road_evidence import (linear_range,overture_records,uniform_whole_part,
    anchor_interval,RoadEvidence,skdf_records,coverage)
from city_sim.tests.test_access import OSM


class RoadSourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory();self.root=Path(self.tmp.name);self.osm=self.root/'osm.xml';self.osm.write_text(OSM)
        self.app=make_app(db_path=self.root/'state.sqlite',building_path=self.osm,graph_path=self.osm)
        self.reg=ObjectRegistry(self.app.state.city)
    def tearDown(self):self.tmp.cleanup()

    def test_linear_ranges_must_be_finite_and_ordered(self):
        self.assertEqual(linear_range(None),(0,1))
        for r in ([0,0],[.5,.4],[-.1,1],[0,1.1],[0,float('nan')]):
            with self.assertRaises(ValueError):linear_range(r)

    def test_overture_source_and_rule_ranges_intersect_without_whole_way_assignment(self):
        road=next(f for f in self.reg.catalog.roads.features if f['id']=='osm:way:20')
        source=dict(type='Feature',id='test-gers',geometry=road['geometry'],properties=dict(subtype='road',
            width_rules=[{'value':4,'between':[.25,.75]}],sources=[{'dataset':'OpenStreetMap','property':'','record_id':'w20@1','between':[.5,1]}]))
        records,audit=overture_records([source],self.reg)
        self.assertEqual(len(records),1)
        self.assertEqual(records[0]['properties']['source_between'],[.5,.75])
        original=transform(TO_METRIC,shape(road['geometry']));support=transform(TO_METRIC,shape(records[0]['geometry']))
        self.assertAlmostEqual(support.length/original.length,.25,places=6)
        self.assertTrue(records[0]['properties']['local_scope_verified'])
        self.assertFalse(records[0]['properties']['field_verified'])
        source['geometry']=mapping(LineString([(159,54),(159.001,54)]))
        records,_=overture_records([source],self.reg)
        self.assertFalse(records[0]['properties']['local_scope_verified'])
        self.assertEqual(coverage(records,self.reg)['width_m']['matched_osm_ways'],0)

    def test_width_comes_from_explicit_field_not_area_over_length(self):
        part={'length':1,'start':'0+000','finish':'1+000'}
        rows=[{'start':'0+000','finish':'1+000','roadway_width':7,'square':20000}]
        self.assertEqual(uniform_whole_part(rows,part,'roadway_width'),7)
        self.assertIsNone(uniform_whole_part([{**rows[0],'finish':'0+500'}],part,'roadway_width'))
        varied=[{**rows[0],'finish':'0+500'},{**rows[0],'start':'0+500','roadway_width':9}]
        self.assertIsNone(uniform_whole_part(varied,part,'roadway_width'))

    def test_km_post_interpolation_checks_geometry_and_never_extrapolates(self):
        line=LineString([(470000,5870000),(473000,5870000)])
        posts=[]
        for i in range(4):
            lon,lat=TO_WGS84(470000+i*1000,5870000)
            posts.append(dict(location=f'{i}+000',longitude=lon,latitude=lat))
        piece,quality=anchor_interval(line,posts,'0+500','2+500')
        self.assertAlmostEqual(piece.length,2000,places=5)
        self.assertFalse(quality['field_boundary_verified'])
        self.assertIsNone(anchor_interval(line,posts,'0+000','4+000'))
        bad=[{**p,'latitude':p['latitude']+.01} for p in posts]
        self.assertIsNone(anchor_interval(line,bad,'0+500','2+500'))

    def test_official_uniform_width_matches_name_and_geometry_only(self):
        road=next(f for f in self.reg.catalog.roads.features if f['id']=='osm:way:20')
        road['properties']['name']='Fixture Road'
        to_web=Transformer.from_crs(4326,3857,always_xy=True).transform
        geom=transform(to_web,shape(road['geometry']))
        self.reg.catalog.roads.features.append({**road,'id':'osm:way:999',
            'properties':{**road['properties'],'mode':'pedestrian','highway':'footway'}})
        obj=dict(registry=dict(id=1,passport_id=2,full_name='Fixture Road',children=[dict(id=1,passport_id=3,length=.1)]),
            card=dict(data=dict(start='0+000',finish='0+100',length=.1,square=10000),blocks={'roadway':1}),
            geometry=dict(features=[dict(geometry=mapping(geom))]),
            blocks={'3:roadway':dict(records=[dict(start='0+000',finish='0+100',roadway_width=4,square=10000)])})
        records,audit=skdf_records([obj],self.reg)
        self.assertEqual(len(records),1);self.assertEqual(records[0]['properties']['value'],4)
        self.assertEqual(records[0]['properties']['kind'],'official_inventory')
        self.assertFalse(audit[0]['area_divided_by_length_used'])
        obj['registry']['full_name']='Different town road'
        self.assertFalse(skdf_records([obj],self.reg)[0])

    def test_external_api_is_opt_in_validates_revision_and_never_changes_graph(self):
        client=TestClient(self.app);before=client.get('/api/v1/registry/graph').json()
        with patch.dict('os.environ',{'CITY_SIM_ROAD_EVIDENCE':''}):
            self.assertEqual(client.get('/api/v1/registry/road-evidence').json()['metadata']['status'],'not_configured')
        path=self.root/'road-evidence.json'
        road=next(f for f in self.reg.catalog.roads.features if f['id']=='osm:way:20')
        data=dict(type='FeatureCollection',features=[dict(type='Feature',id='test',geometry=road['geometry'],
            properties=dict(osm_way_id=road['id'],source='test inventory',license='local_only',kind='official_inventory',quantity='width_m',value=4))],
            contexts=[dict(source='test inventory',license='local_only',kind='official_inventory',quantity='sidewalk_width_m',value=2.2,
                osm_way_ids=[road['id']],support_bbox=list(shape(road['geometry']).bounds),local_scope_verified=False)],
            metadata={'city_revision':self.reg.revision})
        path.write_text(json.dumps(data))
        with patch.dict('os.environ',{'CITY_SIM_ROAD_EVIDENCE':str(path)}):
            response=client.get('/api/v1/registry/road-evidence');self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(len(response.json()['features']),1)
            self.assertEqual(len(response.json()['contexts']),1)
            self.assertFalse(response.json()['contexts'][0]['local_scope_verified'])
            data['metadata']['city_revision']='foreign';path.write_text(json.dumps(data))
            self.assertEqual(client.get('/api/v1/registry/road-evidence').status_code,409)
            data['metadata']['city_revision']=self.reg.revision
            data['features'][0]['properties']['value']=None;path.write_text(json.dumps(data))
            self.assertEqual(client.get('/api/v1/registry/road-evidence').status_code,422)
        self.assertEqual(before,client.get('/api/v1/registry/graph').json())

    def test_entrance_tags_are_not_accessibility_features(self):
        text=OSM.replace('<tag k="entrance" v="staircase"/>','')
        text=text.replace('<way id="10">','<node id="100" lon="158.6405" lat="53.030"><tag k="entrance" v="yes"/></node><way id="10">')
        text=text.replace('<nd ref="1"/><nd ref="2"/>','<nd ref="1"/><nd ref="100"/><nd ref="2"/>',1)
        text=text.replace('<way id="22"><nd ref="5"/><nd ref="1"/>','<way id="22"><nd ref="5"/><nd ref="100"/>')
        self.osm.write_text(text)
        app=make_app(db_path=self.root/'label.sqlite',building_path=self.osm,graph_path=self.osm)
        reg=ObjectRegistry(app.state.city);infer=FacadeInference(reg)
        self.assertTrue(infer.labels)
        original=AccessFeatures(infer,app.state.city)
        for e in reg.catalog_city_elements.values():
            for tag in list(e.findall('tag')):
                if tag.get('k') in {'entrance','door','exit'}:e.remove(tag)
        blind=AccessFeatures(infer,app.state.city)
        for bid in original.values:np.testing.assert_array_equal(original.values[bid],blind.values[bid])


if __name__=='__main__':unittest.main()
