# check_kicad.py
#
# The KiCad import (geometry/kicad_step.py) on a board written here the way
# KiCad exports it (parts named t_PCB, t_copper, t_pad, t_via):
#   - top copper: a pour with a clearance slot and a 0.3 mm trace in it
#     whose end is rounded, 0.25 mm from the slot's rounded end; bottom
#     copper: a full plane; a 40 um pad on the trace (KiCad's pads are 5 um
#     thicker than the copper) and two vias;
#   - plated holes the way newer exports draw them: a via as a tube (outer
#     radius 0.15 = the drill, inner 0.125), and in the pads two tubes
#     40 um past the copper with a ring on each outer layer; the board
#     drilled for all but one of them. Each tube becomes a solid cylinder
#     of radius 0.15 over the copper's z range in "vias", the rings join
#     their layers, no "pads" body, and the undrilled one is reported;
#   - a blind via (from F.Cu halfway down) is made through and counted;
#     "vias" then holds 6 through vias: their volume, the copper's z range;
#   - the result: bodies F.Cu (2 pieces, one body), B.Cu, vias, board; the
#     pad cut to the copper's 35 um, no face at the pad's top left;
#   - discretized at min step 0.08 mm, cells per feature 4, ~0.25 mm cells
#     along the trace (the rounded
#     gap at the trace's end spans about one cell), the trace is not joined
#     to the pour by PEC edges: the closure of a conductor's metal runs per
#     piece. Closed per body (the pieces dropped) the same grid joins them:
#     that bridge was the short of a KiCad board;
#   - a lumped element across the trace-pour gap: both ends on F.Cu is fine;
#     MaterialGrid.shorts is empty, and lists the element (bypassed) when
#     the pieces are dropped and the grid bridges the gap.
#
#   python checks/check_kicad.py      (~20 s)

import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

import numpy as np                                                       # noqa: E402
import scipy.sparse as sp                                                # noqa: E402
from OCP.BRep import BRep_Builder                                        # noqa: E402
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse            # noqa: E402
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder  # noqa: E402
from OCP.TopoDS import TopoDS_Compound                                   # noqa: E402
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt                                # noqa: E402
from scipy.sparse.csgraph import connected_components                    # noqa: E402

from discretize.materials import discretize                              # noqa: E402
from geometry.kicad_step import prepare_kicad_step                       # noqa: E402
from geometry.step_io import read_step, write_step                       # noqa: E402
from mesh.rectilinear import build_grid                                  # noqa: E402
from project.project_file import Project                                 # noqa: E402

FAILED = []
T = 1.5                     # board thickness, mm
CU = 0.035
W, GAP = 0.3, 0.25          # trace width, clearance
END = 6.0                   # x of the trace's rounded end (center of the arc)


def check(label, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {label} {detail}")
    if not ok:
        FAILED.append(label)


def box(x0, y0, z0, x1, y1, z1):
    return BRepPrimAPI_MakeBox(gp_Pnt(x0, y0, z0), gp_Pnt(x1, y1, z1)).Shape()


def disk(x, y, z0, r, h):
    return BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(x, y, z0), gp_Dir(0, 0, 1)), r, h).Shape()


def compound(shapes):
    c = TopoDS_Compound()
    b = BRep_Builder()
    b.MakeCompound(c)
    for s in shapes:
        b.Add(c, s)
    return c


def stadium(x0, x1, half, z0, h):
    """A slot along x from x0 to x1 (rounded at x1), half width `half`."""
    return BRepAlgoAPI_Fuse(box(x0, -half, z0, x1, half, z0 + h), disk(x1, 0, z0, half, h)).Shape()


