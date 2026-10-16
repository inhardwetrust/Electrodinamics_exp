# check_raycast.py
#
# The fast grid queries (geometry/raycast.py) used by discretize against
# the brute-force reference (geometry/queries.py: every point / segment x
# every triangle, the original implementation, kept here):
#   - cells inside each body (dielectric fill, cell_body);
#   - PEC E-edges per conductor (inside or on, plus the thin-body rule).
# Models: the coax with ports, the microstrip lines with port / capacitor,
# the patch antenna and the dipole (boxes, cylinders, cones, merged thin
# copper, sheets). Exact agreement is expected; ties on the order of 1e-6
# of the tolerance may flip a handful of edges, so up to 0.1 % of the
# body's PEC edges may differ. Prints the speedup.
#
#   python checks/check_raycast.py      (~1-2 min, mostly the reference)

import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

import numpy as np                                                       # noqa: E402

from discretize import materials as dm                                   # noqa: E402
from geometry.queries import points_in_or_on, points_inside, segment_crossings  # noqa: E402
from geometry.step_io import read_step                                   # noqa: E402
from mesh.rectilinear import build_grid                                  # noqa: E402
from project.object_types import CONDUCTOR_TYPES                         # noqa: E402
from project.project_file import Project                                 # noqa: E402

FAILED = []
MODELS = ["03_coax_line.stp", "01_linie50x30_3.stp", "02_linie50x30_3_with_cap.stp",
          "patch_2g4.step", "dipole_2g4.step"]


def check(label, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {label} {detail}")
    if not ok:
        FAILED.append(label)


# --- reference (brute force) -------------------------------------------------

def reference_edges_in_metal(body, midpoints, axis, tolerance):
    result = np.zeros(len(midpoints), dtype=bool)
    lo, hi = body.bounds
    near = np.nonzero(np.all((midpoints >= lo - tolerance) & (midpoints <= hi + tolerance), axis=1))[0]
    if len(near) == 0:
        return result
    m = midpoints[near]
    hit = points_in_or_on(body, m, 1e-6 * tolerance)
    needed = 1 if body.kind == "sheet" else 2
    for u in range(3):
        if u == axis:
            continue
        offset = np.zeros(3)
        offset[u] = tolerance
        rest = ~hit
        if rest.any():
            hit[rest] |= segment_crossings(body, m[rest] - offset, m[rest] + offset) >= needed
    result[near] = hit
    return result


def reference_inside(body, points):
    result = np.zeros(len(points), dtype=bool)
    lo, hi = body.bounds
    idx = np.nonzero(np.all((points >= lo) & (points <= hi), axis=1))[0]
    if len(idx):
        result[idx] = points_inside(body, points[idx])
    return result


def midpoints(lines, axis):
    coords = [0.5 * (l[1:] + l[:-1]) if a == axis else l for a, l in enumerate(lines)]
    g = np.meshgrid(*coords, indexing="ij")
    return np.stack(g, axis=-1).reshape(-1, 3)


# -----------------------------------------------------------------------------

def main():
    total_fast = total_ref = 0.0
    for name in MODELS:
        path = os.path.join(ROOT, "models", name)
        g = read_step(path)
        p = Project.load(path)
        if not p.assignments:                       # the dipole has no project: all PEC
            for b in g.bodies:
                p.set_type(b, "pec")
        mesh = build_grid(g, p, {**p.ports(g), **p.elements(g)}, p.waveports(g))
        lines = mesh.grid.lines
        shape = mesh.grid.shape
        tolerance = 0.99 * mesh.min_step
        centers = [0.5 * (l[1:] + l[:-1]) for l in lines]
        cell_points = np.stack(np.meshgrid(*centers, indexing="ij"), axis=-1).reshape(-1, 3)

        typed = [b for b in g.bodies if p.get(b.name) is not None
                 and p.get(b.name).type in CONDUCTOR_TYPES + ("dielectric",)]
        for body in typed:
            t = time.perf_counter()
            fast_cells = dm._cells_inside(body, centers)
            t_fast = time.perf_counter() - t
            t = time.perf_counter()
            ref_cells = reference_inside(body, cell_points).reshape(shape)
            t_ref = time.perf_counter() - t
            diff = int(np.sum(fast_cells != ref_cells))
            check(f"{name} {body.name}: cells inside", diff <= max(1, ref_cells.sum() // 1000),
                  f"{int(ref_cells.sum())} cells, {diff} differ")

            if p.get(body.name).type in CONDUCTOR_TYPES:
                fast_n = ref_n = diff = 0
                for axis in range(3):
                    t = time.perf_counter()
                    fast = dm._edges_in_metal(body, lines, axis, tolerance)
                    t_fast += time.perf_counter() - t
                    t = time.perf_counter()
                    ref = reference_edges_in_metal(body, midpoints(lines, axis), axis, tolerance)
                    ref = ref.reshape(dm.edge_shape(shape, axis))
                    t_ref += time.perf_counter() - t
                    fast_n += int(fast.sum())
                    ref_n += int(ref.sum())
                    diff += int(np.sum(fast != ref))
                check(f"{name} {body.name}: PEC edges", diff <= max(1, ref_n // 1000),
                      f"{ref_n} edges, {diff} differ ({body.kind}, {len(body.triangles)} triangles)")
            total_fast += t_fast
            total_ref += t_ref
        print(f"    {name}: grid {shape}")

    print(f"time: fast {total_fast:.2f} s, reference {total_ref:.1f} s "
          f"({total_ref / max(total_fast, 1e-9):.0f}x)")
    print("FAILED: " + ", ".join(FAILED) if FAILED else "all checks passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
