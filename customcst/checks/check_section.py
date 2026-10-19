# check_section.py
#
# The section view's geometry (view/section.py), on a board (FR4 box with a
# 15 um copper layer on top) and a PEC tube lying across it:
#   - fill: the pixel at a known point shows the right body, the copper
#     paints over the FR4 where both are (conductors last), the tube's bore
#     is empty;
#   - a plane exactly on a face shows the body on the kept side only;
#   - outline: a box cut through its middle gives its rectangle (the total
#     length = the perimeter), the plane coordinate is exact, a thin layer
#     far below a pixel still has its outline.
#
#   python checks/check_section.py

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

from geometry.step_io import read_step, write_step                       # noqa: E402
from view.section import section_fill, section_outline                   # noqa: E402

FAILED = []
MM = 1e-3
FR4, CU, TUBE = (0.2, 0.6, 0.3), (0.9, 0.6, 0.2), (0.5, 0.5, 0.9)


def check(label, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {label} {detail}")
    if not ok:
        FAILED.append(label)


def box(x0, y0, z0, x1, y1, z1):
    return BRepPrimAPI_MakeBox(gp_Pnt(x0, y0, z0), gp_Pnt(x1, y1, z1)).Shape()


def pixel(image, u_range, v_range, u, v):
    nv, nu = image.shape[:2]
    i = int((u - u_range[0]) / (u_range[1] - u_range[0]) * nu)
    j = int((v - v_range[0]) / (v_range[1] - v_range[0]) * nv)
    return image[j, i]


def same(rgba, rgb):
    return rgba[3] > 0 and np.allclose(rgba[:3], rgb)


def main():
    folder = tempfile.mkdtemp()
    try:
        axes = gp_Ax2(gp_Pnt(-6, 0, 3), gp_Dir(1, 0, 0))
        tube = BRepAlgoAPI_Cut(BRepPrimAPI_MakeCylinder(axes, 1.5, 12).Shape(),
                               BRepPrimAPI_MakeCylinder(axes, 1.0, 12).Shape()).Shape()
        path = os.path.join(folder, "s.step")
        write_step(path, [("fr4", box(-5, -4, 0, 5, 4, 1.6), None),
                          ("cu", box(-5, -1, 1.6, 5, 1, 1.615), None),
                          ("tube", tube, None)])
        g = read_step(path)
        bodies = [(g["fr4"], FR4), (g["cu"], CU), (g["tube"], TUBE)]
        lo, hi = g.bounds

        # Plane x = 0 (u = y, v = z).
        image, ur, vr = section_fill(bodies, 0, 0.0, lo, hi)
        check("fill: FR4 inside the board", same(pixel(image, ur, vr, 2 * MM, 0.8 * MM), FR4))
        check("fill: tube wall", same(pixel(image, ur, vr, 0.0, 4.25 * MM), TUBE))
        check("fill: tube bore empty", pixel(image, ur, vr, 0.0, 3.0 * MM)[3] == 0)
        check("fill: air beside the board empty", pixel(image, ur, vr, 3.5 * MM, 2.5 * MM)[3] == 0)

        # A plane exactly on the board's top face (z = 1.6): below -> FR4, above -> copper.
        below, ur, vr = section_fill(bodies, 2, 1.6 * MM, lo, hi, keep_below=True)
        above, _, _ = section_fill(bodies, 2, 1.6 * MM, lo, hi, keep_below=False)
        check("plane on a face, kept below: FR4", same(pixel(below, ur, vr, 0.0, 3 * MM), FR4))
        check("plane on a face, kept above: copper over the strip", same(pixel(above, ur, vr, 0.0, 0.8 * MM), CU)
              and pixel(above, ur, vr, 0.0, 3 * MM)[3] == 0)

        # Outline of the board cut at x = 0: its rectangle, 2 * (8 + 1.6) mm.
        s = section_outline(g["fr4"], 0, 0.0)
        length = float(np.sum(np.linalg.norm(s[:, 1] - s[:, 0], axis=1)))
        check("outline: the board's rectangle", abs(length - 19.2 * MM) < 1e-6 and np.all(s[:, :, 0] == 0.0),
              f"{length / MM:.4f} mm in {len(s)} segments")
        cu = section_outline(g["cu"], 0, 0.0)
        z = cu[:, :, 2] / MM
        check("outline: 15 um copper has its outline", len(cu) >= 4 and z.min() >= 1.6 - 1e-9
              and z.max() <= 1.615 + 1e-9, f"{len(cu)} segments, z {z.min():.4f}..{z.max():.4f} mm")
        check("outline: a plane missing the body gives nothing", len(section_outline(g["cu"], 2, 5 * MM)) == 0)
    finally:
        shutil.rmtree(folder)

    print("FAILED: " + ", ".join(FAILED) if FAILED else "all checks passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
