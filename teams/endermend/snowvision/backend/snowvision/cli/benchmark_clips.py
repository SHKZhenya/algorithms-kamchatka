"""Compare OpenCV detectors on independently labelled local video excerpts."""

import argparse
import hashlib
import json
import math
from pathlib import Path
from collections import defaultdict

import cv2
import numpy as np

from snowvision.adapters.local_weather import (
    BACKBONE_SHA256, LocalWeather, classify, fit_centroids, normalized, sample_images, transient_images,
)
from snowvision.cli.evaluate_quality import rates
from snowvision.domain.pilot_vision import detect
from snowvision.domain.vision import analyze_frames


def read_frames(path, start, *, native=False, lag_ms=500):
    video = cv2.VideoCapture(str(path))
    try:
        fps = video.get(cv2.CAP_PROP_FPS)
        if not video.isOpened() or not math.isfinite(fps) or fps < 2:
            raise ValueError(f'Need readable video at 2 FPS or higher: {path}')
        first = round(start * fps)
        video.set(cv2.CAP_PROP_POS_FRAMES, first)
        frames = []
        index = first
        end = round(3.5 * fps)
        if native:
            selected = set(range(end + 1))
        else:
            selected = {
                round(i * lag_ms * fps / 1000)
                for i in range(3500 // lag_ms + 1)
            }
            if len(selected) < 8:
                raise ValueError(f'Fewer than eight distinct frames for {lag_ms} ms: {path}')
        last = first + max(selected)
        while index <= last:
            ok, frame = video.read()
            if not ok:
                raise ValueError(f'Incomplete 3.5-second excerpt: {path} at {start}s')
            if index - first in selected:
                frames.append(cv2.resize(frame, (640, 360)))
            index += 1
        return frames
    finally:
        video.release()


def read_manifest(path):
    raw = path.read_bytes()
    entries = json.loads(raw)
    if not entries:
        raise ValueError('Manifest has no labelled excerpts')
    seen = set()
    file_hashes = {}
    for row in entries:
        if type(row['snow']) is not bool or not 0 <= row['start_seconds'] < float('inf'):
            raise ValueError('Each excerpt needs a boolean snow label and nonnegative start_seconds')
        source = (path.parent / row['path']).resolve()
        if source not in file_hashes:
            with source.open('rb') as stream:
                file_hashes[source] = hashlib.file_digest(stream, 'sha256').hexdigest()
        key = file_hashes[source], row['start_seconds']
        if key in seen:
            raise ValueError('Duplicate excerpt')
        seen.add(key)
    return entries, hashlib.sha256(raw).hexdigest(), file_hashes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', type=Path)
    parser.add_argument('--compare-native', action='store_true',
                        help='Also evaluate the same window at native frame rate')
    parser.add_argument('--lag-ms', action='append', type=int, default=[],
                        help='Compare a fixed inter-frame interval in ms, repeatable (1–500)')
    parser.add_argument('--image-weights', type=Path, help='Local MobileNetV3 Small weights')
    parser.add_argument('--train-images', type=Path, help='Separate manifest for image classifier training')
    args = parser.parse_args()
    lags = sorted(set(args.lag_ms) - {500})
    if any(not 1 <= lag <= 500 for lag in lags):
        parser.error('--lag-ms must be within 1–500')
    if bool(args.image_weights) != bool(args.train_images):
        parser.error('--image-weights and --train-images must be supplied together')
    items, manifest_hash, file_hashes = read_manifest(args.manifest)
    model = None
    weather_centroids = None
    if args.image_weights:
        training, training_hash, training_files = read_manifest(args.train_images)
        if set(file_hashes.values()) & set(training_files.values()):
            parser.error('Training and evaluation share an identical video file')
        model = LocalWeather(args.image_weights)
        examples = []
        transient_examples = []
        weather_examples = defaultdict(list)
        for row in training:
            frames = read_frames(args.train_images.parent / row['path'], row['start_seconds'])
            vector = model.embedding(sample_images(frames))
            examples.append((row['snow'], vector))
            transient_examples.append((row['snow'], model.embedding(transient_images(frames))))
            label = row.get('precipitation')
            if label is not None:
                if (label == 'snow') != row['snow']:
                    raise ValueError('Precipitation label contradicts snow flag')
                weather_examples[label].append(vector)
        centroids = fit_centroids(examples)
        transient_centroids = fit_centroids(transient_examples)
        if 'snow' in weather_examples and len(weather_examples) > 2:
            weather_centroids = {label: normalized(np.mean(vectors, axis=0))
                                 for label, vectors in sorted(weather_examples.items())}

    rows = []
    for item in items:
        path = (args.manifest.parent / item['path']).resolve()
        frames = read_frames(path, item['start_seconds'])
        result = {
            **item,
            'baseline': analyze_frames(frames)['status'],
            'shadow': detect(frames, 'opencv_motion', item.get('mask', {}))['status'],
        }
        if args.compare_native:
            native = read_frames(path, item['start_seconds'], native=True)
            result.update(native_frames=len(native),
                          baseline_native=analyze_frames(native)['status'],
                          shadow_native=detect(native, 'opencv_motion', item.get('mask', {}))['status'])
        for lag in lags:
            sampled = read_frames(path, item['start_seconds'], lag_ms=lag)
            for name, analysis in (
                ('baseline', analyze_frames(sampled)),
                ('shadow', detect(sampled, 'opencv_motion', item.get('mask', {}))),
            ):
                result[f'{name}_{lag}ms'] = analysis['status']
                result[f'{name}_{lag}ms_score'] = analysis['snow_probability']
        if model:
            image_result = classify(model, centroids, sample_images(frames), weather_centroids=weather_centroids)
            transient_result = classify(model, transient_centroids, transient_images(frames))
            result.update(
                image=image_result['status'],
                image_margin=image_result['similarity_margin'],
                image_elapsed_seconds=image_result['elapsed_seconds'],
                transient_image=transient_result['status'],
                transient_image_margin=transient_result['similarity_margin'],
                image_cv_agreement=(
                    result['shadow'] if result['shadow'] == image_result['status'] else 'unknown'
                ),
            )
            if weather_centroids:
                result['image_weather_class'] = image_result['weather_class']
                result['image_weather'] = 'snowing' if image_result['weather_class'] == 'snow' else 'clear'
        rows.append(result)
    methods = ('baseline', 'shadow')
    if args.compare_native:
        methods += ('baseline_native', 'shadow_native')
    methods += tuple(f'{name}_{lag}ms' for lag in lags for name in ('baseline', 'shadow'))
    if model:
        methods += ('image', 'image_cv_agreement', 'transient_image')
    if weather_centroids:
        methods += ('image_weather',)
    print(json.dumps({
        'manifest_sha256': manifest_hash, 'excerpts': rows,
        'summary': {
            name: rates([('snow' if row['snow'] else 'other', row[name]) for row in rows])
            for name in methods
        },
        **({'image_model': {
            'backbone': 'MobileNetV3 Small IMAGENET1K_V1',
            'weights_sha256': BACKBONE_SHA256,
            'training_manifest_sha256': training_hash,
            'training_excerpts': len(training),
        }} if model else {}),
    }, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
