# check_discretize_kernels.py
#
# The discretization's numba paths (discretize/kernels.py: the edge
# average; geometry/raycast.py: the crossing counts) against the numpy
# paths, on the same grids:
#   - the edge average with and without skipped (thick metal) cells, on a
#     random non-uniform grid, every axis: bit-identical;
#   - whole models discretized both ways: every array of the MaterialGrid
#     (eps, sigma, mu, PEC and dielectric edges, the cells' bodies) equal.
#     The line with a lumped port and element, the coax (curved faces),
#     the SMA + 4-layer board (vias, thin layers), and that board turned
#     30 degrees and cut to a box as the Z0 path's sections are
#     (solver/path_profile.py: every face slanted, build_grid's region);
#   - numba is faster on the board.
# Skipped (passes) without numba.
#
#   python checks/check_discretize_kernels.py      (~2 min)

import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

import numpy as np                                                       # noqa: E402

from discretize import kernels                                           # noqa: E402
from discretize import materials as dm                                   # noqa: E402
from geometry import raycast                                             # noqa: E402
from geometry.body import Geometry                                       # noqa: E402
from geometry.step_io import read_step                                   # noqa: E402
from mesh.rectilinear import build_grid                                  # noqa: E402
from project.project_file import Project                                 # noqa: E402
from solver import path_profile as pp                                    # noqa: E402

FAILED = []
FIELDS = ("eps_r", "sigma", "mu_r", "pec", "dielectric")


def check(label, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {label} {detail}")
    if not ok:
        FAILED.append(label)


def numba(on):
    kernels.ENABLED = on and kernels.AVAILABLE
    raycast.ENABLED = on and raycast.NUMBA


def both(make):
    """make() with the numpy paths, then with numba: (numpy result, numba result, seconds each)."""
    out, times = [], []
    for on in (False, True):
        numba(on)
        start = time.time()
        out.append(make())
        times.append(time.time() - start)
    return out[0], out[1], times


def differences(a, b):
    """[(name, differing entries)] of two MaterialGrids."""
    found = []
    for name in FIELDS:
        for axis, (x, y) in enumerate(zip(getattr(a, name), getattr(b, name))):
            if x.shape != y.shape:
                found.append((f"{name}[{axis}] shape", -1))
            elif not np.array_equal(x, y):
                found.append((f"{name}[{axis}]", int(np.count_nonzero(x != y))))
    if not np.array_equal(a.cell_body, b.cell_body):
        found.append(("cell_body", int(np.count_nonzero(a.cell_body != b.cell_body))))
    return found


def edge_average():
    rng = np.random.default_rng(1)
    shape = (7, 9, 5)
    h = [rng.uniform(0.5, 2.0, n) * 1e-4 for n in shape]
    values = rng.uniform(1.0, 10.0, shape)
    skip = rng.random(shape) < 0.3
    skip[2:5, 3:7, :] = True                        # edges with only skipped cells around
    worst = 0
    for axis in range(3):
        for s in (None, skip):
            a, b, _ = both(lambda: dm._edge_average(values, axis, h, s))
            worst = max(worst, int(np.count_nonzero(a != b)))
    check("edge average: numba = numpy, bit for bit (every axis, with and without skipped cells)",
          worst == 0, f"{worst} differing edges")


def model(name):
    path = os.path.join(ROOT, "models", name)
    return read_step(path), Project.load(path)


def whole(label, g, p, region=None):
    ports, elements = p.ports(g), p.elements(g)
    mesh = build_grid(g, p, {**ports, **elements}, region=region)
    a, b, (t_numpy, t_numba) = both(lambda: dm.discretize(mesh, g, p, ports, elements))
    found = differences(a, b)
    check(f"{label}: numba = numpy, every material array", not found,
          f"{mesh.grid.shape}, numpy {t_numpy:.1f} s, numba {t_numba:.1f} s"
          + (f"; differ: {found}" if found else ""))
    return t_numpy, t_numba


def main():
    if not (kernels.AVAILABLE and raycast.NUMBA):
        print("numba not installed (or CUSTOMCST_NUMPY=1): nothing to compare")
        print("all checks passed")
        return
    numba(True)                                     # compile once outside the timings
    dm._edge_average(np.ones((2, 2, 2)), 0, [np.ones(2)] * 3, np.zeros((2, 2, 2), bool))

    edge_average()
    whole("line with a lumped port and element", *model("02_linie50x30_3_with_cap.stp"))
    whole("coax", *model("03_coax_line.stp"))
    g, p = model("08_coonector_plus_02mm_4layers.stp")
    t_numpy, t_numba = whole("SMA + 4-layer board", g, p)
    check("numba is faster on the board", t_numba < t_numpy, f"{t_numpy / t_numba:.1f}x")

    # The board as a Z0 path section sees it: turned 30 degrees, cut to a box.
    lo, hi = g.bounds
    center = 0.5 * (lo + hi)
    path = pp.Path(2, float(hi[2]), np.array([[center[0], center[1]],
                                              [center[0] + 2e-3 * np.cos(0.52), center[1] + 2e-3 * np.sin(0.52)]]),
                   1.5e-3)
    rotation = pp.segment_frame(path, 0)
    turned = Geometry([pp.rotated_body(b, rotation) for b in g.bodies], g.source)
    a = rotation @ path.world(path.points).T
    region = (np.array([a[0, 0] - 1.5e-3, a[1, 0] - 1.5e-3, a[2].min() - 1.5e-3]),
              np.array([a[0, 0] + 1.5e-3, a[1, 0] + 1.5e-3, a[2].max() + 1.5e-3]))
    whole("the board turned 30 degrees, cut to a box", turned, pp._fine(p), region)

    numba(True)
    print("all checks passed" if not FAILED else f"FAILED: {', '.join(FAILED)}")
    sys.exit(1 if FAILED else 0)


if __name__ == "__main__":
    main()
