import json
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from contextlib import closing
from pathlib import Path

import numpy as np
import cv2

from snowvision.adapters.quality_archive import QualityArchive
from snowvision.adapters.local_weather import fit_centroids, transient_images
from snowvision.cli.evaluate_quality import classify_scene, score_alerts
from snowvision.cli.heldout_dataset import validate as validate_heldout
from snowvision.domain.onset_forecast import evaluate, onset_targets, weather_features
from snowvision.domain.pilot_vision import detect
from snowvision.services.runtime import demo


class QualityArchiveTests(unittest.TestCase):
    def test_heldout_requires_rights_media_and_separated_episodes(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = QualityArchive(Path(directory) / 'pilot.db', media_limit_bytes=1_000_000)
            camera = {'id': 'pilot', 'type': 'jpeg', 'view_revision': 'fixed-1'}
            for timestamp, version in ((100, 'a'), (6000, 'b')):
                archive.record_sample(camera, {'timestamp': timestamp, 'frame_version': version}, [demo(timestamp)])
            with closing(archive.connect()) as db:
                stored = [json.loads(row[0])['media'][0] for row in db.execute('SELECT payload FROM samples')]
            entries = [dict(sample_id=i + 1, camera_id='pilot', episode_id=f'event-{i}',
                            split=split, view_revision='fixed-1', received_at=timestamp,
                            captured_at=None, captured_at_basis='unknown', frame_sha256=stored[i],
                            precipitation='snow', air_view='good', lane_view='good',
                            lane_coverage='partial', lane_polygon=[[0, 0], [1, 0], [0, 1]],
                            annotators=['one', 'two'], adjudication='agreed')
                       for i, (timestamp, split) in enumerate(((100, 'tune'), (6000, 'test')))]
            manifest = {'provenance': dict(source='operator archive', rights_document='grant-1',
                                          permitted_uses=['offline_evaluation'], retention_until='2027-01-01',
                                          access='private'), 'samples': entries}
            self.assertEqual(validate_heldout(archive.path, manifest)['samples']['test'], 1)
            manifest_path = Path(directory) / 'heldout.json'
            manifest_path.write_text(json.dumps(manifest))
            report = json.loads(subprocess.check_output([
                sys.executable, '-m', 'snowvision.cli.evaluate_quality', str(archive.path),
                '--heldout', str(manifest_path),
            ]))
            self.assertEqual(report['labelled_samples'], 1)
            self.assertEqual(report['heldout']['samples'], {'tune': 1, 'test': 1})
            with patch('snowvision.cli.heldout_dataset.GAP_SECONDS', 6000):
                with self.assertRaisesRegex(ValueError, 'Adjacent camera frames'):
                    validate_heldout(archive.path, manifest)
            entries[1]['received_at'] = 100 + 5400
            with self.assertRaisesRegex(ValueError, 'Receive time mismatch'):
                validate_heldout(archive.path, manifest)
            entries[1]['received_at'] = 6000
            entries[1]['episode_id'] = 'event-0'
            with self.assertRaisesRegex(ValueError, 'Episode shared'):
                validate_heldout(archive.path, manifest)
            entries[1]['episode_id'] = 'event-1'
            manifest['provenance']['permitted_uses'] = []
            with self.assertRaisesRegex(ValueError, 'required|permitted'):
                validate_heldout(archive.path, manifest)

    def test_clip_benchmark_decodes_video_and_reports_both_detectors(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            video = cv2.VideoWriter(str(base / 'clip.avi'), cv2.VideoWriter_fourcc(*'MJPG'),
                                    4, (640, 360))
            self.assertTrue(video.isOpened())
            try:
                for i in range(24):
                    video.write(demo(i))
            finally:
                video.release()
            manifest = base / 'labels.json'
            manifest.write_text(json.dumps([{'path': 'clip.avi', 'start_seconds': 1, 'snow': True}]))
            result = json.loads(subprocess.check_output([
                sys.executable, '-m', 'snowvision.cli.benchmark_clips', str(manifest), '--compare-native',
                '--lag-ms', '250',
            ]))
            self.assertEqual(result['summary']['baseline']['n'], 1)
            self.assertEqual(result['summary']['shadow']['n'], 1)
            self.assertEqual(result['summary']['shadow_native']['n'], 1)
            self.assertEqual(result['excerpts'][0]['native_frames'], 15)
            self.assertEqual(result['summary']['shadow_250ms']['n'], 1)

    def test_image_classifier_requires_both_classes_and_keeps_class_order(self):
        snow, other = np.array([1., 0.]), np.array([0., 1.])
        with self.assertRaises(ValueError):
            fit_centroids([(True, snow)])
        np.testing.assert_array_equal(fit_centroids([(True, snow), (False, other)]),
                                      [other, snow])

    def test_transient_images_keep_small_flashes_and_exclude_static_or_broad_motion(self):
        frame = np.zeros((64, 64, 3), dtype=np.uint8)
        frame[:, :8] = 180
        frames = [frame.copy() for _ in range(8)]
        frames[2][10:12, 20:22] = 100
        frames[2][30:50, 30:50] = 100
        first = transient_images(frames)[0]
        self.assertEqual(int(first[11, 21, 0]), 100)
        self.assertFalse(first[:, :8].any())
        self.assertFalse(first[32:48, 32:48].any())

    def test_scene_model_abstains_when_only_ground_is_visible(self):
        evidence = dict(format_valid=True, air_view='unusable', falling_snow='yes',
                        snow_on_ground='yes')
        with patch('snowvision.cli.evaluate_quality.analyze_scene', return_value=evidence):
            result, scene = classify_scene('http://localhost:8000', demo(1))
        self.assertEqual(result['status'], 'unknown')
        self.assertIsNone(result['snow_probability'])
        self.assertEqual(scene['snow_on_ground'], 'yes')

    def test_refuses_media_in_shared_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'shared'
            path.mkdir()
            path.chmod(0o755)
            with self.assertRaises(PermissionError):
                QualityArchive(path / 'pilot.db', media_limit_bytes=10)
            self.assertFalse((path / 'pilot.db').exists())

    def test_report_reads_archived_media_and_matches_independent_labels(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            archive = QualityArchive(base / 'pilot.db', media_limit_bytes=100_000)
            archive.record_sample(
                {'id': 'pilot', 'type': 'jpeg'},
                {'timestamp': 500, 'frame_timestamp': 499, 'frame_version': 'v',
                 'source_status': 'online', 'analysis_method': 'opencv_snapshot',
                 'status': 'snowing', 'snow_probability': .9, 'confidence': .8},
                [demo(1)],
            )
            labels = base / 'labels.json'
            labels.write_text(json.dumps({'frames': {'1': {'precipitation': 'snow'}}}))
            command = [
                sys.executable, '-m', 'snowvision.cli.evaluate_quality', str(archive.path),
                '--labels', str(labels), '--export-media', str(base / 'frames'),
            ]
            reply = json.loads(subprocess.check_output(command))
            self.assertEqual(reply['labelled_samples'], 1)
            self.assertEqual(len(list((base / 'frames').glob('*.png'))), 1)
            reports = {row['run']: row for row in reply['comparisons'] if row['condition'] == 'all'}
            self.assertEqual(reports['recorded']['recall'], 1)
            self.assertEqual(reports['shadow']['n'], 1)
            self.assertEqual(reply['recorded_alert_replay']['early_episodes'], 0)

    def test_archive_is_explicit_bounded_deduplicated_and_has_no_provider_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'archive.db'
            camera = {'id': 'pilot', 'type': 'jpeg', 'url': 'https://example.invalid/secret'}
            frame = demo(1)
            sample = {'timestamp': 500, 'frame_version': 'example'}
            QualityArchive(path).record_sample(camera, sample, [frame])
            QualityArchive(path, media_limit_bytes=1).record_sample(camera, sample, [frame])
            archive = QualityArchive(path, media_limit_bytes=100_000)
            for _ in range(2):
                archive.record_sample(camera, sample, [frame])
                archive.record_release(500, [{'timestamp': 600}])
            with closing(archive.connect()) as db:
                rows = [json.loads(row[0]) for row in db.execute('SELECT payload FROM samples ORDER BY id')]
                self.assertEqual(db.execute('SELECT count(*) FROM media').fetchone()[0], 1)
                self.assertEqual(db.execute('SELECT count(*) FROM releases').fetchone()[0], 1)
            self.assertNotIn('secret', json.dumps(rows))
            self.assertFalse(rows[0]['media'])
            self.assertEqual(rows[1]['media_reason'], 'media_budget')
            self.assertEqual(rows[2]['media'], rows[3]['media'])

    def test_shadow_mask_rejects_overexposed_scene(self):
        frame = np.full((360, 640, 3), 255, dtype=np.uint8)
        result = detect([frame], 'opencv_snapshot', {})
        self.assertEqual(result['analysis_reason'], 'air_overexposed')
        self.assertIsNone(result['snow_probability'])

    def test_replay_does_not_count_late_match_as_false_alert(self):
        episodes = [
            {'camera': 'a', 'from': 0, 'to': 2000, 'onset': [1000, 1010]},
            {'camera': 'a', 'from': 2000, 'to': 3000},
        ]
        alerts = [
            {'camera_id': 'a', 'opened_at': 1131},
            {'camera_id': 'a', 'opened_at': 2100},
            {'camera_id': 'a', 'opened_at': 3150},
        ]
        report = score_alerts(alerts, episodes)
        self.assertEqual(report['late_episodes'], 1)
        self.assertEqual(report['false_episodes'], 1)
        self.assertEqual(report['unclassified_episodes'], 1)
        self.assertEqual(report['early_recall'], 0)


class OnsetForecastTests(unittest.TestCase):
    def test_uncertain_onset_never_becomes_a_negative_label(self):
        window = {'issued_at': 0, 'observed_until': 2500, 'onset': [800, 1000]}
        self.assertEqual(onset_targets(window), [None, 1, 1, 1])
        self.assertEqual(onset_targets({'issued_at': 0, 'observed_until': 1500}), [0, None, None, None])

    def test_future_and_expired_weather_cannot_enter_features(self):
        releases = [
            (4000, [{'timestamp': 4800, 'precipitation': 'rain'}]),
            (5010, [{'timestamp': 4800, 'precipitation': 'snow', 'precipitation_probability': 98}]),
        ]
        self.assertEqual(weather_features(releases, 5000, 5900, True)[:3], [0, 0, 1])
        self.assertEqual(weather_features(releases, 5210, 6110, False)[-1], 0)
        self.assertEqual(weather_features(releases, 7000, 7900, True)[-1], 0)

    def test_backtest_with_distinct_splits_reports_all_requested_horizons(self):
        windows = []
        start = 1_700_000_000
        for index, split in enumerate(('train', 'tune', 'calibration', 'test')):
            for i in range(12):
                issued = start + index * 7 * 86400 + i * 86400 / 2
                onset = {'onset': [issued + 60, issued + 120]} if i % 2 else {}
                windows.append(dict(split=split, episode=f'{split}-{i}', issued_at=issued,
                                    zone='pilot', clear_at_issue=True, observed_until=issued + 3600,
                                    **onset))
        result = evaluate(windows, [], [])
        self.assertEqual(result['status'], 'offline_evaluation')
        for row in result['horizons_minutes'].values():
            self.assertEqual(row['n'], 12)
            self.assertEqual(len(row['brier_skill_ci95']), 2)
        windows[-1]['episode'] = windows[0]['episode']
        with self.assertRaisesRegex(ValueError, 'shared'):
            evaluate(windows, [], [])
