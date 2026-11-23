"""Evaluate archived SnowVision inputs against independently annotated observations."""

import argparse
import asyncio
import hashlib
import json
import os
import sqlite3
import tempfile
from collections import Counter, defaultdict
from contextlib import closing
from pathlib import Path

import cv2
import httpx
import numpy as np

from snowvision.adapters.alerts import AlertJournal
from snowvision.adapters.scene_model import analyze_scene
from snowvision.domain.onset_forecast import evaluate, score_order
from snowvision.domain.pilot_vision import detect, unknown
from snowvision.domain.vision import SNAPSHOT_METHOD, analyze_frames, analyze_snapshot


def rates(rows):
    result = Counter(rows)
    true = result['snow', 'snowing']
    predicted = result['snow', 'snowing'] + result['other', 'snowing']
    actual = sum(n for (label, _), n in result.items() if label == 'snow')
    return {
        'n': len(rows), 'precision': true / predicted if predicted else None,
        'recall': true / actual if actual else None,
        'unknown': sum(n for (_, outcome), n in result.items() if outcome == 'unknown'),
    }


def camera_config(camera, row, masks):
    config = masks.get(camera, {})
    return config if config.get('view_revision') == row['view_revision'] else {}


def seconds_summary(values):
    if not values:
        return None
    p50, p90 = np.quantile(values, [.5, .9]).tolist()
    return {'n': len(values), 'p50': p50, 'p90': p90}


def replay_events(journal):
    with closing(journal.connect()) as db:
        return [json.loads(payload) for (payload,) in db.execute('SELECT payload FROM onset_events')]


def classify_scene(url, frame):
    ok, encoded = cv2.imencode('.jpg', frame)
    if not ok:
        return unknown('encoding_failed'), None
    async def request():
        async with httpx.AsyncClient(trust_env=False) as client:
            return await analyze_scene(client, url, encoded.tobytes())

    try:
        scene = asyncio.run(request())
    except (httpx.HTTPError, TimeoutError, ValueError) as exc:
        return unknown(type(exc).__name__), None
    status = 'unknown'
    if scene['format_valid'] and scene['air_view'] in ('good', 'limited'):
        status = {'yes': 'snowing', 'no': 'clear'}.get(scene['falling_snow'], 'unknown')
    return {'status': status, 'snow_probability': None}, scene


def detection_report(db, samples, labels, masks, journal, shadow_journal, events, scene_url=None):
    by_method = defaultdict(list)
    score_by_method = defaultdict(list)
    previous = {}
    shadow_state = {}
    counts = defaultdict(Counter)
    last_changed = {}
    intervals = defaultdict(list)
    latency = defaultdict(list)
    scene_counts = Counter()
    scene_versions = set()
    for sample_id, available, camera, row in samples:
        method = row['analysis_method']
        counts[camera]['samples'] += 1
        if row['source_status'] == 'online' and not row['is_stale']:
            counts[camera]['fresh_samples'] += 1
        if row['snow_probability'] is not None:
            counts[camera]['scored_samples'] += 1
        timing = row.get('acquisition') or {}
        if timing.get('received_at') is not None and row['last_analysis_at'] is not None:
            delay = row['last_analysis_at'] - timing['received_at']
            if delay >= 0:
                latency[camera].append(delay)
        version = row['frame_version']
        if version and row['frame_timestamp'] is not None and last_changed.get(camera, (None,))[0] != version:
            counts[camera]['frame_changes'] += 1
            if camera in last_changed:
                intervals[camera].append(row['frame_timestamp'] - last_changed[camera][1])
            last_changed[camera] = (version, row['frame_timestamp'])

        cfg = {'id': camera, 'name': camera, 'type': row['source_type'],
               'interval_seconds': row.get('interval_seconds', 30)}
        journal.observe(cfg, row, now=available)
        label = labels.get(str(sample_id))
        frames = []
        for digest in row['media']:
            blob = db.execute('SELECT image FROM media WHERE digest=?', (digest,)).fetchone()
            if blob is None:
                raise ValueError('Archive media object is missing')
            frame = cv2.imdecode(np.frombuffer(blob[0], np.uint8), cv2.IMREAD_COLOR)
            if frame is None:
                raise ValueError('Archive media object is invalid')
            frames.append(frame)
        if frames:
            config = camera_config(camera, row, masks)
            context = previous.get(camera)
            if context and (context[1] != row['view_revision'] or available-context[0] > 3600):
                context = None
            candidate = detect(frames, method, config, context[2] if context else None)
            shadow_state[camera] = (version, candidate)
            previous[camera] = (available, row['view_revision'], cv2.cvtColor(frames[-1], cv2.COLOR_BGR2GRAY))
            baseline = analyze_snapshot(frames[-1]) if method == SNAPSHOT_METHOD else analyze_frames(frames)
        else:
            baseline = candidate = unknown('missing_media')
        if method == 'opencv_motion':
            if not frames and version and row['source_status'] == 'online' and not row['is_stale']:
                cached_version, cached_result = shadow_state.get(camera, (None, None))
                if cached_version == version:
                    candidate = cached_result
            shadow_journal.observe(cfg, row | candidate, now=available)
        else:
            shadow_journal.observe(cfg, row, now=available)
        expected_snow = expected_visual_snow(label)
        if expected_snow is not None:
            expected = 'snow' if expected_snow else 'other'
            runs = [('recorded', row), ('opencv_replay', baseline), ('shadow', candidate)]
            if scene_url:
                result = unknown('missing_media')
                if frames:
                    result, scene = classify_scene(scene_url, frames[-1])
                    if scene:
                        scene_versions.add((scene['model'], scene['prompt_version']))
                scene_counts[result.get('analysis_reason') or result['status']] += 1
                runs.append(('weather_vlm_v2', result))
            for name, result in runs:
                for condition in dict.fromkeys(('all', label.get('condition', 'unspecified'))):
                    key = method, condition, name
                    by_method[key].append((expected, result['status']))
                    score = result['snow_probability']
                    if score is not None:
                        score_by_method[key].append((int(expected == 'snow'), score))
    comparisons = []
    for key, rows in sorted(by_method.items()):
        scored = score_by_method[key]
        comparisons.append(dict(zip(('method', 'condition', 'run'), key), **rates(rows),
                                average_precision=score_order(*zip(*scored)) if scored else None))
    return {
        'camera_availability': {
            camera: dict(summary, frame_change_interval_seconds=seconds_summary(intervals[camera]),
                         receive_to_analysis_seconds=seconds_summary(latency[camera]))
            for camera, summary in sorted(counts.items())
        },
        'labelled_samples': len(labels),
        'comparisons': comparisons,
        **({'weather_vlm': {'outcomes': dict(scene_counts), 'versions': sorted(scene_versions)}} if scene_url else {}),
        'recorded_alert_replay': score_alerts(replay_events(journal), events),
        'shadow_alert_replay': score_alerts(replay_events(shadow_journal), events),
    }


