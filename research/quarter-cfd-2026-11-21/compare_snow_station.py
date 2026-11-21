"""Decode complete WMO SYNOP reports, then compare like-for-like weather fields."""
import csv
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory

import numpy as np
from shapely.affinity import scale, translate
from shapely.geometry import shape

from city_sim.api import make_app
from city_sim.geodata import polygons
from snowmap.model import estimate
from snowmap.sources import Building, projection

HERE = Path(__file__).resolve().parent
SOURCES = HERE/"sources"
OUTPUT = HERE/"results"
STATION = (158.6552,52.9897)
READINGS = "https://www.pogodaiklimat.ru/station/32583.htm"
LOCAL = timezone(timedelta(hours=12))


def load_telegrams():
    records = {}
    requests = json.loads((SOURCES/"fetch.json").read_text())
    for source in sorted(SOURCES.glob("*.txt")):
        if "ogimet" not in source.name and not source.name.startswith(("jan-","dec-edge")):
            continue
        with source.open() as file:
            for number,line in enumerate(file,1):
                cells = line.strip().split(",",6)
                if len(cells)!=7 or cells[0]!="32583" or not cells[6].endswith("="):
                    continue
                stamp = datetime(*map(int,cells[1:6]),tzinfo=timezone.utc)
                raw = cells[6].rstrip("=").split()
                if raw[:1]!=["AAXX"] or raw[2]!="32583" or raw[1][:4]!=stamp.strftime("%d%H"):
                    continue
                if stamp in records:
                    if records[stamp]["raw_groups"]!=" ".join(raw):
                        raise ValueError(f"Conflicting duplicate SYNOP: {stamp}")
                    continue
                region = raw[raw.index("333")+1:] if "333" in raw else []
                groups = []
                for token in region:
                    if token in {"444","555"}:
                        break
                    groups.append(token)
                ground = [g for g in groups if re.fullmatch(r"4[0-9/][0-9/]{3}",g)]
                snow = ground[0] if len(ground)==1 else None
                depth = int(snow[2:]) if snow and snow[2:].isdigit() and 1<=int(snow[2:])<=996 else None
                fresh_groups = [g for g in groups if re.fullmatch(r"931[0-9/]{2}",g)]
                wind = raw[4]
                speed = int(wind[3:]) if wind[3:].isdigit() else None
                if speed==99:
                    speed = int(raw[5][2:]) if re.fullmatch(r"00\d{3}",raw[5]) else None
                mode=raw[1][-1]
                if speed is not None:
                    speed = speed * (1 if mode in ("0","1") else .514444) if mode in "0134" else None
                bearing = int(wind[1:3])*10%360 if wind[1:3].isdigit() and 1<=int(wind[1:3])<=36 else None
                temp_group=next((g for g in raw[5:] if re.fullmatch(r"1[01]\d{3}",g)),None)
                temp = int(temp_group[2:])/10*(-1 if temp_group[1]=="1" else 1) if temp_group else None
                records[stamp]={"station_wmo":"32583","lon":STATION[0],"lat":STATION[1],
                                "coordinate_source":READINGS,
                                "time_utc":stamp.isoformat(),"time_local":stamp.astimezone(LOCAL).isoformat(),
                                "wind_ms":speed,"wind_from_deg":bearing,"temperature_c":temp,"snow_group":snow,
                                "ground_state_wmo0975":snow[1] if snow else None,"total_snow_cm":depth,
                                "encoding_step_cm":1 if depth is not None else None,
                                "instrument_uncertainty_cm":None,
                                "possible_fresh_snow_groups":" ".join(fresh_groups) or None,
                                "source_file":source.name,"source_line":number,"raw_groups":" ".join(raw),
                                "source_url":requests.get(source.name,{}).get("url"),
                                "source_sha256":sha256(source.read_bytes()).hexdigest()}
    return sorted(records.values(),key=lambda r:r["time_utc"])


