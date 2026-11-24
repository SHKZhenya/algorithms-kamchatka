import asyncio
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch
from snowvision.api.app import analyze, demo
from snowvision.services.monitor import CameraMonitor

class MonitorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        provider = patch.dict(os.environ, {"SCENE_MODEL_PROVIDER": "weather_vlm"})
        provider.start()
        self.addCleanup(provider.stop)
        self.directory = tempfile.TemporaryDirectory()
        self.database = self.directory.name + '/history.db'
        with sqlite3.connect(self.database) as db:
            db.execute('CREATE TABLE observations (camera TEXT, ts REAL, payload TEXT)')
            db.execute('CREATE TABLE frame_cache (camera TEXT PRIMARY KEY, image BLOB, payload TEXT)')
        self.acquisition = asyncio.Semaphore(4)
        self.processing = asyncio.Semaphore(2)

    async def asyncTearDown(self):
        self.directory.cleanup()

    def make(self, id='a', motion=True, interval=30):
        return CameraMonitor(dict(id=id,type='jpeg',url='https://example.org/'+id,
                                  interval_seconds=interval,motion_analysis=motion),None,
                             self.acquisition,self.processing,analyze,demo,self.database)

    async def test_slow_camera_does_not_block_fast(self):
        slow, fast = self.make('slow'), self.make('fast')
        started, release = asyncio.Event(), asyncio.Event()
        async def fetch(camera, client):
            if camera['id']=='slow':
                started.set()
                await release.wait()
            return demo(0)
        with patch('snowvision.services.monitor.fetch_frame',side_effect=fetch):
            task=asyncio.create_task(slow.attempt())
            await started.wait()
            await asyncio.wait_for(fast.attempt(),1)
            self.assertIsNotNone(fast.image)
            self.assertIsNone(slow.image)
            self.assertEqual(await slow.attempt(),'in_progress')
            release.set()
            await task

    async def test_failure_keeps_frame_and_success_time(self):
        monitor=self.make()
        with patch('snowvision.services.monitor.fetch_frame',AsyncMock(return_value=demo(0))):
            await monitor.attempt()
        previous=(monitor.image,monitor.state['frame_version'],monitor.state['last_success_at'],monitor.state['frame_timestamp'])
        monitor.state['next_attempt_at']=0
        with patch('snowvision.services.monitor.fetch_frame',AsyncMock(side_effect=ValueError('corrupt JPEG'))):
            await monitor.attempt()
        self.assertEqual(previous,(monitor.image,monitor.state['frame_version'],monitor.state['last_success_at'],monitor.state['frame_timestamp']))
        self.assertEqual(monitor.state['source_status'],'degraded')
        self.assertIsNone(monitor.state['snow_probability'])
        self.assertEqual(monitor.state['consecutive_failures'],1)

    async def test_identical_frame_and_rate_limit(self):
        monitor=self.make(interval=.5)
        with patch('snowvision.services.monitor.fetch_frame',AsyncMock(return_value=demo(0))) as fetch:
            await monitor.attempt()
            version, timestamp = monitor.state['frame_version'],monitor.state['frame_timestamp']
            self.assertEqual(await monitor.attempt(),'rate_limited')
            self.assertEqual(fetch.await_count,1)
            monitor.state['next_attempt_at']=0
            await monitor.attempt()
        self.assertEqual(monitor.state['frame_version'],version)
        self.assertEqual(monitor.state['frame_timestamp'],timestamp)
        self.assertEqual(len(monitor.frames),1)

    async def test_recent_frame_versions_remain_available_for_dashboard_snapshot(self):
        monitor=self.make()
        with patch('snowvision.services.monitor.fetch_frame',AsyncMock(side_effect=[demo(0),demo(1)])):
            await monitor.attempt()
            first=monitor.state['frame_version']
            monitor.state['next_attempt_at']=0
            await monitor.attempt()
        second=monitor.state['frame_version']
        self.assertNotEqual(first,second)
        self.assertIn(first,monitor.recent_images)
        self.assertIn(second,monitor.recent_images)
        self.assertEqual(monitor.recent_images[second],monitor.image)

    async def test_snapshot_only_uses_a_single_frame(self):
        monitor=self.make(motion=False)
        monitor.analyze=Mock(side_effect=AssertionError('must not analyze'))
        with patch('snowvision.services.monitor.fetch_frame',AsyncMock(return_value=demo(0))):
            await monitor.attempt()
        monitor.analyze.assert_not_called()
        self.assertEqual(monitor.frames,[])
        self.assertEqual(monitor.state['analysis_method'],'opencv_snapshot')
        self.assertIsNotNone(monitor.state['snow_probability'])
        self.assertGreater(monitor.state['confidence'], .5)

    async def test_older_source_cannot_replace_newer_frame(self):
        monitor=self.make()
        async def fresh(camera,client):
            camera['source_timestamp']=2000000000
            return demo(0)
        with patch('snowvision.services.monitor.fetch_frame',side_effect=fresh):
            await monitor.attempt()
        version=monitor.state['frame_version']
        monitor.state['next_attempt_at']=0
        async def old(camera,client):
            camera['source_timestamp']=1000000000
            return demo(1)
        with patch('snowvision.services.monitor.fetch_frame',side_effect=old):
            await monitor.attempt()
        self.assertEqual(monitor.state['frame_version'],version)

    async def test_cache_survives_restart(self):
        monitor=self.make()
        monitor.state['analysis_method']='legacy'
        with patch('snowvision.services.monitor.fetch_frame',AsyncMock(return_value=demo(0))):
            await monitor.attempt()
        restored=self.make()
        restored.restore()
        self.assertEqual(restored.image,monitor.image)
        self.assertEqual(restored.state['analysis_method'],'opencv_snapshot')
        self.assertGreater(restored.state['revision'],monitor.state['revision'])
        self.assertEqual(restored.state['frame_timestamp'],monitor.state['frame_timestamp'])
        self.assertIsNotNone(restored.state['snow_probability'])
        self.assertGreater(restored.state['confidence'], .5)
        self.assertEqual(restored.state['next_attempt_at'],monitor.state['next_attempt_at'])
        self.assertEqual(await restored.attempt(),'rate_limited')

    async def test_demo_is_independent_of_external_network_slots(self):
        monitor = CameraMonitor(dict(id='demo', type='demo'), None,
                                asyncio.Semaphore(0), self.processing, analyze, demo, self.database)
        await asyncio.wait_for(monitor.attempt(), 1)
        self.assertIsNotNone(monitor.image)

    async def test_http_and_corrupt_jpeg_do_not_replace_cache(self):
        import httpx
        import cv2
        encoded=cv2.imencode('.jpg',demo(0))[1].tobytes()
        for code,body in ((503,b'unavailable'),(200,b'broken jpeg')):
            with self.subTest(status=code):
                responses=[httpx.Response(200,content=encoded),httpx.Response(code,content=body)]
                transport=httpx.MockTransport(lambda request:responses.pop(0))
                async with httpx.AsyncClient(transport=transport) as client:
                    monitor=self.make()
                    monitor.client=client
                    await monitor.attempt()
                    image,version=monitor.image,monitor.state['frame_version']
                    monitor.state['next_attempt_at']=0
                    await monitor.attempt()
                    self.assertEqual(monitor.image,image)
                    self.assertEqual(monitor.state['frame_version'],version)
                    self.assertEqual(monitor.state['source_status'],'degraded')

    async def test_expired_score_is_hidden_between_attempts(self):
        import time
        monitor=self.make()
        monitor.state.update(frame_timestamp=time.time()-300, source_status='online', snow_probability=0.9, status='snowing')
        state=monitor.observation()
        self.assertTrue(state['is_stale'])
        self.assertIsNone(state['snow_probability'])
        self.assertEqual(state['source_status'],'stale')

    async def test_sparse_sequence_is_unknown(self):
        import time
        monitor=self.make(interval=.5)
        monitor.frames=[(time.time()-30,demo(0))]
        monitor.state.update(snow_probability=.9,status='snowing')
        with patch('snowvision.services.monitor.fetch_frame',AsyncMock(return_value=demo(1))):
            await monitor.attempt()
        self.assertIsNone(monitor.state['snow_probability'])
        self.assertEqual(monitor.state['analysis_reason'],'sampling_too_sparse')

    async def test_hls_burst_uses_actual_sequence_despite_poll_interval(self):
        monitor=CameraMonitor(dict(id='hls',type='hls',url='https://example.org/live',interval_seconds=60),None,
                              self.acquisition,self.processing,Mock(return_value=(.8,.7,'snowing')),demo,self.database)
        with patch('snowvision.services.monitor.fetch_burst',AsyncMock(return_value=[demo(i) for i in range(8)])):
            await monitor.attempt()
        self.assertEqual(monitor.state['snow_probability'],.8)
        self.assertEqual(len(monitor.frames),8)
        self.assertLess(monitor.state['next_attempt_at']-monitor.state['timestamp'], 2.2)

    async def test_duplicate_burst_is_unknown(self):
        monitor=CameraMonitor(dict(id='hls',type='hls',url='https://example.org/live',interval_seconds=60),None,
                              self.acquisition,self.processing,Mock(side_effect=AssertionError('duplicated frames')),demo,self.database)
        with patch('snowvision.services.monitor.fetch_burst',AsyncMock(return_value=[demo(0) for _ in range(8)])):
            await monitor.attempt()
        self.assertIsNone(monitor.state['snow_probability'])
        self.assertEqual(monitor.state['analysis_reason'],'insufficient_distinct_frames')

    async def test_repeated_frame_expires_even_when_download_succeeds(self):
        import time
        monitor=self.make()
        with patch('snowvision.services.monitor.fetch_frame',AsyncMock(return_value=demo(0))):
            await monitor.attempt()
            original=monitor.state['frame_timestamp']
            monitor.changed_at=time.time()-300
            monitor.state['next_attempt_at']=0
            await monitor.attempt()
        self.assertEqual(monitor.state['frame_timestamp'],original)
        self.assertEqual(monitor.observation()['freshness_reason'],'unchanged_frame')
        self.assertEqual(monitor.observation()['source_status'],'stale')

    async def test_visual_mode_uses_model_for_non_city_camera_without_cv_score(self):
        monitor = self.make()
        monitor.scene_url = 'http://model.test'
        scene = dict(format_valid=True, air_view='good', falling_snow='yes',
                     snow_on_ground='yes', possible_icing='uncertain')
        with patch.dict(os.environ, {'SCENE_MODEL_PROVIDER': 'ollama'}), \
                patch('snowvision.services.monitor.fetch_frame', AsyncMock(return_value=demo(0))), \
                patch('snowvision.services.monitor.analyze_scene', AsyncMock(return_value=scene)):
            await monitor.attempt()
        self.assertEqual(monitor.state['analysis_method'], 'weather_vlm_v2')
        self.assertIsNone(monitor.state['snow_probability'])
        self.assertEqual(monitor.state['status'], 'snowing')
        self.assertEqual(monitor.state['scene_frame_version'], monitor.state['frame_version'])

    async def test_failed_model_retries_same_fresh_frame(self):
        monitor = self.make()
        monitor.scene_url = 'http://model.test'
        scene = dict(format_valid=True, air_view='good', falling_snow='yes')
        model = AsyncMock(side_effect=[RuntimeError('temporary outage'), scene])
        with patch.dict(os.environ, {'SCENE_MODEL_PROVIDER': 'ollama'}), \
                patch('snowvision.services.monitor.fetch_frame', AsyncMock(return_value=demo(0))), \
                patch('snowvision.services.monitor.analyze_scene', model):
            await monitor.attempt()
            self.assertEqual(monitor.state['scene_error'], 'RuntimeError')
            version = monitor.state['frame_version']
            monitor.state['next_attempt_at'] = 0
            await monitor.attempt()
        self.assertEqual(model.await_count, 2)
        self.assertEqual(monitor.state['scene_frame_version'], version)
        self.assertEqual(monitor.state['status'], 'snowing')
        self.assertIsNone(monitor.state['scene_error'])

    async def test_unchanged_fresh_frame_keeps_model_status(self):
        monitor = self.make()
        monitor.scene_url = 'http://model.test'
        scene = dict(format_valid=True, air_view='good', falling_snow='yes')
        model = AsyncMock(return_value=scene)
        with patch.dict(os.environ, {'SCENE_MODEL_PROVIDER': 'ollama'}), \
                patch('snowvision.services.monitor.fetch_frame', AsyncMock(return_value=demo(0))), \
                patch('snowvision.services.monitor.analyze_scene', model):
            await monitor.attempt()
            monitor.state['next_attempt_at'] = 0
            await monitor.attempt()
        self.assertEqual(model.await_count, 1)
        self.assertEqual(monitor.state['status'], 'snowing')

    async def test_expired_frame_cancels_queued_or_slow_model(self):
        import asyncio
        monitor = self.make()
        monitor.camera['stale_after_seconds'] = 0.05
        monitor.scene_url = 'http://model.test'
        async def slow(*args):
            await asyncio.sleep(1)
            return dict(format_valid=True, air_view='good', falling_snow='yes')
        with patch.dict(os.environ, {'SCENE_MODEL_PROVIDER': 'ollama'}), \
                patch('snowvision.services.monitor.fetch_frame', AsyncMock(return_value=demo(0))), \
                patch('snowvision.services.monitor.analyze_scene', slow):
            await monitor.attempt()
        self.assertEqual(monitor.state['scene_error'], 'TimeoutError')
        self.assertIsNone(monitor.state['scene'])
        self.assertEqual(monitor.observation()['status'], 'unknown')
