import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from fastapi.testclient import TestClient
from snowvision.api.app import app, monitors


class FeedbackTests(unittest.TestCase):
    def test_feedback_is_bound_to_current_frame_and_persisted(self):
        client = TestClient(app)
        self.addCleanup(client.close)
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / 'history.db'
            monitor = SimpleNamespace(state={'frame_version': 'v1'}, recent_images={'v1': b'image'},
                                      observation=lambda: {'scene': {'falling_snow': 'no'}})
            payload = dict(version='v1', snow_on_ground='yes', possible_icing='uncertain', falling_snow='yes')
            with patch('snowvision.api.app.HISTORY_DATABASE', db), patch.dict(monitors, {'test-camera': monitor}), \
                    patch('snowvision.api.app.cameras', [{'id': 'test-camera', 'type': 'jpeg'}]):
                self.assertEqual(client.post('/api/cameras/test-camera/feedback', json=payload).status_code, 200)
                result = client.get('/api/cameras/test-camera/feedback?version=v1').json()
                self.assertEqual(result['labels']['falling_snow'], 'yes')
                self.assertIsNone(client.get('/api/cameras/test-camera/feedback?version=v2').json())
                monitor.state['frame_version'] = 'v2'
                self.assertEqual(client.post('/api/cameras/test-camera/feedback', json=payload).status_code, 409)
                payload['falling_snow'] = 'invalid'
                self.assertEqual(client.post('/api/cameras/test-camera/feedback', json=payload).status_code, 422)
