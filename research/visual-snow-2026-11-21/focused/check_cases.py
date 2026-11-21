"""Sample January city-sim checkpoints at visible snow sectors and draw case plates."""
from datetime import datetime
import gzip
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import matplotlib.pyplot as plt
from matplotlib.colors import TwoSlopeNorm
from matplotlib.patches import Polygon, Rectangle
import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
ALBUM = HERE.parent
ARCHIVE = ROOT / "out/citywide-storms-full"

CASES = {
    "house-42": {
        "anchor": (158.6433658, 53.053129), "feature": ["106049027", "106049024"],
        "title": "50 лет Октября, 4/2 · северо-западный торец",
        "labels": [("Адресная табличка", (.211, .124), (.05, .24)), ("Кромка у стены", (.465, .36), (.28, .52)),
                   ("Передок машины", (.458, .762), (.08, .91)), ("Гребень", (.88, .285), (.71, .11))],
        "limits": (-72, 108, -113, 77), "map_time": "2026-01-15T23:00:00+12:00",
    },
    "report-29": {
        "anchor": (158.64282786, 53.02546652), "feature": ["843317511"],
        "title": "Озерновская коса · открытая площадка перед памятником",
        "labels": [("Пьедестал", (.525, .63), (.60, .47)), ("Ровная поверхность", (.40, .83), (.07, .70)),
                   ("Тень", (.86, .82), (.77, .67))],
        "limits": (-72, 64, -74, 50), "map_time": "2026-01-17T11:00:00+12:00",
    },
    "report-26": {
        "anchor": (158.64580895, 53.02229455), "feature": ["98328203"],
        "title": "Ленинская, 75 · вал перед драмтеатром",
        "labels": [("Вход", (.43, .72), (.11, .60)), ("Грязные комья на гребне", (.79, .50), (.46, .23)),
                   ("Фронт вала", (.67, .81), (.43, .95))],
        "limits": (-88, 73, -82, 77), "map_time": "2026-01-17T11:00:00+12:00",
    },
}
DATES = ["2026-01-14T23:00", "2026-01-15T11:00", "2026-01-15T23:00"]
COLORS = ("#067a55", "#9a5500", "#714aa1")


def osm_geometry():
    with gzip.open(ROOT / "data/pkc/city.osm.gz") as source:
        root = ET.parse(source).getroot()
    nodes = {node.attrib["id"]: (float(node.attrib["lon"]), float(node.attrib["lat"]))
             for node in root.iterfind("node")}
    features = {}
    for way in root.iterfind("way"):
        tags = {tag.attrib["k"]: tag.attrib["v"] for tag in way.iterfind("tag")}
        if set(tags) & {"building", "highway", "artwork_type"}:
            features[way.attrib["id"]] = (tags, np.array([nodes[nd.attrib["ref"]] for nd in way.iterfind("nd")]))
    return features


def local_coordinates(coordinates, anchor):
    return (np.asarray(coordinates) - anchor) * (111195 * np.cos(np.deg2rad(anchor[1])), 111195)


def wall_band(x, y, wall, outside, near, far, margin):
    midpoint = wall.mean(axis=0)
    tangent = (wall[1] - wall[0]) / np.linalg.norm(wall[1] - wall[0])
    normal = np.array([-tangent[1], tangent[0]])
    if (outside - midpoint) @ normal < 0:
        normal = -normal
    along = (x - midpoint[0]) * tangent[0] + (y - midpoint[1]) * tangent[1]
    across = (x - midpoint[0]) * normal[0] + (y - midpoint[1]) * normal[1]
    half_length = np.linalg.norm(wall[1] - wall[0]) / 2 + margin
    coords = np.array([midpoint + a * tangent + b * normal
                       for a, b in [(-half_length, near), (half_length, near), (half_length, far), (-half_length, far)]])
    return (np.abs(along) <= half_length) & (across > near) & (across <= far), coords


def sector(x, y, low, high, heading_min, heading_max):
    distance = np.hypot(x, y)
    heading = np.degrees(np.arctan2(x, y)) % 360
    angles = np.radians(np.linspace(heading_min, heading_max, 31))
    rays = np.column_stack((np.sin(angles), np.cos(angles)))
    return ((distance > low) & (distance <= high) & (heading >= heading_min) & (heading <= heading_max),
            np.concatenate((low * rays, high * rays[::-1])))


def zones_for(identifier, x, y, target, reference):
    if identifier == "house-42":
        wing, south_wing = target
        return {
            "Торец 0–25 м": wall_band(x, y, wing[3:5], reference, 0, 25, 12),
            "Продолжение 25–55 м": wall_band(x, y, wing[3:5], reference, 25, 55, 12),
            "ЮВ торец 0–25 м": wall_band(x, y, south_wing[:2], south_wing[:2].mean(axis=0) + [10, -10], 0, 25, 12),
        }
    if identifier == "report-29":
        return {
            "Перед пьедесталом 8–35 м": sector(x, y, 8, 35, 170, 260),
            "Передний план 35–60 м": sector(x, y, 35, 60, 170, 260),
        }
    north_wall = target[0][[3, 0]]
    return {"Перед фасадом 0–65 м": wall_band(x, y, north_wall, np.array([0, 30]), 0, 65, 18)}


