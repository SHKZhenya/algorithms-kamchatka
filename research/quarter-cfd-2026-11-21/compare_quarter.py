"""Compare flat snowfall, Danil's template model and deposition in 3-D RANS wind."""
import csv
import json
import math
from pathlib import Path

import numpy as np
from scipy.ndimage import map_coordinates
from scipy.stats import spearmanr
from shapely import intersects_xy
from shapely.geometry import box, shape

from snowmap.model import estimate
from snowmap.sources import Building

HERE = Path(__file__).resolve().parent
RESULTS = HERE/"results"


def settle(grid, speed, bearing, snow, fall=1.0, seeds=3):
    """Advect uniformly supplied parcels with a fixed settling speed.

    Weights are equivalent deposited centimetres times planimetric area.
    Parcels stick at first contact. Supply enters from above and from inflow faces.
    """
    ux = np.asarray(grid["U"],dtype=float)
    nz,ny,nx,_ = ux.shape
    xs,ys = grid["x"],grid["y"]
    dx,dy = xs[1]-xs[0],ys[1]-ys[0]
    top=float(grid["top"])
    z_edges=grid["z_edges"]
    ground=grid["ground"]
    x0,y0=xs[0]-dx/2,ys[0]-dy/2
    x1,y1=xs[-1]+dx/2,ys[-1]+dy/2
    fluid=np.isfinite(ux[...,0])
    floor_index=np.argmax(fluid,axis=0)
    floor = ground+(top-ground)*z_edges[floor_index]
    deposited=np.zeros((ny,nx))
    if snow==0:
        return np.where(floor_index==0,deposited,np.nan),dict(incoming=0,ground=0,buildings=0,escaped=0,unresolved=0)
    # Cells removed from the flow have zero velocity. First-contact absorption
    # uses the voxel surface. Near the continuous ground use bilinear terrain.
    winds=np.nan_to_num(ux)
    z_centers=.5*(z_edges[:-1]+z_edges[1:])
    angle=math.radians(bearing)
    u,v=-speed*math.sin(angle),-speed*math.cos(angle)
    injection,weights=[],[]
    xx,yy=np.meshgrid(x0+(np.arange(nx*seeds)+.5)*dx/seeds,y0+(np.arange(ny*seeds)+.5)*dy/seeds)
    injection.append(np.column_stack((xx.ravel(),yy.ravel(),np.full(xx.size,top-.001))))
    weights.append(np.full(xx.size,snow*dx*dy/seeds**2))
    for component, face_x in ((u,True),(v,False)):
        if abs(component)<1e-8:
            continue
        if face_x:
            boundary = (x0+.001) if component>0 else (x1-.001)
            base = ground[:,0 if component>0 else -1]
            along = ys
            span = dy
        else:
            boundary = (y0+.001) if component>0 else (y1-.001)
            base = ground[0 if component>0 else -1,:]
            along = xs
            span = dx
        side_layers=nz*(seeds-1)
        levels=(np.arange(side_layers)+.5)/side_layers
        z = base[None,:]+levels[:,None]*(top-base[None,:])
        y = np.broadcast_to(along,z.shape).ravel()
        x = np.full(z.size,boundary)
        injection.append(np.column_stack((x,y,z.ravel()) if face_x else (y,x,z.ravel())))
        cross = np.broadcast_to(span*(top-base)/side_layers,z.shape).ravel()
        weights.append(snow*abs(component)/fall*cross)
    xyz=np.concatenate(injection)
    masses=np.concatenate(weights)
    incoming=float(masses.sum())
    escaped=roofs=0.

    def sample2(field, xy, order):
        coords=np.asarray([(xy[:,1]-ys[0])/dy,(xy[:,0]-xs[0])/dx])
        return map_coordinates(field,coords,order=order,mode="nearest",prefilter=False)

    def velocity(coords):
        local_ground=sample2(ground,coords,1)
        fraction=(coords[:,2]-local_ground)/(top-local_ground)
        k=np.interp(fraction,z_centers,np.arange(nz))
        pos=np.asarray([k,(coords[:,1]-ys[0])/dy,(coords[:,0]-xs[0])/dx])
        values = np.column_stack([map_coordinates(winds[...,j],pos,order=1,mode="nearest",prefilter=False) for j in range(3)])
        values[:,2]-=fall
        return values

    dt=min(.50, .34*min(dx,dy)/max(float(np.max(np.linalg.norm(winds,axis=3))),fall))
    for _ in range(math.ceil(2.5*top/(fall*dt))):
        if not len(xyz):
            break
        mid=xyz+dt/2*velocity(xyz)
        nxt=xyz+dt*velocity(mid)
        inside=(nxt[:,0]>=x0)&(nxt[:,0]<x1)&(nxt[:,1]>=y0)&(nxt[:,1]<y1)&(nxt[:,2]<=top)
        escaped+=float(masses[~inside].sum())
        nxt,masses=nxt[inside],masses[inside]
        i=np.floor((nxt[:,0]-x0)/dx).astype(int)
        j=np.floor((nxt[:,1]-y0)/dy).astype(int)
        building=floor_index[j,i]>0
        surface=np.where(building,floor[j,i],sample2(ground,nxt,1))
        hit=nxt[:,2]<=surface
        landed=hit & ~building
        np.add.at(deposited,(j[landed],i[landed]),masses[landed])
        roofs+=float(masses[hit & building].sum())
        xyz,masses=nxt[~hit],masses[~hit]
    budget={"incoming":incoming,"ground":float(deposited.sum()),"buildings":roofs,
            "escaped":escaped,"unresolved":float(masses.sum()),"step_seconds":dt}
    remainder=(incoming-sum(budget[key] for key in ("ground","buildings","escaped","unresolved")))/incoming
    if abs(remainder)>1e-9:
        raise ValueError(f"Parcel budget does not close: {remainder:.6g}")
    return np.where(floor_index==0,deposited/(dx*dy),np.nan),budget


