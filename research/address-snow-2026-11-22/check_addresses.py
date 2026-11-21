"""Reproduce address-scale checks using the city adapter and pinned snow engine.

Run from repository root. CSV files are analytical intermediates, not workbooks.
"""
import csv
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import xml.etree.ElementTree as ET

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault('MPLCONFIGDIR', '/tmp/kam-matplotlib')
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.colors import TwoSlopeNorm
import numpy as np
from shapely import distance, points, contains_xy, union_all
from shapely.affinity import scale, translate
from shapely.geometry import Polygon, mapping
from city_sim.api import make_app
from city_sim.city_snow import remap

CASES = {'SNOW-001': ('Бохняка, 3', ['102609577']),
         'SNOW-002': ('50 лет Октября, 4/2', ['106049027', '106049024']),
         'SNOW-003': ('Рыбаков, 13/2', ['106795218'])}
ARCHIVE = ROOT / 'out/citywide-storms-full'
RUN = '1c49fd9523125e8290948015c37b7417c6aa77cbced65c64457dea8d1039f605'
STAMPS = ['2026-01-15T11:00:00+12:00', '2026-01-15T23:00:00+12:00',
          '2026-01-16T23:00:00+12:00', '2026-01-17T23:00:00+12:00',
          '2026-01-19T11:00:00+12:00', '2026-01-19T23:00:00+12:00']


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')


