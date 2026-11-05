# check_local_mesh.py
#
# A body's local mesh ([local_mesh.NAME], project/project_file.py; mesh/
# rectilinear.py step 4b).
#
# The project file:
#   - created from the main mesh's values (max step 0), set, refused
#     values, removed; saved and read back; an unknown key refused; a body
#     no longer in the model listed as an orphan;
#   - the run fingerprint: the same as before without local meshes (older
#     runs stay current), changed by one and by a value of it.
# The grid (a closed can with a short probe pin inside, its bottom 1 mm
# thick):
#   - the step over the can's extent follows its local cells per lambda
#     (and max step); the padding air and the pin keep the main mesh's;
#   - the local cells per feature refines the can's inside (one feature
#     across: the wall is curved) but not its 1 mm bottom (its own metal:
#     a wall, not a gap): the smallest step stays the main mesh's;
#   - no local mesh: the grid is the same as before.
# Physics: the can as a cavity, TM010, f = 2.405 c / (2 pi a), a = 10 mm:
# 11.47 GHz (the probe pin, 1.5 mm at the axis, pulls it down a little).
# The staircased wall makes the coarse main mesh (10 cells per lambda,
# 2.1 mm steps on a 10 mm radius) miss it; the local mesh on the can (40
# per lambda) lands within 0.3 % of the main mesh at 40 everywhere, with
# fewer cells (the 10 mm of air around keep the coarse step), and both
# within 1 % of the formula.
#
#   python checks/check_local_mesh.py      (~1 min)

import math
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

import numpy as np                                                       # noqa: E402
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut                             # noqa: E402

from checks.check_farfield import cone_z, rod_z                          # noqa: E402
from discretize.materials import discretize                              # noqa: E402
from geometry.step_io import read_step, write_step                       # noqa: E402
from mesh.rectilinear import build_grid                                  # noqa: E402
from project.project_file import Project, ProjectError                   # noqa: E402
from project.settings import C0                                          # noqa: E402
from solver.fdtd import FdtdSolver                                       # noqa: E402
from solver.result_file import fingerprint                               # noqa: E402

FAILED = []
A = 10.0                # mm, the can's inner radius
H = 15.0                # mm, its inner height
F_LO, F_HI = 9.0, 14.0  # GHz (TE111 at 13.3)


