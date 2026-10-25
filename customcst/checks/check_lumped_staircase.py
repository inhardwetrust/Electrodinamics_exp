# check_lumped_staircase.py
#
# A lumped element on a slanted axis (discretize/materials.py, a staircase
# of edges): the microstrip of check_lumped, its 1 mm gap cut at 45 degrees
# across the line, the element along the gap's normal (45 degrees in the
# board plane). The same VNA-like calibration (short, open, R 50 define
# the fixture and the gap) must recover R = 100 Ohm, C = 1 pF and L = 5 nH
# within 3 %, as for the straight element:
#   - the element sits on a staircase of x and y edges (none along z), its
#     path length ~ sqrt(2) x the gap;
#   - the three values within 3 %, the gap capacitance plausible, on a
#     thin element (one staircase): a wide one's parallel staircases have
#     different lengths (1.18-1.43 mm on this coarse grid), and the
#     current divides over them evenly through a C but not through the
#     short standard, so its C read 1.4-4.5 % high (~0.07 nH of geometry
#     the short does not cancel, not a wrong value).
# Width, as check_lumped's for the straight element: a 0 Ohm element 1 mm
# wide (r 0.5 mm) spreads over parallel staircases and comes near a 1 mm
# copper bridge across the same diagonal gap (its B - B(bridge) under
# 25 % of a thin element's, r 0.01 mm, one staircase); no edge carries two
# paths. One staircase for any width was a thin wire: the slanted 0402 of
# a board saw ~0.85 nH where the same 0402 along x saw 0.42.
#
#   python checks/check_lumped_staircase.py      (~3 min)

import math
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "checks"))
sys.stdout.reconfigure(encoding="utf-8")

import numpy as np                                                       # noqa: E402
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut                              # noqa: E402
from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform                  # noqa: E402
from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder                     # noqa: E402
from OCP.gp import gp_Ax1, gp_Ax2, gp_Dir, gp_Pnt, gp_Trsf               # noqa: E402

import check_lumped                                                      # noqa: E402
from check_lumped import box, check, cone_z                              # noqa: E402
from discretize.materials import discretize                              # noqa: E402
from geometry.step_io import read_step, write_step                       # noqa: E402
from mesh.rectilinear import build_grid                                  # noqa: E402
from project.project_file import Project                                 # noqa: E402

GAP = 1.0
Z_TOP = 1.67                # top of the copper


def turned(shape):
    """Rotated 45 degrees about the z axis through the origin."""
    t = gp_Trsf()
    t.SetRotation(gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)), math.radians(45))
    return BRepBuilderAPI_Transform(shape, t, True).Shape()


def model(folder, name="diagonal_gap", radius=0.5, bridge=False):
    """The diagonal-gap microstrip with an element of `radius` (or a 1 mm copper bridge); its path."""
    trace = box(-25, -1.5, 1.635, 25, 1.5, Z_TOP)
    beyond_minus = turned(box(-GAP / 2, -50, 0, 50, 50, 5))       # n.p > -GAP/2, n = (1, 1)/sqrt 2
    beyond_plus = turned(box(-50, -50, 0, GAP / 2, 50, 5))        # n.p < +GAP/2
    n = np.array([1.0, 1.0, 0.0]) / math.sqrt(2)
    start = -GAP / 2 * n
    if bridge:
        extra = ("bridge", turned(box(-GAP / 2, -0.5, 1.635, GAP / 2, 0.5, Z_TOP)), None)
    else:
        extra = ("element", BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(start[0], start[1], Z_TOP), gp_Dir(*n)),
                                                     radius, GAP).Shape(), None)
    path = os.path.join(folder, f"{name}.step")
    write_step(path, [
        ("gnd", box(-25, -15, 0, 25, 15, 0.035), None),
        ("substrate", box(-25, -15, 0.035, 25, 15, 1.635), None),
        ("line_a", BRepAlgoAPI_Cut(trace, beyond_minus).Shape(), None),
        ("line_b", BRepAlgoAPI_Cut(trace, beyond_plus).Shape(), None),
        extra,
        ("port1", cone_z(-25, 0, 0, Z_TOP, 0.8, 0.5), None),
        ("port2", cone_z(25, 0, 0, Z_TOP, 0.8, 0.5), None),
    ])
    return path


def width(folder, mesh):
    """The width check (see the header), on the wide element's grid."""
    paths = {"wide": model(folder, "wide"), "thin": model(folder, "thin", radius=0.01),
             "bridge": model(folder, "bridge", bridge=True)}
    runs = {name: check_lumped.simulate(path, check_lumped.SHORT, mesh) for name, path in paths.items()}
    wide = runs["wide"][1].elements[0]
    keys = {(int(a), *map(int, e)) for a, e in zip(wide.axes, wide.edges)}
    check("a 1 mm wide slanted element spreads over parallel staircases, a thin one is one",
          wide.columns > 1 and [e.columns for e in runs["thin"][1].elements] == [1] and len(keys) == len(wide.edges),
          f"{wide.columns} paths, {len(wide.edges)} edges, {len(keys)} distinct")
    b = {name: check_lumped.abcd_12(s) for name, (s, _) in runs.items()}
    ratio = np.median(np.abs(b["wide"] - b["bridge"]) / np.abs(b["thin"] - b["bridge"]))
    check("the wide 0 Ohm element is near the 1 mm copper bridge (< 25 % of a thin one's excess)",
          ratio < 0.25, f"{ratio * 100:.0f} %")


def main():
    folder = tempfile.mkdtemp()
    try:
        path = model(folder)
        g = read_step(path)
        project = Project(path)
        for body in g.bodies:
            project.set_type(body, check_lumped.TYPES[body.name])
        project.set_value("substrate", "eps_r", 4.3)          # as check_lumped.simulate: the same mesh
        ports, elements = project.ports(g), project.elements(g)
        mesh = build_grid(g, project, {**ports, **elements})
        m = discretize(mesh, g, project, ports, elements)
        e = m.elements[0] if m.elements else None
        counts = np.bincount(e.axes, minlength=3) if e is not None else None
        check("the element is a staircase of x and y edges", e is not None and not m.errors
              and counts[0] > 0 and counts[1] > 0 and counts[2] == 0, f"{counts} {m.errors}")
        if e is not None:
            check("its path ~ sqrt(2) x the gap", abs(e.length * 1e3 / (GAP * math.sqrt(2)) - 1) < 0.25,
                  f"{e.length * 1e3:.3f} mm")
            check_lumped.calibrate(model(folder, "values", radius=0.01))
        width(folder, mesh)
    finally:
        shutil.rmtree(folder)

    failed = check_lumped.FAILED
    print("FAILED: " + ", ".join(failed) if failed else "all checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
