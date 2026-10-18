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
# Box ports on PCB lines (lossless FR4 4.3, 1.6 mm, 30 mm long; boxes on
# both board edges covering the board width and 8 mm of air above):
#   - microstrip 3 mm and grounded coplanar 1 mm / 0.4 mm gaps (side
#     grounds walled to the ground, as a dense via fence): plane,
#     direction and rectangle; S11 < -30 dB, |S21| = 1 within 0.01, S21
#     phase = the delay with the mode's eps_eff within 3 deg;
#   - a box overlapping the board on two faces -> error;
#   - a box on the end of a thin board (0.2 mm + copper, 6 mm of air below
#     the copper): found (an even 9 x 9 sample grid on its faces missed
#     the board);
#   - microstrip with a small box at one end (10 x 4 mm, the line's field
#     runs past it): the mode is solved on the whole plane, so both ports
#     see the same line: S12 = S21 within 0.002 dB, |S|² of each column
#     <= 1 (cut at the box, port 2's V I was 5-9 % low: S12 +0.2 dB).
#
#   python checks/check_waveport.py      (~1.5 min)

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
from OCP.BRepPrimAPI import (                                             # noqa: E402
    BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCone, BRepPrimAPI_MakeCylinder,
)
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


def box(x0, y0, z0, x1, y1, z1):
    return BRepPrimAPI_MakeBox(gp_Pnt(x0, y0, z0), gp_Pnt(x1, y1, z1)).Shape()


def pcb_line(folder, kind, port1=None, port2=None):
    """A 30 mm PCB line along x with box ports on both edges: (geometry, project)."""
    bodies = [("sub", box(0, -10, 0, LENGTH, 10, 1.6), None), ("gnd", box(0, -10, -0.035, LENGTH, 10, 0), None)]
    if kind == "microstrip":
        bodies.append(("line", box(0, -1.5, 1.6, LENGTH, 1.5, 1.635), None))
    else:
        # Side grounds tied to the bottom ground by walls 0.5 mm from the gaps
        # (the limit of a dense via fence): without them the floating side
        # planes and the ground guide a parallel-plate wave that leaks ~1 %.
        bodies += [("line", box(0, -0.5, 1.6, LENGTH, 0.5, 1.635), None),
                   ("left", box(0, -10, 1.6, LENGTH, -0.9, 1.635), None),
                   ("right", box(0, 0.9, 1.6, LENGTH, 10, 1.635), None),
                   ("wall_l", box(0, -1.6, 0, LENGTH, -1.4, 1.6), None),
                   ("wall_r", box(0, 1.4, 0, LENGTH, 1.6, 1.6), None)]
    bodies += [("port1", port1 or box(-1, -10, -0.035, 0, 10, 8), None),
               ("port2", port2 or box(LENGTH, -10, -0.035, LENGTH + 1, 10, 8), None)]
    path = os.path.join(folder, f"{kind}.step")
    write_step(path, bodies)
    g = read_step(path)
    p = Project(path)
    for name in ("gnd", "line", "left", "right", "wall_l", "wall_r"):
        if name in [b.name for b in g.bodies]:
            p.set_type(g[name], "pec")
    p.set_type(g["sub"], "dielectric")
    p.set_value("sub", "eps_r", 4.3)                 # lossless: |S21| must be 1
    for name in ("port1", "port2"):
        p.set_type(g[name], "waveport")
    p.set_setting("simulation", "f_min", 1.0)
    p.set_setting("simulation", "f_max", 5.0)
    p.set_setting("simulation", "padding", 5.0)
    return g, p


