"""Overlay independently documented locations and compare timing without inventing labels."""
import base64
import csv
from datetime import datetime, timedelta
import gzip
import json
from pathlib import Path
import sys
import zlib

import numpy as np

from city_sim.api import make_app
from city_sim.terrain import TO_METRIC

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "results"


def timespan(row):
    start, end = (row[key] for key in ("time_start", "time_end"))
    a = datetime.fromisoformat(start).replace(tzinfo=None)
    b = datetime.fromisoformat(end).replace(tzinfo=None)
    return a, b + (timedelta(hours=23, minutes=59) if len(end) == 10 else timedelta())


def bounds_cells(obj, bounds, rows, cols):
    """Circle of possible observation locations using local projected distances."""
    w, s, e, n = bounds
    dx, dy = (e-w)/cols, (n-s)/rows
    lon, lat, radius = [float(obj[key]) for key in ("lon", "lat", "accuracy_m")]
    x0, y0 = TO_METRIC.transform(lon, lat)
    degrees = radius/55000
    i = np.arange(max(0, int((lon-degrees-w)/dx)), min(cols, int((lon+degrees-w)/dx)+1))
    j = np.arange(max(0, int((n-lat-degrees)/dy)), min(rows, int((n-lat+degrees)/dy)+1))
    x, y = TO_METRIC.transform(*np.meshgrid(w+(i+.5)*dx, n-(j+.5)*dy))
    jj, ii = np.where((x-x0)**2+(y-y0)**2 <= radius**2)
    return j[jj], i[ii]


def map_panel(event, hours, field, rows, bounds, outfile):
    import matplotlib.pyplot as plt
    from matplotlib.patches import Ellipse

    fig = plt.figure(figsize=(7.2, 7))
    ax = fig.add_axes([.10, .14, .64, .72])
    scale_axis = fig.add_axes([.76, .14, .045, .72])
    w, s, e, n = bounds
    view_max = 5 * np.ceil(np.nanmax(field)/5)
    plotted = ax.imshow(field, extent=(w, e, s, n), origin="upper", cmap="Blues", vmin=0,
                        vmax=view_max, interpolation="nearest")
    ax.set_facecolor("#edf0f2")
    ax.set_xlim(158.60, 158.71)
    ax.set_ylim(52.976, 53.08)
    ax.set_aspect(1/np.cos(np.deg2rad(53)))
    fig.text(.10, .935, f"{hours}", fontsize=13, fontweight="bold")
    fig.text(.10, .895, "Свежий снег с начала архивного прогона", fontsize=11)
    for row in rows:
        if not row["lon"]:
            continue
        lon, lat, radius = [float(row[key]) for key in ("lon", "lat", "accuracy_m")]
        if row["evidence_id"] in {"J16-01", "J16-02", "J21-01", "J21-02", "J21-03"}:
            continue
        remote = row["match_status"] == "outside_replay"
        color = "#626772" if remote else "#b2321c"
        ax.add_patch(Ellipse((lon, lat), 2*radius/67000, 2*radius/111200, fill=False,
                             linewidth=1.3, color=color))
        ax.scatter(lon, lat, s=18, color=color, zorder=4)
        identifier = row["evidence_id"]
        if identifier == "J13-01":
            identifier = "МГ-1"
        if identifier == "J21-04":
            identifier = "Зеркальная 52–58\n(21 января)"
        offset = (9, 8) if row["evidence_id"] != "J21-04" else (10, -14)
        ax.annotate(identifier, (lon, lat), xytext=offset, textcoords="offset points",
                    fontsize=8, color=color, fontweight="bold")
    ax.set_xlabel("Долгота, ° в.д.")
    ax.set_ylabel("Широта, ° с.ш.")
    fig.colorbar(plotted, cax=scale_axis, label="Расчётный свежий снег, см")
    fig.text(.10, .035, "Контуры — радиусы поиска. Серые метки — события за пределами прогона.",
             fontsize=8, color="#424b58")
    fig.savefig(outfile, dpi=150)
    plt.close(fig)


