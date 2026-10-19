# check_vias.py
#
# Vias assigned to recognized holes (project hole rules + holes.via_body),
# on a board built here: FR4 20 x 10 x 1.6 mm, top copper and ground
# (35 um); holes 0.4 mm at x = -6, -3, 0 (the one at 0 passes a 1.0 mm
# antipad in the ground), a 3.2 mm mounting hole.
#   - rules: the 0.4 mm drill -> via, the hole at x = -3 -> none (a single
#     rule beats the diameter rule); round trip through the TOML; bad rules
#     rejected; the results fingerprint follows the rules;
#   - solver model: a PEC column joins top and ground through each via, the
#     excluded hole and the mounting hole stay open; the antipad via touches
#     the top copper only;
#   - the same vias drawn as PEC cylinders in the STEP give the same PEC
#     edges (up to a few edges of tessellation noise).
#
#   python checks/check_vias.py

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
from geometry.holes import find_holes, via_body                          # noqa: E402
from geometry.step_io import read_step, write_step                       # noqa: E402
from mesh.rectilinear import build_grid                                  # noqa: E402
from project.project_file import Project, ProjectError                   # noqa: E402
from solver.result_file import fingerprint                               # noqa: E402

FAILED = []
MM = 1e-3
VIAS = [(-6.0, 0.0), (-3.0, 0.0), (0.0, 0.0)]


def check(label, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {label} {detail}")
    if not ok:
        FAILED.append(label)


def box(x0, y0, z0, x1, y1, z1):
    return BRepPrimAPI_MakeBox(gp_Pnt(x0, y0, z0), gp_Pnt(x1, y1, z1)).Shape()


def cylinder(x, y, d, z0, z1):
    return BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(x, y, z0), gp_Dir(0, 0, 1)), d / 2, z1 - z0).Shape()


def cut(shape, *tools):
    for tool in tools:
        shape = BRepAlgoAPI_Cut(shape, tool).Shape()
    return shape


def board(path, drawn_vias=()):
    drills = [cylinder(x, y, 0.4, -1, 3) for x, y in VIAS] + [cylinder(7.0, 0.0, 3.2, -1, 3)]
    bodies = [
        ("substrate", cut(box(-10, -5, 0, 10, 5, 1.6), *drills), None),
        ("top", cut(box(-10, -5, 1.6, 10, 5, 1.635), *drills), None),
        ("gnd", cut(box(-10, -5, -0.035, 10, 5, 0), *drills[:2], drills[3], cylinder(0, 0, 1.0, -1, 3)), None),
    ]
    bodies += [(f"pin{n}", cylinder(x, y, 0.4, -0.035, 1.635), None) for n, (x, y) in enumerate(drawn_vias)]
    write_step(path, bodies)
    g = read_step(path)
    p = Project(path)
    for name in ["top", "gnd"] + [f"pin{n}" for n in range(len(drawn_vias))]:
        p.set_type(g[name], "pec")
    p.set_type(g["substrate"], "dielectric")
    p.set_material("substrate", "FR4")
    p.set_setting("simulation", "padding", 1.0)
    return g, p


def solve(g, p, vias):
    mesh = build_grid(g, p, {}, None, vias)
    return mesh, discretize(mesh, g, p, vias=vias)


def column(mesh, m, x, y):
    """PEC state of the z-edges through the node nearest (x, y) between z = -0.035 and 1.635 mm."""
    lx, ly, lz = mesh.grid.lines
    i, j = int(np.argmin(np.abs(lx - x * MM))), int(np.argmin(np.abs(ly - y * MM)))
    k0, k1 = int(np.argmin(np.abs(lz + 0.035 * MM))), int(np.argmin(np.abs(lz - 1.635 * MM)))
    return m.pec[2][i, j, k0:k1]


def joined(mesh, m):
    """Top copper and ground in one PEC component (edge graph of the grid nodes)."""
    import scipy.sparse as sp
    from scipy.sparse.csgraph import connected_components

    shape = tuple(len(l) for l in mesh.grid.lines)
    ids = np.arange(np.prod(shape)).reshape(shape)
    a, b = [], []
    for axis in range(3):
        lo = [slice(None)] * 3
        hi = [slice(None)] * 3
        lo[axis], hi[axis] = slice(0, -1), slice(1, None)
        a.append(ids[tuple(lo)][m.pec[axis]])
        b.append(ids[tuple(hi)][m.pec[axis]])
    a, b = np.concatenate(a), np.concatenate(b)
    n = ids.size
    _, label = connected_components(sp.coo_matrix((np.ones(len(a)), (a, b)), shape=(n, n)), directed=False)
    lx, ly, lz = mesh.grid.lines
    corner = lambda z: ids[int(np.argmin(np.abs(lx + 9 * MM))), int(np.argmin(np.abs(ly + 4 * MM))),
                           int(np.argmin(np.abs(lz - z * MM)))]
    return label[corner(1.635)] == label[corner(0.0)]