def score_alerts(alerts, annotations):
    if not annotations:
        return {'status': 'unlabelled', 'early_episodes': len(alerts)}
    matched = set()
    delays = []
    false = 0
    unclassified = 0
    late = 0
    # Annotated intervals cover both positive and negative camera time.
    for alert in sorted(alerts, key=lambda event: event['opened_at']):
        camera = alert['camera_id']
        opened = alert['opened_at']
        covering = [(i, row) for i, row in enumerate(annotations) if row['camera'] == camera
                    and row['from'] <= opened < row['to']]
        if not covering:
            unclassified += 1
            continue
        if len(covering) > 1:
            raise ValueError('Overlapping labelled intervals for camera')
        i, row = covering[0]
        onset = row.get('onset')
        if onset is None or opened < onset[0] or i in matched:
            false += 1
            continue
        lower, upper = onset
        matched.add(i)
        if opened < upper or opened - lower > 120 >= opened - upper:
            unclassified += 1
        elif opened - lower <= 120:
            delays.append(opened - lower)
        else:
            late += 1
    annotated_seconds = sum(row['to']-row['from'] for row in annotations)
    positives = sum(row.get('onset') is not None for row in annotations)
    return {'status': 'evaluated', 'starts': positives, 'detected_within_2m': len(delays),
            'early_recall': len(delays) / positives if positives else None, 'late_episodes': late,
            'false_episodes': false, 'unclassified_episodes': unclassified,
            'false_per_camera_day': false * 86400 / annotated_seconds if annotated_seconds else None,
            'conservative_delay_seconds': seconds_summary(delays)}


def expected_visual_snow(label):
    if not label:
        return None
    precipitation = label['precipitation']
    if precipitation == 'snow':
        return True
    if precipitation in ('none', 'rain'):
        return False
    if precipitation == 'mixed' and type(label.get('snow_present')) is bool:
        return label['snow_present']
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('--labels', type=Path)
    parser.add_argument('--masks', type=Path)
    parser.add_argument('--scene-url', help='Send labelled images to an authorized Weather VLM v2 endpoint')
    parser.add_argument('--manifest', action='store_true', help='Export sample IDs and capture metadata')
    parser.add_argument('--export-media', type=Path, help='Write archived PNGs locally for annotation')
    args = parser.parse_args()
    labels = json.loads(args.labels.read_text()) if args.labels else {}
    masks = json.loads(args.masks.read_text()) if args.masks else {}
    with closing(sqlite3.connect(args.archive.resolve().as_uri() + '?mode=ro', uri=True)) as db:
        samples = [(sample, ts, camera, json.loads(payload)) for sample, ts, camera, payload
                   in db.execute('SELECT id, available_at, camera, payload FROM samples ORDER BY available_at, id')]
        if not set(labels.get('frames', {})).issubset(str(sample[0]) for sample in samples):
            parser.error('Frame labels include IDs absent from the archive')
        if args.export_media:
            args.export_media.mkdir(parents=True, exist_ok=True, mode=0o700)
            if args.export_media.stat().st_mode & 0o077:
                parser.error('The media export directory must be private (0700)')
            for digest, media in db.execute('SELECT digest, image FROM media'):
                if hashlib.sha256(media).hexdigest() != digest:
                    raise ValueError('Archive checksum mismatch')
                path = args.export_media / f'{digest}.png'
                try:
                    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                    with os.fdopen(fd, 'wb') as file:
                        file.write(media)
                except FileExistsError:
                    if path.read_bytes() != media:
                        raise ValueError(f'Conflicting file: {path}') from None
        if args.manifest:
            for sample, available, camera, row in samples:
                print(json.dumps({'id': sample, 'available_at': available, 'camera': camera, **row}))
            return
        releases = [(ts, json.loads(hours)) for ts, hours
                    in db.execute('SELECT available_at, hours FROM releases ORDER BY available_at')]
        with tempfile.TemporaryDirectory(prefix='snowvision-replay-') as tmp:
            journal = AlertJournal(Path(tmp) / 'replay.db')
            shadow_journal = AlertJournal(Path(tmp) / 'shadow.db')
            result = detection_report(db, samples, labels.get('frames', {}), masks,
                                      journal, shadow_journal, labels.get('intervals', []), args.scene_url)
    result['forecast_releases'] = len(releases)
    result['onset_forecast'] = evaluate(labels.get('forecast_windows', []), samples, releases)
    print(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False))


if __name__ == '__main__':
    main()
