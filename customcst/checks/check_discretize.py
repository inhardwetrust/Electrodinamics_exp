# check_discretize.py
#
# Level 3 checks on the microstrip (Z up, built here):
#   - the 35 um ground and strip become sheets of PEC edges on the substrate
#     faces (and nothing else is PEC);
#   - eps on edges: 4.3 inside the substrate, the dual-area weighted mean on
#     its faces, 1 in air; mu = 1; sigma from tan d at f_ref;
#   - ports: a column of edges along z between the sheets, touching PEC at
#     both ends, direction from - to +;
#   - a lossless model gives sigma = 0 everywhere;
#   - a thin board (0.2 mm FR4, auto min_step ~0.14 mm, so its cells are
#     finer than the merge tolerance): copper and ground stay sheets on
#     their own grid lines, no edge inside the board is PEC (the thin-body
#     test reached across a cell and filled the board with metal).
#
#   python checks/check_discretize.py

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

from discretize.materials import EPS0, discretize                        # noqa: E402
from geometry.step_io import read_step, write_step                       # noqa: E402
from mesh.rectilinear import build_grid                                  # noqa: E402
from project.project_file import Project                                 # noqa: E402

FAILED = []
MM = 1e-3


def check(label, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {label} {detail}")
    if not ok:
        FAILED.append(label)


def box(x0, y0, z0, x1, y1, z1):
    return BRepPrimAPI_MakeBox(gp_Pnt(x0, y0, z0), gp_Pnt(x1, y1, z1)).Shape()


def cone_z(x, y, z0, z1, r0, r1):
    return BRepPrimAPI_MakeCone(gp_Ax2(gp_Pnt(x, y, z0), gp_Dir(0, 0, 1)), r0, r1, z1 - z0).Shape()


def index_of(lines, value):
    i = int(np.argmin(np.abs(lines - value)))
    assert abs(lines[i] - value) < 1e-9, (value, lines[i])
    return i


def via_in_hole_vs_solid(folder):
    """
    A via (PEC cylinder through FR4 and both copper layers) in a drilled
    hole and the same via over solid substrate: metal cells are left out of
    the edge eps mean, so both must give the same solver materials.
    """
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder

    def cylinder(r, z0, z1):
        return BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(0, 0, z0), gp_Dir(0, 0, 1)), r, z1 - z0).Shape()

    results = []
    for drilled in (True, False):
        substrate = box(-5, -5, 0, 5, 5, 1.6)
        if drilled:
            substrate = BRepAlgoAPI_Cut(substrate, cylinder(0.3, -1, 3)).Shape()
        path = os.path.join(folder, f"via_{drilled}.step")
        write_step(path, [("substrate", substrate, None),
                          ("gnd", box(-5, -5, -0.035, 5, 5, 0), None),
                          ("top", box(-5, -5, 1.6, 5, 5, 1.635), None),
                          ("via", cylinder(0.3, -0.035, 1.635), None)])
        g = read_step(path, linear_deflection=2e-6)
        project = Project(path)
        for name in ("gnd", "top", "via"):
            project.set_type(g[name], "pec")
        project.set_type(g["substrate"], "dielectric")
        project.set_value("substrate", "eps_r", 4.3)
        project.set_setting("simulation", "padding", 1.0)
        mesh = build_grid(g, project, {})
        results.append((mesh, discretize(mesh, g, project)))

    (mesh_a, a), (mesh_b, b) = results
    # Up to rounding: the hole and the via are tessellated separately.
    same_grid = all(len(la) == len(lb) and np.allclose(la, lb, rtol=0, atol=1e-9)
                    for la, lb in zip(mesh_a.grid.lines, mesh_b.grid.lines))
    check("via: drilled and solid substrate give the same grid", same_grid)
    if not same_grid:
        return
    pec_diff = sum(int(np.sum(pa != pb)) for pa, pb in zip(a.pec, b.pec))
    check("via: same PEC edges", pec_diff == 0, f"{pec_diff} differ")
    free = [~(pa | pb) for pa, pb in zip(a.pec, b.pec)]
    worst = max(float(np.max(np.abs(ea - eb)[f], initial=0.0)) for ea, eb, f in zip(a.eps_r, b.eps_r, free))
    check("via: same eps on every free edge", worst < 1e-5, f"max |Δεr| = {worst:.2e}")


