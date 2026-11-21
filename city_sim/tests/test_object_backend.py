"""Analytical raster truth and independent topology/API contracts (no live network)."""
import csv
import gzip
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import numpy as np
from fastapi.testclient import TestClient
from shapely.geometry import LineString, Point, box, Polygon
from shapely.ops import transform
from weather_coverage.geography import TO_METRIC
from city_sim.spatial import RasterAssessment
from city_sim.object_registry import ObjectRegistry, facade
from city_sim.tests.test_access import AccessTests, OSM


class RasterTests(unittest.TestCase):
    def test_long_road_short_hotspot_and_missing_length(self):
        raster = RasterAssessment([[40., 0., 0., np.nan]], (158., 53., 158.004, 53.001))
        line = LineString([(158., 53.0005), (158.004, 53.0005)])
        result = raster.assess(line, 20)
        self.assertAlmostEqual(result['coverage_fraction'], .75, places=6)
        self.assertAlmostEqual(result['mean_cm'], 40/3, places=5)
        self.assertEqual(result['p90_cm'], 40)
        self.assertEqual(result['midpoint_baseline_cm'], 0)
        self.assertAlmostEqual(result['above_fraction_total'], .25, places=6)
        fragment = LineString(result['problem_fragments'][0]['coordinates'])
        self.assertAlmostEqual(fragment.bounds[2],158.001)
        self.assertAlmostEqual(result['above_threshold'], transform(TO_METRIC, fragment).length)

    def test_gridline_no_double_count_and_outside_is_unknown(self):
        raster=RasterAssessment([[10,20],[30,40]], (158,53,158.002,53.002))
        line=LineString([(158,53.001),(158.002,53.001)])
        result=raster.assess(line)
        self.assertAlmostEqual(result['coverage_fraction'],1)
        self.assertAlmostEqual(result['mean_cm'],35,places=5)
        self.assertEqual(raster.point(Point(158.002,53.001)),None)
        outside=raster.assess(LineString([(157.999,53.001),(158.001,53.001)]))
        self.assertAlmostEqual(outside['coverage_fraction'],.5,places=6)

    def test_area_mask_weighting_and_resolution_invariance(self):
        bounds=(158,53,158.004,53.002)
        geom=box(*bounds)
        coarse=RasterAssessment([[10,np.nan],[30,50]],bounds).assess(geom)
        fine=RasterAssessment(np.repeat(np.repeat([[10,np.nan],[30,50]],2,0),2,1),bounds).assess(geom)
        for key in ('coverage_fraction','mean_cm','above_fraction_total'):
            self.assertAlmostEqual(coarse[key],fine[key],places=5)
        self.assertEqual(coarse['p90_cm'],50)
        self.assertIsNone(RasterAssessment([[np.nan]],bounds).assess(geom)['mean_cm'])
        self.assertIsNone(RasterAssessment([[1]],bounds).assess(Polygon())['mean_cm'])

    def test_bent_line_and_repeat_cell_length_preserved(self):
        line=LineString([(158,53.0005),(158.002,53.0005),(158,53.0005)])
        result=RasterAssessment([[10,50]],(158,53,158.002,53.001)).assess(line,30)
        self.assertAlmostEqual(result['covered'],transform(TO_METRIC,line).length,places=5)
        self.assertAlmostEqual(result['mean_cm'],30,places=4)
        self.assertAlmostEqual(result['above_fraction_total'],.5,places=5)

    def test_facade_outward_normal_for_both_ring_windings(self):
        polygon=box(0,0,10,20)
        for b in (polygon,Polygon(list(polygon.exterior.coords)[::-1])):
            _,p,nx,ny=facade(b,Point(0,10))
            self.assertLess(nx,0)
            self.assertAlmostEqual(ny,0)
            self.assertFalse(b.covers(Point(p.x+nx,p.y+ny)))


