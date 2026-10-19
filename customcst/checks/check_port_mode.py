# check_port_mode.py
#
# The 2D mode solver of waveguide ports (solver/port_mode.py) on a coax
# cross-section (pin 1.27 mm, PTFE 2.1, outer 4.25 / 5.0 mm):
#   - Z0 vs 60 / sqrt(eps_r) ln(D / d) = 50.0 Ohm (staircased circles:
#     within 1 % at ~70 x 70 nodes across the port);
#   - eps_eff = eps_r (a homogeneous TEM line; metal cells are left out of
#     the edge eps mean, so the edges next to the pin keep eps_r);
#   - the mode field: E is radial and falls as 1/r between the conductors;
#   - errors: no ground in the region, two signal conductors;
#   - a coplanar section whose gaps are ONE cell wide (synthetic grid): the
#     signal and the side grounds have neighbouring metal nodes with a free
#     edge between them, which the FDTD sees as a gap: still two conductors
#     (joining neighbours shorted them: "no signal conductor").
# Also guards the thin-body PEC rule: an edge just outside a THICK curved
# conductor is not metal (otherwise the pin grows and Z0 drops by ~25 %).
#
#   python checks/check_port_mode.py      (~40 s)

import math
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

import numpy as np                                                       # noqa: E402
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut                              # noqa: E402
from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder                     # noqa: E402
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt                                # noqa: E402

from discretize.materials import discretize                              # noqa: E402
from geometry.step_io import read_step, write_step                       # noqa: E402
from mesh.rectilinear import build_grid                                  # noqa: E402
from project.project_file import Project                                 # noqa: E402
from solver.port_mode import solve_port_mode                             # noqa: E402

FAILED = []
D_IN, D_DIEL, D_OUT, EPS_R = 1.27, 4.25, 5.0, 2.1


def check(label, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {label} {detail}")
    if not ok:
        FAILED.append(label)


def rod(r, y=0.0, length=3.0):
    return BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(0, y, 0), gp_Dir(1, 0, 0)), r, length).Shape()


def tube(r0, r1):
    return BRepAlgoAPI_Cut(rod(r1), rod(r0)).Shape()


def materials_for(path, types, eps=None):
    g = read_step(path, linear_deflection=5e-6)
    project = Project(path)
    for name, t in types.items():
        project.set_type(g[name], t)
    if eps:
        project.set_value(eps, "eps_r", EPS_R)
    project.set_setting("simulation", "padding", 3.0)
    project.set_setting("mesh", "cells_per_feature", 8)
    project.set_setting("mesh", "min_step", 0.01)
    mesh = build_grid(g, project, {})
    return mesh, discretize(mesh, g, project)


def one_cell_gap():
    """Port plane x = 0 of a synthetic grid (1 mm cells): ground plane at z = 0,
    side grounds y 0..2 and 6..10 and a strip y 3..5 at z = 1 (gaps 2..3, 5..6)."""
    from types import SimpleNamespace
    nx, ny, nz = 2, 10, 4
    lines = [np.arange(n + 1) * 1e-3 for n in (nx, ny, nz)]
    pec = [np.zeros((nx, ny + 1, nz + 1), bool), np.zeros((nx + 1, ny, nz + 1), bool),
           np.zeros((nx + 1, ny + 1, nz), bool)]
    pec[1][:, :, 0] = True                              # ground plane
    pec[1][:, [0, 1, 6, 7, 8, 9], 1] = True             # side grounds
    pec[1][:, [3, 4], 1] = True                         # strip y 3..5
    pec[2][:, [0, 10], 0] = True                        # side grounds tied down at the walls
    eps = [np.full(a.shape, 4.3) for a in pec]
    m = SimpleNamespace(grid=SimpleNamespace(lines=lines), pec=pec, eps_r=eps)
    mode = solve_port_mode(m, 0, 0, region=((0.0, 10e-3), (0.0, 4e-3)))
    check("one-cell coplanar gaps: the strip is the signal", mode.ok,
          f"Z0 {mode.z0:.1f} Ohm" if mode.ok else "; ".join(mode.errors))


