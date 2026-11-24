"""Collect an isolated pilot from explicitly selected cameras."""

import argparse
import asyncio
import hashlib
import json
import logging
import tempfile
from contextlib import closing
from pathlib import Path

import httpx

from snowvision.adapters.observation_store import initialize
from snowvision.adapters.quality_archive import QualityArchive
from snowvision.domain.vision import analyze_frames
from snowvision.services.monitor import CameraMonitor
from snowvision.services.runtime import demo

CAMERAS = Path(__file__).resolve().parents[1] / 'resources' / 'cameras.json'
PIPELINE_FILES = ('domain/vision.py', 'adapters/alerts.py', 'services/monitor.py', 'adapters/sources.py')


async def collect(args):
    catalogue = {camera['id']: camera for camera in json.loads(args.catalogue.read_text())}
    ids = list(dict.fromkeys(args.camera))
    if any(camera_id not in catalogue or catalogue[camera_id]['type'] not in ('jpeg', 'hls')
           for camera_id in ids):
        raise ValueError('Select existing real JPEG or HLS camera IDs')
    pipeline = {
        name: hashlib.sha256((CAMERAS.parents[1] / name).read_bytes()).hexdigest()
        for name in PIPELINE_FILES
    }
    archive = QualityArchive(args.archive, media_limit_bytes=args.media_mib * 1024 ** 2,
                             pipeline=pipeline)
    async with httpx.AsyncClient(timeout=12, follow_redirects=True) as client:
        with tempfile.TemporaryDirectory(prefix='snowvision-pilot-') as tmp:
            database = Path(tmp) / 'history.db'
            initialize(database)
            acquisition = asyncio.Semaphore(4)
            processing = asyncio.Semaphore(2)
            source_locks = {}
            monitors = [
                CameraMonitor(camera, client, acquisition, processing, analyze_frames, demo,
                              database=database, quality_archive=archive,
                              source_lock=source_locks.setdefault(camera['url'], asyncio.Lock()))
                for camera in (catalogue[camera_id] for camera_id in ids)
            ]
            loop = asyncio.get_running_loop()
            deadline = loop.time() + args.seconds
            forecast_checked_at = -float('inf')
            while True:
                await asyncio.gather(*(m.attempt() for m in monitors))
                if args.outlook and loop.time() - forecast_checked_at >= 60:
                    forecast_checked_at = loop.time()
                    try:
                        response = await client.get(args.outlook)
                        response.raise_for_status()
                        forecast = response.json()['forecast']
                        if forecast['status'] in ('available', 'stale') and forecast['hours']:
                            await asyncio.to_thread(
                                archive.record_release, forecast['fetched_at'], forecast['hours'],
                            )
                    except (httpx.HTTPError, KeyError, ValueError, TypeError):
                        logging.warning('Forecast release unavailable')
                if loop.time() >= deadline:
                    break
                await asyncio.sleep(min(1, deadline - loop.time()))
    with closing(archive.connect()) as db:
        print(json.dumps({
            'samples': db.execute('SELECT COUNT(*) FROM samples').fetchone()[0],
            'forecast_releases': db.execute('SELECT COUNT(*) FROM releases').fetchone()[0],
            'media_objects': db.execute('SELECT COUNT(*) FROM media').fetchone()[0],
        }))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('--catalogue', type=Path, default=CAMERAS)
    parser.add_argument('--camera', action='append', required=True)
    parser.add_argument('--seconds', type=int, default=0, help='0 collects one round')
    parser.add_argument('--outlook', help='Existing SnowVision /api/city/outlook URL')
    parser.add_argument('--media-mib', type=int, default=0,
                        help='Store lossless model inputs up to this budget on authorized cameras')
    args = parser.parse_args()
    if args.seconds < 0 or args.media_mib < 0:
        parser.error('duration and media budget must be nonnegative')
    asyncio.run(collect(args))


if __name__ == '__main__':
    main()
