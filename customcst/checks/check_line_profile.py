# check_line_profile.py
#
# The Z0 profile along an axis (solver/line_profile.py) on a coax along x
# (pin 1.27 mm for x 0..2, PTFE 2.1 to 4.25 mm, outer tube 4.25 / 5.0 mm,
# x 0..3) lying in a wider FR4 block (x 0..3, 10 x 10 mm), so the shell
# never reaches the model's side:
#   - every section with the pin: Z0 = 60 / sqrt(2.1) ln(4.25 / 1.27) =
#     50.0 Ohm within 1.5 % (staircased circles) and eps_eff = 2.1: the
#     ground is found from the metal's own extent on the plane;
#   - the sections past the pin's end have no signal: NaN and a reason;
#   - line_impedance at one grid line gives the profile's value there.
#
#   python checks/check_line_profile.py      (~30 s)

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
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder  # noqa: E402
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt                                # noqa: E402

from discretize.materials import discretize                              # noqa: E402
from geometry.step_io import read_step, write_step                       # noqa: E402
from mesh.rectilinear import build_grid                                  # noqa: E402
from project.project_file import Project                                 # noqa: E402
from solver.line_profile import impedance_profile, line_impedance        # noqa: E402

FAILED = []
MM = 1e-3
D_IN, D_DIEL, D_OUT, EPS_R, PIN_END = 1.27, 4.25, 5.0, 2.1, 2.0


def check(label, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {label} {detail}")
    if not ok:
        FAILED.append(label)


def rod(r, length=3.0):
    return BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(1, 0, 0)), r, length).Shape()


def main():
    folder = tempfile.mkdtemp()
    try:
        path = os.path.join(folder, "coax.step")
        block = BRepAlgoAPI_Cut(BRepPrimAPI_MakeBox(gp_Pnt(0, -5, -5), gp_Pnt(3, 5, 5)).Shape(),
                                rod(D_OUT / 2)).Shape()
        write_step(path, [("pin", rod(D_IN / 2, PIN_END), None),
                          ("ptfe", BRepAlgoAPI_Cut(rod(D_DIEL / 2), rod(D_IN / 2, PIN_END)).Shape(), None),
                          ("shell", BRepAlgoAPI_Cut(rod(D_OUT / 2), rod(D_DIEL / 2)).Shape(), None),
                          ("fr4", block, None)])
        g = read_step(path, linear_deflection=5e-6)
        project = Project(path)
        for name, t in (("pin", "pec"), ("shell", "pec"), ("ptfe", "dielectric"), ("fr4", "dielectric")):
            project.set_type(g[name], t)
        project.set_value("ptfe", "eps_r", EPS_R)
        project.set_value("fr4", "eps_r", 4.3)
        project.set_setting("simulation", "padding", 1.0)
        project.set_setting("mesh", "cells_per_feature", 8)
        project.set_setting("mesh", "min_step", 0.01)
        mesh = build_grid(g, project, {})
        m = discretize(mesh, g, project)

        profile = impedance_profile(m, 0, g.bounds)
        x = profile.coordinates / MM
        with_pin = (x > 0.05) & (x < PIN_END - 0.05)
        past = x > PIN_END + 0.05
        ideal = 60 / math.sqrt(EPS_R) * math.log(D_DIEL / D_IN)
        z = profile.z0[with_pin]
        check(f"sections with the pin: Z0 = {ideal:.1f} Ω within 1.5 % (shell inside the block)",
              len(z) > 3 and np.all(np.abs(z / ideal - 1) < 0.015),
              f"{len(z)} sections, {np.nanmin(z):.2f}..{np.nanmax(z):.2f} Ω")
        e = profile.eps_eff[with_pin]
        check("eps_eff = 2.1", np.all(np.abs(e - EPS_R) < 0.02), f"{e.min():.3f}..{e.max():.3f}")
        errors = [s.error for s, p in zip(profile.sections, past) if p]
        check("past the pin's end: no Z0, with a reason",
              len(errors) > 0 and np.all(np.isnan(profile.z0[past])) and all(errors),
              errors[0] if errors else "no sections")

        k = int(np.nonzero(with_pin)[0][len(z) // 2])
        index = int(np.argmin(np.abs(m.grid.lines[0] - profile.sections[k].coordinate)))
        one = line_impedance(m, 0, index, g.bounds)
        check("line_impedance at one grid line = the profile there", abs(one.z0 - profile.sections[k].z0) < 1e-9,
              f"{one.z0:.3f} Ω")
    finally:
        shutil.rmtree(folder)

    print("FAILED: " + ", ".join(FAILED) if FAILED else "all checks passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