def main():
    folder = tempfile.mkdtemp()
    try:
        path = os.path.join(folder, "board.step")
        g, p = board(path)
        holes = find_holes(g)
        by_x = {round(h.center[0] / MM, 3): h for h in holes if abs(h.diameter - 0.4 * MM) < 1e-9}

        # --- rules ---------------------------------------------------------------
        base = fingerprint(p)
        p.set_hole_diameter(0.4, "via")
        p.set_hole_single(by_x[-3.0].center / MM, "none")
        check("fingerprint follows the rules", fingerprint(p) != base)
        vias = p.vias(holes)
        check("0.4 mm -> via, except the single 'none'", sorted(h.center[0] / MM for h in vias) == [-6.0, 0.0],
              str([round(h.center[0] / MM, 3) for h in vias]))
        check("rule sources", p.hole_rule(by_x[-6.0]) == ("via", "diameter")
              and p.hole_rule(by_x[-3.0]) == ("none", "single"))
        mounting = [h for h in holes if abs(h.diameter - 3.2 * MM) < 1e-9][0]
        check("no rule -> not a via", p.hole_rule(mounting) == ("none", ""))
        p.save()
        back = Project.load(path)
        check("rules round trip", back.hole_diameters == p.hole_diameters and back.hole_single == p.hole_single,
              f"{back.hole_diameters} {back.hole_single}")
        check("same vias after reload", [h.id for h in back.vias(holes)] == [h.id for h in vias])
        text = open(back.path, encoding="utf-8").read()
        check("readable TOML", '"0.4" = "via"' in text and '"-3,0,0.8" = "none"' in text,
              text[text.find("[holes"):])
        p.set_hole_single(by_x[-3.0].center / MM, None)
        check("removing the single rule -> the diameter rule again", p.hole_rule(by_x[-3.0]) == ("via", "diameter"))
        p.set_hole_single(by_x[-3.0].center / MM, "none")

        for label, text_, needle in (
            ("bad kind", '[holes.diameters]\n"0.4" = "gold"\n', "expected one of"),
            ("bad drill", '[holes.diameters]\n"abc" = "via"\n', "not a diameter"),
            ("bad center", '[holes.single]\n"1,2" = "via"\n', "not a center"),
            ("unknown table", '[holes.other]\n', "expected the tables"),
        ):
            with open(p.path, "w", encoding="utf-8") as f:
                f.write(text_)
            try:
                Project.load(path)
                ok, msg = False, "no error"
            except ProjectError as error:
                ok, msg = needle in str(error), str(error)
            check(f"rejected: {label}", ok, msg)

        # --- solver model ------------------------------------------------------------
        via_bodies = [via_body(h) for h in vias]
        mesh, m = solve(g, p, via_bodies)
        check("discretized without errors", not m.errors, m.errors)
        col = {x: column(mesh, m, x, 0.0) for x in (-6.0, -3.0, 0.0)}
        check("via at x = -6: PEC column top -> ground", col[-6.0].all(), str(col[-6.0].astype(int)))
        check("excluded hole at x = -3: open", not col[-3.0].any(), str(col[-3.0].astype(int)))
        check("mounting hole: open", not column(mesh, m, 7.0, 0.0).any())
        check("antipad via: PEC column (touches the top copper)", col[0.0].all(), str(col[0.0].astype(int)))
        # Only the antipad via: top and ground must stay apart (the clearance
        # is staircased, but no metal path may cross it); with the x = -6
        # via they are one conductor.
        only = Project(path)
        only.assignments, only.materials, only.settings = p.assignments, p.materials, p.settings
        only.set_hole_single(by_x[0.0].center / MM, "via")
        mesh_a, m_a = solve(g, only, [via_body(h) for h in only.vias(holes)])
        check("antipad via alone: top and ground stay apart", not joined(mesh_a, m_a))
        check("a via through the ground joins top and ground", joined(mesh, m))

        _, m_none = solve(g, p, [])
        check("without vias the holes are open", not column(mesh, m_none, -6.0, 0.0).any())

        # --- same as drawn cylinders -----------------------------------------------
        path2 = os.path.join(folder, "drawn.step")
        g2, p2 = board(path2, drawn_vias=[(-6.0, 0.0), (0.0, 0.0)])
        mesh2, m2 = solve(g2, p2, [])
        # Within 1 um: only the extents of the round vias are fixpoints, and a
        # 48-gon's extent and OCP's tessellation differ by ~0.5 um.
        same_grid = all(len(a) == len(b) and np.allclose(a, b, rtol=0, atol=1e-6)
                        for a, b in zip(mesh.grid.lines, mesh2.grid.lines))
        check("drawn cylinders: same grid (within 1 um)", same_grid)
        if same_grid:
            diff = sum(int(np.sum(a != b)) for a, b in zip(m.pec, m2.pec))
            via_edges = sum(int(np.sum(a)) for a in m.pec) - sum(int(np.sum(a)) for a in m_none.pec)
            check("drawn cylinders: same PEC edges (<= 2 % of the via edges)", diff <= max(2, 0.02 * via_edges),
                  f"{diff} differ of {via_edges} via edges")
    finally:
        shutil.rmtree(folder)

    print("FAILED: " + ", ".join(FAILED) if FAILED else "all checks passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