def main():
    folder = tempfile.mkdtemp()
    try:
        coax = os.path.join(folder, "coax.step")
        write_step(coax, [("inner", rod(D_IN / 2), None),
                          ("dielectric", tube(D_IN / 2, D_DIEL / 2), None),
                          ("outer", tube(D_DIEL / 2, D_OUT / 2), None)])
        mesh, m = materials_for(coax, {"inner": "pec", "outer": "pec", "dielectric": "dielectric"},
                                eps="dielectric")
        ix = int(np.argmin(np.abs(mesh.grid.lines[0] - 1.5e-3)))
        r = D_OUT / 2 * 1e-3
        mode = solve_port_mode(m, 0, ix, region=((-r, r), (-r, r)))
        z_theory = 60 / math.sqrt(EPS_R) * math.log(D_DIEL / D_IN)

        check("coax mode solved", mode.ok, "; ".join(mode.errors))
        check("coax Z0 within 1 % of 60/sqrt(er) ln(D/d)", abs(mode.z0 / z_theory - 1) < 0.01,
              f"{mode.z0:.3f} vs {z_theory:.3f} Ohm, {mesh.grid.shape[1]} x {mesh.grid.shape[2]} cells")
        check("coax eps_eff = eps_r (within 0.1 %)", abs(mode.eps_eff / EPS_R - 1) < 0.001,
              f"{mode.eps_eff:.4f}")

        # E along +y on the y axis between the conductors (z = 0 line): ~ 1/r.
        y = mesh.grid.lines[1]
        z = mesh.grid.lines[2]
        kz = int(np.argmin(np.abs(z)))
        mid_y = 0.5 * (y[1:] + y[:-1])
        e_y = mode.e_u[:, kz]
        sel = (mid_y > D_IN / 2 * 1e-3 * 1.3) & (mid_y < D_DIEL / 2 * 1e-3 * 0.85)
        expected = 1.0 / (mid_y[sel] * math.log(D_DIEL / D_IN))      # E(r) = V / (r ln(D/d))
        ratio = e_y[sel] / expected
        check("mode E is radial ~ 1/r (within 8 % away from the stairs)",
              np.all(np.abs(ratio - 1) < 0.08), f"E / (V / r ln(D/d)) = {np.round(ratio, 3)}")

        # Errors: the pin alone (no ground), two pins in one shield.
        lone = os.path.join(folder, "lone.step")
        write_step(lone, [("inner", rod(D_IN / 2), None)])
        mesh_l, m_l = materials_for(lone, {"inner": "pec"})
        ix = int(np.argmin(np.abs(mesh_l.grid.lines[0] - 1.5e-3)))
        bad = solve_port_mode(m_l, 0, ix, region=((-r, r), (-r, r)))
        check("no ground -> error", not bad.ok and "no conductor touches" in bad.errors[0], bad.errors)

        twin = os.path.join(folder, "twin.step")
        write_step(twin, [("a", rod(0.4, y=-1.0), None), ("b", rod(0.4, y=1.0), None),
                          ("outer", tube(D_DIEL / 2, D_OUT / 2), None)])
        mesh_t, m_t = materials_for(twin, {"a": "pec", "b": "pec", "outer": "pec"})
        ix = int(np.argmin(np.abs(mesh_t.grid.lines[0] - 1.5e-3)))
        bad = solve_port_mode(m_t, 0, ix, region=((-r, r), (-r, r)))
        check("two signal conductors -> error", not bad.ok and "2 separate" in bad.errors[0], bad.errors)

        one_cell_gap()
    finally:
        shutil.rmtree(folder)

    print("FAILED: " + ", ".join(FAILED) if FAILED else "all checks passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
