"""Recover short API responses and capture raw station telegrams."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from hashlib import sha256
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import urlopen
import json

root = Path(__file__).parent / "sources"
prior = json.loads((root / "fetch.json").read_text())
urls = {}
for name, data in prior.items():
    if name.startswith("control-") and name.endswith(".json") or name == "january-era5.json":
        original = urlsplit(data["url"])
        coords = parse_qs(original.query)
        for i, (lat, lon) in enumerate(zip(coords["latitude"][0].split(","), coords["longitude"][0].split(","))):
            args = {k: v[0] for k, v in coords.items()}
            args.update(latitude=lat, longitude=lon)
            urls[name.removesuffix(".json")+f"-{i}.json"] = original._replace(query=urlencode(args)).geturl()
for label, first, last in [("december", "2025-12-07", "2025-12-15"), ("january", "2026-01-09", "2026-01-17"),
                           ("march", "2025-03-22", "2025-03-25")]:
    start = datetime.fromisoformat(first) - timedelta(hours=12)
    end = datetime.fromisoformat(last) + timedelta(hours=12)
    params = urlencode(dict(block="32583", begin=start.strftime("%Y%m%d%H%M"), end=end.strftime("%Y%m%d%H%M"),
                            header="yes", lang="eng"))
    urls[f"ogimet-{label}.csv"] = "https://www.ogimet.com/cgi-bin/getsynop?" + params

def get(entry):
    name, url = entry
    error = None
    for _ in range(2):
        try:
            with urlopen(url, timeout=15) as response:
                data = response.read()
            if name.endswith(".json"):
                assert "hourly" in json.loads(data)
            elif b"AAXX" not in data:
                raise ValueError(data[:140].decode(errors="replace"))
            (root / name).write_bytes(data)
            return name, {"url": url, "bytes": len(data), "sha256": sha256(data).hexdigest()}
        except Exception as exc:
            error = str(exc)
    return name, {"url": url, "error": error}

with ThreadPoolExecutor(max_workers=3) as executor:
    downloaded = dict(executor.map(get, urls.items()))
(root / "recovery.json").write_text(json.dumps(downloaded, indent=2)+"\n")
for name, info in downloaded.items():
    print(name, info.get("bytes", info.get("error")))
