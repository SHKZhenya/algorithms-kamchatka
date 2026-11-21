"""Compute terrain-following RANS for one block with the local OpenFOAM image."""
import argparse
import json
import math
from pathlib import Path
import re
import tempfile
import time

import numpy as np
from shapely import intersects_xy
from shapely.geometry import shape

from snowmap.foam import FACES, run_foam, setup_fields, vectors, write

HERE = Path(__file__).resolve().parent
BASHRC = Path("/usr/lib/openfoam/openfoam2512/etc/bashrc")
OPTIONS = {
    "coarse": (8, 28, True, True),
    "fine": (6, 36, True, True),
    "flat": (8, 28, False, True),
    "narrow": (8, 28, True, True),
    "no-buildings": (8, 28, True, False),
}


def sample_ground(terrain, x, y):
    xi, yi, field = (terrain[k] for k in ("x","y","ground"))
    i = np.clip(np.searchsorted(xi,x)-1,0,len(xi)-2)
    j = np.clip(np.searchsorted(yi,y)-1,0,len(yi)-2)
    u = (x-xi[i])/(xi[i+1]-xi[i])
    v = (y-yi[j])/(yi[j+1]-yi[j])
    return ((1-v)*((1-u)*field[j,i]+u*field[j,i+1])+
            v*((1-u)*field[j+1,i]+u*field[j+1,i+1]))


def move_ground(case, terrain, top):
    path = case/"constant/polyMesh/points"
    body = path.read_text()
    match = re.search(r"\n(\d+)\s*\(\s*((?:\([^()]+\)\s*)+)\)",body)
    if not match:
        raise ValueError("Cannot parse blockMesh points")
    points = np.asarray([tuple(map(float,row.split())) for row in re.findall(r"\(([^()]+)\)",match[2])])
    assert len(points)==int(match[1])
    z0 = sample_ground(terrain,points[:,0],points[:,1])
    points[:,2] = z0 + (top-z0)*points[:,2]/top
    payload = "\n".join(f"({x:.9f} {y:.9f} {z:.9f})" for x,y,z in points)
    path.write_text(body[:match.start(2)]+payload+"\n"+body[match.end(2):])


def cell_index(centres, pad, dx, dy, nx, ny):
    ix = np.clip(np.floor((centres[:,0]+pad)/dx).astype(int),0,nx-1)
    iy = np.clip(np.floor((centres[:,1]+pad)/dy).astype(int),0,ny-1)
    return ix,iy


def read_scalar(path, count):
    text=path.read_text()
    match=re.search(r"internalField\s+nonuniform\s+List<scalar>\s+(\d+)\s*\((.*?)\n\)",text,re.S)
    if not match:
        uniform=re.search(r"internalField\s+uniform\s+([^;]+);",text)
        if uniform:
            return np.full(count,float(uniform[1]))
        raise ValueError(f"Cannot read internal scalar field {path}")
    field=np.fromstring(match[2],sep=" ")
    if len(field)!=int(match[1]) or len(field)!=count or not np.isfinite(field).all():
        raise ValueError(f"Invalid scalar field {path}")
    return field


