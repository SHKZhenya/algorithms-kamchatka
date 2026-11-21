"""Gather dated station-source snapshots and archive status for the follow-up."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from gzip import decompress
from hashlib import sha256
from http.client import IncompleteRead
import json
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

root = Path(__file__).parent/"sources"
urls = {
    "infoclimat-index.html": "https://www.infoclimat.fr/observations-meteo/temps-reel/petropavlovsk-kamchatskij/32583.html",
    "infoclimat-dec2025.html": "https://www.infoclimat.fr/climatologie-mensuelle/32583/decembre/2025/petropavlovsk-kamchatskij.html",
    "infoclimat-jan2026.html": "https://www.infoclimat.fr/climatologie-mensuelle/32583/janvier/2026/petropavlovsk-kamchatskij.html",
    "tutiempo-dec2025.html": "https://en.tutiempo.net/climate/12-2025/ws-325830.html",
    "tutiempo-jan2026.html": "https://en.tutiempo.net/climate/01-2026/ws-325830.html",
    "dec2025-ghcnh.csv": "https://www.ncei.noaa.gov/access/services/data/v1?"+urlencode(dict(
        dataset="global-hourly", stations="32583099999", startDate="2025-12-07", endDate="2025-12-15", format="csv")),
    "jan2026-ghcnh.csv": "https://www.ncei.noaa.gov/access/services/data/v1?"+urlencode(dict(
        dataset="global-hourly", stations="32583099999", startDate="2026-01-12", endDate="2026-01-17", format="csv")),
    "january-press.html": "https://city-pages.info/news/novosti-kamchatki/v-petropavlovske-s-nachala-goda-vypalo-poltory-normy-snega/",
    "wmo-32583.json": "https://oscar.wmo.int/surface/rest/api/search/station?stationIdentifier=0-20000-0-32583",
    "meteo-fund.html": "https://meteo.ru/data",
}
for label, month, start, stop in (("dec", "decembre", date(2025,12,7), date(2025,12,15)),
                                   ("jan", "janvier", date(2026,1,12), date(2026,1,17))):
    for d in (start+timedelta(i) for i in range((stop-start).days+1)):
        urls[f"ic-{d}.html"] = (
            f"https://www.infoclimat.fr/observations-meteo/temps-reel/petropavlovsk-kamchatskij/"
            f"{d.day}/{month}/{d.year}/32583.html")
    for host in ("www.ogimet.com", "ogimet.com"):
        params = urlencode(dict(block="32583", begin=start.strftime("%Y%m%d")+"0000",
                                end=stop.strftime("%Y%m%d")+"2359", header="yes"))
        urls[f"{label}-{host}.txt"] = f"https://{host}/cgi-bin/getsynop?"+params
    params = urlencode(dict(lang="en", ind="32583", ndays=(stop-start).days+1, ano=stop.year,
                            mes=f"{stop.month:02}", day=f"{stop.day:02}", hora=23, ord="DIR", Send="Send"))
    urls[f"{label}-ogimet-summary.html"] = "https://www.ogimet.com/cgi-bin/gsynres?"+params


def fetch(item):
    name, url = item
    error = None
    for _ in range(2):
        try:
            with urlopen(Request(url,headers={"User-Agent":"Mozilla/5.0","Accept-Encoding":"identity"}),timeout=12) as response:
                data = response.read()
                final = response.url
            (root/name).write_bytes(data)
            return name, {"url":url,"final_url":final,"bytes":len(data),"sha256":sha256(data).hexdigest()}
        except (OSError, IncompleteRead) as exc:
            error = str(exc)
    return name, {"url":url,"error":error}


with ThreadPoolExecutor(max_workers=5) as pool:
    output = dict(pool.map(fetch,urls.items()))
(root/"fetch.json").write_text(json.dumps(output,indent=2)+"\n")
for name, item in output.items():
    print(name,item.get("bytes",item.get("error")),flush=True)