class ObjectBackendTests(AccessTests):
    def test_registry_and_topology_do_not_invent_links(self):
        buildings=self.client.get('/api/v1/registry/buildings').json()['features']
        self.assertEqual(buildings[0]['properties']['entrance_ids'],['osm:node:1'])
        objects={f['id']:f['properties'] for f in self.client.get('/api/v1/registry/objects').json()['features']}
        self.assertEqual(objects['osm:node:1']['pedestrian_link_status'],'shared_osm_node')
        self.assertEqual(objects['osm:node:99']['pedestrian_link_status'],'missing')
        graph=self.client.get('/api/v1/registry/graph').json()
        self.assertFalse(graph['synthetic_connections'])
        self.assertFalse(any(e['way_id']=='osm:way:21' for e in graph['edges']))
        self.assertTrue(any(e['way_id']=='osm:way:22' for e in graph['edges']))
        self.assertEqual(self.client.get('/api/v1/registry/coverage').json()['fabricated_connections'],0)

    def test_spatial_api_weather_and_separate_ranking_axes(self):
        replay={'tiles':[{'i':0,'j':0,'distance_to_source_m':4000,'extrapolated':True,
                         'hours':[{'snowfall_cm':5,'wind_from_deg':270,'wind_speed_ms':8,'valid_at':'2026-01-01T00:00Z'}]}]}
        self.record.update(tile_cells=100,source={'kind':'manual'})
        self.runner._path(self.run,'json').write_text(json.dumps(self.record))
        self.runner.replay_path(self.run).write_bytes(gzip.compress(json.dumps(replay).encode()))
        path=f'/api/v1/operations/city/runs/{self.run}'
        response=self.client.get(path+'/assessments?kind=road&threshold_cm=40')
        self.assertEqual(response.status_code,200,response.text)
        rows=response.json()['features']
        self.assertEqual(len(rows),4)
        for f in rows:
            p=f['properties']
            self.assertIn('physical',p); self.assertIn('significance',p); self.assertIn('quality',p)
            self.assertEqual(p['physical']['measure'],'length_m')
            self.assertEqual(p['physical']['actual_accessibility'],'unknown')
        obj=self.client.get(path+'/assessment?object_id=osm:node:1').json()['properties']
        self.assertTrue(obj['approaches'])
        self.assertEqual(obj['physical']['wind_context']['contexts'][0]['distance_to_source_m'],4000)
        weather=self.client.get(path+'/object-weather?object_id=osm:node:1').json()
        self.assertIsNone(weather['source_resolution_m'])
        self.assertFalse(weather['street_resolution_verified'])
        first=self.client.get(path+'/assessments?order=importance_then_physical').json()['features'][0]
        self.assertEqual(first['properties']['significance']['category'],'medical')
        frame=self.client.get(path+'/assessment?object_id=osm:node:1&frame=0').json()['properties']['physical']
        self.assertEqual(frame['max_cm'],0)
        self.assertAlmostEqual(frame['uniform_weather_baseline']['mean_cm'],5)
        self.assertEqual(self.client.get(path+'/assessments?threshold_cm=nan').status_code,422)
        self.assertEqual(self.client.get(path+'/assessments?frame=60').status_code,422)
        self.assertEqual(self.client.get(path+'/assessment?object_id=missing').status_code,404)

    def test_new_api_missing_cells_revision_and_pending_are_explicit(self):
        base=f'/api/v1/operations/city/runs/{self.run}'
        self.runner.probe_fields(self.run)['snow_cm'][:]=np.nan
        response=self.client.get(base+'/assessments?kind=road').json()
        self.assertEqual(response['metadata']['unknown'],4)
        self.assertTrue(all(f['properties']['quality']['status']=='missing' for f in response['features']))
        self.assertTrue(all(f['properties']['physical']['mean_cm'] is None for f in response['features']))
        self.record['provenance']['city_revision']='different'
        self.runner._path(self.run,'json').write_text(json.dumps(self.record))
        self.assertEqual(self.client.get(base+'/assessments').status_code,409)
        self.record['status']='running'
        self.runner._path(self.run,'json').write_text(json.dumps(self.record))
        self.assertEqual(self.client.get(base+'/assessments').status_code,409)
        self.assertEqual(self.client.get('/api/v1/operations/city/runs/'+'b'*64+'/assessments').status_code,404)

    def test_crossing_lines_without_shared_node_never_connect(self):
        from city_sim.api import make_app
        extra='<node id="100" lon="158.6385" lat="53.0295"/>\n<node id="101" lon="158.6385" lat="53.0305"/>\n<way id="100"><nd ref="100"/><nd ref="101"/><tag k="highway" v="residential"/><tag k="oneway" v="yes"/></way>'
        self.path.write_text(OSM.replace('</osm>',extra+'</osm>'))
        reg=ObjectRegistry(make_app(db_path=self.root/'cross.db',building_path=self.path,graph_path=self.path).state.city)
        graph=reg.graph((158.63,53.02,158.65,53.04))
        self.assertFalse(any(e['from']=='osm:node:101' and e['to']=='osm:node:100' and e['mode']=='vehicle' for e in graph['edges']))
        self.assertTrue(any(e['from']=='osm:node:101' and e['to']=='osm:node:100' and e['mode']=='pedestrian' for e in graph['edges']))
        self.assertFalse(any(e['from'] in {'osm:node:100','osm:node:101'} and e['to'] not in {'osm:node:100','osm:node:101'} for e in graph['edges']))

    def test_csv_rejects_synthetic_and_off_facade(self):
        path=self.root/'entrances.csv'
        path.write_text('point_source,entrance_id,building_id,lon,lat,building_type,entrance\n'
                        'candidate,888,way/10,158.640,53.030,hospital,yes\n'
                        'osm_node,889,way/10,158.640,53.030,hospital,yes\n'
                        'osm_node,890,way/10,158.7,53.1,hospital,yes\n')
        registry=ObjectRegistry(self.city,path)
        self.assertNotIn('osm:node:888',registry.objects)
        self.assertIn('osm:node:889',registry.objects)
        self.assertEqual(registry.import_audit['off_facade'],1)
        self.assertFalse(registry.objects['osm:node:889']['properties']['location_verified'])

    def test_absent_entrance_is_coverage_gap_not_invented_point(self):
        from city_sim.api import make_app
        content=OSM.replace('<tag k="entrance" v="staircase"/>','')
        self.path.write_text(content)
        city=make_app(db_path=self.root/'other.db',building_path=self.path,graph_path=self.path).state.city
        reg=ObjectRegistry(city)
        self.assertEqual(reg.audit['buildings_without_mapped_entrance'],1)
        self.assertEqual(reg.building_records[0]['properties']['entrance_coverage'],'unknown')
        self.assertEqual(reg.building_records[0]['properties']['entrance_ids'],[])

if __name__=='__main__': unittest.main()