def check(label, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {label} {detail}")
    if not ok:
        FAILED.append(label)


def write_can(folder):
    path = os.path.join(folder, "can.step")
    can = BRepAlgoAPI_Cut(rod_z(-1.0, H + 1.0, A + 1.0), rod_z(0.0, H, A)).Shape()
    write_step(path, [
        ("CAN", can, None),
        ("pin", rod_z(0.5, 2.0, 0.25), None),
        ("feed", cone_z(0.0, 0.5, 0.2, 0.15), None),       # bottom (-) to the pin (+)
    ])
    g = read_step(path)
    p = Project(path)
    for body in g.bodies:
        p.set_type(body, "port" if body.name == "feed" else "pec")
    p.set_setting("simulation", "f_max", F_HI)
    p.set_setting("simulation", "f_min", F_LO)
    p.set_setting("simulation", "padding", 10.0)        # air around, as an antenna has: what the local mesh spares
    p.set_setting("mesh", "cells_per_feature", 4)
    p.set_setting("mesh", "min_step", 0.1)
    p.save()
    return path, g


def project_checks(path, g):
    p = Project.load(path)
    before = fingerprint(p)
    p.set_setting("mesh", "cells_per_wavelength", 25)
    p.create_local_mesh("CAN")
    check("created from the main mesh's values",
          p.local_mesh["CAN"] == {"cells_per_wavelength": 25.0, "cells_per_feature": 4.0, "max_step": 0.0})
    p.set_setting("mesh", "cells_per_wavelength", 20)
    with_local = fingerprint(p)
    p.remove_local_mesh("CAN")
    check("no local mesh: the fingerprint as before", fingerprint(p) == before)
    p.create_local_mesh("CAN")
    p.local_mesh["CAN"]["cells_per_wavelength"] = 25.0
    check("a local mesh changes the fingerprint", fingerprint(p) == with_local != before)
    p.set_local_mesh_value("CAN", "max_step", "0.7")
    check("a value of it changes the fingerprint too", fingerprint(p) != with_local)
    for key, bad in (("cells_per_wavelength", 2), ("max_step", -1), ("max_step", "x"), ("nope", 1)):
        try:
            p.set_local_mesh_value("CAN", key, bad)
            check(f"{key} = {bad!r} refused", False)
        except (ValueError, ProjectError) as error:
            check(f"{key} = {bad!r} refused", True, f"({error})")
    p.create_local_mesh("gone")
    p.save()
    back = Project.load(path)
    check("saved and read back", back.local_mesh == p.local_mesh, f"{back.local_mesh}")
    check("a local mesh of a missing body is an orphan", back.orphans(g) == ["gone"])
    back.remove_type("CAN")
    check("removing the type keeps the local mesh", "CAN" in back.local_mesh)
    with open(back.path, encoding="utf-8") as f:
        text = f.read()
    with open(back.path, "w", encoding="utf-8") as f:
        f.write(text.replace("max_step = 0.7", "max_step = 0.7\nfoo = 1"))
    try:
        Project.load(path)
        check("an unknown key refused", False)
    except ProjectError as error:
        check("an unknown key refused", True, f"({error})")
    p.local_mesh.clear()
    p.save()


def steps_inside(lines, lo, hi):
    l = np.asarray(lines) * 1e3
    st, mid = np.diff(l), 0.5 * (l[1:] + l[:-1])
    return st[(mid > lo) & (mid < hi)]


def grid_of(path, g, cells, local=None):
    p = Project.load(path)
    p.set_setting("mesh", "cells_per_wavelength", cells)
    if local:
        p.create_local_mesh("CAN")
        for key, value in local.items():
            p.set_local_mesh_value("CAN", key, value)
    ports = p.ports(g)
    return p, ports, build_grid(g, p, ports)


def grid_checks(path, g):
    _, _, coarse = grid_of(path, g, 10)
    _, _, local = grid_of(path, g, 10, {"cells_per_wavelength": 40})
    x, z = coarse.grid.lines[0], coarse.grid.lines[2]
    lx, lz = local.grid.lines[0], local.grid.lines[2]
    h40 = C0 / (F_HI * 1e9) / 40 * 1e3
    inside = steps_inside(lx, -A, A)
    check("local: the step inside the can follows its 40 per λ", inside.max() <= h40 * 1.001,
          f"max {inside.max():.3f} mm, λ/40 {h40:.3f} mm (main: {steps_inside(x, -A, A).max():.3f})")
    check("local: the padding air keeps the main mesh's step",
          abs(np.diff(lx)[0] - np.diff(x)[0]) < 0.25 * np.diff(x)[0],
          f"{np.diff(lx)[0] * 1e3:.2f} against {np.diff(x)[0] * 1e3:.2f} mm")
    check("local: the pin keeps its cells", len(steps_inside(lx, -0.25, 0.25)) == len(steps_inside(x, -0.25, 0.25)),
          f"{len(steps_inside(lx, -0.25, 0.25))} cells across")
    check("local: finest step reported", abs(local.local_steps["CAN"] * 1e3 - h40) < 1e-6)
    _, _, capped = grid_of(path, g, 10, {"max_step": 0.7})
    check("max step bounds the step over the can", steps_inside(capped.grid.lines[0], -A, A).max() <= 0.7 + 1e-9)

    _, _, feature = grid_of(path, g, 10, {"cells_per_feature": 30})
    fz = feature.grid.lines[2]
    check("local cells per feature: the inside refined (one feature across)",
          steps_inside(fz, 2.0, H).max() <= (H - 0.0) / 30 * 1.001 + 1e-9,
          f"max {steps_inside(fz, 2.0, H).max():.3f} mm")
    check("local cells per feature: not the 1 mm bottom (metal)",
          len(steps_inside(fz, -1.0, 0.0)) == len(steps_inside(z, -1.0, 0.0)),
          f"{len(steps_inside(fz, -1.0, 0.0))} cells in the bottom")
    check("local cells per feature: the smallest step the main mesh's",
          min(np.diff(a).min() for a in feature.grid.lines) >= min(np.diff(a).min() for a in coarse.grid.lines) - 1e-12)

    p = Project.load(path)
    p.set_setting("mesh", "cells_per_wavelength", 10)
    plain = build_grid(g, p, p.ports(g))
    same = all(np.array_equal(a, b) for a, b in zip(plain.grid.lines, coarse.grid.lines))
    check("no local mesh: the same grid", same and plain.local == [[], [], []] and plain.local_steps == {})


def resonance(path, g, cells, local=None, steps=None):
    p, ports, mesh = grid_of(path, g, cells, local)
    materials = discretize(mesh, g, p, ports)
    if materials.errors or not materials.ports:
        raise RuntimeError(f"bad model: {materials.errors}")
    solver = FdtdSolver(materials, F_LO, F_HI, active_port="feed")
    n = steps or int(round(12e-9 / solver.dt))          # 12 ns: the closed can rings long
    solver.step(n)
    f = np.linspace(10.5e9, 12.5e9, 801)                # TM010, below TE111 (13.3 GHz)
    spectrum = np.concatenate([np.abs(solver.spectra(f[i:i + 50])["feed"][0])    # in chunks: memory
                               for i in range(0, len(f), 50)])
    k = int(np.argmax(spectrum))
    y0, y1, y2 = np.log(spectrum[k - 1:k + 2])
    k = k + 0.5 * (y0 - y2) / (y0 - 2 * y1 + y2)
    return float(np.interp(k, np.arange(len(f)), f)), mesh.grid.cell_count


def physics_checks(path, g):
    exact = 2.404826 * C0 / (2 * math.pi * A * 1e-3)
    coarse, n_coarse = resonance(path, g, 10)
    local, n_local = resonance(path, g, 10, {"cells_per_wavelength": 40})
    fine, n_fine = resonance(path, g, 40)
    for label, f, n in (("main 10/λ", coarse, n_coarse), ("main 10/λ + local 40/λ", local, n_local),
                        ("main 40/λ", fine, n_fine)):
        print(f"     {label:24s} TM010 {f / 1e9:.4f} GHz ({100 * (f / exact - 1):+.2f} % of the formula), "
              f"{n:,} cells")
    check("the coarse mesh misses TM010 by more than the local one",
          abs(coarse - fine) > 3 * abs(local - fine), f"{100 * (coarse / fine - 1):+.2f} % against "
          f"{100 * (local / fine - 1):+.2f} %")
    check("local mesh within 0.3 % of the fine main mesh", abs(local / fine - 1) < 0.003)
    check("local mesh: fewer cells than the fine main mesh", n_local < 0.7 * n_fine, f"{n_local:,} / {n_fine:,}")
    check("fine and local within 1 % of the formula", abs(fine / exact - 1) < 0.01 and abs(local / exact - 1) < 0.01,
          f"{exact / 1e9:.4f} GHz")


def main():
    folder = tempfile.mkdtemp()
    try:
        path, g = write_can(folder)
        project_checks(path, g)
        grid_checks(path, g)
        physics_checks(path, g)
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    print()
    print("all checks passed" if not FAILED else f"FAILED: {len(FAILED)}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
