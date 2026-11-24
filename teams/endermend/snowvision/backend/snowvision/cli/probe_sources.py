"""Verify configured public sources by decoding an actual image."""
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
import httpx
from snowvision.adapters.sources import fetch_frame

async def main():
    default_path = Path(__file__).resolve().parents[1] / 'resources' / 'candidates.json'
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else default_path
    candidates = json.loads(path.read_text())
    semaphore = asyncio.Semaphore(4)
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        async def probe(camera):
            async with semaphore:
                result = dict(camera, checked_at=datetime.now(timezone.utc).isoformat())
                config = dict(camera, type=camera['kind'])
                try:
                    frame = await fetch_frame(config, client)
                    result.update(valid=True, width=frame.shape[1], height=frame.shape[0], source_timestamp=config.get('source_timestamp'))
                except Exception as exc:
                    result.update(valid=False, error=f'{type(exc).__name__}: {exc}')
                return result
        results = await asyncio.gather(*(probe(c) for c in candidates if c['kind'] in ('jpeg', 'hls')))
    print(json.dumps(results, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    asyncio.run(main())
