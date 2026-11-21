"""Fetch the same IFS archive query as the project, extended to 19 January."""
import gzip
import hashlib
import json
import httpx
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
source = json.load(gzip.open(ROOT / 'city_sim/data/storm-weather.json.gz'))['january-2026']
url = source['url'].replace('end_date=2026-01-17', 'end_date=2026-01-19')
for attempt in range(3):
    try:
        response = httpx.get(url, headers={'User-Agent': 'KamchatkaSnowValidation/1.0'}, timeout=60)
        response.raise_for_status()
        data = response.content
        json.loads(data)
        break
    except (httpx.HTTPError, ValueError):
        if attempt == 2:
            raise
rows = json.loads(data)
assert len(rows) == 6 and all(len(r['hourly']['time']) == 192 for r in rows)
folder = HERE / 'sources'
folder.mkdir(parents=True, exist_ok=True)
(folder / 'ifs-2026-01-12--19.json').write_bytes(data)
(folder / 'weather-fetch.json').write_text(json.dumps({'url': url, 'sha256': hashlib.sha256(data).hexdigest(),
    'retrieved_local_date': '2026-11-22', 'model': 'ECMWF IFS', 'license': 'CC BY 4.0'}, indent=2) + '\n')
print('Saved', len(data), 'bytes; SHA256', hashlib.sha256(data).hexdigest())
