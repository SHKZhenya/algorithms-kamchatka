"""Fetch the small public source snapshot used in the reality audit."""
import concurrent.futures
from http.client import HTTPException
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import json

output = Path(__file__).parent / "sources"
archive = "https://archive-api.open-meteo.com/v1/archive"
params = dict(latitude="53.110,53.076,53.034,52.958,52.982,53.045",
              longitude="158.639,158.663,158.644,158.677,158.658,158.617",
              hourly="snowfall,precipitation,snow_depth,wind_speed_10m,wind_direction_10m,wind_gusts_10m,temperature_2m",
              wind_speed_unit="ms", timezone="Asia/Kamchatka")
cases = {"march": ("2025-03-22", "2025-03-25"), "december": ("2025-12-07", "2025-12-15"),
         "january": ("2026-01-12", "2026-01-17"), "control": ("2026-01-09", "2026-01-11")}
urls = {}
for event, (start, end) in cases.items():
    models = ["era5_seamless", "ecmwf_ifs"] if event == "control" else ["era5_seamless"]
    if event == "january":
        models += ["era5", "era5_land"]
    for model in models:
        urls[f"{event}-{model}.json"] = archive + "?" + urlencode(
            dict(params, models=model, start_date=start, end_date=end))
urls.update({f"gsod-{year}.csv": f"https://www.ncei.noaa.gov/data/global-summary-of-the-day/access/{year}/32583099999.csv"
             for year in (2025, 2026)})
urls.update({f"isd-{year}.csv": f"https://www.ncei.noaa.gov/data/global-hourly/access/{year}/32583099999.csv"
             for year in (2025, 2026)})
urls["ugms.pdf"] = "https://kammeteo.ru/kamsneg2012.pdf"
urls["ugms-slope.pdf"] = "https://kammeteo.ru/060326.pdf"
for label, year, month, first, last in [("december", 2025, 12, 7, 15), ("january", 2026, 1, 9, 17),
                                         ("march", 2025, 3, 22, 25)]:
    urls[f"synop-{label}.html"] = "https://www.pogodaiklimat.ru/weather.php?" + urlencode(
        dict(id=32583, bday=first, fday=last, amonth=month, ayear=year))

for event, (start, end) in cases.items():
    urls[f"station-{event}.csv"] = "https://www.ncei.noaa.gov/access/services/data/v1?" + urlencode(
        dict(dataset="global-summary-of-the-day", stations="32583099999", startDate=start, endDate=end, format="csv",
             includeStationName="true", includeStationLocation="true", units="metric"))
urls.update({"december-kam24.html": "https://kam24.ru/news/main/20251209/123506.html",
             "december-aif.html": "https://kamchatka.aif.ru/society/centr-petropavlovska-kamchatskogo-skovali-probki-iz-za-ciklona"})

def fetch(item):
    name, url = item
    last_error = None
    for _ in range(3):
        try:
            with urlopen(Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=18) as response:
                data = response.read()
            if name.endswith(".json"):
                json.loads(data)
            (output / name).write_bytes(data)
            return name, {"url": url, "bytes": len(data), "sha256": sha256(data).hexdigest()}
        except (OSError, HTTPException, ValueError) as error:
            last_error = str(error)
    return name, {"url": url, "error": last_error}

with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
    results = dict(executor.map(fetch, urls.items()))
(output / "fetch.json").write_text(json.dumps(results, indent=2) + "\n")
for name, result in results.items():
    print(name, result.get("bytes", result.get("error")))
