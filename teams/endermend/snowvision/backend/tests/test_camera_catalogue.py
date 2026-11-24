import json
import unittest
from pathlib import Path

from snowvision.domain.demo_city import aggregate


class CameraCatalogueTests(unittest.TestCase):
    def test_remote_sources_are_available_to_demonstration(self):
        path = Path(__file__).resolve().parents[1] / 'snowvision/resources/cameras.json'
        cameras = json.loads(path.read_text())
        ids = [camera['id'] for camera in cameras]
        self.assertEqual(len(ids), len(set(ids)))
        for camera_id in ('leninskaya', 'ran-nlc-avh', 'ran-asa', 'ran-mutnovsky-grl',
                          'noyabrsk', 'kirovsk-k6-bottom', 'rovaniemi', 'pine-pass'):
            camera = next(camera for camera in cameras if camera['id'] == camera_id)
            camera['observation'] = {'frame_timestamp': 1000, 'frame_version': camera_id}
            self.assertEqual(aggregate([camera], now=1000)['eligible_cameras'], 1)
