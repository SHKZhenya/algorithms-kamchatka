"""Freeze one real block, its surroundings, and the historical weather boundary."""
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
from scipy.ndimage import distance_transform_edt, gaussian_filter
from shapely import intersects_xy
from shapely.affinity import scale, translate
from shapely.geometry import box, mapping, shape

from city_sim.api import make_app
from city_sim.geodata import polygons

HERE = Path(__file__).resolve().parent
BOUNDS = (158.641, 53.0512, 158.6467, 53.0547)
PADDING = 144
TOP = 220
ROOT = HERE.parents[1]


def prepare(runner):
    w, s, e, n = BOUNDS
    sx = np.pi*6371008.8/180*np.cos(np.deg2rad((s+n)/2))
    sy = np.pi*6371008.8/180
    width, height = (e-w)*sx, (n-s)*sy
    bounds = (w-PADDING/sx, s-PADDING/sy, e+PADDING/sx, n+PADDING/sy)
    core = box(0, 0, width, height)
    data = []
    for item in runner.city.buildings(bounds)["features"]:
        props = item["properties"]
        for outline in polygons(shape(item["geometry"])):
            footprint = scale(translate(outline, xoff=-w, yoff=-s), xfact=sx, yfact=sy, origin=(0,0))
            if footprint.area >= 4:
                row = {"id":item["id"], "height_m":props["height_m"] or 9.0,
                       "height_source":props["height_source"], "address":props.get("address"),
                       "within_core":footprint.intersects(core), "footprint_local_m":mapping(footprint)}
                data.append(row)

    terrain = runner.city.terrain
    # Mask DSM samples near footprints, then fill from surrounding ground and
    # smooth at the DSM scale. This renders a ground estimate, not a surveyed DTM.
    x = np.linspace(-PADDING-60, width+PADDING+60, 84)
    y = np.linspace(-PADDING-60, height+PADDING+60, 84)
    X,Y = np.meshgrid(x,y)
    dsm = terrain.sample(w+X/sx, s+Y/sy)
    mask = ~np.isfinite(dsm)
    for row in data:
        mask |= intersects_xy(shape(row["footprint_local_m"]).buffer(9), X, Y)
    if not (~mask).any():
        raise ValueError("All terrain samples are masked")
    ids = distance_transform_edt(mask, sampling=(y[1]-y[0],x[1]-x[0]),
                                 return_distances=False, return_indices=True)
    ground = gaussian_filter(dsm[tuple(ids)], sigma=1.2)
    datum = float(ground.min())
    core_mask = (X>=0)&(X<=width)&(Y>=0)&(Y<=height)
    weather = {}
    for episode, specifications in {
            "december-2025": (("dec-snow", "2025-12-09T09"), ("dec-west-dry", "2025-12-10T12")),
            "january-2026": (("jan-snow", "2026-01-15T17"), ("jan-calm", "2026-01-16T23")),
            "march-2025": (("mar-strong", "2025-03-24T04"),)}.items():
        source = runner.storms[episode]
        series = min(source["locations"],key=lambda row:
                     np.hypot((row["coordinates"][0]-(w+e)/2)*sx,(row["coordinates"][1]-(s+n)/2)*sy))
        for name, stamp in specifications:
            hour = next(h for h in series["hours"] if h["valid_at"].startswith(stamp))
            weather[name] = {**hour, "episode":episode, "source_cell":series["coordinates"],
                             "weather_sha256":source["raw_sha256"], "weather_url":source["url"],
                             "distance_to_source_m":round(float(np.hypot((series["coordinates"][0]-(w+e)/2)*sx,
                                                                         (series["coordinates"][1]-(s+n)/2)*sy)))}
    spec = {"bounds_wgs84":BOUNDS,"size_m":[width,height],"projection_m_per_degree":[sx,sy],
            "padding_m":PADDING,"domain_top_m_above_datum":TOP,"datum_egm2008_m":datum,
            "terrain_core_egm2008_range_m": [float(ground[core_mask].min()),float(ground[core_mask].max())],
            "terrain_domain_range_above_datum_m":[0.,float(ground.max()-datum)],
            "dsm_mask_fraction":float(mask.mean()),"city_revision":runner.city.data_revision,
            "building_count_core":sum(b["within_core"] for b in data),
            "height_sources":dict(Counter(b["height_source"] for b in data if b["within_core"])),
            "buildings":data, "weather":weather,
            "sources_sha256":{name:sha256((ROOT/"city_sim/data"/name).read_bytes()).hexdigest() for name in (
                "terrain.npz","terrain.json","building-location-checks.json.gz","storm-weather.json.gz")}}
    (HERE/"sources/quarter.json").write_text(json.dumps(spec,ensure_ascii=False,indent=2)+"\n")
    np.savez_compressed(HERE/"sources/ground.npz",x=x,y=y,dsm=dsm,ground=ground-datum,footprints_mask=mask)
    print(json.dumps({k:v for k,v in spec.items() if k!="buildings"},ensure_ascii=False,indent=2))


if __name__ == "__main__":
    with TemporaryDirectory() as tmp:
        prepare(make_app(db_path=Path(tmp)/"db.sqlite").state.city_snow)