def audit(archive):
    rows = list(csv.DictReader((OUT / "observations.csv").open()))
    frames = []
    matches = []
    for record in json.loads((archive / "manifest.json").read_text()):
        name = record["event"]
        with gzip.open(archive / (record["run_id"]+".json.gz"), "rt") as file:
            raw = json.load(file)
        binary = raw["timeline_binary"]
        stamps = [frame["valid_at"] for frame in raw["wind_frames"]]
        for i, stamp in enumerate(stamps):
            frames.append({"event": name, "run_id": raw["run_id"], "time": stamp,
                           "field": "timeline_binary" if i < len(binary["frames"]) else "grids.snow_cm",
                           "index": i, "bounds": ";".join(map(str, raw["bounds"]))})
        included = [row for row in rows if row["event_id"] == name]
        timestamps = [datetime.fromisoformat(stamp).replace(tzinfo=None) for stamp in stamps]
        arrays = [np.frombuffer(zlib.decompress(base64.b64decode(frame)), dtype="<f4").reshape(raw["rows"], raw["cols"])
                  for frame in binary["frames"]]
        arrays.append(np.asarray(raw["grids"]["snow_cm"], dtype=float))
        field_time = {"december-2025": "2025-12-09T11:00", "january-2026": "2026-01-15T23:00"}.get(name)
        if field_time:
            i = next(i for i, stamp in enumerate(stamps) if stamp.startswith(field_time))
            map_panel(name, stamps[i][:-6]+" UTC+12", arrays[i], included, raw["bounds"], OUT/f"{name}-context.png")
        for row in included:
            first, last = timespan(row)
            applicable = [i for i, t in enumerate(timestamps) if first <= t <= last]
            sampled = []
            cells = 0
            if row["lon"] and applicable:
                jj, ii = bounds_cells(row, raw["bounds"], raw["rows"], raw["cols"])
                cells = len(jj)
                for i in applicable:
                    values = arrays[i][jj, ii]
                    sampled.extend(values[np.isfinite(values)])
            matches.append({"evidence_id": row["evidence_id"], "model_run_id": raw["run_id"],
                            "hit_miss_false_alarm_unknown": "unknown",
                            "reported_match_status": row["match_status"],
                            "frames_in_observation_window": "|".join(stamps[i] for i in applicable),
                            "geometric_cells": cells, "valid_cell_frames": len(sampled),
                            "replay_fresh_snow_min_cm": round(float(min(sampled)), 3) if sampled else None,
                            "replay_fresh_snow_max_cm": round(float(max(sampled)), 3) if sampled else None,
                            "paired_error_cm": None})
    for name, result in (("archived-frames.csv", frames), ("observation-matches.csv", matches)):
        with (OUT/name).open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(result[0]))
            writer.writeheader()
            writer.writerows(result)

    with gzip.open(OUT/"december-segments.csv.gz", "rt") as handle:
        ranks = list(csv.DictReader(handle))
    runner = make_app(db_path="/tmp/kam-reality-geo.sqlite").state.city_snow
    graph = runner.city.roads((158.48, 52.91, 158.80, 53.16), extended=True)
    by_physical = {edge["properties"]["physical_road_id"]: edge for edge in graph["features"]}
    from shapely.geometry import LineString, Point
    points = {}
    for row in rows:
        if row["evidence_id"] in ("D09-02", "D09-03"):
            points[row["evidence_id"]] = (Point(*TO_METRIC.transform(float(row["lon"]), float(row["lat"]))),
                                          float(row["accuracy_m"]))
    candidates = {key: set() for key in points}
    for id_, edge in by_physical.items():
        coords = [TO_METRIC.transform(*p) for p in edge["geometry"]["coordinates"]]
        line = LineString(coords)
        for id_row, (point, radius) in points.items():
            if line.distance(point) <= radius:
                candidates[id_row].add(id_)
    out = []
    for id_, rule in {"D09-01": "Ленинская", "D09-02": "", "D09-03": "", "D09-04": "Ленинградская"}.items():
        for stamp in sorted({row["time"] for row in ranks}):
            observations = [row for row in ranks if row["time"] == stamp and
                            (rule in row["road_name"] if rule else row["physical_road_id"] in candidates[id_])]
            top = observations[0] if observations else {}
            overlap = sum(int(row["rank"]) <= 20 for row in observations)
            out.append({"evidence_id": id_, "hour": stamp, "candidate_segments": len(observations),
                        "candidate_top20_segments": overlap, "best_rank_in_search_area": top.get("rank"),
                        "best_segment": top.get("physical_road_id"),
                        "best_segment_excess_cm": top.get("modeled_excess_cm"),
                        "relation_to_incident": "unknown"})
    with (OUT/"december-top20-screen.csv").open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(out[0]))
        writer.writeheader()
        writer.writerows(out)


if __name__ == "__main__":
    audit(Path(sys.argv[1]))
