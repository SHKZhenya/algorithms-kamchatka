import unittest
from snowvision.domain.demo_city import aggregate


class DemoCityTests(unittest.TestCase):
    def test_all_recent_frames_have_normalized_weights_and_scores(self):
        def camera(name, timestamp, **observation):
            return {'id': name, 'name': name, 'type': 'jpeg', 'observation': dict(
                frame_timestamp=timestamp, frame_version=name, source_status='offline',
                is_stale=True, confidence=0, **observation)}
        cameras = [camera('recent', 1000, demo_snapshot={'frame_version':'recent', 'score':.8}), camera('boundary', -6200),
                   camera('old', -6201), dict(camera('demo', 1000), type='demo')]
        result = aggregate(cameras, now=1000)
        self.assertEqual(result['eligible_cameras'], 2)
        self.assertEqual({x['id'] for x in result['excluded_cameras']}, {'old', 'demo'})
        self.assertAlmostEqual(sum(x['weight'] for x in result['representatives']), 1)
        self.assertAlmostEqual(result['snow_score'], .65)
        self.assertAlmostEqual(sum(x['contribution'] for x in result['representatives']), .65)
        self.assertEqual(result['representatives'][1]['score_source'], 'neutral')

    def test_model_operator_and_fallback_are_bound_to_frame(self):
        observation = dict(frame_timestamp=100, frame_version='v', scene_frame_version='v',
                           scene={'format_valid': True, 'air_view':'good', 'falling_snow': 'no'},
                           feedback={'falling_snow': 'yes'},
                           demo_snapshot={'frame_version':'v', 'score':.3})
        camera = {'id': 'one', 'type': 'jpeg', 'observation': observation}
        result = aggregate([camera], now=100)
        self.assertEqual(result['snow_score'], 1)
        self.assertEqual(result['representatives'][0]['score_source'], 'operator')
        observation.pop('feedback')
        self.assertEqual(aggregate([camera], now=100)['snow_score'], 0)
        observation['scene_frame_version'] = 'old'
        self.assertEqual(aggregate([camera], now=100)['snow_score'], .3)
        observation['demo_snapshot']['frame_version'] = 'old'
        result = aggregate([camera], now=100)
        self.assertEqual(result['snow_score'], .5)
        self.assertEqual(result['snowfall_state'], 'inconclusive')

    def test_empty_data_does_not_imply_no_snow(self):
        result = aggregate([], now=100)
        self.assertIsNone(result['snow_score'])
        self.assertEqual(result['total_weight'], 0)
        self.assertEqual(result['snowfall_state'], 'unknown')
