# check_farfield.py
#
# The far-field monitor (solver/farfield.py) on dipoles in free space: two
# PEC rods along z (radius 0.5 mm, 1 mm gap) fed by a lumped port in the
# gap, band 2-4 GHz, the pattern at 2.8, 3.0 and 3.2 GHz.
#
# Reference: the sinusoidal-current dipole of the same length L,
#   U(theta) ~ [(cos(kL/2 cos theta) - cos(kL/2)) / sin theta]^2.
#
# Short dipole (L = 10 mm, ~lambda / 10): any current on so short a wire
# radiates this pattern (sin^2 theta, D = 1.5 = 1.76 dBi, plus a correction
# of order (kL)^2 the formula carries), so it checks the transformation
# itself:
#   - directivity and the pattern at every theta 20..160 within 0.05 dB;
#   - round in phi, nulls along the axis, no cross-polarization;
#   - the pattern integrated over the sphere equals the box's Poynting flux
#     (within 1 %);
#   - the cuts of the view: in the phi = 0 plane the -3 dB beamwidth of
#     sin^2 is 90 degrees (within 1), the horizon is round (no beamwidth),
#     and all of it is E_theta (Ludwig-3 x and y add up to the total);
#   - the 3D surface: closed, its radius 1 along the maximum, wound outward
#     (positive volume: the outer side is the front face, the one drawn).
# Half-wave dipole (L = 47 mm): the real current of a rod on the grid is
# not quite sinusoidal (D comes out ~0.1 dB above the formula's, the same
# on a finer mesh), so it checks the whole run, looser:
#   - directivity within 0.2 dB, the pattern at 30/45/60 degrees within
#     0.6 dB;
#   - sphere = box flux (1 %), and the rods are lossless: the radiated power
#     equals the power the port delivers (within 2 %).
#
# Both run through solver/simulation.py simulate() with the far-field
# frequencies, the way the window and run.py do, and the far field is read
# back from the saved run file (solver/result_file.py).
#
# Fed through a coax (a connector's case): a waveguide port at the end of a
# lossless PTFE coax (z 0..20 mm) coming up through a ground disk (r 30 mm,
# 1 mm thick, on the coax's top), its inner conductor a 25 mm monopole
# above the disk. The port plane ends the domain, so the coax crosses the
# box face there. (Without the disk the shield's outside carries a strong
# wave down the cable into the absorber: through the box but never to the
# far field, the sphere then gets half the box flux: a cable, not the box.)
# Three boxes in one run:
#   - the port's line left out (the default): the box is in air otherwise,
#     radiated = accepted (lossless, within 2 %); the sphere gets 94-95 % of
#     the box flux (within 6 %): the shield's outside current crosses the
#     box face (a conductor through the surface), the 15 mm of cable inside
#     the box are not quite represented;
#   - the same with the box face just under the disk (gap 8 cells, 3 mm
#     below it): almost no cable inside, sphere = box flux within 3 % (at
#     3 GHz the ratio went 0.946, 0.962, 0.983, 0.996 for gaps 2, 4, 6, 8:
#     the shortfall is the cable inside; this close to the disk's edge its
#     near field leaves +-2.5 % across the band);
#   - nothing left out (the old way): the feed power through the coax
#     inside the box spoils the balance (radiated 4-6 % of the accepted:
#     off by > 20 %), and the crossing is reported.
#
#   python checks/check_farfield.py      (~1 min)

import math
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

import numpy as np                                                       # noqa: E402
from OCP.BRepPrimAPI import BRepPrimAPI_MakeCone, BRepPrimAPI_MakeCylinder  # noqa: E402
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt                                # noqa: E402

from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut                             # noqa: E402

from discretize.materials import discretize                              # noqa: E402
from geometry.step_io import read_step, write_step                       # noqa: E402
from mesh.rectilinear import build_grid                                  # noqa: E402
from project.project_file import Project                                 # noqa: E402
from solver.farfield import (FarFieldBox, beamwidth, cut, far_field, intensity, summary,  # noqa: E402
                             surface)
from solver.fdtd import FdtdSolver                                       # noqa: E402
from solver.result_file import read_result, write_result                 # noqa: E402
from solver.simulation import simulate                                   # noqa: E402

FAILED = []
C0 = 299_792_458.0
GAP = 1.0               # mm
RADIUS = 0.5            # mm
FREQUENCIES = np.array([2.8e9, 3.0e9, 3.2e9])


