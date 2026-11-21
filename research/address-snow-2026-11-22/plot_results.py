"""Plot saved numerical outputs without repeating the snow simulation."""
import gzip
import json
import os
from pathlib import Path
import xml.etree.ElementTree as ET
os.environ.setdefault('MPLCONFIGDIR','/tmp/kam-matplotlib')
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.colors import TwoSlopeNorm
import numpy as np
from shapely.geometry import shape, Polygon
from shapely import union_all
from shapely.affinity import scale,translate

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]

def main():
    run=json.loads((HERE/'run.json').read_text())
    features=json.loads((HERE/'buildings.geojson').read_text())['features']
    arrays=np.load(HERE/'fields.npz')
    osm=ET.parse(gzip.open(ROOT/'data/pkc/city.osm.gz')).getroot()
    nodes={n.get('id'):(float(n.get('lon')),float(n.get('lat'))) for n in osm.findall('node')}
    buildings=[Polygon([nodes[n.get('ref')] for n in w.findall('nd')]) for w in osm.findall('way')
               if any(t.get('k')=='building' for t in w.findall('tag')) and len(w.findall('nd'))>=4]
    fig,axes=plt.subplots(1,3,figsize=(15,5),layout='constrained')
    stamp='2026-01-16T23:00:00+12:00'
    for ax,case in zip(axes,['SNOW-001','SNOW-002','SNOW-003']):
        chosen=[f for f in features if f['properties']['case_id']==case]
        polygon=union_all([shape(f['geometry']) for f in chosen])
        anchor=np.array(polygon.centroid.coords[0]);sx=111195*np.cos(np.deg2rad(anchor[1]));sy=111195
        for tile in run['tiles']:
            i,j=tile['i'],tile['j'];w,s,e,n=tile['bounds']
            f=arrays[f'tile_{i}_{j}_2026-01-16_23'];bg=tile['frames'][stamp]['background_cm']
            extent=[(w-anchor[0])*sx,(e-anchor[0])*sx,(s-anchor[1])*sy,(n-anchor[1])*sy]
            im=ax.imshow(f-bg,extent=extent,origin='upper',cmap='RdBu_r',norm=TwoSlopeNorm(0,vmin=-100,vmax=100))
        for p in buildings:
            if p.distance(polygon)>.003:continue
            c=np.array(p.exterior.coords);ax.fill((c[:,0]-anchor[0])*sx,(c[:,1]-anchor[1])*sy,color='#333333')
        for p in ([polygon] if polygon.geom_type=='Polygon' else polygon.geoms):
            c=np.array(p.exterior.coords);ax.plot((c[:,0]-anchor[0])*sx,(c[:,1]-anchor[1])*sy,color='#ffcc00',lw=2)
        local=scale(translate(polygon,xoff=-anchor[0],yoff=-anchor[1]),sx,sy,origin=(0,0))
        for radius in [10,20,40]:
            b=local.buffer(radius)
            for p in ([b] if b.geom_type=='Polygon' else b.geoms):
                c=np.array(p.exterior.coords);ax.plot(c[:,0],c[:,1],color='#333333',lw=.6,alpha=.6)
        ax.set(xlim=(-110,110),ylim=(-110,110),aspect='equal',title=chosen[0]['properties']['address'],
               xlabel='Восток, м',ylabel='Север, м')
    fig.colorbar(im,ax=axes,label='Модель минус равномерный снег, см',shrink=.7)
    fig.suptitle('12–16 января 2026 · новый снег · жёлтым выделен проверяемый дом')
    fig.savefig(HERE/'comparison.png',dpi=170)
    plt.close(fig)

if __name__=='__main__':main()
