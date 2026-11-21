"""Compare archived model inputs on identical hours and request locations."""
import csv
from collections import Counter
from datetime import datetime, timedelta
from hashlib import sha256
import itertools
import json
import math
from pathlib import Path

ROOT = Path(__file__).parent
SOURCES = ROOT / "sources"
OUT = ROOT / "results"
VARIABLES = ("snow_depth", "snowfall", "precipitation", "temperature_2m",
             "wind_speed_10m", "wind_direction_10m", "wind_gusts_10m")
LATS = [53.110, 53.076, 53.034, 52.958, 52.982, 53.045]
LONS = [158.639, 158.663, 158.644, 158.677, 158.658, 158.617]
STATION = (52.9897, 158.6552)


def read(event, model):
    path = SOURCES / f"{event}-{model}.json"
    if path.exists():
        return dict(enumerate(json.loads(path.read_text())))
    return {i: json.loads(p.read_text()) for i in range(6)
            if (p := SOURCES / f"{event}-{model}-{i}.json").exists()}


def difference(a, b, field):
    return abs((a-b+180) % 360-180) if field == "wind_direction_10m" else abs(a-b)


def grid_distance(row):
    latitude, longitude = STATION
    return math.hypot(111.2*(row["latitude"]-latitude),
                      111.2*math.cos(math.radians(latitude))*(row["longitude"]-longitude))


def stats():
    inventory, comparisons, hourly, controls = [], [], [], []
    for event, hours in (("march", 96), ("december", 216), ("january", 144), ("control", 72)):
        by_model = {model: read(event, model)
                    for model in ("ecmwf_ifs", "era5_seamless", "era5", "era5_land")}
        usable = {name: rows for name, rows in by_model.items() if rows}
        for model, rows in usable.items():
            counts, coords = Counter(), set()
            for i, row in rows.items():
                series = row["hourly"]
                assert len(series["time"]) == hours
                assert all((datetime.fromisoformat(b)-datetime.fromisoformat(a)).total_seconds() == 3600
                           for a, b in itertools.pairwise(series["time"]))
                assert row["utc_offset_seconds"] == 43200
                cells = (row["latitude"], row["longitude"])
                coords.add(cells)
                for k, stamp in enumerate(series["time"]):
                    vals = [series[v][k] for v in VARIABLES]
                    counts.update(v for v, x in zip(VARIABLES, vals) if x is None)
                    hourly.append([event, model, i, LONS[i], LATS[i], cells[1], cells[0],
                                   row["elevation"], stamp+"+12:00", *vals])
            inventory.append({"event": event, "model": model, "response_count": len(rows), "hours": hours,
                              "reported_cells_lat_lon": sorted(coords), "units": next(iter(rows.values()))["hourly_units"],
                              "missing_request_hours": {k: counts[k] for k in VARIABLES}})
        pairs = sorted(usable["era5_seamless"].keys() & usable["ecmwf_ifs"].keys())
        for t0, t1, context in [(None, None, "full")]+(
                [("2026-01-13T01", "2026-01-17T00", "UGMS_calendar_approximation")] if event == "january" else []):
            for i in pairs:
                seam, ifs = (usable[key][i] for key in ("era5_seamless", "ecmwf_ifs"))
                a, b = seam["hourly"], ifs["hourly"]
                assert a["time"] == b["time"]
                inds = [k for k, stamp in enumerate(a["time"]) if t0 is None or t0 <= stamp[:13] <= t1]
                for field in VARIABLES:
                    compared = [(a[field][k], b[field][k]) for k in inds
                                if a[field][k] is not None and b[field][k] is not None]
                    compare = {"event": event, "window": context, "request_index": i, "variable": field,
                               "era_distance_to_station_km": round(grid_distance(seam), 2),
                               "ifs_distance_to_station_km": round(grid_distance(ifs), 2),
                               "paired_hours": len(compared),
                               "model_mean_abs_difference": sum(difference(x, y, field) for x, y in compared)/len(compared)
                               if compared else None}
                    for key, series in (("era", a), ("ifs", b)):
                        values = [series[field][k] for k in inds if series[field][k] is not None]
                        compare[key+"_value_hours"] = len(values)
                        compare[key+"_sum"] = round(sum(values), 3) if values and field in ("snowfall", "precipitation") else None
                        compare[key+"_max"] = max(values) if values and field != "wind_direction_10m" else None
                    comparisons.append(compare)
        if event == "control":
            a, b = [usable[key] for key in ("era5_seamless", "ecmwf_ifs")]
            times = next(iter(a.values()))["hourly"]["time"]
            # Choose the quietest six-hour spell jointly across comparable requests.
            windows = []
            for start in range(len(times)-5):
                stop = start+6
                met = [r[i]["hourly"] for r in (a, b) for i in pairs]
                snow = max(sum(s["snowfall"][start:stop]) for s in met)
                precip = max(sum(s["precipitation"][start:stop]) for s in met)
                speed = max(max(s["wind_speed_10m"][start:stop]) for s in met)
                windows.append((snow, precip, speed, times[start], times[stop-1]))
            controls = [{"selection": "six hours lexicographic min max snowfall, precipitation, wind; joint paired requests",
                         "common_requests": pairs, "rank_tuple": min(windows)}]
    origin = {}
    for field in VARIABLES:
        origin[field] = {}
        for model in ("era5", "era5_land", "ecmwf_ifs"):
            left, right = read("january", "era5_seamless"), read("january", model)
            values = [(a, b) for i in sorted(left.keys() & right.keys())
                      for a, b in zip(left[i]["hourly"][field], right[i]["hourly"][field])
                      if a is not None and b is not None]
            origin[field][model] = {"paired": len(values), "exact": sum(a == b for a, b in values)}
    with (OUT / "weather-hourly.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["event", "model", "request_index", "request_lon", "request_lat", "reported_lon",
                         "reported_lat", "reported_elevation_m", "valid_at", *VARIABLES])
        writer.writerows(hourly)
    with (OUT / "weather-comparison.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(comparisons[0]))
        writer.writeheader()
        writer.writerows(comparisons)
    hashes = {p.name: sha256(p.read_bytes()).hexdigest() for p in SOURCES.glob("*ifs*.json") if "-ifs" not in p.name}
    (OUT / "weather-audit.json").write_text(json.dumps({"inventory": inventory, "january_origin_check": origin,
                                                       "quiet_control": controls, "ifs_raw_sha256": hashes}, indent=2)+"\n")


if __name__ == "__main__":
    stats()
