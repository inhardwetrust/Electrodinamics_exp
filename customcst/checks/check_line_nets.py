# check_line_nets.py
#
# The Z0 profile sees what joins out of the plane (solver/line_profile.py,
# Nets). A microstrip along x (0.9 mm on 0.5 mm FR4, x 0..6) with two pads
# beside it at x 2..4, like the pins of a chip beside the signal pin: one
# tied to the ground plane by a via at x 3.6, one unconnected. A port at
# x 0.2 drives the line. At the plane x 3:
#   - the plane alone sees three conductors besides the ground: no Z0;
#   - with the 3D nets the tied pad is ground, the unconnected one floats:
#     Z0 found, below the bare line's (the pads load it);
#   - the same with the via in the plane (x 3, the pad joined to the
#     ground in the plane itself): Z0 within 1.5 %;
#   - the free pad floats: removing it changes Z0 by less than 3 %, and
#     raises it;
#   - with the line shorted to the ground by a via at x 5.5, no signal: the
#     metal is joined to the ground elsewhere;
#   - without a port the free pad and the line are two candidates: no Z0.
#
#   python checks/check_line_nets.py      (~30 s)

import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

import numpy as np                                                       # noqa: E402
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCone, BRepPrimAPI_MakeCylinder  # noqa: E402
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt                                # noqa: E402

from discretize.materials import discretize                              # noqa: E402
from geometry.step_io import read_step, write_step                       # noqa: E402
from mesh.rectilinear import build_grid                                  # noqa: E402
from project.project_file import Project                                 # noqa: E402
from solver.line_profile import line_impedance, pec_nets                 # noqa: E402

FAILED = []
MM = 1e-3


def check(label, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {label} {detail}")
    if not ok:
        FAILED.append(label)


def box(x0, y0, z0, x1, y1, z1):
    return BRepPrimAPI_MakeBox(gp_Pnt(x0, y0, z0), gp_Pnt(x1, y1, z1)).Shape()


def via(x, y):
    return BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(x, y, -0.035), gp_Dir(0, 0, 1)), 0.15, 0.57).Shape()


def section(folder, name, via_x=3.6, free_pad=True, short=False, port=True):
    """(Section at x 3 without nets, with nets, Section at x 1 with nets) of one variant."""
    bodies = [("gnd", box(0, -3, -0.035, 6, 3, 0), "pec"),
              ("fr4", box(0, -3, 0, 6, 3, 0.5), "dielectric"),
              ("air", box(0, -3, 0.535, 6, 3, 2.5), "dielectric"),     # the model's top above the line
              ("line", box(0, -0.45, 0.5, 6, 0.45, 0.535), "pec"),
              ("tied_pad", box(2, -1.6, 0.5, 4, -1.0, 0.535), "pec"),
              ("tie", via(via_x, -1.3), "pec")]
    if free_pad:
        bodies.append(("free_pad", box(2, 1.0, 0.5, 4, 1.6, 0.535), "pec"))
    if short:
        bodies.append(("short", via(5.5, 0), "pec"))
    if port:
        bodies.append(("port", BRepPrimAPI_MakeCone(gp_Ax2(gp_Pnt(0.2, 0, 0), gp_Dir(0, 0, 1)),
                                                    0.2, 0.1, 0.5).Shape(), "port"))
    path = os.path.join(folder, name + ".step")
    write_step(path, [(n, s, None) for n, s, _ in bodies])
    g = read_step(path, linear_deflection=5e-6)
    project = Project(path)
    for n, _, t in bodies:
        project.set_type(g[n], t)
    project.set_value("fr4", "eps_r", 4.3)
    project.set_value("air", "eps_r", 1.0)
    project.set_setting("simulation", "padding", 0.0)
    project.set_setting("mesh", "cells_per_feature", 4)
    project.set_setting("mesh", "min_step", 0.05)
    ports = project.ports(g)
    mesh = build_grid(g, project, ports)
    m = discretize(mesh, g, project, ports)
    nets = pec_nets(m)
    x = m.grid.lines[0]
    at3, at1 = int(np.argmin(abs(x - 3 * MM))), int(np.argmin(abs(x - 1 * MM)))
    return (line_impedance(m, 0, at3, g.bounds), line_impedance(m, 0, at3, g.bounds, nets),
            line_impedance(m, 0, at1, g.bounds, nets))


def main():
    folder = tempfile.mkdtemp()
    try:
        alone, joined, bare = section(folder, "pads")
        check("the plane alone: three conductors besides the ground, no Z0",
              np.isnan(alone.z0) and "3 separate" in alone.error, alone.error)
        check("with the nets: the tied pad is ground, the free one floats: Z0 below the bare line's",
              np.isfinite(joined.z0) and joined.z0 < bare.z0 and 1 < joined.eps_eff < 4.3,
              f"{joined.z0:.2f} Ω (bare line {bare.z0:.2f} Ω), eps_eff {joined.eps_eff:.3f}")

        _, in_plane, _ = section(folder, "via_in_plane", via_x=3.0)
        check("the via in the plane itself: the same Z0 within 1.5 %",
              abs(in_plane.z0 / joined.z0 - 1) < 0.015, f"{in_plane.z0:.2f} Ω")

        _, no_free, _ = section(folder, "no_free_pad", free_pad=False)
        check("the free pad floats: without it Z0 is higher, by less than 3 %",
              joined.z0 < no_free.z0 < 1.03 * joined.z0, f"{no_free.z0:.2f} Ω")

        _, shorted, _ = section(folder, "shorted", short=True)
        check("the line shorted to the ground beyond the plane: no signal",
              np.isnan(shorted.z0) and "joined to the ground" in shorted.error, shorted.error)

        _, no_port, _ = section(folder, "no_port", port=False)
        check("no port: the line and the free pad are two candidates, no Z0",
              np.isnan(no_port.z0) and "2 separate" in no_port.error, no_port.error)
    finally:
        shutil.rmtree(folder)

    print("FAILED: " + ", ".join(FAILED) if FAILED else "all checks passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