def check(label, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {label} {detail}")
    if not ok:
        FAILED.append(label)


def rod_z(z0, z1, r):
    return BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(0, 0, z0), gp_Dir(0, 0, 1)), r, z1 - z0).Shape()


def cone_z(z0, z1, r0, r1):
    return BRepPrimAPI_MakeCone(gp_Ax2(gp_Pnt(0, 0, z0), gp_Dir(0, 0, 1)), r0, r1, z1 - z0).Shape()


def dipole_pattern(theta, frequency, length):
    """U(theta) of the sinusoidal-current dipole `length` mm long (unnormalized)."""
    half = math.pi * frequency / C0 * length * 1e-3          # k L / 2
    s = np.sin(theta)
    with np.errstate(divide="ignore", invalid="ignore"):
        u = ((np.cos(half * np.cos(theta)) - math.cos(half)) / s) ** 2
    return np.where(s > 1e-9, u, 0.0)


def dipole_directivity(frequency, length):
    theta = np.linspace(0.0, math.pi, 20001)
    u = dipole_pattern(theta, frequency, length)
    return 2.0 * u.max() / np.trapezoid(u * np.sin(theta), theta)


def run(folder, length):
    """The far field (solver/farfield.py far_field()) of the dipole `length` mm long."""
    path = os.path.join(folder, f"dipole{length:g}.step")
    half = 0.5 * length
    write_step(path, [
        ("arm_low", rod_z(-half, -0.5 * GAP, RADIUS), None),
        ("arm_high", rod_z(0.5 * GAP, half, RADIUS), None),
        ("feed", cone_z(-0.5 * GAP, 0.5 * GAP, 0.4, 0.3), None),      # a port is a truncated cone
    ])
    g = read_step(path)
    project = Project(path)
    for body in g.bodies:
        project.set_type(body, "port" if body.name == "feed" else "pec")
    project.set_setting("simulation", "f_min", 2.0)
    project.set_setting("simulation", "f_max", 4.0)
    ports = project.ports(g)
    mesh = build_grid(g, project, ports)
    materials = discretize(mesh, g, project, ports)
    if materials.errors or not materials.ports:
        raise RuntimeError(f"bad model: {materials.errors}")

    result = simulate(materials, 2.0, 4.0, farfield=FREQUENCIES / 1e9)
    saved = os.path.join(folder, f"dipole{length:g}.npz")
    write_result(saved, result)
    back = read_result(saved)
    ff = back.farfield.get("feed", {})
    print(f"   L = {length:g} mm: {result.info[0]}, grid {mesh.grid.shape}, DFT every {int(ff['stride'])} steps")
    for line in summary(ff):
        print(f"     {line}")
    same = (set(ff) == set(result.farfield["feed"])
            and all(np.array_equal(ff[key], result.farfield["feed"][key]) for key in ff))
    check(f"L = {length:g} mm: the far field comes back from the run file", same, f"{sorted(ff)}")
    check(f"L = {length:g} mm: the box is in air", int(ff["cut_edges"]) == 0)
    return ff


def at(ff, angle):
    return int(np.argmin(np.abs(ff["theta"] - angle)))


def common(ff, u, k, label):
    """Round in phi, nulls on the axis, no cross-polarization, sphere = box."""
    ring = u[k, at(ff, 90.0)]
    check(f"{label} round in phi at theta = 90", 10 * math.log10(ring.max() / ring.min()) < 0.1,
          f"{10 * math.log10(ring.max() / ring.min()):.3f} dB")
    peak = u[k].max()
    null = 10 * math.log10(max(u[k, 0].max(), u[k, -1].max(), 1e-30 * peak) / peak)
    check(f"{label} nulls along the axis", null < -25, f"{null:.1f} dB")
    e_t, e_p = np.abs(ff["e_theta"][k]).max(), np.abs(ff["e_phi"][k]).max()
    cross = 20 * math.log10(max(e_p, 1e-15 * e_t) / e_t)
    check(f"{label} no cross-polarization", cross < -30, f"E_phi {cross:.1f} dB")
    ratio = ff["p_sphere"][k] / ff["p_box"][k]
    check(f"{label} sphere integral = box flux", abs(ratio - 1) < 0.01, f"{ratio:.4f}")


