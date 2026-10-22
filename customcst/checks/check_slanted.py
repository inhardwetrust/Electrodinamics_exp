# check_slanted.py
#
# The mesh's refinement at slanted conductor faces (mesh/rectilinear.py,
# step 4) on a board with a trace at 45 degrees:
#   - top copper: a pour with a 0.7 mm slot at 45 degrees and a 0.3 mm trace
#     in it (0.2 mm gaps, 0.5 mm at its ends), full bottom copper, 1.5 mm
#     board; min step 0.05 mm, cells per feature 4, 20 cells per
#     wavelength at 6 GHz;
#   - slanted regions along x and y (none along z), the narrowest feature
#     the 0.2 mm gap, steps <= 0.05 mm across the slot;
#   - the trace is not joined to the pour by PEC edges. With the
#     refinement off (Mesh > refine slanted metal) it is (the check sees
#     the bug: the first diagonal KiCad board shorted its trace to the
#     pour);
#   - the same board with the slot along x: no slanted regions, the same
#     grid as with the refinement off;
#   - an um-sized notch in the pour's outline (a polygonized arc) is not a
#     feature.
#
#   python checks/check_slanted.py      (~30 s)

import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

import numpy as np                                                       # noqa: E402
import scipy.sparse as sp                                                # noqa: E402
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut                              # noqa: E402
from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform                  # noqa: E402
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox                          # noqa: E402
from OCP.gp import gp_Ax1, gp_Dir, gp_Pnt, gp_Trsf                       # noqa: E402
from scipy.sparse.csgraph import connected_components                    # noqa: E402

from discretize.materials import discretize                              # noqa: E402
from geometry.step_io import read_step, write_step                       # noqa: E402
from mesh.rectilinear import build_grid                                  # noqa: E402
from project.project_file import Project                                 # noqa: E402

FAILED = []
T, CU = 1.5, 0.035
W, GAP = 0.3, 0.2
L = 8.0                     # slot and trace length, mm
C = (5.0, 5.0)              # their center


def check(label, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {label} {detail}")
    if not ok:
        FAILED.append(label)


def box(x0, y0, z0, x1, y1, z1):
    return BRepPrimAPI_MakeBox(gp_Pnt(x0, y0, z0), gp_Pnt(x1, y1, z1)).Shape()


def turned(shape, degrees, center=C):
    t = gp_Trsf()
    t.SetRotation(gp_Ax1(gp_Pnt(center[0], center[1], 0), gp_Dir(0, 0, 1)), np.radians(degrees))
    return BRepBuilderAPI_Transform(shape, t, True).Shape()


def board(folder, degrees, notch=False):
    """The board's STEP with the slot at `degrees`; its path."""
    half = W / 2 + GAP
    slot = turned(box(C[0] - L / 2, C[1] - half, T - 1, C[0] + L / 2, C[1] + half, T + 1), degrees)
    pour = BRepAlgoAPI_Cut(box(0, 0, T, 10, 10, T + CU), slot).Shape()
    if notch:               # a cut at 30 deg from 0.5 um inside the pour's edge: a um-sized sliver
        corner = (0.0005, 1.0)
        cut = box(corner[0], corner[1], T - 1, corner[0] + 2, corner[1] + 2, T + 1)
        pour = BRepAlgoAPI_Cut(pour, turned(cut, 30, corner)).Shape()
    trace = turned(box(C[0] - L / 2 + 0.5, C[1] - W / 2, T, C[0] + L / 2 - 0.5, C[1] + W / 2, T + CU), degrees)
    path = os.path.join(folder, f"board_{degrees}{'_notch' if notch else ''}.step")
    write_step(path, [("top", pour, None), ("trace", trace, None),
                      ("bottom", box(0, 0, -CU, 10, 10, 0), None), ("board", box(0, 0, 0, 10, 10, T), None)])
    return path


def model(path):
    g = read_step(path)
    project = Project(path)
    for b in g.bodies:
        project.set_type(b, "dielectric" if b.name == "board" else "pec")
    project.set_setting("simulation", "f_max", 6.0)
    project.set_setting("mesh", "min_step", 0.05)
    project.set_setting("mesh", "cells_per_feature", 4)
    project.set_setting("mesh", "cells_per_wavelength", 20)
    return g, project


def joined(g, project, mesh):
    """Whether the trace's middle and the pour beside the slot are one piece on the grid."""
    m = discretize(mesh, g, project, {}, {}, {}, [])
    lines = m.grid.lines
    n = [len(l) for l in lines]
    idx = np.arange(np.prod(n)).reshape(n)
    rows, cols = [], []
    for a in range(3):
        lo = [slice(None)] * 3
        hi = [slice(None)] * 3
        lo[a], hi[a] = slice(0, n[a] - 1), slice(1, n[a])
        rows.append(idx[tuple(lo)][m.pec[a]])
        cols.append(idx[tuple(hi)][m.pec[a]])
    r, c = np.concatenate(rows), np.concatenate(cols)
    _, label = connected_components(sp.coo_matrix((np.ones(len(r)), (r, c)), shape=(idx.size,) * 2),
                                    directed=False)

    def node(x, y):
        return idx[tuple(int(np.argmin(abs(lines[k] - v * 1e-3))) for k, v in enumerate((x, y, T + CU / 2)))]

    return label[node(*C)] == label[node(2.0, 8.0)]


def main():
    folder = tempfile.mkdtemp()
    try:
        g, project = model(board(folder, 45))
        mesh = build_grid(g, project)
        s = mesh.slanted
        check("slanted regions along x and y, none along z", len(s[0]) > 0 and len(s[1]) > 0 and not s[2],
              f"{[len(r) for r in s]}")
        f = mesh.slanted_feature * 1e3
        check("the narrowest feature is the 0.2 mm gap", abs(f - GAP) < 0.01 * GAP, f"{f:.4f} mm")
        x = mesh.grid.lines[0] * 1e3
        across = np.diff(x)[(x[:-1] > C[0] - 2) & (x[:-1] < C[0] + 2)]
        check("steps <= 0.05 mm across the slot", across.max() <= 0.05 + 1e-9, f"max {across.max():.4f} mm")
        check("the trace not joined to the pour", not joined(g, project, mesh),
              f"{mesh.grid.cell_count:,} cells")

        project.set_setting("mesh", "slanted", False)
        coarse = build_grid(g, project)
        check("refinement off: the grid joins them (the check sees the bug)",
              not any(coarse.slanted) and joined(g, project, coarse), f"{coarse.grid.cell_count:,} cells")

        g, project = model(board(folder, 0))
        mesh = build_grid(g, project)
        project.set_setting("mesh", "slanted", False)
        plain = build_grid(g, project)
        check("slot along x: no slanted regions, the same grid as with it off",
              not any(mesh.slanted) and all(np.array_equal(a, b) for a, b in zip(mesh.grid.lines, plain.grid.lines)),
              f"{[len(r) for r in mesh.slanted]}")

        g, project = model(board(folder, 45, notch=True))
        mesh = build_grid(g, project)
        f = mesh.slanted_feature * 1e3
        check("a 0.5 um notch in the pour is not a feature", abs(f - GAP) < 0.01 * GAP, f"{f:.4f} mm")
    finally:
        shutil.rmtree(folder)

    print("FAILED: " + ", ".join(FAILED) if FAILED else "all checks passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
