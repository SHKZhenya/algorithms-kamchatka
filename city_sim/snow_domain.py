"""Metric finite-volume domain and polygon support; source geometry stays explicit."""
import math
import numpy as np
from shapely import STRtree, intersects_xy
from shapely.geometry import box, shape
from shapely.ops import transform, unary_union
from weather_coverage.geography import TO_METRIC, TO_WGS84
from .snow_state import SnowState


def create_state(bounds,rows,cols,loose,bound,density=200.,density_min=100.,density_max=400.,city=None,parameters=None,patches=()):
    domain=transform(TO_METRIC,box(*bounds))
    w,s,e,n=domain.bounds
    dx,dy=(e-w)/cols,(n-s)/rows
    buildings=[]
    if city:
        buildings=[transform(TO_METRIC,shape(f['geometry'])) for f in city.osm_layers[0].query(bounds)]
        water=[transform(TO_METRIC,shape(f['geometry'])) for f in city.osm_layers[1]['features'] if f['properties'].get('kind')=='water']
        obstacles=buildings+water
    else: obstacles=[]
    tree=STRtree(obstacles)
    area=np.zeros((rows,cols))
    for j in range(rows):
        for i in range(cols):
            cell=box(w+i*dx,n-(j+1)*dy,w+(i+1)*dx,n-j*dy).intersection(domain)
            ids=tree.query(cell,predicate='intersects')
            if len(ids): cell=cell.difference(unary_union([obstacles[k] for k in ids]))
            area[j,i]=cell.area
    if not np.any(area>0): raise ValueError('No open ground in territory')
    def grid(value):
        a=np.broadcast_to(np.asarray(value,dtype=float),(rows,cols)).copy()
        if not np.isfinite(a).all() or (a<0).any(): raise ValueError('Initial mass must be finite, nonnegative; unknown is not zero')
        return np.where(area>0,a,0.)
    state=SnowState((w,s,e,n),grid(loose),grid(bound),area,np.ones_like(area),np.full_like(area,np.nan),density,density_min,density_max)
    state.ground=domain.difference(unary_union(obstacles)) if obstacles else domain
    if parameters: state.parameters.update(parameters)
    for patch in patches:
        weights=state.overlaps(transform(TO_METRIC,shape(patch['geometry'])))
        share=np.divide(weights,area,out=np.zeros_like(area),where=area>0)
        for field in ('loose','bound'):
            if field+'_kg_m2' in patch:
                a=getattr(state,field);a*=1-share;a+=float(patch[field+'_kg_m2'])*share
    state.ledger['initial_kg']=state.total_kg()
    return state


def attach_ground(state,bounds,city):
    domain=transform(TO_METRIC,box(*bounds))
    obstacles=[transform(TO_METRIC,shape(f['geometry'])) for f in city.osm_layers[0].query(bounds)]
    obstacles.extend(transform(TO_METRIC,shape(f['geometry'])) for f in city.osm_layers[1]['features'] if f['properties'].get('kind')=='water')
    state.ground=domain.difference(unary_union(obstacles))


def shelter(state,city,wind_from_deg,unknown_height_m=None):
    """Directional reduced wake closure, NOT an inferred CFD velocity field.

    No height is invented: unknown footprints still block storage; only an explicit
    scenario height can add their unresolved shelter effect.
    """
    w,s,e,n=state.bounds;rows,cols=state.area.shape
    angle=math.radians(wind_from_deg);fx,fy=-math.sin(angle),-math.cos(angle)
    state.exposure[:]=1.
    used=unknown=0
    bounds=transform(TO_WGS84,box(w-250,s-250,e+250,n+250)).bounds
    for b in city.osm_layers[0].query(bounds):
        p=b['properties'];h=p.get('height_m')
        if not h:
            unknown+=1;h=unknown_height_m
        if not h: continue
        h=min(float(h),80.)
        geom=transform(TO_METRIC,shape(b['geometry']));center=geom.centroid
        reach=min(250,8*h)
        x0,y0,x1,y1=geom.buffer(reach).bounds
        il,ih=max(0,int((x0-w)/state.dx)),min(cols,int(math.ceil((x1-w)/state.dx)))
        jl,jh=max(0,int((n-y1)/state.dy)),min(rows,int(math.ceil((n-y0)/state.dy)))
        if il>=ih or jl>=jh: continue
        x=w+(np.arange(il,ih)+.5)*state.dx;y=n-(np.arange(jl,jh)+.5)*state.dy
        X,Y=np.meshgrid(x-center.x,y-center.y)
        along=X*fx+Y*fy;cross=-X*fy+Y*fx
        radius=math.sqrt(geom.area)/2
        down=np.maximum(along-radius,0)
        effect=np.where(along>radius,.7*np.exp(-down/(4*h))*np.exp(-(cross/(radius+h*.5+down*.1))**2),0)
        state.exposure[jl:jh,il:ih]=np.minimum(state.exposure[jl:jh,il:ih],1-effect)
        used+=1
    return {'method':'directional_exponential_shelter_research_closure','buildings_used':used,
            'unknown_heights':unknown,'unknown_height_scenario_m':unknown_height_m}
