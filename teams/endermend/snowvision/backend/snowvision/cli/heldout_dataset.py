"""Validate rights, annotations and leakage boundaries for an archived camera test set."""

import argparse
import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from contextlib import closing
from pathlib import Path

ALLOWED_SPLITS = {'tune', 'test'}
AIR = {'good', 'limited', 'unusable'}
PRECIPITATION = {'none', 'rain', 'snow', 'mixed', 'unknown'}
COVERAGE = {'clear', 'partial', 'covered', 'unknown'}
GAP_SECONDS = 5400


def require(condition, message):
    if not condition:
        raise ValueError(message)


def validate(archive, manifest):
    rights = manifest.get('provenance', {})
    for field in ('source', 'rights_document', 'permitted_uses', 'retention_until', 'access'):
        require(rights.get(field), f'provenance.{field} is required')
    require('offline_evaluation' in rights['permitted_uses'], 'Offline evaluation must be permitted')
    require(rights['access'] != 'public' or rights.get('redistribution') is True,
            'Public access requires explicit redistribution rights')
    with closing(sqlite3.connect(archive.resolve().as_uri() + '?mode=ro', uri=True)) as db:
        rows = {sample: (available, camera, json.loads(payload)) for sample, available, camera, payload
                in db.execute('SELECT id, available_at, camera, payload FROM samples')}
        stored = {digest: image for digest, image in db.execute('SELECT digest, image FROM media')}
    samples = manifest.get('samples', [])
    require(samples, 'At least one annotated sample is required')
    ids, digests, episodes, by_camera, counts = set(), {}, {}, defaultdict(list), Counter()
    for entry in samples:
        sample = entry['sample_id']
        require(type(sample) is int and sample in rows and sample not in ids,
                f'Unknown or duplicate sample_id: {sample}')
        ids.add(sample)
        available, camera, row = rows[sample]
        split = entry['split']
        require(split in ALLOWED_SPLITS, f'Invalid split for sample {sample}')
        require(entry['camera_id'] == camera and entry['view_revision'] == row['view_revision']
                and entry['view_revision'] != 'unverified', f'Camera/view mismatch for sample {sample}')
        episode = entry['episode_id']
        require(isinstance(episode, str) and episode.strip(), f'Missing episode for sample {sample}')
        require(episodes.setdefault(episode, split) == split, f'Episode shared across splits: {episode}')
        received = entry['received_at']
        require(isinstance(received, (int, float)) and abs(received - available) < 1,
                f'Receive time mismatch for sample {sample}')
        captured = entry.get('captured_at')
        basis = entry.get('captured_at_basis')
        require(basis in ('source', 'http_last_modified', 'unknown'),
                f'Capture time basis missing for sample {sample}')
        require((captured is None) == (basis == 'unknown'), f'Capture time/basis mismatch for sample {sample}')
        if captured is not None:
            require(isinstance(captured, (int, float)) and captured <= received,
                    f'Invalid capture time for sample {sample}')
        require(entry['precipitation'] in PRECIPITATION and entry['air_view'] in AIR,
                f'Invalid precipitation or air view for sample {sample}')
        require(entry['lane_view'] in AIR and entry['lane_coverage'] in COVERAGE,
                f'Invalid lane annotation for sample {sample}')
        require(entry['lane_coverage'] == 'unknown' or entry['lane_view'] != 'unusable',
                f'Lane coverage requires usable view for sample {sample}')
        polygon = entry.get('lane_polygon')
        if entry['lane_coverage'] != 'unknown':
            require(isinstance(polygon, list) and len(polygon) >= 3 and all(
                isinstance(p, list) and len(p) == 2 and all(type(v) in (int, float) and 0 <= v <= 1 for v in p)
                for p in polygon), f'Lane polygon required for sample {sample}')
        require(isinstance(entry.get('annotators'), list) and len(set(entry['annotators'])) >= 2,
                f'Two independent annotators required for sample {sample}')
        require(entry.get('adjudication') in ('agreed', 'resolved'),
                f'Annotation adjudication missing for sample {sample}')
        media = row.get('media') or []
        digest = entry['frame_sha256']
        require(digest in media and digest in stored and hashlib.sha256(stored[digest]).hexdigest() == digest,
                f'Archived frame/checksum mismatch for sample {sample}')
        require(digest not in digests or digests[digest] == split,
                f'Identical frame shared across splits: {digest}')
        digests[digest] = split
        by_camera[camera].append((received, split, episode))
        counts[split] += 1
    require(counts['test'] > 0 and counts['tune'] > 0, 'Both tune and test need annotated samples')
    for camera, observations in by_camera.items():
        observations.sort()
        for earlier, later in zip(observations, observations[1:]):
            require(earlier[1] == later[1] or later[0] - earlier[0] > GAP_SECONDS,
                    f'Adjacent camera frames cross splits within 90 minutes: {camera}')
    return {'status': 'validated', 'archive_sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
            'samples': dict(counts), 'cameras': len(by_camera), 'episodes': len(episodes),
            'media_objects': len(digests), 'gap_seconds': GAP_SECONDS}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('manifest', type=Path)
    args = parser.parse_args()
    print(json.dumps(validate(args.archive, json.loads(args.manifest.read_text())), indent=2))


if __name__ == '__main__':
    main()