def main():
    folder = tempfile.mkdtemp()
    try:
        src = os.path.join(folder, "board.step")
        top = T
        pour = BRepAlgoAPI_Cut(box(0, -4, top, 10, 4, top + CU), stadium(-1, END, W / 2 + GAP, top, CU)).Shape()
        trace = stadium(-1, END, W / 2, top, CU)
        trace = BRepAlgoAPI_Cut(trace, box(-2, -1, top - 1, 0, 1, top + 1)).Shape()   # from the board's edge
        bottom = box(0, -4, -CU, 10, 4, 0)
        pad = box(2, -W / 2, top, 3, W / 2, top + 0.04)                                  # 40 um, on the trace
        vias = [disk(8, 3, -CU, 0.15, T + 2 * CU), disk(8, -3, -CU, 0.15, T + 2 * CU)]

        def tube(x, y, z0, h, outer=0.15, inner=0.125):
            return BRepAlgoAPI_Cut(disk(x, y, z0, outer, h), disk(x, y, z0 - 1, inner, h + 2)).Shape()

        vias.append(tube(8, 0, -CU, T + 2 * CU))                                       # a via as plating
        vias.append(disk(8, 1.5, T / 2, 0.15, T / 2 + CU))                             # blind, from F.Cu
        plated = [(5, -3), (3, 3)]                                                       # (3, 3): not drilled
        rings = []
        for x, y in plated:
            rings += [tube(x, y, top, 0.04, 0.3), tube(x, y, -0.04, 0.04, 0.3)]
        barrels = [tube(x, y, -0.04, T + 0.08) for x, y in plated]
        board = box(0, -4, 0, 10, 4, T)
        for x, y in [(8, 0), plated[0]]:
            board = BRepAlgoAPI_Cut(board, disk(x, y, -1, 0.15, T + 2)).Shape()
        write_step(src, [("t_PCB", board, None),
                         ("t_copper", compound([pour, trace, bottom]), None),
                         ("t_pad", compound([pad] + rings + barrels), None),
                         ("t_via", compound(vias), None)])

        dst = os.path.join(folder, "board_prep.stp")
        report = prepare_kicad_step(src, dst)
        g = read_step(dst)
        names = sorted(b.name for b in g.bodies)
        check("bodies F.Cu, B.Cu, vias, board", names == ["B.Cu", "F.Cu", "board", "vias"], str(names))
        f_cu = g["F.Cu"]
        check("F.Cu: one body of 2 pieces (pour, trace)", f_cu.pieces is not None and len(f_cu.pieces) == 2,
              f"{0 if f_cu.pieces is None else len(f_cu.pieces)} pieces")
        top_z = np.array(f_cu.bounds)[1][2] * 1e3
        check("pad cut to the copper (no 40 um top)", abs(top_z - (top + CU)) < 1e-6, f"top {top_z:.4f} mm")
        v = g["vias"]
        check("vias: one body of 6 (2 solid, a tube, a blind one, 2 plated pad holes)",
              v.pieces is not None and len(v.pieces) == 6, f"{0 if v.pieces is None else len(v.pieces)}")
        want = 6 * np.pi * 0.15 ** 2 * (T + 2 * CU)
        # From the triangles: a few tenths of a percent under the exact
        # cylinders. Unfilled and blind, it would be 0.38 mm3.
        check("tubes filled, the blind via through: the volume of 6 through vias",
              abs(v.volume * 1e9 - want) < 0.02 * want, f"{v.volume * 1e9:.5f} vs {want:.5f} mm3")
        line = next((s for s in report.lines if s.startswith("vias: 4 ")), "")
        check("the report counts the tube and the blind via",
              "1 drawn as plating tubes" in line and "1 blind or buried, made through" in line, line)
        z = np.array(v.bounds)[:, 2] * 1e3
        check("vias span the copper exactly (the pads' 40 um cut)",
              np.allclose(z, [-CU, T + CU], atol=1e-6), f"z {z}")
        undrilled = [w for w in report.warnings if "no hole" in w]
        check("the undrilled plated hole is reported (only it)",
              len(undrilled) == 1 and "(3.000, 3.000)" in undrilled[0], str(report.warnings))

        # A lumped element across the gap, axis in the copper's middle.
        r1 = BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(4.0, 0.0, top + CU / 2), gp_Dir(0, 1, 0)), 0.05, 0.6).Shape()
        with_r = os.path.join(folder, "with_r.stp")
        from geometry.kicad_step import _solids, _write
        _write(with_r, [(b.name, _solids(b.shape), None) for b in g.bodies] + [("R1", [r1], None)])
        g = read_step(with_r)
        project = Project(with_r)
        for b in g.bodies:
            project.set_type(b, "lumped" if b.name == "R1" else "dielectric" if b.name == "board" else "pec")
        project.set_setting("simulation", "padding", 1.0)
        project.set_setting("mesh", "min_step", 0.08)
        project.set_setting("mesh", "cells_per_feature", 4)
        project.set_setting("simulation", "f_max", 6.0)
        project.set_setting("mesh", "cells_per_wavelength", 200)     # ~0.25 mm along x, as on a real board
        elements = project.elements(g)
        e = elements["R1"]
        check("lumped across the gap, both ends on F.Cu", e.ok and e.minus_body == e.plus_body == "F.Cu",
              e.error or f"gap {e.gap_length * 1e3:.3f} mm")

        mesh = build_grid(g, project, elements, {}, ())

        shorts = []

        def joined():
            m = discretize(mesh, g, project, {}, elements, {}, ())
            shorts[:] = m.shorts
            lines = m.grid.lines
            n = [len(l) for l in lines]
            idx = np.arange(np.prod(n)).reshape(n)
            rows, cols = [], []
            for a in range(3):
                lo = [slice(None)] * 3
                hi = [slice(None)] * 3
                lo[a], hi[a] = slice(0, n[a] - 1), slice(1, n[a])
                rows.append(idx[tuple(lo)][m.pec[a]])
                cols.append(idx[tuple(hi)][m.pec[a]])
            r, c = np.concatenate(rows), np.concatenate(cols)
            _, label = connected_components(sp.coo_matrix((np.ones(len(r)), (r, c)), shape=(idx.size,) * 2),
                                            directed=False)

            def node(x, y, z):
                return idx[tuple(int(np.argmin(abs(lines[k] - v * 1e-3))) for k, v in enumerate((x, y, z)))]

            z_cu = top + CU / 2
            return label[node(3.0, 0.0, z_cu)] == label[node(3.0, 3.0, z_cu)]

        x = mesh.grid.lines[0] * 1e3
        near = np.round(x[(x > END - 0.3) & (x < END + 0.6)], 3)
        check("trace not joined to the pour (closure per piece)", not joined(), f"x lines at the end {near}")
        check("  no shorts reported", shorts == [], str(shorts))
        pieces = g["F.Cu"].pieces
        g["F.Cu"].pieces = None
        check("closed per body instead, the same grid shorts them (the check sees the bug)", joined())
        check("  the element is reported as bypassed", len(shorts) == 1 and shorts[0].startswith("R1:"),
              str(shorts))
        g["F.Cu"].pieces = pieces
        print("  " + "; ".join(report.lines))
    finally:
        shutil.rmtree(folder)

    print("FAILED: " + ", ".join(FAILED) if FAILED else "all checks passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
