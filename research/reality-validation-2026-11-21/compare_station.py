"""Compare model hours with measured fields from nearby OГМС 32540."""
from collections import defaultdict
import csv
from datetime import datetime, timedelta
import gzip
import json
import math
from pathlib import Path

from weather_audit import read, difference, LONS, LATS

ROOT = Path(__file__).parent
OUT = ROOT / "results"


def distance(lat, lon, a, b):
    return math.hypot(111.2*(lat-a), 111.2*math.cos(math.radians(lat))*(lon-b))


site = (53.0859, 158.5529)
request_index = min(range(6), key=lambda i: distance(*site, LATS[i], LONS[i]))
results = []
pairs = []
for event, year in (("march", 2025), ("december", 2025), ("january", 2026), ("control", 2026)):
    path = ROOT / "sources" / f"meteostat-hourly-{year}-32540.csv.gz"
    if not path.exists():
        continue
    with gzip.open(path, "rt") as stream:
        station = {
            (datetime(*(int(row[k]) for k in ("year", "month", "day", "hour"))) + timedelta(hours=12))
            .isoformat(timespec="minutes"): row for row in csv.DictReader(stream)
        }
    models = {name: read(event, name) for name in ("ecmwf_ifs", "era5_seamless")}
    common = set.intersection(*(set(rows) for rows in models.values()))
    i = min(common, key=lambda j: distance(*site, LATS[j], LONS[j]))
    assert request_index not in common or request_index == i
    comparison_speed = [rows[i]["hourly"]["wind_speed_10m"] for rows in models.values()]
    for model, response in models.items():
        model_row = response[i]
        hourly = model_row["hourly"]
        errors = defaultdict(list)
        for k, stamp in enumerate(hourly["time"]):
            row = station.get(stamp, {})
            for key, field, factor in (("temp", "temperature_2m", 1), ("wspd", "wind_speed_10m", 1/3.6),
                                       ("wdir", "wind_direction_10m", 1), ("wpgt", "wind_gusts_10m", 1/3.6)):
                if row.get(key+"_source") not in {"ghcnh", "metar", "synop"} or hourly[field][k] is None:
                    continue
                if key == "wdir" and (row.get("wspd_source") not in {"ghcnh", "metar", "synop"}
                                      or not row.get("wspd") or float(row["wspd"])/3.6 < 2
                                      or any(speed[k] < 2 for speed in comparison_speed)):
                    continue
                obs, value = float(row[key])*factor, hourly[field][k]
                err = difference(obs, value, field)
                errors[field].append(err)
                pairs.append([event, model, stamp+"+12:00", field, row[key+"_source"], obs, value, err])
        for field, values in errors.items():
            results.append({"event": event, "model": model, "field": field, "paired_hours": len(values),
                            "station": "32540", "distance_km": round(distance(*site, model_row["latitude"],
                                                                             model_row["longitude"]), 1),
                            "mean_abs_error": round(sum(values)/len(values), 3)})
with (OUT/"station-32540-pairs.csv").open("w", newline="") as stream:
    writer = csv.writer(stream)
    writer.writerow(("event", "model", "time", "variable", "instrumental_source", "observed", "modeled", "abs_error"))
    writer.writerows(pairs)
(OUT/"station-32540-summary.json").write_text(json.dumps(results, indent=2)+"\n")
