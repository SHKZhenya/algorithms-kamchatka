import unittest
from snowvision.domain.demo_city import aggregate


class DemoCityTests(unittest.TestCase):
    def test_only_demo_and_frames_older_than_two_hours_are_excluded(self):
        def camera(name, timestamp, **observation):
            return {'id': name, 'name': name, 'type': 'jpeg', 'observation': dict(
                frame_timestamp=timestamp, frame_version=name, source_status='offline',
                is_stale=True, confidence=0, **observation)}
        cameras = [camera('recent', 1000, snow_probability=.8), camera('boundary', -6200),
                   camera('old', -6201), dict(camera('demo', 1000), type='demo')]
        result = aggregate(cameras, now=1000)
        self.assertEqual(result['eligible_cameras'], 2)
        self.assertEqual({x['id'] for x in result['excluded_cameras']}, {'old', 'demo'})
        self.assertEqual(result['snowfall_state'], 'local_positive')
        self.assertEqual(result['snow_score'], .8)

    def test_unknown_and_operator_correction_do_not_invent_scores(self):
        observation = dict(frame_timestamp=100, frame_version='v', scene_frame_version='v',
                           scene={'format_valid': True, 'falling_snow': 'no'},
                           feedback={'falling_snow': 'yes'})
        result = aggregate([{'id': 'one', 'type': 'jpeg', 'observation': observation}], now=100)
        self.assertEqual(result['snowfall_state'], 'local_positive')
        self.assertIsNone(result['snow_score'])
        self.assertEqual(result['local_evidence'][0]['method'], 'operator')
        observation['feedback']['falling_snow'] = 'uncertain'
        self.assertEqual(aggregate([{'id': 'one', 'type': 'jpeg', 'observation': observation}], now=100)['snowfall_state'], 'inconclusive')
