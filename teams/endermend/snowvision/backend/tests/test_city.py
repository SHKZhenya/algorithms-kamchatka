import unittest
from snowvision.domain.city import aggregate

class CityTests(unittest.TestCase):
    def camera(self,id,point,score=.8):
        return dict(id=id,type='jpeg',city_observation=True,coordinates_lat_lon=point,
                    observation=dict(source_status='online',snow_probability=score,confidence=.8,last_analysis_at=1000))
    def test_same_location_is_not_independent(self):
        rows=[self.camera('a',[53.05,158.64]),self.camera('b',[53.05,158.64])]
        result=aggregate(rows,1000)
        self.assertEqual(result['independent_zones'],1)
        self.assertIsNone(result['snow_score'])
    def test_spatial_groups_and_exclusions(self):
        rows=[self.camera('a',[53.05,158.64]),self.camera('b',[53.09,158.67])]
        rows.append(dict(self.camera('demo',[53.12,158.64]),type='demo'))
        result=aggregate(rows,1000)
        self.assertAlmostEqual(result['snow_score'],.8)
        self.assertEqual(result['eligible_cameras'],2)
        self.assertIsNone(aggregate(rows,2000)['snow_score'])
    def test_connected_nearby_cameras_form_one_zone(self):
        rows=[self.camera(str(i),[53.05+i*.006,158.64]) for i in range(3)]
        self.assertEqual(aggregate(rows,1000)['independent_zones'],1)
        self.assertEqual(aggregate(list(reversed(rows)),1000)['independent_zones'],1)
