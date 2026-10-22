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
#   - the three values within 3 %, the gap capacitance plausible.
#
#   python checks/check_lumped_staircase.py      (~2 min)

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


def model(folder):
    """The diagonal-gap microstrip; its path."""
    trace = box(-25, -1.5, 1.635, 25, 1.5, Z_TOP)
    beyond_minus = turned(box(-GAP / 2, -50, 0, 50, 50, 5))       # n.p > -GAP/2, n = (1, 1)/sqrt 2
    beyond_plus = turned(box(-50, -50, 0, GAP / 2, 50, 5))        # n.p < +GAP/2
    n = np.array([1.0, 1.0, 0.0]) / math.sqrt(2)
    start = -GAP / 2 * n
    element = BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(start[0], start[1], Z_TOP), gp_Dir(*n)), 0.5, GAP).Shape()
    path = os.path.join(folder, "diagonal_gap.step")
    write_step(path, [
        ("gnd", box(-25, -15, 0, 25, 15, 0.035), None),
        ("substrate", box(-25, -15, 0.035, 25, 15, 1.635), None),
        ("line_a", BRepAlgoAPI_Cut(trace, beyond_minus).Shape(), None),
        ("line_b", BRepAlgoAPI_Cut(trace, beyond_plus).Shape(), None),
        ("element", element, None),
        ("port1", cone_z(-25, 0, 0, Z_TOP, 0.8, 0.5), None),
        ("port2", cone_z(25, 0, 0, Z_TOP, 0.8, 0.5), None),
    ])
    return path


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
            check_lumped.calibrate(path)
    finally:
        shutil.rmtree(folder)

    failed = check_lumped.FAILED
    print("FAILED: " + ", ".join(failed) if failed else "all checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