def box_ports(folder):
    f = np.linspace(1e9, 5e9, 9)
    for kind in ("microstrip", "coplanar"):
        g, p = pcb_line(folder, kind)
        waveports, mesh, m = build(g, p)
        w1, w2 = waveports["port1"], waveports["port2"]
        check(f"{kind}: box ports found", w1.ok and w2.ok and w1.shape == "rectangle"
              and w1.axis == 0 and w1.direction == 1 and w2.direction == -1
              and np.allclose(w1.region, ((-0.01, 0.01), (-0.035e-3, 0.008))), f"{w1.error or ''} {w1.region}")
        check(f"{kind}: discretized", len(m.waveports) == 2 and not m.errors, m.errors)
        if len(m.waveports) != 2:
            continue
        mode = m.waveports[0].mode
        solver = FdtdSolver(m, 1.0, 5.0, active_port="port1")
        solver.run()
        column = s_column(solver, f)
        s11, s21 = column["port1"], column["port2"]
        delay = LENGTH * 1e-3 * math.sqrt(mode.eps_eff) / C0
        phase = np.degrees(np.angle(s21 * np.exp(2j * np.pi * f * delay)))
        check(f"{kind}: S11 < -30 dB", np.all(np.abs(s11) < 10 ** (-30 / 20)),
              f"max {20 * np.log10(np.abs(s11).max()):.1f} dB (Z0 {mode.z0:.1f} Ω, eps_eff {mode.eps_eff:.3f})")
        check(f"{kind}: |S21| = 1 within 0.01", np.all(np.abs(np.abs(s21) - 1) < 0.01),
              f"{np.abs(s21).min():.4f} .. {np.abs(s21).max():.4f}")
        check(f"{kind}: S21 phase = delay within 3°", np.all(np.abs(phase) < 3.0),
              f"max {np.abs(phase).max():.2f}°")

    # A box sunk into the board: two of its faces touch the model.
    g, p = pcb_line(folder, "microstrip", port1=box(-1, -10, -0.035, 0.5, 10, 8))
    bad = p.waveports(g)["port1"]
    check("box overlapping the board -> error", not bad.ok, bad.error)

    # A box on a thin board: the board is only 0.27 mm of the box's 6.3 mm.
    path = os.path.join(folder, "thin.step")
    write_step(path, [("sub", box(-8, -5.5, 0, 8, 5.5, 0.2), None),
                      ("gnd", box(-8, -5.5, 0.2, 8, 5.5, 0.235), None),
                      ("line", box(-0.5, -5.5, -0.035, 0.5, 5.5, 0), None),
                      ("port", box(-8, 5.5, -6.035, 8, 6.5, 0.235), None)])
    g = read_step(path)
    p = Project(path)
    for name in ("gnd", "line"):
        p.set_type(g[name], "pec")
    p.set_type(g["sub"], "dielectric")
    p.set_type(g["port"], "waveport")
    thin = p.waveports(g)["port"]
    check("box on a thin board found", thin.ok and thin.shape == "rectangle" and thin.axis == 1
          and thin.direction == -1, thin.error or "")

    # Ports of different sizes on one line: reciprocity and passivity.
    g, p = pcb_line(folder, "microstrip", port2=box(LENGTH, -5, -0.035, LENGTH + 1, 5, 4))
    _, _, m = build(g, p)
    z = [w.mode.z0 for w in m.waveports]
    check("small box: same line, same Z0", abs(z[0] - z[1]) < 0.01 * z[0], f"{z[0]:.2f} / {z[1]:.2f} Ω")
    s = {}
    for k, active in enumerate(("port1", "port2")):
        solver = FdtdSolver(m, 1.0, 5.0, active_port=active)
        solver.run()
        column = s_column(solver, f)
        s[0, k], s[1, k] = column["port1"], column["port2"]
    ratio = 20 * np.log10(np.abs(s[0, 1] / s[1, 0]))
    check("small box: S12 = S21 within 0.002 dB", np.all(np.abs(ratio) < 0.002),
          f"max {np.abs(ratio).max():.4f} dB")
    power = [np.abs(s[0, k]) ** 2 + np.abs(s[1, k]) ** 2 for k in (0, 1)]
    check("small box: |S11|² + |S21|² <= 1 for both columns", all(np.all(c < 1.001) for c in power),
          f"max {max(c.max() for c in power):.4f}")


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

        # Trimmed pulse tails (start / end at 1e-4 of the peak): same S, fewer steps.
        full = FdtdSolver(m, 1.0, 10.0, active_port="port1")
        full.run()
        short = FdtdSolver(m, 1.0, 10.0, active_port="port1", trim_pulse=True)
        short.run()
        a, b = s_column(full, F), s_column(short, F)
        diff = max(float(np.max(np.abs(a[n] - b[n]))) for n in a)
        # The trimmed run stops right at the -40 dB energy level (the full one only
        # after its long tail, at about -54 dB): what remains of the wave at
        # port 2 is cut, 0.004 at the band edges. Run to -80 dB, the two agree
        # within 3e-4: the trimming itself costs nothing.
        check("trimmed pulse: same S within 0.005", diff < 0.005,
              f"max |ΔS| {diff:.5f}; steps {short.step_index} instead of {full.step_index}")
        check("trimmed pulse: fewer steps", short.step_index < full.step_index)
        deep = FdtdSolver(m, 1.0, 10.0, active_port="port1", trim_pulse=True)
        deep.run(end_db=-60.0)
        c = s_column(deep, F)
        diff = max(float(np.max(np.abs(a[n] - c[n]))) for n in a)
        check("trimmed pulse, accuracy -60 dB: same S within 0.001", diff < 0.001,
              f"max |ΔS| {diff:.5f}, {deep.step_index} steps")

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

        box_ports(folder)

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
