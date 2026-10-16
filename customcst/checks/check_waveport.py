# check_waveport.py
#
# Waveguide ports (project/waveports.py, solver/fdtd.py) on a coax line
# (pin 1.27 mm, shield 4.25 / 5.0 mm, 30 mm along x), ports drawn as
# cylinders on both end faces, outside the model:
#   - geometry: plane, direction, radius; the port planes end the domain;
#   - a uniform line (PTFE 2.1): S11 below -50 dB (the port is the line's
#     own mode and the line runs on into the absorber), |S21| = 1,
#     S21 phase = the delay 30 mm sqrt(eps_eff) / c0;
#   - a line with a 10 mm section of eps_r 4.4 in the middle: a dielectric
#     step in a coax keeps the TEM pattern (no fringing), so transmission-
#     line theory with the mode solver's Z0 and eps_eff of each section is
#     exact up to the grid's dispersion: S11, S21 vs the ABCD cascade;
#   - reciprocity S12 = S21 (one run per port), symmetry, |S11|² + |S21|² = 1;
#   - errors: a cone instead of a cylinder, a port with both caps on the model.
#
#   python checks/check_waveport.py      (~1 min)

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
from OCP.BRepPrimAPI import BRepPrimAPI_MakeCone, BRepPrimAPI_MakeCylinder  # noqa: E402
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt                                # noqa: E402

from discretize.materials import discretize                              # noqa: E402
from geometry.step_io import read_step, write_step                       # noqa: E402
from mesh.rectilinear import build_grid                                  # noqa: E402
from project.project_file import Project                                 # noqa: E402
from project.settings import C0                                          # noqa: E402
from solver.fdtd import FdtdSolver                                       # noqa: E402
from solver.port_mode import solve_port_mode                             # noqa: E402
from solver.sparams import s_column                                      # noqa: E402

FAILED = []
D_IN, D_DIEL, D_OUT = 1.27, 4.25, 5.0
LENGTH = 30.0
F = np.linspace(1e9, 10e9, 19)


def check(label, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {label} {detail}")
    if not ok:
        FAILED.append(label)


def rod(r, x0=0.0, length=LENGTH):
    return BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(x0, 0, 0), gp_Dir(1, 0, 0)), r, length).Shape()


def tube(r0, r1, x0=0.0, length=LENGTH):
    return BRepAlgoAPI_Cut(rod(r1, x0, length), rod(r0, x0, length)).Shape()


def coax(path, sections, port1=None, port2=None):
    """sections: [(x0, length, eps_r)] of the dielectric; returns {body: eps_r}."""
    bodies = [("inner", rod(D_IN / 2), None), ("outer", tube(D_DIEL / 2, D_OUT / 2), None)]
    eps = {}
    for n, (x0, length, e) in enumerate(sections):
        bodies.append((f"diel{n}", tube(D_IN / 2, D_DIEL / 2, x0, length), None))
        eps[f"diel{n}"] = e
    bodies.append(("port1", port1 or rod(D_OUT / 2, -1.0, 1.0), None))
    bodies.append(("port2", port2 or rod(D_OUT / 2, LENGTH, 1.0), None))
    write_step(path, bodies)
    return eps


def model(path, eps):
    g = read_step(path)
    p = Project(path)
    for name in ("inner", "outer"):
        p.set_type(g[name], "pec")
    for name, e in eps.items():
        p.set_type(g[name], "dielectric")
        p.set_value(name, "eps_r", e)
    for name in ("port1", "port2"):
        p.set_type(g[name], "waveport")
    p.set_setting("simulation", "f_min", 1.0)
    p.set_setting("simulation", "f_max", 10.0)
    p.set_setting("simulation", "padding", 1.0)
    return g, p


def build(g, p):
    waveports = p.waveports(g)
    mesh = build_grid(g, p, {}, waveports)
    return waveports, mesh, discretize(mesh, g, p, waveports=waveports)


def s_matrix(materials, both=False):
    s = {}
    for k, active in enumerate(("port1", "port2") if both else ("port1",)):
        solver = FdtdSolver(materials, 1.0, 10.0, active_port=active)
        solver.run()
        column = s_column(solver, F)
        for j, name in enumerate(("port1", "port2")):
            s[j, k] = column[name]
    return s


def section_mode(materials, mesh, x):
    ix = int(np.argmin(np.abs(mesh.grid.lines[0] - x * 1e-3)))
    return solve_port_mode(materials, 0, ix, circle=(0.0, 0.0, D_OUT / 2 * 1e-3))