def main():
    (HERE / 'sources').mkdir(exist_ok=True)
    for extension in ['csv', 'md']:
        source = Path('/Users/konstantin/Downloads') / ('KAMCHATKA_SNOWDRIFT_VALIDATION_CASES.' + extension)
        target = HERE / 'sources' / source.name
        if source.exists() and not target.exists():
            shutil.copyfile(source, target)
    with gzip.open(ROOT / 'data/pkc/city.osm.gz') as f:
        osm = ET.parse(f).getroot()
    nodes = {n.get('id'): (float(n.get('lon')), float(n.get('lat'))) for n in osm.findall('node')}
    ways = {w.get('id'): w for w in osm.findall('way')}
    features = []
    targets = {}
    for case, (name, identifiers) in CASES.items():
        polygons = []
        for identifier in identifiers:
            way = ways[identifier]
            tags = {t.get('k'): t.get('v') for t in way.findall('tag')}
            polygon = Polygon([nodes[n.get('ref')] for n in way.findall('nd')])
            polygons.append(polygon)
            features.append({'type': 'Feature', 'geometry': mapping(polygon),
                'properties': {'case_id': case, 'address': name, 'osm_way': identifier,
                    'url': f'https://www.openstreetmap.org/way/{identifier}', 'tags': tags}})
        targets[case] = union_all(polygons)
    write_json(HERE / 'buildings.geojson', {'type': 'FeatureCollection', 'features': features})

    raw = json.load(gzip.open(ARCHIVE / (RUN + '.json.gz')))
    archive = np.load(ARCHIVE / (RUN + '.npz'))
    archive_stamps = [f['valid_at'] for f in raw['wind_frames']]
    assert len(archive_stamps) == len(archive['timeline']) + 1
    extended_path = HERE / 'sources/ifs-2026-01-12--19.json'
    extended = json.loads(extended_path.read_text()) if extended_path.exists() else None
    app = make_app(db_path='/tmp/kam-address-validation.sqlite3')
    runner = app.state.city_snow
    model, _ = runner.study.engine  # Validates the engine lock hashes.
    west, south, east, north = raw['bounds']
    rows, cols, cells = raw['rows'], raw['cols'], raw['tile_cells']
    nx, ny = cols // cells, rows // cells
    xs = west + (np.arange(cols) + .5) * (east-west) / cols
    ys = north - (np.arange(rows) + .5) * (north-south) / rows
    needed = set()
    for polygon in targets.values():
        w, s, e, n = polygon.bounds
        for j in range(ny):
            for i in range(nx):
                tw, te = west+i*(east-west)/nx, west+(i+1)*(east-west)/nx
                tn, ts = north-j*(north-south)/ny, north-(j+1)*(north-south)/ny
                if tw <= e+.001 and te >= w-.001 and ts <= n+.0006 and tn >= s-.0006:
                    needed.add((i, j))
    frames, metadata, arrays = {}, [], {}
    for i, j in sorted(needed):
        tile = next(t for t in raw['tiles'] if (t['i'], t['j']) == (i, j))
        bounds = (west+i*(east-west)/nx, north-(j+1)*(north-south)/ny,
                  west+(i+1)*(east-west)/nx, north-j*(north-south)/ny)
        buildings, ground, _ = runner.tile_geometry(bounds, cells, runner.city.data_revision)
        hours = tile['hours']
        if extended:
            center = ((bounds[0]+bounds[2])/2, (bounds[1]+bounds[3])/2)
            from winter_audit.relief import distance as geographic_distance
            source = min(extended, key=lambda r: geographic_distance(center, (r['longitude'], r['latitude'])))
            h = source['hourly']
            assert source['hourly_units']['snowfall'] == 'cm'
            assert source['hourly_units']['wind_speed_10m'] == 'm/s'
            assert source['utc_offset_seconds'] == 43200
            hours = [{'valid_at': t+':00+12:00', 'snowfall_cm': h['snowfall'][k],
                'wind_speed_ms': h['wind_speed_10m'][k], 'wind_from_deg': h['wind_direction_10m'][k],
                'temperature_c': h['temperature_2m'][k]} for k, t in enumerate(h['time'])]
            assert all(all(h[k] is not None for k in ['snowfall_cm', 'wind_speed_ms', 'wind_from_deg']) for h in hours)
            old = tile['hours']
            delta = max(abs(a[k]-b[k]) for a,b in zip(old,hours) for k in ['snowfall_cm','wind_speed_ms','wind_from_deg'])
        else:
            delta = None
        record = {'i': i, 'j': j, 'bounds': bounds, 'building_count': len(buildings),
            'archive_input_max_abs_difference': delta, 'hours': hours, 'frames': {}}
        for stamp in STAMPS:
            if stamp > hours[-1]['valid_at']:
                continue
            prefix = [h for h in hours if h['valid_at'] <= stamp]
            def estimate(b, h):
                field = remap(model.estimate(bounds, b, h, cells, True)['snow_depth_cm'], cells)
                return np.where(ground, field, np.nan)
            field = estimate(buildings, prefix)
            bg = sum(h['snowfall_cm'] for h in prefix)
            frames[i,j,stamp,'model'] = field
            frames[i,j,stamp,'background'] = np.where(np.isfinite(field), bg, np.nan)
            arrays[f'tile_{i}_{j}_{stamp[:10]}_{stamp[11:13]}'] = field
            if stamp == STAMPS[2]:
                for name, b, h in [('reverse', buildings, [{**h, 'wind_from_deg': (h['wind_from_deg']+180)%360} for h in prefix]),
                                   ('calm', buildings, [{**h, 'wind_speed_ms': 0} for h in prefix]),
                                   ('no_buildings', [], prefix)]:
                    frames[i,j,stamp,name] = np.where(np.isfinite(field), estimate(b,h), np.nan)
            record['frames'][stamp] = {'background_cm': bg}
        metadata.append(record)
        print('Completed tile', i, j, flush=True)

    summary, samples = [], []
    for case, polygon in targets.items():
        anchor = np.array(polygon.centroid.coords[0])
        sx, sy = 111195*np.cos(np.deg2rad(anchor[1])), 111195
        local = scale(translate(polygon, xoff=-anchor[0], yoff=-anchor[1]), sx, sy, origin=(0,0))
        coords = []
        for i,j in sorted(needed):
            lon,lat = np.meshgrid(xs[i*cells:(i+1)*cells], ys[j*cells:(j+1)*cells])
            x,y = (lon-anchor[0])*sx, (lat-anchor[1])*sy
            d = distance(points(x,y), local)
            mask = (d>0) & (d<=40) & ~contains_xy(local,x,y)
            for a,b in zip(*np.where(mask)):
                coords.append((i,j,int(a),int(b),float(d[a,b]),float(lon[a,b]),float(lat[a,b]),float(x[a,b]),float(y[a,b])))
        for stamp in STAMPS:
            for variant in ['model','background','reverse','calm','no_buildings','archive']:
                values = []
                for i,j,a,b,d,lon,lat,x,y in coords:
                    field = frames.get((i,j,stamp,variant))
                    if variant == 'archive' and stamp in archive_stamps:
                        field = archive['snow_cm'] if stamp == archive_stamps[-1] else archive['timeline'][archive_stamps.index(stamp)]
                        value = float(field[j*cells+a,i*cells+b])
                    elif field is not None:
                        value = float(field[a,b])
                    else:
                        continue
                    if np.isfinite(value):
                        values.append((value,d,lon,lat,x,y))
                        samples.append({'case_id':case,'time':stamp,'variant':variant,'row':j*cells+a,'col':i*cells+b,
                            'distance_to_building_m':d,'lon':lon,'lat':lat,'snow_cm':value})
                for radius in [5,10,20,40]:
                    chosen = [v for v in values if v[1]<=radius]
                    if chosen:
                        maximum = max(chosen)
                        summary.append({'case_id':case,'address':CASES[case][0],'time':stamp,'variant':variant,
                            'radius_m':radius,'cells':len(chosen),'min_cm':min(v[0] for v in chosen),
                            'median_cm':float(np.median([v[0] for v in chosen])), 'max_cm':maximum[0],
                            'max_lon':maximum[2], 'max_lat':maximum[3],
                            'max_side':('N' if maximum[5]>=0 else 'S')+('E' if maximum[4]>=0 else 'W')})
        print(case, 'centroid',anchor.tolist(), flush=True)

    for filename, records in [('comparison.csv', summary), ('samples.csv', samples)]:
        with (HERE / filename).open('w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=list(records[0]))
            writer.writeheader(); writer.writerows(records)
    write_json(HERE / 'run.json', {'engine_commit':runner.study.provenance and '087118ff9119eaa211fa19e89251b73f232406da',
        'engine_sha256':runner.study.provenance, 'city_revision':runner.city.data_revision,
        'archive_commit':raw['provenance']['engine_commit'], 'archive_run_id':RUN,
        'archive_sha256':sha(ARCHIVE/(RUN+'.json.gz')), 'osm_sha256':sha(ROOT/'data/pkc/city.osm.gz'),
        'weather_extended':bool(extended), 'initial_snow_cm':0,'clearing':False,
        'compaction_or_melt':False,'grid_size_m':[(east-west)/cols*111195*np.cos(np.deg2rad(53.06)),(north-south)/rows*111195],
        'stamps':STAMPS,'tiles':metadata})
    np.savez_compressed(HERE/'fields.npz',**arrays)
    # A fixed date and colour scale permit side-by-side comparison without fitting observations.
    fig,axes=plt.subplots(1,3,figsize=(15,5),layout='constrained')
    stamp=STAMPS[2]
    for ax,(case,polygon) in zip(axes,targets.items()):
        anchor=np.array(polygon.centroid.coords[0]); sx=111195*np.cos(np.deg2rad(anchor[1])); sy=111195
        for i,j in sorted(needed):
            f=frames[i,j,stamp,'model']; bg=frames[i,j,stamp,'background']
            ex=[(west+i*(east-west)/nx-anchor[0])*sx,(west+(i+1)*(east-west)/nx-anchor[0])*sx,
                (north-(j+1)*(north-south)/ny-anchor[1])*sy,(north-j*(north-south)/ny-anchor[1])*sy]
            im=ax.imshow(f-bg,extent=ex,origin='upper',cmap='RdBu_r',norm=TwoSlopeNorm(0,vmin=-100,vmax=100))
        nearby=runner.city.buildings((anchor[0]-.002,anchor[1]-.0015,anchor[0]+.002,anchor[1]+.0015))['features']
        from shapely.geometry import shape
        from city_sim.geodata import polygons as parts
        for feature in nearby:
            for p in parts(shape(feature['geometry'])):
                c=np.array(p.exterior.coords); ax.fill((c[:,0]-anchor[0])*sx,(c[:,1]-anchor[1])*sy,color='#333333')
        for p in ([polygon] if polygon.geom_type=='Polygon' else polygon.geoms):
            c=np.array(p.exterior.coords); ax.plot((c[:,0]-anchor[0])*sx,(c[:,1]-anchor[1])*sy,color='#ffcc00',lw=2)
        for radius in [10,20,40]:
            b=scale(translate(polygon,xoff=-anchor[0],yoff=-anchor[1]),sx,sy,origin=(0,0)).buffer(radius)
            for p in ([b] if b.geom_type=='Polygon' else b.geoms):
                c=np.array(p.exterior.coords);ax.plot(c[:,0],c[:,1],color='#333333',lw=.6,alpha=.6)
        ax.set(xlim=(-110,110),ylim=(-110,110),aspect='equal',title=CASES[case][0],xlabel='Восток, м',ylabel='Север, м')
    fig.colorbar(im,ax=axes,label='Модель минус равномерный снег, см',shrink=.7)
    fig.suptitle('12–16 января 2026 · новый снег · жёлтым выделен проверяемый дом')
    fig.savefig(HERE/'comparison.png',dpi=170)
    plt.close(fig)
    print('Saved',len(summary),'summary rows;',len(samples),'cell samples',flush=True)


if __name__=='__main__':
    main()
