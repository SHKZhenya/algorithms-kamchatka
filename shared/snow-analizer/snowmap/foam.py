"""Generate near-ground wind templates using OpenCFD OpenFOAM simpleFoam."""
import argparse
import json
import math
import os
import re
import subprocess
import tempfile
import time
from itertools import product
from pathlib import Path

HEIGHTS = (6, 15, 30)
ANGLES = (0, 30, 60, 90)
REFERENCE_SPEED = 8
STEP = 3
HALF_LENGTH, HALF_WIDTH = 12, 6
SIZE = 90
KERNELS = Path(os.environ.get("SNOW_KERNEL_DIR", "kernels"))

# Vertices of a hex block, in OpenFOAM order.
CORNERS = ((0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0),
           (0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1))
FACES = ((0, 4, 7, 3), (1, 2, 6, 5), (0, 1, 5, 4),
         (3, 7, 6, 2), (0, 3, 2, 1), (4, 5, 6, 7))
OFFSETS = ((-1, 0, 0), (1, 0, 0), (0, -1, 0), (0, 1, 0),
           (0, 0, -1), (0, 0, 1))


def write(case, name, body, field_class="dictionary"):
    path = case / name
    path.parent.mkdir(exist_ok=True, parents=True)
    path.write_text(f"FoamFile {{version 2.0; format ascii; class {field_class}; "
                    f"object {path.name};}}\n{body}\n")


