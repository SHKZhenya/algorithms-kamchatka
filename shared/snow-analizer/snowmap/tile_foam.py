"""Solve airflow around the buildings of one tile on a Cartesian mesh."""
import math
import os
from pathlib import Path
import tempfile
import time

import numpy as np
from shapely import intersects_xy

from .foam import FACES, run_foam, setup_fields, solve_flow, vectors, write

PADDING = 90
VERTICAL_STEP = 3
ITERATIONS = 120


def solve_tile(buildings, width, height, rows, cols, wind_from_deg, speed):
    started = time.perf_counter()
    bashrc = Path(os.getenv(
        "OPENFOAM_BASHRC", "/usr/lib/openfoam/openfoam2512/etc/bashrc"
    )).resolve()
    if not bashrc.is_file():
        raise RuntimeError(f"OpenFOAM bashrc not found: {bashrc}")

    domain_x, domain_y = width + 2*PADDING, height + 2*PADDING
    top = 75
    nx, ny = min(120, math.ceil(domain_x/6)), min(120, math.ceil(domain_y/6))
    nz = top//VERTICAL_STEP
    dx, dy = domain_x/nx, domain_y/ny
    xs = (np.arange(nx)+0.5)*dx-PADDING
    ys = (np.arange(ny)+0.5)*dy-PADDING
    roofs = np.zeros((ny, nx))
    for building in buildings:
        left, bottom, right, upper = building.footprint.bounds
        ix = slice(xs.searchsorted(left), xs.searchsorted(right, side="right"))
        iy = slice(ys.searchsorted(bottom), ys.searchsorted(upper, side="right"))
        x, y = np.meshgrid(xs[ix], ys[iy])
        roof = np.clip(round(building.height/VERTICAL_STEP), 1, 15)*VERTICAL_STEP
        roofs[iy, ix] = np.maximum(roofs[iy, ix], roof*intersects_xy(building.footprint, x, y))
    roofs[[0, -1], :] = 0
    roofs[:, [0, -1]] = 0

    direction = math.radians(wind_from_deg)
    u, v = -speed*math.sin(direction), -speed*math.cos(direction)
    boundaries = []
    for component in (u, v):
        if abs(component) < 1e-8:
            boundaries.extend(("symmetry", "symmetry"))
        elif component > 0:
            boundaries.extend(("inlet", "outlet"))
        else:
            boundaries.extend(("outlet", "inlet"))
    boundaries.extend(("ground", "symmetry"))
    patches = {name: [] for name in ("inlet", "outlet", "ground", "building", "symmetry")}
    for name, face in zip(boundaries, FACES):
        patches[name].append("(" + " ".join(map(str, face)) + ")")
    boundary = " ".join(
        f"{name} {{type {kind}; faces ({' '.join(patches[name])});}}"
        for name, kind in (("inlet", "patch"), ("outlet", "patch"), ("ground", "wall"),
                           ("building", "wall"), ("symmetry", "symmetry"))
    )
    with tempfile.TemporaryDirectory(prefix="snow-tile-foam-") as temp:
        case = Path(temp)
        west, south, east, north = -PADDING, -PADDING, width+PADDING, height+PADDING
        vertices = [(x,y,z) for z in (0,top) for x,y in
                    ((west,south),(east,south),(east,north),(west,north))]
        points = " ".join(f"({x} {y} {z})" for x, y, z in vertices)
        write(case, "system/blockMeshDict",
              f"vertices ({points}); blocks (hex (0 1 2 3 4 5 6 7) "
              f"({nx} {ny} {nz}) simpleGrading (1 1 1)); boundary ({boundary});")
        setup_fields(case, u, v, ITERATIONS)
        run_foam(case, bashrc, "blockMesh")
        if np.any(roofs):
            run_foam(case, bashrc, "postProcess", "-func", "writeCellCentres", "-time", "0")
            centres = np.asarray(vectors(case/"0/C"))
            cx = np.floor((centres[:,0]+PADDING)/dx).astype(int)
            cy = np.floor((centres[:,1]+PADDING)/dy).astype(int)
            fluid = np.flatnonzero(centres[:,2] > roofs[cy, cx])
            # topoSet consumes OpenFOAM's list of cell labels.
            labels = " ".join(map(str, fluid))
            write(case, "system/topoSetDict",
                  "actions ({ name fluid; type cellSet; action new; source labelToCell; "
                  f"value ({labels}); }});")
            run_foam(case, bashrc, "topoSet")
            run_foam(case, bashrc, "subsetMesh", "fluid", "-patch", "building", "-overwrite")
            setup_fields(case, u, v, ITERATIONS)
        residual = solve_flow(case, bashrc, timeout=240)
        final = case/str(ITERATIONS)
        centres = np.asarray(vectors(final/"C"))
        velocity = np.asarray(vectors(final/"U"))
        if centres.shape != velocity.shape or not np.isfinite(velocity).all():
            raise RuntimeError("Invalid OpenFOAM velocity field")
        ground = centres[:,2] < VERTICAL_STEP
        cx = np.floor((centres[ground,0]+PADDING)/dx).astype(int)
        cy = np.floor((centres[ground,1]+PADDING)/dy).astype(int)
        field = np.full((ny,nx), np.nan)
        field[cy,cx] = np.linalg.norm(velocity[ground,:2],axis=1)/speed
        # Map cell-centred speeds to the output raster, omitting solid cells.
        x = ((np.arange(cols)+0.5)*width/cols+PADDING)/dx-0.5
        y = (height-(np.arange(rows)+0.5)*height/rows+PADDING)/dy-0.5
        ix, iy = np.meshgrid(x, y)
        left, bottom = ix.astype(int), iy.astype(int)
        tx, ty = ix-left, iy-bottom
        result, weight_sum = np.zeros_like(ix), np.zeros_like(ix)
        for i, j in ((0,0),(0,1),(1,0),(1,1)):
            sample = field[bottom+j,left+i]
            valid = np.isfinite(sample)
            weight = (tx if i else 1-tx)*(ty if j else 1-ty)*valid
            result += np.where(valid,sample,0)*weight
            weight_sum += weight
        ratio = np.divide(result,weight_sum,out=np.ones_like(result),where=weight_sum > 0)
    return ratio, {
        "iterations": ITERATIONS, "cells": len(centres), "cell_size_m": [dx,dy,VERTICAL_STEP],
        "sample_height_m": VERTICAL_STEP/2, "buffer_m": PADDING,
        "wind_from_deg": wind_from_deg, "wind_speed_ms": speed,
        "max_final_initial_residual": residual, "solver_ms": round((time.perf_counter()-started)*1000, 2),
    }