def thin_board(folder):
    path = os.path.join(folder, "thin.step")
    write_step(path, [("substrate", box(-8, -5, 0, 8, 5, 0.2), None),
                      ("gnd", box(-8, -5, 0.2, 8, 5, 0.235), None),
                      ("line", box(-0.5, -5, -0.035, 0.5, 5, 0), None)])
    g = read_step(path)
    project = Project(path)
    for name in ("gnd", "line"):
        project.set_type(g[name], "pec")
    project.set_type(g["substrate"], "dielectric")
    project.set_value("substrate", "eps_r", 4.3)
    project.set_setting("simulation", "f_max", 5.0)
    mesh = build_grid(g, project, {})
    m = discretize(mesh, g, project)
    z = mesh.grid.lines[2]
    inner = (z > 0.01 * MM) & (z < 0.19 * MM)
    check("thin board: a grid line inside the board, cells under the tolerance",
          inner.any() and np.diff(z[(z > -0.01 * MM) & (z < 0.21 * MM)]).min() < 0.99 * mesh.min_step,
          f"z {np.round(z[(z > -0.1 * MM) & (z < 0.3 * MM)] / MM, 3)} mm, min_step {mesh.min_step / MM:.3f} mm")
    pec_inside = int(m.pec[0][:, :, inner].sum() + m.pec[1][:, :, inner].sum())
    zc = 0.5 * (z[1:] + z[:-1])
    pec_inside += int(m.pec[2][:, :, (zc > 0.0) & (zc < 0.2 * MM)].sum())
    check("thin board: no PEC edge inside the board", pec_inside == 0, f"{pec_inside} PEC edges")
    x, y = mesh.grid.lines[0], mesh.grid.lines[1]
    kg, kl = int(np.argmin(np.abs(z - 0.2 * MM))), int(np.argmin(np.abs(z)))
    ix = slice(index_of(x, -8 * MM), index_of(x, 8 * MM))
    iy = slice(index_of(y, -5 * MM), index_of(y, 5 * MM) + 1)
    strip = slice(index_of(x, -0.5 * MM), index_of(x, 0.5 * MM))
    check("thin board: ground and strip are still sheets",
          m.pec[0][ix, iy, kg].all() and m.pec[0][strip, iy, kl].all()
          and not m.pec[0][index_of(x, 0.5 * MM) + 1:, iy, kl].any())


