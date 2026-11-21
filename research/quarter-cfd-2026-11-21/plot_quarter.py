"""Create static research figures and a qualitative address comparison."""
import json
from hashlib import sha256
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection
import numpy as np
from shapely import distance, points, union_all
from shapely.geometry import shape

ROOT=Path(__file__).resolve().parent
OUT=ROOT/"results"
spec=json.loads((ROOT/"sources/quarter.json").read_text())
core_width,core_height=spec["size_m"]
buildings=[shape(row["footprint_local_m"]) for row in spec["buildings"] if row["within_core"]]
house=union_all([shape(row["footprint_local_m"]) for row in spec["buildings"]
                 if row["id"] in {"osm:way:106049024","osm:way:106049027"}])
source_hash=sha256(Path("/tmp/kam-quarter-photo-inspection.webp").read_bytes()).hexdigest() if Path(
    "/tmp/kam-quarter-photo-inspection.webp").exists() else None

with np.load(OUT/"comparison.npz") as a:
    jan="jan-snow-coarse"
    section=a[jan+"__cfd_cm"]
    x,y=np.meshgrid((np.arange(section.shape[1])+.5)*24,(np.arange(section.shape[0])+.5)*24)
    centerpoints=points(x,y)
    dist=distance(centerpoints,house)
    near=(dist>0)&(dist<=24)
    photo={"address":"Петропавловск-Камчатский, просп. 50 лет Октября, 4/2",
           "address_sign_visible":True,"date_local":"2026-01-15","capture_time":None,"facade_bearing":None,
           "asset_lon_lat":[158.64384,53.05285],"vehicle_position_accuracy_m":None,
           "observed_type":"covered_car_and_facade_snow","observed_value_cm":None,
           "known_snow_depth_uncertainty_cm":None,"plow_or_old_snow":"unknown",
           "photo_url":"https://s0.rbk.ru/v6_top_pics/media/img/5/07/347685582741075.webp",
           "publication_url":"https://www.rbc.ru/life/news/696a09209a794762f3e75d3e",
           "photo_sha256":source_hash,"test":"spatial context only",
           "model_hour_local":spec["weather"]["jan-snow"]["valid_at"], "near_bin_center_distance_m":24,
           "model_values":{}}
    for tag in ("cfd_cm","danil_cm","uniform_cm"):
        values=a[jan+"__"+tag][near]
        values=values[np.isfinite(values)]
        photo["model_values"][tag]={"bins":len(values),"min":float(values.min()),"max":float(values.max())}
    (OUT/"photo-4-2-context.json").write_text(json.dumps(photo,ensure_ascii=False,indent=2)+"\n")
    for event, title in [("dec-snow-coarse","9 декабря 2025 · 09:00"),
                         (jan,"15 января 2026 · 17:00")]:
        panels=[("uniform_cm","Равномерный снегопад"),("danil_cm","Модель Данила"),
                ("cfd_cm","Осаждение по полю OpenFOAM"),("cfd_wind_ms","Скорость ветра OpenFOAM на 2 м")]
        fig,axes=plt.subplots(2,2,figsize=(9.6,9.4),layout="constrained")
        fig.suptitle(f"Квартал у проспекта 50 лет Октября, 4/2\n{title} (Камчатка), ячейки 24 м, дом 4/2 выделен",
                     fontsize=15)
        cap=max(float(np.nanquantile(a[event+"__"+key],.97)) for key,_ in panels[:3])
        for ax,(key,header) in zip(axes.ravel(),panels):
            grid=a[event+"__"+key]
            ymax,xmax=np.array(grid.shape)*24
            wind=key.endswith("_ms")
            limit=float(np.nanquantile(grid,.97)) if wind else cap
            img=ax.imshow(grid,extent=(0,xmax,0,ymax),origin="lower",cmap="cividis" if wind else "Blues",
                          vmin=0,vmax=limit,interpolation="nearest")
            ax.add_collection(LineCollection([np.asarray(s.exterior.coords) for s in buildings],
                                              colors="#62636a",linewidths=.45,alpha=.9))
            for poly in house.geoms if house.geom_type=="MultiPolygon" else [house]:
                xh,yh=poly.exterior.xy
                ax.plot(xh,yh,color="#ac321b",linewidth=1.15)
            ax.set_xlim(0,core_width)
            ax.set_ylim(0,core_height)
            ax.set_title(header,fontsize=10)
            ax.set_xlabel("Восток, м")
            ax.set_ylabel("Север, м")
            fig.colorbar(img,ax=ax,shrink=.74,pad=.037,label="м/с" if wind else "см",extend="max")
        fig.savefig(OUT/f"{event}-comparison.png",dpi=140)
        plt.close(fig)