def mesh(case, height, angle):
    coordinates = ((-SIZE, -HALF_LENGTH, HALF_LENGTH, SIZE),
                   (-SIZE, -HALF_WIDTH, HALF_WIDTH, SIZE), (0, height, 60))
    indices = list(product(*(range(len(axis)) for axis in coordinates)))
    labels = {point: index for index, point in enumerate(indices)}
    vertices = ["(%s %s %s)" % tuple(axis[i] for axis, i in zip(coordinates, point))
                for point in indices]
    hole = (1, 1, 0)
    cells = set(product(range(3), range(3), range(2))) - {hole}
    patches = {name: [] for name in ("inlet", "outlet", "ground", "building", "symmetry")}
    boundaries = ("inlet" if angle < 90 else "symmetry",
                  "outlet" if angle < 90 else "symmetry",
                  "inlet" if angle > 0 else "symmetry",
                  "outlet" if angle > 0 else "symmetry", "ground", "symmetry")
    blocks = []
    for cell in sorted(cells):
        ids = [labels[tuple(a+b for a, b in zip(cell, corner))] for corner in CORNERS]
        divisions = [(axis[i+1]-axis[i])//STEP for axis, i in zip(coordinates, cell)]
        blocks.append(f"hex ({' '.join(map(str, ids))}) ({' '.join(map(str, divisions))}) simpleGrading (1 1 1)")
        for face, offset, boundary in zip(FACES, OFFSETS, boundaries):
            neighbor = tuple(a+b for a, b in zip(cell, offset))
            if neighbor not in cells:
                patches["building" if neighbor == hole else boundary].append(
                    "(" + " ".join(str(ids[index]) for index in face) + ")")
    patch_types = {"ground": "wall", "building": "wall", "symmetry": "symmetry"}
    boundary = " ".join(f"{name} {{type {patch_types.get(name, 'patch')}; "
                        f"faces ({' '.join(faces)});}}" for name, faces in patches.items())
    write(case, "system/blockMeshDict", f"vertices ({' '.join(vertices)}); "
          f"blocks ({' '.join(blocks)}); boundary ({boundary});")


def setup(case, height, angle, iterations):
    mesh(case, height, angle)
    u, v = (REFERENCE_SPEED*f(math.radians(angle)) for f in (math.cos, math.sin))
    setup_fields(case, u, v, iterations)


def setup_fields(case, u, v, iterations):
    velocity = f"({u:.9f} {v:.9f} 0)"
    fields = {
        "U": ("0 1 -1", velocity, f"fixedValue; value uniform {velocity};",
              f"inletOutlet; inletValue uniform {velocity}; value uniform {velocity};",
              "noSlip;"),
        "p": ("0 2 -2", "0", "zeroGradient;", "fixedValue; value uniform 0;", "zeroGradient;"),
        "k": ("0 2 -2", "0.24", "fixedValue; value uniform 0.24;",
              "inletOutlet; inletValue uniform 0.24; value uniform 0.24;",
              "kqRWallFunction; value uniform 0.24;"),
        "epsilon": ("0 2 -3", "0.01", "fixedValue; value uniform 0.01;",
                    "inletOutlet; inletValue uniform 0.01; value uniform 0.01;",
                    "epsilonWallFunction; value uniform 0.01;"),
        "nut": ("0 2 -1", "0", "calculated; value uniform 0;",
                "calculated; value uniform 0;", "nutkWallFunction; value uniform 0;"),
    }
    for field, (dims, initial, inlet, outlet, wall) in fields.items():
        write(case, f"0/{field}", f"""dimensions [{dims} 0 0 0 0];
internalField uniform {initial};
boundaryField
{{
    inlet {{type {inlet}}}
    outlet {{type {outlet}}}
    ground {{type {wall}}}
    building {{type {wall}}}
    symmetry {{type symmetry;}}
}}""", "volVectorField" if field == "U" else "volScalarField")
    write(case, "constant/transportProperties", "transportModel Newtonian; nu [0 2 -1 0 0 0 0] 1.5e-5;")
    write(case, "constant/turbulenceProperties", "simulationType RAS; RAS {RASModel kEpsilon; turbulence on;}")
    write(case, "system/fvSchemes", """
ddtSchemes {default steadyState;}
gradSchemes {default Gauss linear;}
divSchemes
{
    default none;
    div(phi,U) bounded Gauss upwind;
    div(phi,k) bounded Gauss upwind;
    div(phi,epsilon) bounded Gauss upwind;
    div((nuEff*dev2(T(grad(U))))) Gauss linear;
}
laplacianSchemes {default Gauss linear corrected;}
interpolationSchemes {default linear;}
snGradSchemes {default corrected;}
wallDist {method meshWave;}
""")
    write(case, "system/fvSolution", """
solvers
{
    p {solver GAMG; smoother GaussSeidel; tolerance 1e-6; relTol 0.05;}
    "(U|k|epsilon)" {solver smoothSolver; smoother symGaussSeidel; tolerance 1e-6; relTol 0.05;}
}
SIMPLE {nNonOrthogonalCorrectors 0;}
relaxationFactors
{
    fields {p 0.3;}
    equations {"(U|k|epsilon)" 0.7;}
}
""")
    write(case, "system/controlDict", f"""
application simpleFoam;
startFrom startTime;
startTime 0;
stopAt endTime;
endTime {iterations};
deltaT 1;
writeControl timeStep;
writeInterval {iterations};
writeFormat ascii;
writePrecision 8;
runTimeModifiable false;
""")


def vectors(path):
    contents = path.read_text()
    match = re.search(r"internalField\s+nonuniform\s+List<vector>\s+(\d+)\s*\((.*?)\n\)", contents, re.S)
    if not match:
        raise ValueError(f"Cannot read internal vector field from {path}")
    values = [tuple(map(float, row.split())) for row in re.findall(r"\(([^()]+)\)", match[2])]
    if len(values) != int(match[1]):
        raise ValueError(f"Wrong vector count in {path}")
    return values


def run_foam(case, bashrc, *command, timeout=900):
    load_environment = """
ulimit -c 0
foam_env=$1
shift
load_foam() { source "$foam_env"; }
load_foam && exec "$@"
"""
    result = subprocess.run(
        ["bash", "-c", load_environment, "foam", str(bashrc), *command, "-case", str(case)],
        cwd=case, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=timeout,
    )
    if result.returncode:
        raise RuntimeError(f"{command[0]} failed ({result.returncode}):\n{result.stdout[-5000:]}")
    return result.stdout


def solve_flow(case, bashrc, *, timeout=900):
    output = run_foam(case, bashrc, "simpleFoam", timeout=timeout)
    residuals = [float(value) for value in re.findall(r"Initial residual = ([^,]+)", output)[-6:]]
    if not residuals or any(not math.isfinite(value) for value in residuals):
        raise RuntimeError("simpleFoam did not produce finite final residuals")
    run_foam(case, bashrc, "postProcess", "-func", "writeCellCentres", "-latestTime")
    return max(residuals)


def generate(height, angle, iterations, bashrc, output):
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="snow-foam-") as temp:
        case = Path(temp)
        setup(case, height, angle, iterations)
        run_foam(case, bashrc, "blockMesh")
        residual = solve_flow(case, bashrc)
        final = case / str(iterations)
        grid_size = 2*SIZE//STEP
        grid = [[None]*grid_size for _ in range(grid_size)]
        for (x, y, z), (u, v, _) in zip(vectors(final/"C"), vectors(final/"U"), strict=True):
            if z < STEP:
                ratio = math.hypot(u, v)/REFERENCE_SPEED
                if not math.isfinite(ratio):
                    raise RuntimeError("Non-finite wind speed")
                grid[int((y+SIZE)//STEP)][int((x+SIZE)//STEP)] = round(ratio, 5)
        solid_cells = (2*HALF_LENGTH//STEP)*(2*HALF_WIDTH//STEP)
        if sum(value is None for row in grid for value in row) != solid_cells:
            raise RuntimeError("Incomplete sampling of the near-ground layer")
        output.mkdir(exist_ok=True, parents=True)
        data = {"version": 1, "height": height, "angle": angle, "iterations": iterations,
                "max_final_initial_residual": residual, "speed_ratio": grid}
        with tempfile.NamedTemporaryFile(mode="w", dir=output, delete=False) as stream:
            json.dump(data, stream, allow_nan=False)
        Path(stream.name).replace(output / f"h{height}-a{angle}.json")
        print(f"{height=} {angle=} {time.perf_counter()-started:.2f}s residual={residual:.4g}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bashrc", type=Path, default=os.getenv(
        "OPENFOAM_BASHRC", "/usr/lib/openfoam/openfoam2512/etc/bashrc"))
    parser.add_argument("--height", type=int, choices=HEIGHTS, action="append")
    parser.add_argument("--angle", type=int, choices=ANGLES, action="append")
    parser.add_argument("--iterations", type=int, default=220)
    parser.add_argument("--output", type=Path, default=KERNELS)
    args = parser.parse_args()
    if args.iterations < 1:
        parser.error("--iterations must be positive")
    for height, angle in product(args.height or HEIGHTS, args.angle or ANGLES):
        generate(height, angle, args.iterations, args.bashrc.resolve(), args.output)


if __name__ == "__main__":
    main()
