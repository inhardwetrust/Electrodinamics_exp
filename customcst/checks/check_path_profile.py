# check_path_profile.py
#
# The Z0 profile along a drawn path (solver/path_profile.py). A microstrip
# (0.9 mm on 0.5 mm FR4 over a ground plane, air above) on two boards:
# straight along x, and turned by 30 degrees about z. A port at the start
# drives each line.
#   - the straight line's path profile reads its converged Z0 (the
#     convergence tool at a plane, solver/line_profile.py) within 1.5 %:
#     the sections have a fine grid of their own;
#   - the turned line's path profile, square to it, reads the straight
#     line's Z0 within 1.5 % and is flat along the line (no staircase:
#     the solver sees the geometry square to the trace);
#   - its profile along x cuts it obliquely (1 / cos 30 wider): Z0 low by
#     more than 5 %;
#   - sections at most SECTION_STEP apart on a uniform run;
#   - the plane drawn high above the board (half_width over its height
#     above the ground) reads the same Z0: the section spans the model's
#     height whatever the plane (cut in height, an SMA's coax above a
#     board lost its shield);
#   - a path with a vertex (the turned line, then back along x) gives
#     sections on both segments, none at the vertex, coordinates growing
#     along the path.
#
#   python checks/check_path_profile.py      (~60 s)

import math
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

import numpy as np                                                       # noqa: E402
from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform                  # noqa: E402
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCone    # noqa: E402
from OCP.gp import gp_Ax1, gp_Ax2, gp_Dir, gp_Pnt, gp_Trsf               # noqa: E402

from discretize.materials import discretize                              # noqa: E402
from geometry.step_io import read_step, write_step                       # noqa: E402
from mesh.rectilinear import build_grid                                  # noqa: E402
from project.project_file import Project                                 # noqa: E402
from solver.line_profile import impedance_profile, pec_nets, z0_convergence             # noqa: E402
from solver.path_profile import SECTION_STEP, Path, path_profile                      # noqa: E402

FAILED = []
MM = 1e-3
ANGLE = 30.0                # degrees
HALF_WIDTH = 3 * MM


def check(label, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {label} {detail}")
    if not ok:
        FAILED.append(label)


def box(x0, y0, z0, x1, y1, z1):
    return BRepPrimAPI_MakeBox(gp_Pnt(x0, y0, z0), gp_Pnt(x1, y1, z1)).Shape()


def turned(shape, degrees):
    t = gp_Trsf()
    t.SetRotation(gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)), math.radians(degrees))
    return BRepBuilderAPI_Transform(shape, t, True).Shape()


def board(folder, name, degrees):
    """The model and its 3D materials; the line runs from (-4, 0) to (4, 0) turned by `degrees`."""
    bodies = [("gnd", box(-7, -7, -0.035, 7, 7, 0), "pec"),
              ("fr4", box(-7, -7, 0, 7, 7, 0.5), "dielectric"),
              ("air", box(-7, -7, 0.535, 7, 7, 2.5), "dielectric"),
              ("line", turned(box(-4, -0.45, 0.5, 4, 0.45, 0.535), degrees), "pec"),
              ("port", turned(BRepPrimAPI_MakeCone(gp_Ax2(gp_Pnt(-3.8, 0, 0), gp_Dir(0, 0, 1)),
                                                   0.2, 0.1, 0.5).Shape(), degrees), "port")]
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
    return g, project, mesh, discretize(mesh, g, project, ports)


def line_path(points_mm, degrees=0.0):
    c, s = math.cos(math.radians(degrees)), math.sin(math.radians(degrees))
    points = np.array([[c * x - s * y, s * x + c * y] for x, y in points_mm]) * MM
    return Path(axis=2, at=0.535 * MM, points=points, half_width=HALF_WIDTH)


def finite(profile):
    z = profile.z0
    return z[np.isfinite(z)]


def main():
    folder = tempfile.mkdtemp()
    try:
        g, project, mesh, m = board(folder, "straight", 0.0)
        nets = pec_nets(m)
        converged = z0_convergence(g, project, 0, 0.3 * MM, mesh.min_step,
                                   project.settings["mesh"]["cells_per_feature"], nets=nets)
        reference = converged.estimate[0]
        straight = path_profile(g, project, line_path([(-2, 0), (2, 0)]), nets=nets)
        z = finite(straight)
        check("straight line: the path profile reads the converged Z0",
              len(z) > 5 and abs(np.median(z) / reference - 1) < 0.015,
              f"{np.median(z):.2f} Ω vs {reference:.2f} ± {converged.estimate[1]:.2f} Ω converged")
        high = line_path([(-2, 0), (2, 0)])
        high.at += 1.5 * HALF_WIDTH
        z_high = finite(path_profile(g, project, high, nets=nets))
        check("the plane high above the board reads the same Z0",
              len(z_high) > 5 and abs(np.median(z_high) / np.median(z) - 1) < 0.002,
              f"{np.median(z_high):.2f} Ω at {high.at / MM:.2f} mm vs {np.median(z):.2f} Ω")
        gaps = np.diff(straight.coordinates)
        check("sections at most SECTION_STEP apart", gaps.max() <= SECTION_STEP * 1.0001,
              f"{len(straight.sections)} sections over 4 mm, largest gap {gaps.max() / MM:.3f} mm")

        g, project, _, m = board(folder, "turned", ANGLE)
        nets = pec_nets(m)
        turned_path = path_profile(g, project, line_path([(-2, 0), (2, 0)], ANGLE), nets=nets)
        z = finite(turned_path)
        check(f"line turned {ANGLE:g}°: the path profile reads the straight line's Z0",
              len(z) > 5 and abs(np.median(z) / reference - 1) < 0.015,
              f"{np.median(z):.2f} Ω ({len(z)} of {len(turned_path.sections)} sections)")
        check("and is flat along the line (no staircase)", len(z) and np.ptp(z) < 0.01 * reference,
              f"spread {np.ptp(z):.2f} Ω")

        oblique = impedance_profile(m, 0, g.bounds, nets=nets)
        x = oblique.coordinates
        z_x = oblique.z0[(x > -1.5 * MM) & (x < 1.5 * MM)]
        check("its profile along x cuts it obliquely: low by more than 5 %",
              np.nanmedian(z_x) < 0.95 * reference, f"{np.nanmedian(z_x):.2f} Ω")

        bent = line_path([(-3, 0), (0, 0)], ANGLE)
        bent.points = np.vstack([bent.points, bent.points[-1] + [1.5 * MM, 0.0]])
        two = path_profile(g, project, bent, nets=nets)
        c = two.coordinates
        vertex = 3 * MM
        check("a path with a vertex: sections on both segments, none at the vertex, growing",
              (c < vertex).any() and (c > vertex).any() and np.all(np.abs(c - vertex) > 1e-9)
              and np.all(np.diff(c) > 0) and c[-1] < vertex + 1.5 * MM,
              f"{np.count_nonzero(c < vertex)} + {np.count_nonzero(c > vertex)} sections, "
              f"last at {c[-1] / MM:.3f} mm of {bent.length / MM:.3f}")
    finally:
        shutil.rmtree(folder)

    print("FAILED: " + ", ".join(FAILED) if FAILED else "all checks passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