def calculate(case, spec, terrain, mode, condition, iterations, logs, save_scalars=False):
    nominal_step,nz,relief,buildings = OPTIONS[mode]
    width,height = spec["size_m"]
    pad = 108 if mode == "narrow" else spec["padding_m"]
    top = spec["domain_top_m_above_datum"]
    nx,ny = math.ceil((width+2*pad)/nominal_step),math.ceil((height+2*pad)/nominal_step)
    dx,dy = (width+2*pad)/nx,(height+2*pad)/ny
    xs,ys = (np.arange(nx)+.5)*dx-pad,(np.arange(ny)+.5)*dy-pad
    x,y = np.meshgrid(xs,ys)
    ground = sample_ground(terrain,x,y) if relief else np.zeros_like(x)
    ratios = np.geomspace(1,7,nz)
    z_edges = np.r_[0,np.cumsum(ratios)/ratios.sum()]
    z_edges[-1] = 1.
    roof = ground.copy()-1
    used = []
    if buildings:
        for row in spec["buildings"]:
            footprint = shape(row["footprint_local_m"])
            covered = intersects_xy(footprint,x,y)
            covered[[0,-1],:]=False
            covered[:,[0,-1]]=False
            if covered.any():
                centre=footprint.representative_point()
                base=float(sample_ground(terrain,np.array(centre.x),np.array(centre.y))) if relief else 0.
                roof[covered] = np.maximum(roof[covered],base+row["height_m"])
                used.append((row["id"],row["within_core"]))
    speed=condition["wind_speed_ms"]
    rad=math.radians(condition["wind_from_deg"])
    u,v=-speed*math.sin(rad),-speed*math.cos(rad)
    sides=[]
    for component in (u,v):
        if abs(component)<1e-8:
            sides.extend(("symmetry","symmetry"))
        elif component>0:
            sides.extend(("inlet","outlet"))
        else:
            sides.extend(("outlet","inlet"))
    sides.extend(("ground","symmetry"))
    patches={name:[] for name in ("inlet","outlet","ground","building","symmetry")}
    for side, face in zip(sides,FACES):
        patches[side].append("("+" ".join(map(str,face))+")")
    boundary = " ".join(f"{name} {{type {kind}; faces ({' '.join(patches[name])});}}"
                        for name,kind in (("inlet","patch"),("outlet","patch"),("ground","wall"),
                                          ("building","wall"),("symmetry","symmetry")))
    points = " ".join(f"({X} {Y} {Z})" for Z in (0,top) for X,Y in (
        (-pad,-pad),(width+pad,-pad),(width+pad,height+pad),(-pad,height+pad)))
    write(case,"system/blockMeshDict",f"vertices ({points}); blocks (hex (0 1 2 3 4 5 6 7) "
          f"({nx} {ny} {nz}) simpleGrading (1 1 7)); boundary ({boundary});")
    setup_fields(case,u,v,iterations)

    def call(command,*args):
        started=time.perf_counter()
        try:
            output=run_foam(case,BASHRC,command,*args,timeout=600)
        except RuntimeError as exc:
            (logs/(command+".failure.txt")).write_text(str(exc))
            raise
        (logs/(command+".log")).write_text(output)
        print(logs.name,command,round(time.perf_counter()-started,2),flush=True)
        return output

    call("blockMesh")
    if relief:
        move_ground(case,terrain,top)
    if buildings:
        call("postProcess","-func","writeCellCentres","-time","0")
        centres=np.asarray(vectors(case/"0/C"))
        i,j=cell_index(centres,pad,dx,dy,nx,ny)
        above=centres[:,2]>roof[j,i]
        assert above.any() and (~above).any()
        # In the rare case a roof lies just below the first centre, it stays
        # outside this mesh's represented solids, which is tested by refinement.
        labels=" ".join(map(str,np.flatnonzero(above)))
        write(case,"system/topoSetDict","actions ({name fluid; type cellSet; action new; "
              f"source labelToCell; value ({labels}); }});")
        call("topoSet")
        call("subsetMesh","fluid","-patch","building","-overwrite")
        setup_fields(case,u,v,iterations)
    else:
        above=np.ones(nx*ny*nz,dtype=bool)

    check=call("checkMesh")
    if not re.search(r"\bMesh OK\.",check):
        raise RuntimeError(f"Mesh check failed: {logs/'checkMesh.log'}")
    # Retain an intermediate checkpoint to measure stability, not only residuals.
    path=case/"system/controlDict"
    path.write_text(path.read_text().replace(f"writeInterval {iterations};",f"writeInterval {iterations//2};"))
    started=time.perf_counter()
    solve=call("simpleFoam")
    seconds=time.perf_counter()-started
    call("postProcess","-func","writeCellCentres")
    early=case/str(iterations//2)
    final=case/str(iterations)
    centres=np.asarray(vectors(final/"C"))
    early_u=np.asarray(vectors(early/"U"))
    velocity=np.asarray(vectors(final/"U"))
    if centres.shape!=velocity.shape or early_u.shape!=velocity.shape or not np.isfinite(velocity).all():
        raise RuntimeError("Non-finite or inconsistent velocity output")
    i,j=cell_index(centres,pad,dx,dy,nx,ny)
    fraction=(centres[:,2]-ground[j,i])/(top-ground[j,i])
    k=np.clip(np.searchsorted(z_edges,fraction)-1,0,nz-1)
    flat_id=np.ravel_multi_index((k,j,i),(nz,ny,nx))
    if len(set(flat_id))!=len(flat_id):
        raise ValueError("Voxel mapping contains collisions")
    fields=np.full((nz,ny,nx,3),np.nan,dtype=np.float32)
    fields[k,j,i]=velocity
    core=(centres[:,0]>=0)&(centres[:,0]<width)&(centres[:,1]>=0)&(centres[:,1]<height)&(k==0)
    drift=np.linalg.norm((velocity-early_u)[core],axis=1)
    residual={field:float(value) for field,value in re.findall(
        r"Solving for (\w+), Initial residual = ([^,]+),",solve)}
    continuity=re.findall(r"global = ([^,]+), cumulative",solve)
    meta={"weather":condition,"mode":mode,"iterations":iterations,"grid":[nx,ny,nz],
          "cells":len(centres),"solid_cells":int((~above).sum()),"cell_xy_m":[dx,dy],"padding_m":pad,
          "building_footprints_with_horizontal_samples":len(used),
          "core_footprints_with_horizontal_samples":sum(core for _,core in used),
          "solver_s":round(seconds,3),"final_initial_residual_by_field":residual,
          "continuity_global_last":float(continuity[-1]) if continuity else None,
          "near_ground_vector_change_p95_ms":float(np.quantile(drift,.95)),
          "near_ground_vector_change_mean_ms":float(np.mean(drift)),
          "quarter_ground_cells":int(core.sum())}
    if save_scalars:
        extra={}
        for name in ("p","k","epsilon","nut"):
            field=np.full((nz,ny,nx),np.nan,dtype=np.float32)
            field[k,j,i]=read_scalar(final/name,len(centres))
            extra[name]=field
        np.savez_compressed(logs/"pressure-turbulence.npz",**extra)
    np.savez_compressed(logs/"wind.npz",U=fields,x=xs,y=ys,z_edges=z_edges,ground=ground,roof=roof,
                        top=top,pad=pad)
    (logs/"summary.json").write_text(json.dumps(meta,indent=2)+"\n")
    print(json.dumps(meta),flush=True)


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("event")
    parser.add_argument("--mode",choices=OPTIONS,default="coarse")
    parser.add_argument("--iterations",type=int,default=360)
    parser.add_argument("--save-scalars",action="store_true")
    args=parser.parse_args()
    if args.iterations<2 or args.iterations%2:
        parser.error("Choose a positive even iteration count")
    spec=json.loads((HERE/"sources/quarter.json").read_text())
    out=HERE/"results"/f"{args.event}-{args.mode}"
    out.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="quarter-of-") as folder, np.load(HERE/"sources/ground.npz") as terrain:
        calculate(Path(folder),spec,terrain,args.mode,spec["weather"][args.event],args.iterations,out,args.save_scalars)