def main():
    folder = tempfile.mkdtemp()
    try:
        path = os.path.join(folder, "line.step")
        write_step(path, [
            ("gnd", box(-25, -15, 0, 25, 15, 0.035), None),
            ("substrate", box(-25, -15, 0.035, 25, 15, 1.635), None),
            ("line", box(-25, -1.5, 1.635, 25, 1.5, 1.67), None),
            ("port1", cone_z(25, 0, 0, 1.67, 0.8, 0.5), None),
            ("port2", cone_z(-25, 0, 0, 1.67, 0.8, 0.5), None),
        ])
        g = read_step(path)
        project = Project(path)
        for name, t in (("gnd", "pec"), ("line", "pec"), ("substrate", "dielectric"),
                        ("port1", "port"), ("port2", "port")):
            project.set_type(g[name], t)
        project.set_value("substrate", "eps_r", 4.3)
        project.set_value("substrate", "tan_d", 0.02)
        project.set_value("substrate", "f_ref", 2.4)
        project.set_setting("simulation", "padding", 10.0)

        ports = project.ports(g)
        mesh = build_grid(g, project, ports)
        m = discretize(mesh, g, project, ports)
        x, y, z = mesh.grid.lines
        nx, ny, nz = mesh.grid.shape
        check("no errors", not m.errors, "; ".join(m.errors))

        kb, kt = index_of(z, 0.035 * MM), index_of(z, 1.635 * MM)
        ix0, ix1 = index_of(x, -25 * MM), index_of(x, 25 * MM)
        iy0, iy1 = index_of(y, -15 * MM), index_of(y, 15 * MM)
        js0, js1 = index_of(y, -1.5 * MM), index_of(y, 1.5 * MM)

        # --- PEC ------------------------------------------------------------
        ex, ey, ez = m.pec
        ground = ex[ix0:ix1, iy0:iy1 + 1, kb]
        check("ground plane: all Ex edges on z = 0.035 are PEC", ground.all(), f"{ground.mean():.3f}")
        strip = ex[ix0:ix1, js0:js1 + 1, kt]
        check("strip: Ex edges on z = 1.635, |y| <= 1.5 are PEC", strip.all())
        beside = ex[ix0:ix1, js1 + 1:iy1 + 1, kt]
        check("beside the strip: not PEC", not beside.any())
        expected = (ix1 - ix0) * (iy1 - iy0 + 1) + (ix1 - ix0) * (js1 - js0 + 1)
        check("no other Ex edge is PEC", ex.sum() == expected, f"{ex.sum()} vs {expected}")
        check("no Ez edge is PEC (copper has no vertical extent on the grid)", not ez.any())

        # --- eps / mu / sigma ----------------------------------------------
        eps_x = m.eps_r[0]
        inside = eps_x[ix0:ix1, iy0 + 1:iy1, kb + 1:kt]
        check("eps = 4.3 inside the substrate", np.allclose(inside, 4.3))
        check("eps = 1 far above", np.allclose(eps_x[:, :, -1], 1.0))

        hz = np.diff(z)
        below, above = hz[kt - 1], hz[kt]
        weighted = (4.3 * below + 1.0 * above) / (below + above)
        got = eps_x[(ix0 + ix1) // 2, js1 + 2, kt]
        check("eps on the substrate top = dual-area weighted mean", abs(got - weighted) < 1e-5,
              f"{got:.5f} vs {weighted:.5f}")

        corner = eps_x[(ix0 + ix1) // 2, iy1, kt]          # board edge: 1 of 4 cells is FR4
        hy = np.diff(y)
        w_in = hy[iy1 - 1] * below
        w_all = (hy[iy1 - 1] + hy[iy1]) * (below + above)
        expected_corner = (4.3 * w_in + (w_all - w_in)) / w_all
        check("eps on the board's top edge (1 of 4 cells)", abs(corner - expected_corner) < 1e-5,
              f"{corner:.5f} vs {expected_corner:.5f}")

        check("mu = 1 everywhere", all(np.allclose(a, 1.0) for a in m.mu_r))
        sigma = 2 * math.pi * 2.4e9 * EPS0 * 4.3 * 0.02
        s_in = m.sigma[0][ix0:ix1, iy0 + 1:iy1, kb + 1:kt]
        check("sigma = 2 pi f eps0 eps_r tan d in the substrate", np.allclose(s_in, sigma, rtol=1e-5),
              f"{s_in.mean():.4e} vs {sigma:.4e} S/m")

        # --- ports ----------------------------------------------------------
        check("two ports", len(m.ports) == 2, str([p.name for p in m.ports]))
        for port in m.ports:
            ks = port.edges[:, 2]
            ok = (port.axis == 2 and port.sign == 1 and ks.min() == kb and ks.max() == kt - 1
                  and np.all(np.diff(ks) == 1))
            check(f"{port.name}: z column from the ground to the strip", ok,
                  f"{len(ks)} edges, {port.length / MM:.3f} mm")
            check(f"{port.name}: length = 1.6 mm", abs(port.length - 1.6 * MM) < 1e-9)
            check(f"{port.name}: R = 50", port.resistance == 50.0)

        # --- shapes --------------------------------------------------------
        check("array shapes", m.eps_r[0].shape == (nx, ny + 1, nz + 1)
              and m.mu_r[0].shape == (nx + 1, ny, nz) and m.pec[2].shape == (nx + 1, ny + 1, nz))

        # --- lossless ------------------------------------------------------
        project.set_value("substrate", "tan_d", 0.0)
        m0 = discretize(mesh, g, project, ports)
        check("tan d = 0 -> sigma = 0", all(not a.any() for a in m0.sigma))

        # --- large tolerance (regression) ----------------------------------
        # A big min_step (here: air substrate -> 0.3 mm) once turned the port
        # column next to the ground sheet into PEC: the tolerance must only
        # act across an edge, never along it.
        project.set_value("substrate", "eps_r", 1.0)
        project.set_setting("simulation", "padding", 0.0)
        big = build_grid(g, project, ports)
        mb = discretize(big, g, project, ports)
        zb = big.grid.lines[2]
        check("large tolerance: no port errors", not mb.errors and len(mb.ports) == 2,
              "; ".join(mb.errors) + f" (min_step {big.min_step / MM:.3f} mm)")
        check("large tolerance: no vertical PEC edges", not mb.pec[2].any())
        kb2 = index_of(zb, 0.035 * MM)
        xb, yb = big.grid.lines[0], big.grid.lines[1]
        sheet = mb.pec[0][index_of(xb, -25 * MM):index_of(xb, 25 * MM),
                          index_of(yb, -15 * MM):index_of(yb, 15 * MM) + 1, kb2]
        check("large tolerance: ground is still a full sheet", sheet.all())

        via_in_hole_vs_solid(folder)
        thin_board(folder)
    finally:
        shutil.rmtree(folder)

    print("FAILED: " + ", ".join(FAILED) if FAILED else "all checks passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