def main():
    folder = tempfile.mkdtemp()
    try:
        # --- uniform line ---------------------------------------------------
        path = os.path.join(folder, "coax.step")
        g, p = model(path, coax(path, [(0.0, LENGTH, 2.1)]))
        waveports, mesh, m = build(g, p)
        w1, w2 = waveports["port1"], waveports["port2"]
        check("port1 geometry", w1.ok and w1.axis == 0 and w1.direction == 1 and abs(w1.plane) < 1e-9
              and abs(w1.radius - D_OUT / 2e3) < 1e-9, str(w1))
        check("port2 geometry", w2.ok and w2.direction == -1 and abs(w2.plane - LENGTH * 1e-3) < 1e-9, str(w2))
        x = mesh.grid.lines[0]
        check("port planes end the domain", abs(x[0]) < 1e-12 and abs(x[-1] - LENGTH * 1e-3) < 1e-12,
              f"x = {x[0] * 1e3:.4g} .. {x[-1] * 1e3:.4g} mm")
        check("waveports discretized", len(m.waveports) == 2 and not m.errors, m.errors)

        mode = m.waveports[0].mode
        s = s_matrix(m)
        s11, s21 = s[0, 0], s[1, 0]
        delay = LENGTH * 1e-3 * math.sqrt(mode.eps_eff) / C0
        phase = np.degrees(np.angle(s21 * np.exp(2j * np.pi * F * delay)))
        check("uniform: S11 < -50 dB", np.all(np.abs(s11) < 10 ** (-50 / 20)),
              f"max {20 * np.log10(np.abs(s11).max()):.1f} dB (Z0 {mode.z0:.2f} Ω)")
        check("uniform: |S21| = 1 within 0.003", np.all(np.abs(np.abs(s21) - 1) < 0.003),
              f"{np.abs(s21).min():.4f} .. {np.abs(s21).max():.4f}")
        check("uniform: S21 phase = line delay within 3°", np.all(np.abs(phase) < 3.0),
              f"max {np.abs(phase).max():.2f}° at 10 GHz (grid dispersion)")

        # --- dielectric step: 10 mm of eps_r 4.4 in the middle --------------
        path = os.path.join(folder, "step.step")
        g, p = model(path, coax(path, [(0.0, 10.0, 2.1), (10.0, 10.0, 4.4), (20.0, 10.0, 2.1)]))
        _, mesh, m = build(g, p)
        check("step model discretized", len(m.waveports) == 2 and not m.errors, m.errors)
        outer, middle = section_mode(m, mesh, 5.0), section_mode(m, mesh, 15.0)
        z0, z1 = outer.z0, middle.z0
        b0 = 2 * np.pi * F * math.sqrt(outer.eps_eff) / C0
        b1 = 2 * np.pi * F * math.sqrt(middle.eps_eff) / C0
        l0, l1 = 10e-3, 10e-3
        a_, b_, c_, d_ = np.cos(b1 * l1), 1j * z1 * np.sin(b1 * l1), 1j * np.sin(b1 * l1) / z1, np.cos(b1 * l1)
        den = a_ + b_ / z0 + c_ * z0 + d_
        s11_t = (a_ + b_ / z0 - c_ * z0 - d_) / den * np.exp(-2j * b0 * l0)
        s21_t = 2 / den * np.exp(-2j * b0 * l0)

        s = s_matrix(m, both=True)
        e11 = np.abs(s[0, 0] - s11_t)
        e21 = np.abs(s[1, 0] - s21_t)
        check("step: |S11| reaches > 0.3 (the test means something)", np.abs(s11_t).max() > 0.3,
              f"max |S11| theory {np.abs(s11_t).max():.3f}, Z0 {z0:.2f} / {z1:.2f} Ω")
        check("step: S11 vs line theory within 0.02", np.all(e11 < 0.02),
              f"max |ΔS11| {e11.max():.4f} at {F[np.argmax(e11)] / 1e9:.1f} GHz")
        check("step: S21 vs line theory within 0.03", np.all(e21 < 0.03),
              f"max |ΔS21| {e21.max():.4f} at {F[np.argmax(e21)] / 1e9:.1f} GHz")
        check("step: reciprocity S12 = S21 within 0.005", np.all(np.abs(s[0, 1] - s[1, 0]) < 0.005),
              f"max {np.abs(s[0, 1] - s[1, 0]).max():.5f}")
        check("step: symmetry S22 = S11 within 0.005", np.all(np.abs(s[1, 1] - s[0, 0]) < 0.005),
              f"max {np.abs(s[1, 1] - s[0, 0]).max():.5f}")
        power = np.abs(s[0, 0]) ** 2 + np.abs(s[1, 0]) ** 2
        # +-0.4 % in band, -1.2 % at 10 GHz (band edge: the pulse is -20 dB there
        # and the eps 4.4 section has ~20 cells per wavelength).
        check("step: lossless |S11|² + |S21|² = 1 within 0.015", np.all(np.abs(power - 1) < 0.015),
              f"{power.min():.4f} .. {power.max():.4f}")

        # --- errors -----------------------------------------------------------
        path = os.path.join(folder, "bad.step")
        cone = BRepPrimAPI_MakeCone(gp_Ax2(gp_Pnt(-1.0, 0, 0), gp_Dir(1, 0, 0)), D_OUT / 2, D_OUT / 3, 1.0).Shape()
        inside = rod(D_OUT / 2, 10.0, 1.0)
        g, p = model(path, coax(path, [(0.0, LENGTH, 2.1)], port1=cone, port2=inside))
        waveports = p.waveports(g)
        check("cone -> error", not waveports["port1"].ok and "cone" in waveports["port1"].error,
              waveports["port1"].error)
        check("port inside the model -> error", not waveports["port2"].ok, waveports["port2"].error)
    finally:
        shutil.rmtree(folder)

    print("FAILED: " + ", ".join(FAILED) if FAILED else "all checks passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