def short_dipole(ff, length):
    theta = np.radians(ff["theta"])
    u = intensity(ff["e_theta"].astype(np.complex128), ff["e_phi"].astype(np.complex128))
    i90 = at(ff, 90.0)
    inner = slice(at(ff, 20.0), at(ff, 160.0) + 1)
    for k, f in enumerate(ff["frequencies"]):
        label = f"L = {length:g} mm, {f / 1e9:g} GHz:"
        d = 4.0 * math.pi * u[k].max() / ff["p_sphere"][k]
        d_ref = dipole_directivity(f, length)
        check(f"{label} directivity within 0.05 dB", abs(10 * math.log10(d / d_ref)) < 0.05,
              f"{10 * math.log10(d):.3f} dBi (reference {10 * math.log10(d_ref):.3f})")
        ref = dipole_pattern(theta, f, length)
        shape = 10 * np.log10(u[k, inner, 0] / u[k, i90, 0] / (ref[inner] / ref[i90]))
        check(f"{label} pattern at theta 20..160 within 0.05 dB", np.abs(shape).max() < 0.05,
              f"worst {np.abs(shape).max():.3f} dB")
        common(ff, u, k, label)
        cuts(ff, k, label)
        shell(ff, k, label)


def cuts(ff, k, label):
    angles, values = cut(ff, k, "phi", 0.0)
    peak, width = beamwidth(angles, values)
    check(f"{label} phi = 0 cut: -3 dB beamwidth 90 degrees", width is not None and abs(width - 90.0) < 1.0,
          f"{width:.2f} at {peak:g}" if width else "none")
    check(f"{label} phi = 0 cut: its maximum is the directivity",
          abs(values.max() - cut(ff, k, "theta", 90.0)[1].max()) < 0.01)
    _, horizon = cut(ff, k, "theta", 90.0)
    check(f"{label} theta = 90 cut: round, no beamwidth", beamwidth(*cut(ff, k, "theta", 90.0))[1] is None,
          f"{horizon.max() - horizon.min():.3f} dB")
    total = cut(ff, k, "phi", 30.0, component="total")[1]
    theta = cut(ff, k, "phi", 30.0, component="theta")[1]
    x = 10 ** (cut(ff, k, "phi", 30.0, component="ludwig3_x")[1] / 10)
    y = 10 ** (cut(ff, k, "phi", 30.0, component="ludwig3_y")[1] / 10)
    inner = total > total.max() - 20
    check(f"{label} components: E_theta is the total, Ludwig-3 x + y = total",
          np.abs(total - theta)[inner].max() < 0.01
          and np.abs(10 * np.log10(x + y) - total)[inner].max() < 0.01)


