# check_fdtd_line.py
#
# Level 4, the full chain on the 50 Ohm microstrip (FR4 4.3, h 1.6 mm,
# w 3 mm, 35 um copper, cone ports at both ends, CPML around):
#   - eps_eff of the LINE ALONE from two lengths (50 and 100 mm; the port
#     parasitics cancel in the phase difference) vs the quasi-static
#     Hammerstad-Jensen value with Getsinger dispersion;
#   - reciprocity (S21 = S12) and symmetry (S11 = S22);
#   - passivity: |S11|^2 + |S21|^2 <= 1 + numerical error, a few % loss
#     at the band edge allowed (radiation), |S21| ~ 0 dB;
#   - the match: |S11| < -10 dB over 1-5 GHz.
# The 50 mm line alone shows ~0.02 ns more delay than the bare line: the
# lumped ports' own inductance / end capacitance (expected, not checked).
#
#   python checks/check_fdtd_line.py      (~40 s)

import math
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

import numpy as np                                                       # noqa: E402
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCone    # noqa: E402
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt                                # noqa: E402

from discretize.materials import discretize                              # noqa: E402
from geometry.step_io import read_step, write_step                       # noqa: E402
from mesh.rectilinear import build_grid                                  # noqa: E402
from project.project_file import Project                                 # noqa: E402
from project.settings import C0                                          # noqa: E402
from solver.fdtd import FdtdSolver                                       # noqa: E402
from solver.sparams import db, s_column                                  # noqa: E402

FAILED = []
F = np.linspace(1.0e9, 5.0e9, 9)
EPS_R, H, W = 4.3, 1.6e-3, 3.0e-3


def check(label, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {label} {detail}")
    if not ok:
        FAILED.append(label)


def box(x0, y0, z0, x1, y1, z1):
    return BRepPrimAPI_MakeBox(gp_Pnt(x0, y0, z0), gp_Pnt(x1, y1, z1)).Shape()


def cone_z(x, y, z0, z1, r0, r1):
    return BRepPrimAPI_MakeCone(gp_Ax2(gp_Pnt(x, y, z0), gp_Dir(0, 0, 1)), r0, r1, z1 - z0).Shape()


def simulate(folder, length_mm, active):
    half = length_mm / 2
    path = os.path.join(folder, f"line{length_mm}.step")
    write_step(path, [
        ("gnd", box(-half, -15, 0, half, 15, 0.035), None),
        ("substrate", box(-half, -15, 0.035, half, 15, 1.635), None),
        ("line", box(-half, -1.5, 1.635, half, 1.5, 1.67), None),
        ("port1", cone_z(-half, 0, 0, 1.67, 0.8, 0.5), None),
        ("port2", cone_z(half, 0, 0, 1.67, 0.8, 0.5), None),
    ])
    g = read_step(path)
    project = Project(path)
    for name, t in (("gnd", "pec"), ("line", "pec"), ("substrate", "dielectric"),
                    ("port1", "port"), ("port2", "port")):
        project.set_type(g[name], t)
    project.set_value("substrate", "eps_r", EPS_R)
    project.set_setting("simulation", "padding", 10.0)

    ports = project.ports(g)
    materials = discretize(build_grid(g, project, ports), g, project, ports)
    solver = FdtdSolver(materials, 1.0, 5.0, active_port=active)
    status = solver.run()
    print(f"   {length_mm} mm line, {active} excited: {status} after {solver.step_index} steps")
    return s_column(solver, F)


def eps_eff_theory(f):
    """Hammerstad-Jensen (t = 0) + Getsinger dispersion."""
    u = W / H
    a = 1 + math.log((u ** 4 + (u / 52) ** 2) / (u ** 4 + 0.432)) / 49 + math.log(1 + (u / 18.1) ** 3) / 18.7
    b = 0.564 * ((EPS_R - 0.9) / (EPS_R + 3)) ** 0.053
    e0 = (EPS_R + 1) / 2 + (EPS_R - 1) / 2 * (1 + 10 / u) ** (-a * b)
    z0 = 50.0
    fp = z0 / (2 * 4e-7 * math.pi * H)
    g = 0.6 + 0.009 * z0
    return EPS_R - (EPS_R - e0) / (1 + g * (f / fp) ** 2)


def main():
    folder = tempfile.mkdtemp()
    try:
        a = simulate(folder, 50, "port1")
        b = simulate(folder, 100, "port1")
        c = simulate(folder, 50, "port2")
    finally:
        shutil.rmtree(folder)

    # Line-only eps_eff from the phase difference over 50 mm.
    dphi = np.unwrap(np.angle(a["port2"])) - np.unwrap(np.angle(b["port2"]))
    rough = 2 * math.pi * F * math.sqrt(3.3) / C0 * 0.05
    dphi += 2 * math.pi * np.round((rough - dphi) / (2 * math.pi))
    eps_eff = (dphi / 0.05 * C0 / (2 * math.pi * F)) ** 2
    theory = eps_eff_theory(F)
    error = np.max(np.abs(eps_eff / theory - 1))
    check("eps_eff of the line (two lengths) vs Hammerstad + Getsinger", error < 0.03,
          f"max {error * 100:.1f} %\n     FDTD   {np.round(eps_eff, 3)}\n     theory {np.round(theory, 3)}")

    check("reciprocity S21 = S12", np.max(np.abs(a["port2"] - c["port1"])) < 1e-3)
    check("symmetry S11 = S22", np.max(np.abs(a["port1"] - c["port2"])) < 1e-3)

    # No gain beyond the numerical error (~2 %); a few % loss at the band
    # edges is allowed (likely radiation from the open line ends - an open
    # board in air does radiate - not separately verified).
    power = np.abs(a["port1"]) ** 2 + np.abs(a["port2"]) ** 2
    check("passivity: |S11|^2 + |S21|^2 <= 1.03 (no gain)", np.max(power) <= 1.03,
          f"{np.round(power, 3)}")
    check("losses (radiation) <= 10 %", np.min(power) >= 0.9, f"min {np.min(power):.3f}")
    check("|S21| > -0.7 dB (lossless line)", np.min(db(a["port2"])) > -0.7,
          f"min {np.min(db(a['port2'])):.2f} dB")
    check("|S11| < -10 dB over 1-5 GHz", np.max(db(a["port1"])) < -10,
          f"max {np.max(db(a['port1'])):.1f} dB")

    print("FAILED: " + ", ".join(FAILED) if FAILED else "all checks passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
