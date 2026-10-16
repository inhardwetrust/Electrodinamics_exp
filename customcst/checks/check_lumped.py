# check_lumped.py
#
# Lumped R, L, C elements in a 1 mm gap of the 50 Ohm microstrip, checked
# like a VNA calibration (the fixture - ports and line halves - cancels):
#   ABCD of the two-port is linear in the series impedance Z at the gap:
#     T(Z) = T_h (I + Z e12) T_hr  ->  (T(Z) - T(short))_12 = Z K.
#   Standards: short (R ~ 0), open (all absent: only the gap capacitance
#   Z_g, which is in parallel with any element), R = 50 Ohm (defines K):
#     a = T(R50) - T(short) = K (50 || Z_g),  b = T(open) - T(short) = K Z_g
#     -> Z_g = 50 (b/a - 1),  K = b / Z_g.
#   Devices under test: R = 100 Ohm, C = 1 pF, L = 5 nH (series), extracted
#   as Z = 1 / (1 / Z_total - 1 / Z_g) and compared with the set values.
#   Only frequencies where the calibration is well conditioned are used
#   (|K| large: the gap is not near a voltage node of the line halves).
#   Stability: extreme values (L = 1 pH, parallel C = 1 nF) stay finite.
#
#   python checks/check_lumped.py      (~2 min)

import math
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

import numpy as np                                                       # noqa: E402
from OCP.BRepPrimAPI import (                                            # noqa: E402
    BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCone, BRepPrimAPI_MakeCylinder,
)
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt                                # noqa: E402

from discretize.materials import discretize                              # noqa: E402
from geometry.step_io import read_step, write_step                       # noqa: E402
from mesh.rectilinear import build_grid                                  # noqa: E402
from project.project_file import Project                                 # noqa: E402
from solver.fdtd import FdtdSolver                                       # noqa: E402
from solver.sparams import s_column                                      # noqa: E402

FAILED = []
F = np.linspace(1.0e9, 5.0e9, 41)
W = 2 * math.pi * F


def check(label, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {label} {detail}")
    if not ok:
        FAILED.append(label)


def box(x0, y0, z0, x1, y1, z1):
    return BRepPrimAPI_MakeBox(gp_Pnt(x0, y0, z0), gp_Pnt(x1, y1, z1)).Shape()


def cone_z(x, y, z0, z1, r0, r1):
    return BRepPrimAPI_MakeCone(gp_Ax2(gp_Pnt(x, y, z0), gp_Dir(0, 0, 1)), r0, r1, z1 - z0).Shape()


def rod_x(x0, x1, y, z, r):
    return BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(x0, y, z), gp_Dir(1, 0, 0)), r, x1 - x0).Shape()


TYPES = {"gnd": "pec", "substrate": "dielectric", "port1": "port", "port2": "port",
         "line_a": "pec", "line_b": "pec", "element": "lumped"}


def simulate(path, values):
    g = read_step(path)
    project = Project(path)
    for body in g.bodies:
        project.set_type(body, TYPES[body.name])
    project.set_value("substrate", "eps_r", 4.3)
    project.set_setting("simulation", "padding", 10.0)
    for key, value in values.items():
        project.set_value("element", key, value)

    ports, elements = project.ports(g), project.elements(g)
    mesh = build_grid(g, project, {**ports, **elements})
    materials = discretize(mesh, g, project, ports, elements)
    if materials.errors or len(materials.elements) != 1:
        raise RuntimeError(f"bad model: {materials.errors}")
    solver = FdtdSolver(materials, 1.0, 5.0, active_port="port1")
    status = solver.run()
    s = s_column(solver, F)
    print(f"   {values}: {status} after {solver.step_index} steps")
    return s


def abcd_12(s):
    """B entry of the ABCD matrix (symmetric reciprocal fixture: S22 = S11, S12 = S21)."""
    s11, s21 = s["port1"], s["port2"]
    return 50.0 * ((1 + s11) ** 2 - s21 ** 2) / (2 * s21)


def main():
    folder = tempfile.mkdtemp()
    try:
        path = os.path.join(folder, "gap.step")
        write_step(path, [
            ("gnd", box(-25, -15, 0, 25, 15, 0.035), None),
            ("substrate", box(-25, -15, 0.035, 25, 15, 1.635), None),
            ("line_a", box(-25, -1.5, 1.635, -0.5, 1.5, 1.67), None),
            ("line_b", box(0.5, -1.5, 1.635, 25, 1.5, 1.67), None),
            ("element", rod_x(-0.5, 0.5, 0, 1.67, 0.5), None),     # on top of the copper
            ("port1", cone_z(-25, 0, 0, 1.67, 0.8, 0.5), None),
            ("port2", cone_z(25, 0, 0, 1.67, 0.8, 0.5), None),
        ])

        runs = {
            "short": {"topology": "series", "r": 0.001, "l": 0, "c": 0},
            "open": {"topology": "parallel", "r": 0, "l": 0, "c": 0},
            "R50": {"topology": "series", "r": 50, "l": 0, "c": 0},
            "R100": {"topology": "series", "r": 100, "l": 0, "c": 0},
            "C1p": {"topology": "series", "r": 0, "l": 0, "c": 1.0},
            "L5n": {"topology": "series", "r": 0, "l": 5.0, "c": 0},
        }
        t = {name: abcd_12(simulate(path, values)) for name, values in runs.items()}

        extreme = [
            simulate(path, {"topology": "series", "r": 0, "l": 0.001, "c": 0}),
            simulate(path, {"topology": "parallel", "r": 0, "l": 0, "c": 1000.0}),
        ]
    finally:
        shutil.rmtree(folder)

    a = t["R50"] - t["short"]
    b = t["open"] - t["short"]
    z_gap = 50.0 * (b / a - 1)
    k = b / z_gap

    def element(name):
        return 1.0 / (1.0 / ((t[name] - t["short"]) / k) - 1.0 / z_gap)

    good = np.abs(k) >= 0.3 * np.max(np.abs(k))
    print(f"   well-conditioned frequencies: {np.count_nonzero(good)} of {len(F)} "
          f"({', '.join(f'{f / 1e9:.1f}' for f in F[~good])} GHz dropped)")

    c_gap = -1 / (W * z_gap.imag)
    check("gap capacitance is a plausible constant (0.02-0.15 pF)",
          np.all((c_gap[good] > 0.02e-12) & (c_gap[good] < 0.15e-12)),
          f"{np.median(c_gap[good]) * 1e12:.4f} pF median")

    z = element("R100")
    check("R = 100 Ohm (within 3 %)", np.max(np.abs(z[good] / 100 - 1)) < 0.03,
          f"{np.min(z[good].real):.1f} .. {np.max(z[good].real):.1f} Ohm")

    z = element("C1p")
    c = -1 / (W * z.imag)
    check("C = 1 pF (within 3 %)", np.max(np.abs(c[good] / 1e-12 - 1)) < 0.03,
          f"{np.min(c[good]) * 1e12:.4f} .. {np.max(c[good]) * 1e12:.4f} pF")

    z = element("L5n")
    ell = z.imag / W
    check("L = 5 nH (within 3 %)", np.max(np.abs(ell[good] / 5e-9 - 1)) < 0.03,
          f"{np.min(ell[good]) * 1e9:.4f} .. {np.max(ell[good]) * 1e9:.4f} nH")

    finite = all(np.all(np.isfinite(s[p])) and np.max(np.abs(s[p])) < 1.5
                 for s in extreme for p in s)
    check("stable with L = 1 pH and parallel C = 1 nF", finite)

    print("FAILED: " + ", ".join(FAILED) if FAILED else "all checks passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