def shell(ff, k, label):
    points, faces, db = surface(ff, k)
    a, b, c = (points[faces[:, n]] for n in range(3))
    volume = float(np.sum(np.einsum("ij,ij->i", a, np.cross(b, c)))) / 6.0
    edges = np.sort(np.concatenate([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]]), axis=1)
    n_phi = len(ff["phi"])
    pole = (edges // n_phi == 0).all(axis=1) | (edges // n_phi == len(ff["theta"]) - 1).all(axis=1)
    _, counts = np.unique(edges[~pole], axis=0, return_counts=True)     # a pole row is one point, repeated
    peak = int(np.argmax(db))
    check(f"{label} 3D surface: closed, radius 1 at the maximum, wound outward",
          np.all(counts == 2) and abs(np.linalg.norm(points[peak]) - 1.0) < 1e-9 and volume > 0,
          f"volume {volume:.3f}")


def half_wave_dipole(ff, length):
    theta = np.radians(ff["theta"])
    u = intensity(ff["e_theta"].astype(np.complex128), ff["e_phi"].astype(np.complex128))
    i90 = at(ff, 90.0)
    for k, f in enumerate(ff["frequencies"]):
        label = f"L = {length:g} mm, {f / 1e9:g} GHz:"
        d = 4.0 * math.pi * u[k].max() / ff["p_sphere"][k]
        d_ref = dipole_directivity(f, length)
        check(f"{label} directivity within 0.2 dB", abs(10 * math.log10(d / d_ref)) < 0.2,
              f"{10 * math.log10(d):.2f} dBi (sinusoidal current: {10 * math.log10(d_ref):.2f})")
        ref = dipole_pattern(theta, f, length)
        worst = max(abs(10 * math.log10(u[k, i, 0] / u[k, i90, 0] / (ref[i] / ref[i90])))
                    for i in (at(ff, 30.0), at(ff, 45.0), at(ff, 60.0)))
        check(f"{label} pattern at 30/45/60 degrees within 0.6 dB", worst < 0.6, f"worst {worst:.2f} dB")
        common(ff, u, k, label)
        efficiency = ff["p_box"][k] / ff["p_acc"][k]
        check(f"{label} radiated = accepted (lossless)", abs(efficiency - 1) < 0.02, f"{efficiency:.4f}")


def coax_fed(folder):
    """A monopole fed through a coax whose waveguide port ends the domain: two boxes, one run."""
    path = os.path.join(folder, "coax_monopole.step")
    inner, diel, outer = 0.635, 2.11, 2.5          # mm: an SMA-like coax, PTFE
    write_step(path, [
        ("pin", rod_z(0.0, 46.0, inner), None),
        ("disk", BRepAlgoAPI_Cut(rod_z(20.0, 21.0, 30.0), rod_z(20.0, 21.0, diel)).Shape(), None),
        ("ptfe", BRepAlgoAPI_Cut(rod_z(0.0, 20.0, diel), rod_z(0.0, 20.0, inner)).Shape(), None),
        ("shield", BRepAlgoAPI_Cut(rod_z(0.0, 20.0, outer), rod_z(0.0, 20.0, diel)).Shape(), None),
        ("port", rod_z(-1.0, 0.0, outer), None),
    ])
    g = read_step(path)
    project = Project(path)
    for body in g.bodies:
        project.set_type(body, {"port": "waveport", "ptfe": "dielectric"}.get(body.name, "pec"))
    project.set_value("ptfe", "eps_r", 2.1)
    project.set_setting("simulation", "f_min", 2.0)
    project.set_setting("simulation", "f_max", 4.0)
    project.set_setting("simulation", "padding", 30.0)
    waveports = project.waveports(g)
    mesh = build_grid(g, project, {}, waveports)
    materials = discretize(mesh, g, project, waveports=waveports)
    if materials.errors or not materials.waveports:
        raise RuntimeError(f"bad model: {materials.errors}")
    solver = FdtdSolver(materials, 2.0, 4.0, active_port="port")     # GHz
    fixed = FarFieldBox(solver, FREQUENCIES)
    near_disk = FarFieldBox(solver, FREQUENCIES, gap=8)
    old = FarFieldBox(solver, FREQUENCIES, exclude_ports=False)
    solver.monitors += [fixed, near_disk, old]
    solver.run(end_db=-40.0)
    ff, ff_disk, ff_old = far_field(fixed, solver), far_field(near_disk, solver), far_field(old, solver)
    print(f"   coax-fed monopole over a disk: grid {mesh.grid.shape}, {solver.step_index} steps, "
          f"left out {int(ff['excluded_cells'])} cells of the box")
    for line in summary(ff):
        print(f"     {line}")
    check("coax-fed: the port's line left out, the box in air otherwise",
          int(ff["excluded_cells"]) > 0 and int(ff["cut_edges"]) == 0,
          f"{int(ff['excluded_cells'])} cells, {int(ff['cut_edges'])} edges in material")
    check("  nothing left out: the crossing is reported", int(ff_old["cut_edges"]) > 0,
          f"{int(ff_old['cut_edges'])} edges")
    for k, f in enumerate(FREQUENCIES):
        label = f"coax-fed, {f / 1e9:g} GHz:"
        efficiency = ff["p_box"][k] / ff["p_acc"][k]
        old_efficiency = ff_old["p_box"][k] / ff_old["p_acc"][k]
        check(f"{label} radiated = accepted (lossless)", abs(efficiency - 1) < 0.02,
              f"{efficiency:.4f} (nothing left out: {old_efficiency:.4f})")
        check(f"{label}   nothing left out: off by > 20 %", abs(old_efficiency - 1) > 0.2, f"{old_efficiency:.4f}")
        ratio = ff["p_sphere"][k] / ff["p_box"][k]
        check(f"{label} sphere integral = box flux within 6 % (cable through the box)", abs(ratio - 1) < 0.06,
              f"{ratio:.4f}")
        ratio = ff_disk["p_sphere"][k] / ff_disk["p_box"][k]
        check(f"{label}   the face under the disk: within 3 %", abs(ratio - 1) < 0.03, f"{ratio:.4f}")


def main():
    folder = tempfile.mkdtemp()
    try:
        short_dipole(run(folder, 10.0), 10.0)
        half_wave_dipole(run(folder, 47.0), 47.0)
        coax_fed(folder)
    finally:
        shutil.rmtree(folder, ignore_errors=True)

    print()
    print("all checks passed" if not FAILED else f"FAILED: {len(FAILED)}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
