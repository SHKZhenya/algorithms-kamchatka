"""Re-run the pinned city model on common tile masks and export its sensitivity."""
import csv
import gzip
import hashlib
import json
import math
from pathlib import Path

import numpy as np

from city_sim.api import make_app
from city_sim.city_snow import CitySnowRequest, remap

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "results"
OUTPUT.mkdir(exist_ok=True)
CASES = {
    "december-2025": ("2025-12-09T06", "2025-12-09T11"),
    "january-2026": ("2026-01-15T00", "2026-01-15T23"),
}
VARIANTS = ("b0_cm", "b2_cm", "height_9m_cm", "reverse_wind_cm", "half_wind_cm",
            "no_buildings_cm", "calm_cm", "b1_index", "b2_index", "no_relief_index",
            "background_cm", "weather_distance_m")


def hash_file(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def cells_on_segment(coords, bounds, rows, cols):
    """List grid cells intersected over a positive length, in travel order."""
    west, south, east, north = bounds
    (x1, y1), (x2, y2) = [((x-west)/(east-west)*cols, (north-y)/(north-south)*rows)
                            for x, y in coords]
    dx, dy = x2-x1, y2-y1
    cuts = {0., 1.}
    for a, b, d, size in ((x1, x2, dx, cols), (y1, y2, dy, rows)):
        if d:
            for edge in range(max(0, math.floor(min(a, b))+1), min(size, math.ceil(max(a, b)))+1):
                t = (edge-a)/d
                if 0 < t < 1:
                    cuts.add(t)
    cuts = sorted(cuts)
    hits = []
    for a, b in zip(cuts, cuts[1:]):
        t = (a+b)/2
        col, row = math.floor(x1+dx*t), math.floor(y1+dy*t)
        if 0 <= col < cols and 0 <= row < rows:
            hits.append((row, col))
    return set(hits)


def export_roads(raw, city, frames, backgrounds, weather, path):
    rows, cols = raw["rows"], raw["cols"]
    roads = city.roads(raw["bounds"], extended=True)
    by_physical = {}
    for road in roads["features"]:
        properties = road["properties"]
        key = properties["physical_road_id"]
        if key not in by_physical:
            by_physical[key] = [set(), [], properties["road_name"]]
        by_physical[key][0].update(cells_on_segment(road["geometry"]["coordinates"], raw["bounds"], rows, cols))
        by_physical[key][1].append(road["id"])
    with gzip.open(path, "wt", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["physical_road_id", "edge_ids", "road_name", "time", "modeled_excess_cm",
                         "rank", "weather_distance_m", "source", "sampled_cells", "valid_cells"])
        for stamp, field, bg in zip(raw["audit_hours"], frames, backgrounds):
            excess = np.maximum(field-bg, 0)
            records = []
            for key, (cells, edges, name) in by_physical.items():
                valid = [(excess[j, i], weather[j, i]) for j, i in sorted(cells) if np.isfinite(excess[j, i])]
                if valid:
                    value, distance = max(valid, key=lambda sample: sample[0])
                    records.append((round(float(value), 3), key, "|".join(map(str, sorted(edges))), name,
                                    int(distance), len(cells), len(valid)))
            for rank, (value, key, edges, name, distance, total, valid) in enumerate(
                    sorted(records, key=lambda x: (-x[0], x[1])), 1):
                writer.writerow([key, edges, name, stamp, value, rank, distance, "Open-Meteo ECMWF IFS", total, valid])


def run(city_runner, source_path, event):
    with gzip.open(source_path, "rt") as handle:
        raw = json.load(handle)
    spec = CitySnowRequest(source="reanalysis", event_id=event)
    select, source = city_runner.weather(spec)
    assert raw["run_id"] == city_runner.identity(spec, source), raw["provenance"]
    model, _ = city_runner.study.engine
    west, south, east, north = raw["bounds"]
    rows, cols, cells = raw["rows"], raw["cols"], raw["tile_cells"]
    nx, ny = cols//cells, rows//cells
    xcorners, ycorners = np.linspace(west, east, nx+1), np.linspace(north, south, ny+1)
    revision = city_runner.city.data_revision
    reference = np.isfinite(np.asarray(raw["grids"]["snow_cm"], dtype=float))
    grids = {key: np.full((rows, cols), np.nan, dtype=np.float32) for key in VARIANTS}
    start, end = CASES[event]
    stamps = [h["valid_at"] for h in source["locations"][0]["hours"] if start <= h["valid_at"][:13] <= end]
    frames = np.full((len(stamps), rows, cols), np.nan, dtype=np.float32) if event.startswith("december") else []
    backgrounds = np.full_like(frames, np.nan) if len(frames) else []
    raw["audit_hours"] = stamps
    for tile in raw["tiles"]:
        i, j = tile["i"], tile["j"]
        bounds = (float(xcorners[i]), float(ycorners[j+1]), float(xcorners[i+1]), float(ycorners[j]))
        w, s, e, n = bounds
        _, all_hours, distance = select(((w+e)/2, (s+n)/2))
        assert all_hours == tile["hours"] and distance == tile["distance_to_source_m"]
        hours = [h for h in all_hours if h["valid_at"] in stamps]
        buildings, ground, relief = city_runner.tile_geometry(bounds, cells, revision)
        slot = np.s_[j*cells:(j+1)*cells, i*cells:(i+1)*cells]
        mask = reference[slot]
        bg = sum(h["snowfall_cm"] for h in hours)
        windy = bg > 0 and max(h["wind_speed_ms"] for h in hours) > 2

        def estimate(b, h):
            return remap(model.estimate(bounds, b, h, cells, True)["snow_depth_cm"], cells)

        b2 = estimate(buildings, hours)
        assert np.array_equal(ground & np.isfinite(b2), mask)
        flat_heights = [type(b)(b.footprint, 9., b.height_source) for b in buildings]
        reversed_wind = [{**h, "wind_from_deg": (h["wind_from_deg"]+180) % 360} for h in hours]
        half_speed = [{**h, "wind_speed_ms": h["wind_speed_ms"]/2} for h in hours]
        no_buildings = estimate([], hours)
        calm = estimate(buildings, [{**h, "wind_speed_ms": 0.} for h in hours])
        relief_index = .25*np.nan_to_num(relief) if windy else np.zeros_like(b2)
        snow_index = .75*np.clip((b2/bg-1)/1.5, 0, 1) if windy else np.zeros_like(b2)
        values = [bg, b2, estimate(flat_heights, hours), estimate(buildings, reversed_wind),
                  estimate(buildings, half_speed), no_buildings, calm, relief_index, snow_index+relief_index,
                  snow_index, bg, distance]
        for key, value in zip(VARIANTS, values):
            grids[key][slot] = np.where(mask, value, np.nan)
        if len(frames):
            for k in range(len(stamps)):
                prefix = hours[:k+1]
                frames[k][slot] = np.where(mask, b2 if k+1 == len(hours) else estimate(buildings, prefix), np.nan)
                backgrounds[k][slot] = np.where(mask, sum(h["snowfall_cm"] for h in prefix), np.nan)
    bundle = OUTPUT / f"{event}-ablation.npz"
    np.savez_compressed(bundle, **grids, hourly_cm=frames, hourly_background_cm=backgrounds)
    if len(frames):
        export_roads(raw, city_runner.city, frames, backgrounds, grids["weather_distance_m"],
                     OUTPUT / "december-segments.csv.gz")
    stats = {}
    for name, baseline in (("b2_cm", "b0_cm"), ("height_9m_cm", "b2_cm"), ("reverse_wind_cm", "b2_cm"),
                           ("half_wind_cm", "b2_cm"), ("no_buildings_cm", "b0_cm"), ("calm_cm", "b0_cm"),
                           ("no_relief_index", "b2_index"), ("b1_index", "b2_index")):
        dif = (grids[name]-grids[baseline])[reference]
        stats[name] = {"baseline": baseline, "mean_abs_difference": float(np.abs(dif).mean()),
                       "p95_abs_difference": float(np.quantile(np.abs(dif), .95)),
                       "changed_over_0_01_fraction": float(np.mean(np.abs(dif) > .01))}
    return {"event": event, "previous_run_id": raw["run_id"], "previous_replay_sha256": hash_file(source_path),
            "bundle": bundle.name, "bundle_sha256": hash_file(bundle), "coverage_cells": int(reference.sum()),
            "window_hours": stamps, "model_provenance": raw["provenance"], "bounds": raw["bounds"],
            "rows": rows, "cols": cols, "background_range_cm": [float(np.nanmin(grids["b0_cm"])),
                                                             float(np.nanmax(grids["b0_cm"]))],
            "model_range_cm": [float(np.nanmin(grids["b2_cm"])), float(np.nanmax(grids["b2_cm"]))],
            "sensitivity": stats}


if __name__ == "__main__":
    import argparse
    import subprocess
    import tempfile

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archived_replays", type=Path)
    args = parser.parse_args()
    manifest = json.loads((args.archived_replays / "manifest.json").read_text())
    results = {"model_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
               "audit_sha256": hash_file(Path(__file__)), "windows": []}
    with tempfile.TemporaryDirectory() as temp:
        runner = make_app(db_path=Path(temp) / "audit.sqlite").state.city_snow
        for record in manifest:
            event = record["event"]
            if event in CASES:
                actual = run(runner, args.archived_replays / (record["run_id"]+".json.gz"), event)
                results["windows"].append(actual)
                (OUTPUT / "ablation.json").write_text(json.dumps(results, ensure_ascii=False, indent=2)+"\n")
                print(event, actual["coverage_cells"], actual["sensitivity"], flush=True)