def frame_values(stamp, tile, row, col, value, longitude, latitude):
    hours = [h for h in tile["hours"] if h["valid_at"] <= stamp]
    return {
        "row": int(row), "col": int(col), "lon_lat": [float(longitude), float(latitude)],
        "danil_cm": round(float(value), 4), "background_cm": round(sum(h["snowfall_cm"] for h in hours), 4),
        "tile": [tile["i"], tile["j"]],
    }


def weighted_wind(tile, start, stamp):
    hours = [h for h in tile["hours"] if start < h["valid_at"] <= stamp and h["snowfall_cm"] > 0]
    radians = np.radians([h["wind_from_deg"] for h in hours])
    weights = np.array([h["snowfall_cm"] for h in hours])
    east = float(weights @ np.sin(radians))
    north = float(weights @ np.cos(radians))
    return {"window": [hours[0]["valid_at"], hours[-1]["valid_at"]],
            "from_deg": round(np.degrees(np.arctan2(east, north)) % 360, 1),
            "concentration": round(np.hypot(east, north) / weights.sum(), 3),
            "snow_weighted_speed_ms": round(float(weights @ [h["wind_speed_ms"] for h in hours] / weights.sum()), 2),
            "distance_to_source_m": tile["distance_to_source_m"]}


def plot_plate(case, x, y, dx, dy, features, zones, deviations, wind, reference, photo):
    fig, (left, right) = plt.subplots(1, 2, figsize=(12.8, 5.6), gridspec_kw={"width_ratios": [1.15, 1]})
    fig.subplots_adjust(left=.024, right=.973, top=.875, bottom=.235, wspace=.13)
    fig.suptitle(case["title"], x=.024, ha="left", fontweight="bold", fontsize=17)
    with Image.open(ALBUM / photo["file"]) as original:
        picture = np.asarray(original.convert("RGB"))
    left.imshow(picture)
    left.set_axis_off()
    for label, point, text_point in case["labels"]:
        px, py = point
        left.annotate(label, (px, 1-py), (text_point[0], 1-text_point[1]),
                      xycoords="axes fraction", textcoords="axes fraction",
                      fontsize=8.5, color="#152a3c", va="center",
                      bbox={"boxstyle": "round,pad=.28", "fc": "white", "ec": "none", "alpha": .94},
                      arrowprops={"arrowstyle": "-|>", "color": "#0b6580", "lw": 1.7})
    left.set_title("Архивный кадр", loc="left", fontsize=11, pad=7)

    xmin, xmax, ymin, ymax = case["limits"]
    norm = TwoSlopeNorm(vmin=-65, vcenter=0, vmax=65)
    cmap = plt.get_cmap("PuOr_r")
    for (row, col), deviation in deviations.items():
        right.add_patch(Rectangle((x[row, col]-dx/2, y[row, col]-dy/2), dx, dy, ec="#abb1b8", lw=.6,
                                  fc=cmap(norm(deviation)), alpha=.83, zorder=0))
        if xmin+5 < x[row,col] < xmax-5 and ymin+5 < y[row,col] < ymax-5:
            right.text(x[row,col], y[row,col], f"{deviation:+.0f}" if abs(deviation) >= .5 else "0",
                       fontsize=6.4, color="white" if abs(deviation) > 45 else "#333c44",
                       ha="center", va="center", zorder=1)
    for way_id, (tags, coords) in features.items():
        xy = local_coordinates(coords, case["anchor"])
        if (xy[:,0].min() > xmax or xy[:,0].max() < xmin or xy[:,1].min() > ymax or xy[:,1].max() < ymin):
            continue
        if "highway" in tags:
            right.plot(*xy.T, color="#7b8085", lw=.65, zorder=2)
        else:
            target = way_id in case["feature"]
            right.add_patch(Polygon(xy, fc="#b0bec8" if target else "#e1e5e7", ec="#253c4b" if target else "#858f95",
                                    lw=1.2 if target else .55, zorder=3))
    for (name, (_, outline)), color in zip(zones.items(), COLORS):
        right.add_patch(Polygon(outline, fc="none", ec=color, lw=2, ls="--", zorder=4, label=name))
    if reference is not None:
        right.scatter(*reference, s=36, marker="^", c="#067a55", ec="white", lw=.8, zorder=5, label="Летняя панорама")
    right.set(xlim=(xmin,xmax), ylim=(ymin,ymax), aspect="equal",
              xlabel="Восток, м", ylabel="Север, м")
    right.tick_params(labelsize=7)
    stamp = datetime.fromisoformat(case["map_time"])
    right.set_title(f"Данил минус фон, см · {stamp:%d.%m %H:%M}", loc="left", fontsize=11, pad=7)
    right.legend(loc="upper center", bbox_to_anchor=(.5, -.19), fontsize=7.3, ncols=2, frameon=False)
    wind_angle = np.deg2rad(wind["from_deg"])
    tail = (xmax-20, ymax-22)
    right.annotate("", (tail[0]-17*np.sin(wind_angle), tail[1]-17*np.cos(wind_angle)), tail,
                   arrowprops={"arrowstyle": "-|>", "lw": 1.6, "color": "#074771"})
    right.text(xmax-5, ymax-10, f"ветер с {wind['from_deg']:.0f}°", ha="right", va="center", fontsize=7,
               bbox={"fc": "white", "ec":"none", "alpha":.8, "pad":1.5})
    right.text(xmin+6, ymax-9, "С ↑", va="center", fontsize=8.5,
               bbox={"fc": "white", "ec": "none", "alpha": .85, "pad": 1.5})
    credit = photo["caption"].replace(" ИА KamchatkaMedia", " · ИА KamchatkaMedia")
    fig.text(.024, .07, f"{credit}  ·  Контуры © OpenStreetMap", fontsize=8, color="#394955")
    fig.savefig(HERE / f"{photo['id']}.png", dpi=150)
    plt.close(fig)


