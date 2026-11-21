"""Read archived snow fields at photo landmarks. No model is rerun."""
from base64 import b64decode
from datetime import datetime
import gzip
import json
from pathlib import Path
import zlib

import numpy as np
from shapely import distance, points, union_all
from shapely.geometry import LineString, Point, shape

HERE = Path(__file__).resolve().parent
ARCHIVE = HERE.parents[1] / "out/citywide-storms-full"
QUARTER = HERE.parent / "quarter-cfd-2026-11-21"

# Locations are WGS84 landmark anchors, never camera or snowfall coordinates.
LANDMARKS = {
    "house-42": (158.64384, 53.05285, 80, "2026-01-15", "2026-01-15T23:59", [106049024, 106049027]),
    "report-26": (158.64592924, 53.02209358, 90, "2026-01-13", "2026-01-19T20:08", [98328203]),
    "report-29": (158.64282786, 53.02546652, 65, "2026-01-13", "2026-01-19T20:08", [843317511]),
}


def stats(values):
    return {
        "n": int(values.size),
        "min": float(values.min()),
        "median": float(np.median(values)),
        "max": float(values.max()),
    }


def sample_january():
    record = next(run for run in json.loads((ARCHIVE / "manifest.json").read_text())
                  if run["event"] == "january-2026")
    with gzip.open(ARCHIVE / (record["run_id"] + ".json.gz"), "rt") as handle:
        run = json.load(handle)
    rows, cols = run["rows"], run["cols"]
    west, south, east, north = run["bounds"]
    lon, lat = np.meshgrid(west + (np.arange(cols) + .5) * (east-west) / cols,
                           north - (np.arange(rows) + .5) * (north-south) / rows)
    tables = {(t["i"], t["j"]): t["hours"] for t in run["tiles"]}
    stamps = [datetime.fromisoformat(frame["valid_at"]) for frame in run["wind_frames"]]
    arrays = [np.frombuffer(zlib.decompress(b64decode(frame)), dtype="<f4").reshape(rows, cols)
              for frame in run["timeline_binary"]["frames"]]
    # The archived final grid is the whole-event estimate, while the preceding
    # frames add successive estimate blocks. Keep that distinction in the output.
    arrays.append(np.asarray(run["grids"]["snow_cm"], dtype=float))
    assert len(stamps) == len(arrays)
    result = {"run_id": record["run_id"], "coverage": [stamps[0].isoformat(), stamps[-1].isoformat()], "photos": {}}
    for identifier, (x, y, radius, start, end, osm) in LANDMARKS.items():
        d = np.hypot((lon-x)*111195*np.cos(np.deg2rad(y)), (lat-y)*111195)
        mask = d <= radius
        result["photos"][identifier] = photo = {
            "landmark_lon_lat": [x, y], "radius_m": radius, "osm_ways": osm,
            "candidate_local_dates": [start, end], "frames": []}
        for stamp, field in zip(stamps, arrays):
            if not datetime.fromisoformat(start) <= stamp.replace(tzinfo=None) <= datetime.fromisoformat(end):
                continue
            cell_rows, cell_cols = np.where(mask & np.isfinite(field))
            local = field[cell_rows, cell_cols]
            if not len(local):
                continue
            tile_cells = run["tile_cells"]
            tile_keys = [(int(i)//tile_cells, int(j)//tile_cells) for j, i in zip(cell_rows, cell_cols)]
            baselines = {key: sum(h["snowfall_cm"] for h in tables[key]
                                  if datetime.fromisoformat(h["valid_at"]) <= stamp)
                         for key in set(tile_keys)}
            b0 = np.array([baselines[key] for key in tile_keys])
            neighbors = field[(d <= 350) & np.isfinite(field)]
            photo["frames"].append({
                "local_time": stamp.isoformat(), "field": "final_grid" if stamp == stamps[-1] else "checkpoint",
                "danil_fresh_cm": stats(local), "b0_cm": stats(b0),
                "danil_minus_b0_cm": stats(local-b0),
                "percentile_of_local_median_within_350m": float(np.mean(neighbors <= np.median(local))*100),
            })
    return result


def facade_cells(spec, xx, yy, paired, fields):
    """Find the matched northwest end wall and the neighboring paired cells."""
    ref = json.loads((HERE / "sources/map_reference.json").read_text())
    identifier = f"osm:way:{ref['footprint_osm_way']}"
    wing = shape(next(b["footprint_local_m"] for b in spec["buildings"] if b["id"] == identifier))
    west, south, _, _ = spec["bounds_wgs84"]
    scale_x, scale_y = spec["projection_m_per_degree"]
    pano_lon, pano_lat = ref["point_lon_lat"]
    camera = Point((pano_lon - west) * scale_x, (pano_lat - south) * scale_y)
    vertices = list(wing.exterior.coords)
    wall = min((LineString([a, b]) for a, b in zip(vertices, vertices[1:])), key=camera.distance)
    first, last = np.array(wall.coords)
    tangent = (last-first) / wall.length
    normal = np.array([tangent[1], -tangent[0]])
    if (np.array(camera.coords[0])-first) @ normal < 0:
        normal = -normal
    across = (xx-first[0])*normal[0] + (yy-first[1])*normal[1]
    along = (xx-first[0])*tangent[0] + (yy-first[1])*tangent[1]
    # Half-cell margin acknowledges that a 13 m wall can fall between 24 m centers.
    mask = paired & (across > 0) & (across <= 24) & (along >= -12) & (along <= wall.length + 12)
    return {
        "outward_bearing_degrees": float(np.degrees(np.arctan2(normal[0], normal[1])) % 360),
        "wall_length_m": wall.length,
        "wall_local_m": list(wall.coords),
        "pano_distance_from_wall_m": camera.distance(wall),
        "cell_selection": "0–24 m from footprint and outward wall, with 12 m margin along the wall",
        "cells": [{
            "col": int(i), "row_from_south": int(j), "center_local_m": [xx[j, i], yy[j, i]],
            "distance_to_wall_m": float(wall.distance(Point(xx[j, i], yy[j, i]))),
            "fresh_cm": {key: float(values[j, i]) for key, values in fields.items()},
        } for j, i in zip(*np.where(mask))]
    }


def sample_quarter():
    spec = json.loads((QUARTER / "sources/quarter.json").read_text())
    identifiers = {f"osm:way:{osm}" for osm in LANDMARKS["house-42"][-1]}
    house = union_all([shape(b["footprint_local_m"]) for b in spec["buildings"] if b["id"] in identifiers])
    with np.load(QUARTER / "results/comparison.npz") as files:
        fields = {key: files[f"jan-snow-coarse__{key}_cm"] for key in ("cfd", "danil", "uniform")}
    height, width = fields["cfd"].shape
    xx, yy = np.meshgrid((np.arange(width)+.5)*24, (np.arange(height)+.5)*24)
    dist = distance(points(xx, yy), house)
    paired = (dist > 0) & (dist <= 24) & np.logical_and.reduce([np.isfinite(f) for f in fields.values()])
    center = house.centroid
    east, north = xx-center.x, yy-center.y
    ew = np.abs(east) > np.abs(north)
    sectors = {"N": ~ew & (north >= 0), "S": ~ew & (north < 0), "E": ew & (east >= 0), "W": ew & (east < 0)}
    return {
        "local_time": spec["weather"]["jan-snow"]["valid_at"], "grid_m": 24, "radius_from_footprint_m": 24,
        "hourly_fresh_cm": {key: stats(values[paired]) for key, values in fields.items()},
        "cfd_minus_danil_cm": stats((fields["cfd"]-fields["danil"])[paired]),
        "northwest_wall": facade_cells(spec, xx, yy, paired, fields),
        "sectors": {name: {key: stats(values[paired & sector]) for key, values in fields.items()}
                    for name, sector in sectors.items() if np.any(paired & sector)},
    }


if __name__ == "__main__":
    (HERE / "model_samples.json").write_text(
        json.dumps({"city": sample_january(), "quarter_4_2": sample_quarter()}, ensure_ascii=False, indent=2) + "\n")
