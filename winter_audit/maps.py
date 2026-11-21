"""Render field-review cases from the exported, versioned GeoJSON."""
import argparse
import json
from pathlib import Path
from textwrap import fill

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection

from .network import metric


def features(root, name):
    return json.loads((root / "geo" / (name + ".geojson")).read_text())["features"]


def draw(root):
    roads = features(root, "road_network_enriched")
    roads_by_id = {f["properties"]["edge_id"]: f for f in roads}
    omitted = features(root, "excluded_osm_segments")
    facility = next(f for f in features(root, "infrastructure_objects")
                    if f["properties"]["object_id"] == "osm:node:1391841291")
    near_frunze = roads_by_id["osm:189442552:1096962339:1096962226"]
    origin = metric(near_frunze["geometry"]["coordinates"][0])
    yard = min((f for f in omitted if f["properties"]["osm_tags"].get("service") == "driveway"),
               key=lambda f: sum((a-b)**2 for a,b in zip(metric(f["geometry"]["coordinates"][0]), origin)))
    north = [f for f in roads if f["properties"]["name"] == "Северо-Восточное шоссе"]
    korolev = [f for f in roads if "Академика Корол" in f["properties"]["name"]]
    korolev_nodes = {tuple(n) for f in korolev for n in f["geometry"]["coordinates"]}
    bridge_anchor = next((f for f in north
                         if any(tuple(n) in korolev_nodes for n in f["geometry"]["coordinates"])), None)
    cases = [
        ("grade", "Улица Гагарина · оценка продольного уклона",
         roads_by_id["osm:148397251:1096883064:1425120773"]),
        ("width", "Улица Ватутина · ширина по OSM",
         roads_by_id["osm:101397004:1170911342:10301972334"]),
        ("facility", "Детская краевая больница · точка OSM", facility),
        ("contract", "Мишенная улица · участок для сверки договора",
         roads_by_id["osm:94434670:1096962415:1096962254"]),
        ("yard", "Дворовой подъезд · вне дорожного графа", yard),
        ("winter", "Улица Фрунзе · сценарное перекрытие", near_frunze),
    ]
    if bridge_anchor:
        cases.append(("overpass", "Северо-Восточное шоссе · район проектного путепровода",
                      bridge_anchor))
    context = [[metric(p) for p in f["geometry"]["coordinates"]] for f in roads]
    out = root / "cases"
    out.mkdir(exist_ok=True)
    indexes = []
    for name, title, target in cases:
        geometry = target["geometry"]
        points = ([metric(geometry["coordinates"])] if geometry["type"] == "Point"
                  else [metric(p) for p in geometry["coordinates"]])
        center = tuple(sum(axis)/len(axis) for axis in zip(*points))
        local = [[p[0]-center[0], p[1]-center[1]] for p in points]
        radius = 180 if name != "overpass" else 310
        nearby = [[(x-center[0], y-center[1]) for x,y in line] for line in context
                  if any(abs(x-center[0]) <= radius+50 and abs(y-center[1]) <= radius+50 for x,y in line)]
        fig, ax = plt.subplots(figsize=(6.7, 4.8), facecolor="#f4f6f8")
        ax.set_facecolor("#f4f6f8")
        ax.add_collection(LineCollection(nearby, colors="#8293A6", linewidths=1.0))
        xs, ys = zip(*local)
        ax.plot(xs, ys, color="#AA3128", linewidth=3, marker="o", markersize=4)
        ax.set_xlim(-radius, radius)
        ax.set_ylim(-radius, radius)
        ax.set_aspect("equal")
        ax.axis("off")
        ax.plot([-radius+18, -radius+68], [-radius+18]*2, color="#23364b", lw=2)
        ax.annotate("50 м", (-radius+18, -radius+18), xytext=(0, 5),
                    textcoords="offset points", fontsize=8, va="bottom")
        ax.text(radius-16, radius-15, "↑ С", fontsize=9, ha="right", color="#23364b")
        fig.suptitle(fill(title, 48), fontsize=12, weight="bold", y=.97, color="#193348")
        fig.text(.5,.06,"© OpenStreetMap contributors · исследовательский фрагмент",
                 ha="center", fontsize=9, color="#445971")
        fig.savefig(out / f"{name}.png", dpi=145)
        plt.close(fig)
        indexes.append({"case_id": name, "title": title, "feature": target})
    (out / "map_anchors.json").write_text(json.dumps(indexes, ensure_ascii=False, indent=2),
                                         encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    draw(parser.parse_args().output)