def main():
    record = next(r for r in json.loads((ARCHIVE / "manifest.json").read_text()) if r["event"] == "january-2026")
    run_id = record["run_id"]
    with gzip.open(ARCHIVE / f"{run_id}.json.gz") as handle:
        run = json.load(handle)
    with np.load(ARCHIVE / f"{run_id}.npz") as arrays:
        checkpoints = arrays["timeline"]
    stamps = [f["valid_at"] for f in run["wind_frames"]]
    assert len(stamps) == len(checkpoints) + 1
    west, south, east, north = run["bounds"]
    nrows, ncols = run["rows"], run["cols"]
    lon, lat = np.meshgrid(west + (np.arange(ncols) + .5) * (east-west) / ncols,
                           north - (np.arange(nrows) + .5) * (north-south) / nrows)
    tiles = {(t["i"], t["j"]): t for t in run["tiles"]}
    tile_cells = run["tile_cells"]
    features = osm_geometry()
    photos = {p["id"]: p for p in json.loads((ALBUM / "sources/frames.json").read_text())}
    panorama_lon_lat = json.loads((ALBUM / "sources/map_reference.json").read_text())["point_lon_lat"]
    results = {"run_id": run_id, "engine_commit": run["provenance"]["engine_commit"],
               "start": stamps[0], "final_full_window": stamps[-1], "cases": {}}
    for identifier, case in CASES.items():
        photo = photos[identifier]
        assert hashlib.sha256((ALBUM / photo["file"]).read_bytes()).hexdigest() == photo["sha256"]
        x, y = local_coordinates(np.stack((lon, lat), axis=-1), case["anchor"]).transpose(2, 0, 1)
        dx, dy = x[0,1] - x[0,0], y[0,0] - y[1,0]
        landmarks = [local_coordinates(features[k][1], case["anchor"]) for k in case["feature"]]
        reference = local_coordinates(panorama_lon_lat, case["anchor"]) if identifier == "house-42" else None
        zones = zones_for(identifier, x, y, landmarks, reference)
        data = results["cases"][identifier] = {
            "anchor_lon_lat": case["anchor"], "ways": case["feature"], "grid_m": [dx, dy], "photo": photo["file"],
            "photo_url": photo["url"], "photo_sha256": photo["sha256"],
            "zones_local_east_north_m": {key: polygon.tolist() for key, (_, polygon) in zones.items()}, "frames": {},
        }
        for stamp, field in zip(stamps[:-1], checkpoints, strict=True):
            if identifier == "house-42" and stamp[:16] not in DATES:
                continue
            if identifier != "house-42" and stamp < "2026-01-13":
                continue
            chosen = {}
            for key, (mask, _) in zones.items():
                chosen[key] = [
                    frame_values(stamp, tiles[(col//tile_cells, row//tile_cells)], row, col,
                                 field[row,col], lon[row,col], lat[row,col])
                    for row, col in zip(*np.where(mask & np.isfinite(field)))
                ]
                assert chosen[key], (identifier, key)
            data["frames"][stamp] = chosen
        stamp = case["map_time"]
        frame = checkpoints[stamps.index(stamp)]
        samples = next(iter(data["frames"][stamp].values()))
        center_tile = tiles[tuple(samples[0]["tile"])]
        weighted = weighted_wind(center_tile, "2026-01-15" if identifier == "house-42" else "", stamp)
        data["map"] = {"stamp": stamp, "wind": weighted}
        xmin, xmax, ymin, ymax = case["limits"]
        deviations = {}
        for row, col in zip(*np.where((x >= xmin-dx/2) & (x <= xmax+dx/2) &
                                       (y >= ymin-dy/2) & (y <= ymax+dy/2) & np.isfinite(frame))):
            tile = tiles[(col//tile_cells, row//tile_cells)]
            background = sum(h["snowfall_cm"] for h in tile["hours"] if h["valid_at"] <= stamp)
            deviations[(row, col)] = float(frame[row,col]) - background
        plot_plate(case, x, y, dx, dy, features, zones, deviations, weighted, reference, photo)
    (HERE / "samples.json").write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
