import unittest
from snowvision.domain.city import aggregate

class CityTests(unittest.TestCase):
    def camera(self,id,point,score=.8):
        return dict(id=id,type='jpeg',city_observation=True,coordinates_lat_lon=point,
                    observation=dict(source_status='online',snow_probability=score,confidence=.8,
                                     last_analysis_at=1000,frame_timestamp=1000,frame_version=id,
                                     analysis_method='opencv_motion',is_stale=False))
    def test_same_location_is_not_independent(self):
        rows=[self.camera('a',[53.05,158.64]),self.camera('b',[53.05,158.64])]
        result=aggregate(rows,1000)
        self.assertEqual(result['independent_zones'],1)
        self.assertIsNone(result['snow_score'])
        self.assertEqual(result['representatives'][0]['zone_members'],['a','b'])
        self.assertEqual(result['representatives'][0]['zone_id'],'zone:a')

    def test_zone_representative_and_exclusion_reasons_are_explained(self):
        rows=[self.camera('older',[53.05,158.64]),self.camera('stronger',[53.05,158.64])]
        rows[0]['observation']['confidence']=.3
        rows.append(dict(self.camera('demo',[53.12,158.64]),type='demo'))
        result=aggregate(rows,1000)
        self.assertEqual(result['representatives'][0]['id'],'stronger')
        self.assertEqual(result['representatives'][0]['zone_members'],['older','stronger'])
        self.assertEqual([item['id'] for item in result['excluded_cameras']],['demo'])
        self.assertEqual(result['excluded_cameras'][0]['reason_label'],'Демонстрационный источник')
    def test_spatial_groups_and_exclusions(self):
        rows=[self.camera('a',[53.05,158.64]),self.camera('b',[53.09,158.67])]
        rows.append(dict(self.camera('demo',[53.12,158.64]),type='demo'))
        result=aggregate(rows,1000)
        self.assertAlmostEqual(result['snow_score'],.8)
        self.assertEqual(result['eligible_cameras'],2)
        self.assertIsNone(aggregate(rows,2000)['snow_score'])

    def test_lower_confidence_floor_allows_camera_to_influence_city_index(self):
        rows=[self.camera('a',[53.05,158.64],.8),self.camera('b',[53.09,158.67],.4)]
        for row in rows: row['observation']['confidence']=.2
        result=aggregate(rows,1000)
        self.assertEqual(result['eligible_cameras'],2)
        self.assertAlmostEqual(result['snow_score'],.6)

    def test_confidence_below_city_floor_is_still_excluded(self):
        rows=[self.camera('a',[53.05,158.64]),self.camera('b',[53.09,158.67])]
        for row in rows: row['observation']['confidence']=.19
        result=aggregate(rows,1000)
        self.assertEqual(result['eligible_cameras'],0)
        self.assertEqual(result['excluded']['insufficient_evidence'],2)
        self.assertIsNone(result['snow_score'])
    def test_connected_nearby_cameras_form_one_zone(self):
        rows=[self.camera(str(i),[53.05+i*.006,158.64]) for i in range(3)]
        self.assertEqual(aggregate(rows,1000)['independent_zones'],1)
        self.assertEqual(aggregate(list(reversed(rows)),1000)['independent_zones'],1)

    def test_contributions_use_every_representative_in_denominator(self):
        rows=[self.camera('snow',[53.05,158.64],.8),self.camera('clear',[53.09,158.67],0)]
        result=aggregate(rows,1000)
        self.assertAlmostEqual(result['snow_score'],.4)
        self.assertEqual([p['id'] for p in result['contributors']],['snow'])
        self.assertAlmostEqual(result['contributors'][0]['contribution'],.4)
        self.assertEqual(len(result['representatives']),2)

    def test_equal_weights_and_order_have_stable_ties(self):
        rows=[self.camera('b',[53.09,158.67],.5),self.camera('a',[53.05,158.64],.5)]
        first=aggregate(rows,1000)
        second=aggregate(list(reversed(rows)),1000)
        self.assertEqual([p['id'] for p in first['contributors']],['a','b'])
        self.assertEqual([p['id'] for p in first['contributors']],[p['id'] for p in second['contributors']])

    def test_stale_evidence_expires_from_status_and_contribution(self):
        rows=[self.camera('a',[53.05,158.64],.8),self.camera('b',[53.09,158.67],.8)]
        self.assertEqual(aggregate(rows,1000)['snowfall_state'],'local_positive')
        expired=aggregate(rows,1121)
        self.assertEqual(expired['snowfall_state'],'unknown')
        self.assertEqual(expired['contributors'],[])
        self.assertEqual(expired['excluded']['expired_analysis'],2)

    def test_two_early_zones_do_not_skip_repeat_confirmation_state(self):
        rows=[self.camera('a',[53.05,158.64],.8),self.camera('b',[53.09,158.67],.8)]
        result=aggregate(rows,1000)
        self.assertEqual(result['independent_zones'],2)
        self.assertEqual(result['snowfall_state'],'local_positive')

    def test_local_jpeg_heuristic_is_not_city_evidence(self):
        camera=self.camera('jpeg',[53.05,158.64],.9)
        camera['observation']['analysis_method']='opencv_snapshot'
        camera['observation']['frame_version']='jpeg'
        result=aggregate([camera],1000)
        self.assertEqual(result['snowfall_state'],'unknown')
        self.assertEqual(result['contributors'],[])
        self.assertEqual(result['excluded']['snapshot_only'],1)

    def test_model_signal_remains_local_when_it_has_no_index_score(self):
        camera=self.camera('model',[53.05,158.64],None)
        camera['observation'].update(
            analysis_method='opencv_snapshot',snow_probability=None,scene_frame_version='model',
            scene={'format_valid':True,'air_view':'good','falling_snow':'yes'},
        )
        result=aggregate([camera],1000)
        self.assertEqual(result['snowfall_state'],'local_positive')
        self.assertTrue(result['local_evidence'][0]['model_only'])
        self.assertEqual(result['local_evidence'][0]['frame_version'], 'model')
        self.assertEqual(result['local_evidence'][0]['frame_timestamp'], 1000)
        self.assertEqual(result['contributors'],[])

    def test_confirmed_episode_in_one_zone_stays_local(self):
        rows=[self.camera('a',[53.05,158.64],.8)]
        event={'camera_id':'a','stage':'confirmed','last_seen':1000}
        result=aggregate(rows,1000,events=[event])
        self.assertEqual(result['snowfall_state'],'local_positive')

    def test_two_confirmed_independent_zones_confirm_observed_city_area(self):
        rows=[self.camera('a',[53.05,158.64],.8),self.camera('b',[53.09,158.67],.8)]
        events=[{'camera_id':key,'stage':'confirmed','last_seen':1000} for key in ('a','b')]
        self.assertEqual(aggregate(rows,1000,events=events)['snowfall_state'],'confirmed')

    def test_expired_event_is_only_last_known_and_never_current(self):
        result=aggregate([],1121,events=[{
            'camera_id':'a','name':'Улица','location':'Чехова','stage':'confirmed',
            'last_seen':1000,'evidence_stale':True,
        }])
        self.assertEqual(result['snowfall_state'],'unknown')
        self.assertEqual(result['last_known'][0]['camera_id'],'a')

    def test_conflicting_methods_are_explicit(self):
        camera=self.camera('a',[53.05,158.64],.8)
        camera['observation'].update(scene_frame_version='a',scene={
            'format_valid':True,'air_view':'good','falling_snow':'no'})
        self.assertEqual(aggregate([camera],1000)['snowfall_state'],'conflict')
