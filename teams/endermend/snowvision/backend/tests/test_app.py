import unittest
from types import SimpleNamespace
from unittest.mock import patch
from fastapi.testclient import TestClient
from snowvision.api.app import app, analyze, demo, cameras, monitors


class PipelineTests(unittest.TestCase):
    def setUp(self):
        # Endpoint tests do not start camera workers or write the production cache.
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def test_insufficient(self):
        self.assertIsNone(analyze([demo(0)])[0])

    def test_scenes(self):
        snow = analyze([demo(i) for i in range(16)])[0]
        for kind in ('clear', 'car'):
            value = analyze([demo(i, kind) for i in range(16)])[0]
            self.assertLess(value, snow)
        self.assertTrue(0 <= snow <= 1)

    def test_api_and_failures(self):
        self.assertEqual(self.client.get('/api/health').status_code, 200)
        self.assertEqual(len(self.client.get('/api/cameras').json()), len(cameras))
        self.assertEqual(self.client.get('/api/cameras/missing/status').status_code, 404)
        self.assertEqual(self.client.get('/api/cameras/demo/status').status_code, 200)

    def test_city_outlook_includes_yandex_forecast_and_independent_warning_status(self):
        forecast = {'status': 'available', 'source': 'Яндекс Погода', 'hours': [{'timestamp': 100000}]}
        weather = {'status': 'available', 'precipitation': 'snow', 'observed_at': 100000}
        warnings = {'status': 'unavailable', 'items': []}
        with patch('snowvision.api.app.yandex_weather', SimpleNamespace(forecast=lambda: forecast, observation=lambda: weather)), \
                patch('snowvision.api.app.storm_warnings', SimpleNamespace(observation=lambda: warnings)):
            data = self.client.get('/api/city/outlook').json()
        self.assertEqual(data['forecast'], forecast)
        self.assertEqual(data['weather'], weather)
        self.assertEqual(data['warnings'], warnings)
        self.assertEqual(data['observations']['status'], 'insufficient_coverage')

    def test_snapshot_etag_and_version(self):
        cached = SimpleNamespace(image=b'jpeg', state={'frame_version': 'one'}, recent_images={'one': b'jpeg'})
        with patch.dict(monitors, {'demo': cached}):
            response = self.client.get('/api/cameras/demo/snapshot?v=one')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers['etag'], '"one"')
            conditional = self.client.get('/api/cameras/demo/snapshot', headers={'If-None-Match': '"one"'})
            self.assertEqual(conditional.status_code, 304)
            self.assertEqual(self.client.get('/api/cameras/demo/snapshot?v=old').status_code, 409)

    def test_snapshot_can_serve_recent_version_from_city_calculation(self):
        cached = SimpleNamespace(image=b'new', state={'frame_version': 'two'}, recent_images={'one': b'old', 'two': b'new'})
        with patch.dict(monitors, {'demo': cached}):
            response = self.client.get('/api/cameras/demo/snapshot?v=one')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b'old')
        self.assertEqual(response.headers['etag'], '"one"')

    def test_onset_coverage_requires_usable_current_evidence(self):
        config = dict(id='street', type='hls', interval_seconds=60)
        observation = dict(source_status='online', snow_probability=.8, confidence=.1, frame_version='a')
        monitor = SimpleNamespace(observation=lambda: observation)
        with patch('snowvision.api.app.cameras', [config]), patch.dict(monitors, {'street':monitor}, clear=True):
            self.assertEqual(self.client.get('/api/monitoring').json()['onset_online'],0)
            observation['confidence']=.7
            self.assertEqual(self.client.get('/api/monitoring').json()['onset_online'],1)
            observation['is_stale']=True
            self.assertEqual(self.client.get('/api/monitoring').json()['onset_online'],0)
        config.update(type='jpeg', motion_analysis=False, city_observation=True)
        observation.update(snow_probability=.95, analysis_method='opencv_snapshot',
                           is_stale=False, scene_frame_version='old',
                           scene={'format_valid':True,'air_view':'good','falling_snow':'yes'})
        with patch('snowvision.api.app.cameras', [config]), patch.dict(monitors, {'street':monitor}, clear=True), patch.dict('os.environ', {'SCENE_MODEL_URL':'http://model.test'}):
            coverage=self.client.get('/api/monitoring').json()
            self.assertEqual(coverage['onset_capable'],1)
            self.assertEqual(coverage['onset_online'],0)
            observation['scene_frame_version']='a'
            self.assertEqual(self.client.get('/api/monitoring').json()['onset_online'],1)

    def test_delivery_readiness_requires_opt_in_and_city_view(self):
        base = dict(type='hls', interval_seconds=2, alert_delivery=True)
        configs = [
            dict(base, id='city', city_observation=True),
            dict(base, id='outside', city_observation=False),
            dict(base, id='demo', type='demo', city_observation=True),
            dict(base, id='without_opt_in', city_observation=True, alert_delivery=False),
        ]
        observation = dict(source_status='online', snow_probability=.8, confidence=.7, frame_version='a')
        active = {c['id']: SimpleNamespace(observation=lambda: observation) for c in configs}
        with patch('snowvision.api.app.cameras', configs), patch.dict(monitors, active, clear=True):
            coverage = self.client.get('/api/monitoring').json()
            self.assertEqual((coverage['delivery_cameras'], coverage['delivery_cameras_ready']), (1, 1))

    def test_yandex_context_only_enriches_current_city_alerts_near_reference(self):
        locations = [
            dict(id='near', city_observation=True, coordinates_lat_lon=[53.0533533, 158.6449414]),
            dict(id='far', city_observation=True, coordinates_lat_lon=[53.1, 158.6]),
        ]
        records = [
            dict(camera_id='near', stage='early', is_demo=False, ended_at=None, evidence_stale=False),
            dict(camera_id='near', stage='early', is_demo=False, ended_at=None, evidence_stale=True),
            dict(camera_id='far', stage='early', is_demo=False, ended_at=None, evidence_stale=False),
        ]
        context = {'status': 'available', 'precipitation': 'rain'}
        with patch('snowvision.api.app.cameras', locations), patch('snowvision.api.app.journal', SimpleNamespace(events=lambda: records)), \
                patch('snowvision.api.app.yandex_weather', SimpleNamespace(observation=lambda: context)):
            alerts = self.client.get('/api/alerts').json()
            self.assertEqual(alerts[0]['weather_context'], context)
            self.assertTrue(all(alert['stage'] == 'early' for alert in alerts))
            self.assertTrue(all('weather_context' not in alert for alert in alerts[1:]))