def load_infoclimat():
    from bs4 import BeautifulSoup

    meta = json.loads((SOURCES/"archive-followup.json").read_text())
    if (SOURCES/"infoclimat-recovery.json").exists():
        meta.update(json.loads((SOURCES/"infoclimat-recovery.json").read_text()))
    records = []
    for source in sorted(SOURCES.glob("ic-jan*.html")):
        body = source.read_bytes()
        # Keep a truncated last row only when both the UTC header and the
        # complete quoted snow value survived transmission.
        complete=re.findall(rb"<tr(?: [^>]*)?>.*?</tr>",body,re.S)
        last=body[body.rfind(b"</tr>")+5:]
        partial=re.search(rb"<tr(?: [^>]*)?>.*",last,re.S)
        if not partial and b'de neige au sol"' in body:
            # Some attributes contain embedded table markup. Anchor on the
            # body's data row marker, then use the last available station term.
            tail=body.rfind(b"<!-- heure -->")
            fragment=body[tail:] if tail>=0 else b""
            if fragment and b"</body>" not in body and b'de neige au sol"' in fragment:
                partial_value=re.search(rb'<a[^>]+title="([0-9]+cm de neige au sol)"',fragment)
                if partial_value:
                    complete.append(fragment[:partial_value.end()]+b">x</a></td></tr>")
        elif partial and b'de neige au sol"' in partial[0]:
            partial_value=re.search(rb'<a[^>]+title="([0-9]+cm de neige au sol)"',partial[0])
            if partial_value:
                complete.append(partial[0][:partial_value.end()]+b">x</a></td></tr>")
        for raw in complete:
            if b"Heure r" not in raw or b"h00 UTC" not in raw:
                continue
            markup=BeautifulSoup(raw,"html.parser")
            header=markup.select_one("th span[title]")
            if not header:
                continue
            time=re.search(r"(\d{2}/\d{2}/\d{4}).*?(\d{2})h(\d{2}) UTC",header["title"])
            if not time:
                continue
            stamp = datetime.strptime(" ".join(time.groups()),"%d/%m/%Y %H %M").replace(tzinfo=timezone.utc)
            cells=markup.select("td")
            if len(cells)<3:
                continue
            snow=cells[2].select_one('a[title$="de neige au sol"]')
            value=re.fullmatch(r"(\d+)\s*cm de neige au sol",snow.get("title","")) if snow else None
            t=re.search(r"(-?\d+(?:\.\d+)?)\s*°C",cells[0].get_text(" ",strip=True))
            wind_cell=cells[3] if len(cells)>3 else None
            wind=re.search(r"(\d+(?:\.\d+)?) km/h",wind_cell.get_text(" ",strip=True)) if wind_cell else None
            bearing=next((re.search(r"Vent de direction (\d+)°",d["title"]) for d in wind_cell.select("div[title]")
                          if d["title"].startswith("Vent de direction ")),None) if wind_cell else None
            records.append({"station_wmo":"32583","lon":STATION[0],"lat":STATION[1],"coordinate_source":READINGS,
                            "time_utc":stamp.isoformat(),"time_local":stamp.astimezone(LOCAL).isoformat(),
                            "wind_ms":float(wind[1])/3.6 if wind else None,
                            "wind_from_deg":int(bearing[1])%360 if bearing else None,
                            "temperature_c":float(t[1]) if t else None,
                            "snow_group":None,"ground_state_wmo0975":None,
                            "total_snow_cm":int(value[1]) if value else None,
                            "encoding_step_cm":1 if value else None,"instrument_uncertainty_cm":None,
                            "possible_fresh_snow_groups":None,"source_file":source.name,"source_line":None,
                            "raw_groups":None,"source_url":meta[source.name]["url"],
                            "source_sha256":sha256(body).hexdigest()})
    return records


def write_csv(name, rows):
    if rows:
        with (OUTPUT/name).open("w",newline="") as handle:
            writer=csv.DictWriter(handle,fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)