def binned(field,x,y,area,width,height,step=24.):
    """Aggregate arbitrary cell-centre rasters to shared 24 m comparison bins."""
    a,b=np.meshgrid(x,y)
    valid=(a>=0)&(b>=0)&(a<width)&(b<height)&np.isfinite(field)
    nx,ny=math.ceil(width/step),math.ceil(height/step)
    i,j=(a[valid]//step).astype(int),(b[valid]//step).astype(int)
    values=np.zeros((ny,nx))
    used=np.zeros_like(values)
    np.add.at(values,(j,i),field[valid]*area)
    np.add.at(used,(j,i),area)
    return np.divide(values,used,out=np.full_like(values,np.nan),where=used>=0.30*step**2)


def wind_at_2m(grid):
    """Horizontal speed at a common height above the ground for both meshes."""
    u=grid["U"]
    centers=(grid["z_edges"][:-1]+grid["z_edges"][1:])*.5
    z=2/(float(grid["top"])-grid["ground"])
    hi=np.clip(np.searchsorted(centers,z),1,len(centers)-1)
    j,i=np.indices(z.shape)
    w=np.clip((z-centers[hi-1])/(centers[hi]-centers[hi-1]),0,1)
    velocity=(1-w[...,None])*u[hi-1,j,i,:2]+w[...,None]*u[hi,j,i,:2]
    near=z<centers[0]
    velocity[near]=u[0,j[near],i[near],:2]*(z[near]/centers[0])[:,None]
    return np.linalg.norm(velocity,axis=2)


def evaluate(selected=None):
    spec=json.loads((HERE/"sources/quarter.json").read_text())
    width,height=spec["size_m"]
    outer=box(0,0,width,height)
    all_buildings=[Building(shape(row["footprint_local_m"]),row["height_m"],row["height_source"])
                   for row in spec["buildings"]]
    core_buildings=[b for b in all_buildings if b.footprint.intersects(outer)]
    comparisons,fields={},{}
    if selected:
        comparisons=json.loads((RESULTS/"comparison.json").read_text())["runs"]
        with np.load(RESULTS/"comparison.npz") as prior:
            fields={key:prior[key] for key in prior.files}
    paths=[RESULTS/selected/"summary.json"] if selected else sorted(RESULTS.glob("*/summary.json"))
    for p in paths:
        windfile=p.parent/"wind.npz"
        run=json.loads(p.read_text())
        w=run["weather"]
        snow=w["snowfall_cm"]
        with np.load(windfile) as grid:
            Xf,Yf=grid["x"],grid["y"]
            dA=(Xf[1]-Xf[0])*(Yf[1]-Yf[0])
            snow_field,budget=settle(grid,w["wind_speed_ms"],w["wind_from_deg"],snow)
            x,y=np.meshgrid(Xf,Yf)
            solid=np.zeros(x.shape,dtype=bool)
            for b in core_buildings:
                solid |= intersects_xy(b.footprint,x,y)
            openfoam=binned(np.where(solid,np.nan,snow_field),Xf,Yf,dA,width,height)
            first=np.where(solid,np.nan,wind_at_2m(grid))
            wind=binned(first,Xf,Yf,dA,width,height)
            fields[p.parent.name+"__wind_24m_ms"]=wind
            fields[p.parent.name+"__cfd_native_cm"]=snow_field
            if p.parent.name=="dec-snow-coarse":
                sensitivity={}
                for label,fall,seeds in (("slow_fall",.7,3),("fast_fall",1.3,3),("higher_seed_density",1.,4)):
                    result,alt_budget=settle(grid,w["wind_speed_ms"],w["wind_from_deg"],snow,fall=fall,seeds=seeds)
                    other=binned(np.where(solid,np.nan,result),Xf,Yf,dA,width,height)
                    sensitivity[label]={"fall_speed_ms":fall,"top_seeds_per_cell":seeds**2, "side_vertical_samples":len(grid["z_edges"][:-1])*(seeds-1),
                                        "ma_difference_24m_cm":float(np.nanmean(np.abs(other-openfoam))),
                                        "budget":alt_budget}
        engine=estimate(spec["bounds_wgs84"],all_buildings,[w],64,True)
        danil=np.asarray(engine["snow_depth_cm"],dtype=float)[::-1]
        ys,xs=danil.shape
        XX=(np.arange(xs)+.5)*width/xs
        YY=(np.arange(ys)+.5)*height/ys
        dnl=binned(danil,XX,YY,width*height/(xs*ys),width,height)
        valid=np.isfinite(dnl)&np.isfinite(openfoam)
        a,b=openfoam[valid],dnl[valid]
        if not valid.any():
            raise ValueError("No shared cells for comparison")
        rho=None if not np.ptp(a) or not np.ptp(b) else float(spearmanr(a,b).statistic)
        q={"weather":w,"mode":run["mode"],"bins_24m_common":int(valid.sum()),"uniform_cm":snow,
           "danil_mean_cm":float(b.mean()),"danil_p90_cm":float(np.quantile(b,.90)),
           "cfd_deposition_mean_cm":float(a.mean()),"cfd_deposition_p90_cm":float(np.quantile(a,.90)),
           "cfd_vs_danil_mean_abs_difference_cm":float(np.mean(np.abs(a-b))),
           "cfd_vs_uniform_mean_abs_difference_cm":float(np.mean(np.abs(a-snow))),
           "spearman_cfd_danil":rho,"parcel_budget_cm_m2":budget}
        if p.parent.name=="dec-snow-coarse":
            q["particle_sensitivity"]=sensitivity
        comparisons[p.parent.name]=q
        for name,value in (("cfd_cm",openfoam),("danil_cm",dnl),("uniform_cm",np.where(valid,snow,np.nan)),
                           ("cfd_wind_ms",wind)):
            fields[p.parent.name+"__"+name]=value
    np.savez_compressed(RESULTS/"comparison.npz",**fields)
    controls={}
    base=fields.get("dec-snow-coarse__cfd_wind_ms")
    for mode in ("flat","fine","no-buildings","narrow"):
        key="dec-snow-"+mode
        if base is not None and key in comparisons:
            velocity=fields[key+"__cfd_wind_ms"]
            dep=fields[key+"__cfd_cm"]
            control=np.abs(base-velocity)
            controls[key]={"wind_24m_mean_abs_difference_ms":float(np.nanmean(control)),
                           "wind_24m_p95_abs_difference_ms":float(np.nanquantile(control,.95)),
                           "deposition_24m_mean_abs_difference_cm":
                               float(np.nanmean(np.abs(dep-fields["dec-snow-coarse__cfd_cm"])))}
    (RESULTS/"comparison.json").write_text(json.dumps({"runs":comparisons,"ablation":controls},indent=2)+"\n")
    fields_csv=["case","bins_24m_common","uniform_cm","danil_mean_cm","cfd_deposition_mean_cm",
                "cfd_vs_danil_mean_abs_difference_cm","cfd_vs_uniform_mean_abs_difference_cm","spearman_cfd_danil"]
    with (RESULTS/"comparison.csv").open("w",newline="") as file:
        writer=csv.DictWriter(file,fieldnames=fields_csv,extrasaction="ignore")
        writer.writeheader()
        writer.writerows({"case":case,**row} for case,row in comparisons.items())
    print(json.dumps({case:{k:v for k,v in row.items() if k not in ("weather","particle_sensitivity")}
                      for case,row in comparisons.items()},indent=2),flush=True)


if __name__=="__main__":
    import argparse

    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", help="Update a single case in an existing comparison")
    args=parser.parse_args()
    evaluate(args.case)
