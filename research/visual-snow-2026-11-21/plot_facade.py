"""Plot the panorama point, matched wall and model cell in local metres."""
import json
from math import cos, radians, sin
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.patches import Polygon, Rectangle

HERE = Path(__file__).resolve().parent
spec = json.loads((HERE.parent / "quarter-cfd-2026-11-21/sources/quarter.json").read_text())
wall = json.loads((HERE / "model_samples.json").read_text())["quarter_4_2"]["northwest_wall"]
pano = json.loads((HERE / "sources/map_reference.json").read_text())
west, south, _, _ = spec["bounds_wgs84"]
sx, sy = spec["projection_m_per_degree"]
lon, lat = pano["point_lon_lat"]
xp, yp = (lon-west)*sx, (lat-south)*sy
cell, = wall["cells"]
x, y = cell["center_local_m"]

fig, ax = plt.subplots(figsize=(6.2, 5.1), layout="constrained")
for b in spec["buildings"]:
    points = b["footprint_local_m"]["coordinates"][0]
    ax.add_patch(Polygon(points, facecolor="#dce4eb", edgecolor="#7b8690", lw=.65, zorder=1))
ax.add_patch(Rectangle((x-12, y-12), 24, 24, ec="#336f94", fc="#b9def1", alpha=.55, zorder=2))
ax.scatter(x, y, s=29, color="#084b72", zorder=3)
points = wall["wall_local_m"]
ax.plot(*zip(*points), lw=3, color="#a83b1b", zorder=4)
ax.scatter(xp, yp, s=27, color="#166949", zorder=6)
ax.annotate("Панорама Яндекса", (xp,yp), (111,204), arrowprops={"arrowstyle":"-","color":"#166949"},
            fontsize=9, color="#166949", zorder=8)
ax.annotate("Ячейка 24 × 24 м", (x,y), (115,252), arrowprops={"arrowstyle":"-","color":"#084b72"},
            fontsize=9, color="#084b72")
ax.annotate("Торец 318°", points[0], (178,240), color="#a83b1b", fontsize=9,
            arrowprops={"arrowstyle":"-","color":"#a83b1b"})
angle = radians(wall["outward_bearing_degrees"])
mx, my = [(first+last)/2 for first, last in zip(*points)]
ax.annotate("", (mx+9*sin(angle), my+9*cos(angle)), (mx, my),
            arrowprops={"arrowstyle":"->", "color":"#a83b1b", "lw":1})
ax.text(174,191,"4/2",color="#263344",fontsize=10)
ax.set(xlim=(106,242), ylim=(146,260), xlabel="Восток, м", ylabel="Север, м",
       title="Привязка северо-западного торца дома 4/2")
ax.set_aspect("equal")
fig.savefig(HERE / "facade-42.png", dpi=150)
