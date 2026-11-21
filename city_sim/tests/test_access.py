"""Geographic and numeric contracts for object exposure; fixtures are synthetic."""
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

import numpy as np
from fastapi.testclient import TestClient
from city_sim.api import make_app
from city_sim.geodata import CityData

OSM = '''<osm version="0.6">
<node id="1" lon="158.640" lat="53.030"><tag k="entrance" v="staircase"/></node>
<node id="2" lon="158.641" lat="53.030"/>
<node id="3" lon="158.641" lat="53.031"/>
<node id="4" lon="158.640" lat="53.031"/>
<node id="5" lon="158.639" lat="53.030"><tag k="highway" v="bus_stop"/></node>
<node id="6" lon="158.638" lat="53.030"/>
<node id="7" lon="158.639" lat="53.029"/>
<node id="8" lon="158.637" lat="53.028"/>
<node id="9" lon="158.637" lat="53.029"/>
<node id="99" lon="158.638" lat="53.032"><tag k="entrance" v="yes"/></node>
<way id="10"><nd ref="1"/><nd ref="2"/><nd ref="3"/><nd ref="4"/><nd ref="1"/>
<tag k="building" v="hospital"/><tag k="addr:street" v="Fixture street"/><tag k="addr:housenumber" v="1"/></way>
<way id="20"><nd ref="5"/><nd ref="6"/><tag k="highway" v="residential"/></way>
<way id="21"><nd ref="5"/><nd ref="7"/><tag k="highway" v="service"/><tag k="access" v="private"/></way>
<way id="22"><nd ref="5"/><nd ref="1"/><tag k="highway" v="footway"/></way>
<way id="23"><nd ref="8"/><nd ref="9"/><tag k="highway" v="steps"/></way>
<way id="24"><nd ref="9"/><nd ref="999"/><tag k="highway" v="service"/></way>
</osm>'''

class AccessTests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory(); self.root=Path(self.tmp.name)
        self.path=self.root/'fixture.osm'; self.path.write_text(OSM)
        self.app=make_app(db_path=self.root/'state.db', building_path=self.path, graph_path=self.path)
        self.client=TestClient(self.app)
        self.city=self.app.state.city
        self.catalog=self.city.access_catalog
        self.run='a'*64
        self.runner=self.app.state.city_snow
        self.runner.output.mkdir(parents=True,exist_ok=True)
        self.record={'run_id':self.run,'status':'completed','bounds':[158.636,53.027,158.643,53.034],
                     'source':{'kind':'manual'},'provenance':{'city_revision':self.catalog.revision}}
        self.runner._path(self.run,'json').write_text(json.dumps(self.record))
        grid=np.arange(10000,dtype=float).reshape(100,100)/100
        np.savez_compressed(self.runner._path(self.run,'npz'),snow_cm=grid,weather_snow=grid*0,
                            opposite_snow_cm=grid[::-1],combined=grid/100,timeline=np.array([grid*0,grid]))

    def tearDown(self): self.tmp.cleanup()

    def test_real_topology_and_separate_modes(self):
        roads={f['id']:f['properties'] for f in self.catalog.roads.features}
        self.assertEqual(len(roads),4) # incomplete way is not repaired
        self.assertEqual(roads['osm:way:22']['mode'],'pedestrian')
        self.assertFalse(roads['osm:way:21']['vehicle_candidate'])
        self.assertTrue(roads['osm:way:22']['connected_to_street'])
        self.assertFalse(roads['osm:way:23']['connected_to_street'])
        self.assertEqual(self.catalog.audit['incomplete_ways'],1)
        objects={f['id']:f['properties'] for f in self.catalog.objects.features}
        self.assertEqual(objects['osm:node:1']['building_id'],'osm:way:10')
        self.assertEqual(objects['osm:node:1']['link_status'],'shared_osm_node')
        self.assertIsNone(objects['osm:node:99']['building_id'])
        self.assertEqual(objects['osm:node:99']['link_status'],'missing')

    def test_ranking_frame_threshold_and_probe_agree(self):
        path=f'/api/v1/operations/city/runs/{self.run}/objects'
        a=self.client.get(path+'?frame=0').json(); b=self.client.get(path+'?frame=1').json()
        self.assertEqual(a['metadata']['above_threshold'],0)
        self.assertGreater(b['metadata']['above_threshold'],0)
        depths=[f['properties']['snow_cm'] for f in b['features']]
        self.assertEqual(depths,sorted(depths,reverse=True))
        for f in b['features']:
            p=f['properties']; lon,lat=p['sample_coordinates']
            probe=self.runner.probe(self.run,lon,lat,frame=1)
            self.assertEqual(p['snow_cm'],probe['snow_cm'])
        medical=next(f['properties'] for f in b['features'] if f['properties']['kind']=='medical')
        self.assertEqual(medical['operational_priority'],'high')
        self.assertEqual(medical['snow_cm'],medical['worst_side_snow_cm'])
        c=self.client.get(path+'?frame=1&threshold_cm=1000&limit=1').json()
        self.assertEqual(c['metadata']['above_threshold'],0)
        self.assertEqual(c['features'][0]['properties']['snow_cm'],depths[0])
        self.assertEqual(c['metadata']['total'],len(depths))
        self.assertEqual(self.client.get(path+'?frame=20').status_code,422)
        self.assertEqual(self.client.get(path+'?threshold_cm=nan').status_code,422)
        self.assertEqual(self.client.get(path+'?bbox=0,0,3,3').status_code,422)

    def test_missing_values_do_not_become_zero_and_revision_rejected(self):
        fields=self.runner.probe_fields(self.run); fields['snow_cm'][:]=np.nan
        data=self.client.get(f'/api/v1/operations/city/runs/{self.run}/objects').json()
        self.assertEqual(data['metadata']['unknown'],data['metadata']['total'])
        self.assertTrue(all(f['properties']['risk']=='unknown' for f in data['features']))
        self.record['provenance']['city_revision']='changed'
        self.runner._path(self.run,'json').write_text(json.dumps(self.record))
        self.assertEqual(self.client.get(f'/api/v1/operations/city/runs/{self.run}/objects?frame=0').status_code,409)

if __name__ == '__main__': unittest.main()