def compare(records, runner):
    lon,lat=STATION
    sx,sy,_,_=projection((lon-.001,lat-.001,lon+.001,lat+.001))
    west,south=lon-250/sx,lat-250/sy
    bounds=(west,south,lon+250/sx,lat+250/sy)
    features=runner.city.buildings((west-360/sx,south-360/sy,bounds[2]+360/sx,bounds[3]+360/sy))["features"]
    buildings=[Building(scale(translate(poly,xoff=-west,yoff=-south),xfact=sx,yfact=sy,origin=(0,0)),
                        row["properties"]["height_m"] or 9.,row["properties"]["height_source"])
               for row in features for poly in polygons(shape(row["geometry"]))]
    with (SOURCES/"quarter.json").open() as handle:
        quarter = json.load(handle)
    comparison=[]
    weather_rows=[]
    previous=None
    for record in records:
        stamp=datetime.fromisoformat(record["time_local"])
        episode="december-2025" if stamp.year==2025 else "january-2026"
        source=runner.storms[episode]
        near=min(source["locations"], key=lambda p:np.hypot((p["coordinates"][0]-lon)*sx,(p["coordinates"][1]-lat)*sy))
        indexed={datetime.fromisoformat(hr["valid_at"]):hr for hr in near["hours"]}
        if hr:=indexed.get(stamp):
            weather_rows.append({"time_local":stamp.isoformat(),"station_wmo":record["station_wmo"],
                                 "observed_temp_c":record["temperature_c"],"ifs_temp_c":hr["temperature_c"],
                                 "observed_speed_ms":record["wind_ms"],"ifs_speed_ms":hr["wind_speed_ms"],
                                 "observed_from_deg":record["wind_from_deg"],"ifs_from_deg":hr["wind_from_deg"],
                                 "ifs_cell_lon":near["coordinates"][0],"ifs_cell_lat":near["coordinates"][1],
                                 "source_url":record["source_url"]})
        if record["total_snow_cm"] is None:
            continue
        hours=[indexed.get(stamp-timedelta(hours=h)) for h in range(23,-1,-1)]
        valid=all(h is not None for h in hours)
        modeled=baseline=local_min=local_max=None
        if valid:
            grid=np.asarray(estimate(bounds,buildings,hours,64,True)["snow_depth_cm"],dtype=float)
            height,width=grid.shape
            # The exact station anchor lies at the centre of the georeferenced raster.
            j,i=int(height/2),int(width/2)
            x,y=np.meshgrid((np.arange(width)+.5)*500/width-250,(np.arange(height)+.5)*500/height-250)
            around=grid[(x*x+y*y<=100**2)&np.isfinite(grid)]
            if len(around):
                local_min,local_max=float(around.min()),float(around.max())
            if np.isfinite(grid[j,i]):
                modeled=float(grid[j,i])
            baseline=round(sum(h["snowfall_cm"] for h in hours),3)
        delta=None
        if previous and stamp-previous[0]==timedelta(days=1):
            delta=record["total_snow_cm"]-previous[1]
        previous=(stamp,record["total_snow_cm"])
        comparison.append({**record,"observed_type":"total_snow_cm","total_depth_change_24h_cm":delta,
                           "model_window_start_local":(stamp-timedelta(days=1)).isoformat(),
                           "model_window_end_local":stamp.isoformat(),
                           "ifs_input_hours_present":sum(h is not None for h in hours),
                           "danil_fresh_snow_24h_cm":modeled,"uniform_fresh_snow_24h_cm":baseline,
                           "danil_min_within_100m_cm":local_min,"danil_max_within_100m_cm":local_max,
                           "comparison_for_error":"incompatible_total_vs_fresh","absolute_error_cm":None})
    write_csv("station-synop-decoded.csv",records)
    write_csv("station-snow-context.csv",comparison)
    write_csv("station-weather-pairs.csv",weather_rows)
    by_timestamp={r["time_local"]:r for r in records}
    quarter_wind=[]
    for label,hour in quarter["weather"].items():
        obs=by_timestamp.get(hour["valid_at"],{})
        quarter_wind.append({"case":label,"model_hour_local":hour["valid_at"],
                             "ifs_speed_ms":hour["wind_speed_ms"],"ifs_wind_from_deg":hour["wind_from_deg"],
                             "station_wind_ms_same_term":obs.get("wind_ms"),
                             "station_wind_from_deg_same_term":obs.get("wind_from_deg"),
                             "station_source_url":obs.get("source_url"),
                             "station_to_quarter_m":round(float(np.hypot((lon-(quarter["bounds_wgs84"][0]+quarter["bounds_wgs84"][2])/2)*sx,
                                                            (lat-(quarter["bounds_wgs84"][1]+quarter["bounds_wgs84"][3])/2)*sy))),
                             "quarter_cfd_wind_ground_truth":None})
    write_csv("quarter-vs-station-wind.csv",quarter_wind)


if __name__=="__main__":
    synop=load_telegrams()
    assert len(synop)>=72
    records={r["time_utc"]:r for r in load_infoclimat()}
    records.update((r["time_utc"],r) for r in synop)
    records=sorted(records.values(),key=lambda r:r["time_utc"])
    with TemporaryDirectory() as tmp:
        compare(records,make_app(db_path=Path(tmp)/"db.sqlite").state.city_snow)
