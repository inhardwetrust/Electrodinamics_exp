# check_holes.py
#
# Hole recognition (step_io._holes + geometry/holes.py) on a board built
# here: FR4 20 x 10 x 1.6 mm, top copper and bottom ground (35 um):
#   - 3 vias, drill 0.4 mm, through FR4 and both copper layers; one of them
#     goes through a 1.0 mm clearance (antipad) in the ground instead;
#   - a 3.2 mm mounting hole through everything;
#   - a blind 0.6 mm pocket in the FR4 (from the top, not through);
#   - a 1.0 mm hole filled by a pin body (not open);
#   - a cylinder split into two half faces (a hole cut by two half
#     cylinders) must still be one hole.
# Expected: 0.4 mm x 3 (FR4 + both coppers; the antipad one FR4 + top),
# 1.0 mm x 1 (the antipad, ground only), 3.2 mm x 1 (all three layers),
# 0.8 mm x 1 (the split one); no blind or filled hole.
#
#   python checks/check_holes.py

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

from geometry.holes import find_holes, group_by_diameter                 # noqa: E402
from geometry.step_io import read_step, write_step                       # noqa: E402

FAILED = []
MM = 1e-3


def check(label, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {label} {detail}")
    if not ok:
        FAILED.append(label)


def box(x0, y0, z0, x1, y1, z1):
    return BRepPrimAPI_MakeBox(gp_Pnt(x0, y0, z0), gp_Pnt(x1, y1, z1)).Shape()


def drill(x, y, d, z0=-1.0, z1=3.0, angle=None):
    if angle is None:
        return BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(x, y, z0), gp_Dir(0, 0, 1)), d / 2, z1 - z0).Shape()
    # A half cylinder (angle = pi): two of them make a hole of two half faces.
    return BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(x, y, z0), gp_Dir(0, 0, 1)), d / 2, z1 - z0, angle).Shape()


def cut(shape, *tools):
    for tool in tools:
        shape = BRepAlgoAPI_Cut(shape, tool).Shape()
    return shape


def main():
    folder = tempfile.mkdtemp()
    try:
        vias = [(-6.0, 0.0), (-3.0, 0.0), (0.0, 0.0)]
        through_all = [drill(x, y, 0.4) for x, y in vias] + [drill(7.0, 0.0, 3.2)]
        # Two half cylinders (the second one turned by 180 degrees about z).
        split = [drill(4.0, 3.0, 0.8, angle=np.pi),
                 BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(4.0, 3.0, -1.0), gp_Dir(0, 0, 1), gp_Dir(-1, 0, 0)),
                                          0.4, 4.0, np.pi).Shape()]
        substrate = cut(box(-10, -5, 0, 10, 5, 1.6), *through_all,
                        drill(3.0, -3.0, 0.6, z0=0.8, z1=3.0),       # blind pocket from the top
                        drill(-7.0, -3.0, 1.0), *split)
        top = cut(box(-10, -5, 1.6, 10, 5, 1.635), *through_all, drill(-7.0, -3.0, 1.0), *split)
        # Ground: the via at x = 0 passes a 1.0 mm clearance instead of a 0.4 mm hole.
        gnd = cut(box(-10, -5, -0.035, 10, 5, 0), *through_all[:2], through_all[3],
                  drill(0.0, 0.0, 1.0), drill(-7.0, -3.0, 1.0), *split)
        pin = drill(-7.0, -3.0, 1.0, z0=-0.035, z1=1.635)          # fills its hole

        path = os.path.join(folder, "board.step")
        write_step(path, [("substrate", substrate, None), ("top", top, None),
                          ("gnd", gnd, None), ("pin", pin, None)])
        g = read_step(path)

        holes = find_holes(g)
        groups = group_by_diameter(holes)
        found = {round(d / MM, 3): len(hs) for d, hs in groups.items()}
        print("    found:", found)
        check("diameters and counts", found == {0.4: 3, 0.8: 1, 1.0: 1, 3.2: 1}, str(found))

        small = groups.get(round(0.4 * MM, 7), [])
        layers = {tuple(np.round(h.center[:2] / MM, 3)): h.bodies for h in small}
        check("vias through FR4 and both coppers",
              layers.get((-6.0, 0.0)) == ["gnd", "substrate", "top"]
              and layers.get((-3.0, 0.0)) == ["gnd", "substrate", "top"], str(layers))
        check("the via in the antipad: FR4 + top only", layers.get((0.0, 0.0)) == ["substrate", "top"],
              str(layers.get((0.0, 0.0))))
        via = next((h for h in small if abs(h.center[0] + 6 * MM) < 1e-9), None)
        check("via spans the whole stack", via is not None
              and abs(via.start[2] + 0.035 * MM) < 1e-9 and abs(via.length - 1.67 * MM) < 1e-9,
              f"{via.start / MM if via else ''} L {via.length / MM if via else ''}")
        check("axis along z", via is not None and via.axis_index == 2 and via.axis[2] > 0)

        antipad = groups.get(round(1.0 * MM, 7), [])
        check("antipad: ground only, separate from the via",
              len(antipad) == 1 and antipad[0].bodies == ["gnd"]
              and np.allclose(antipad[0].center[:2], [0, 0], atol=1e-9))
        mounting = groups.get(round(3.2 * MM, 7), [])
        check("mounting hole through all three", len(mounting) == 1
              and mounting[0].bodies == ["gnd", "substrate", "top"])
        halves = groups.get(round(0.8 * MM, 7), [])
        check("two half faces -> one hole", len(halves) == 1 and halves[0].bodies == ["gnd", "substrate", "top"],
              str([h.bodies for h in halves]))
        check("no blind pocket (0.6 mm)", round(0.6 * MM, 7) not in groups)
        check("no filled hole at the pin", all(np.linalg.norm(h.center[:2] - [-7 * MM, -3 * MM]) > 1e-6
                                               for h in holes))
        check("ids by diameter, then position", [h.id for h in holes] == [f"H{n}" for n in range(1, 7)]
              and [round(h.diameter / MM, 1) for h in holes] == sorted(round(h.diameter / MM, 1) for h in holes))

        # The user's coax: shield and insulation bores are filled -> nothing.
        coax = read_step(os.path.join(ROOT, "models", "03_coax_line.stp"))
        check("coax: filled bores are not holes", find_holes(coax) == [],
              str([(h.id, h.bodies) for h in find_holes(coax)]))
    finally:
        shutil.rmtree(folder)

    print("FAILED: " + ", ".join(FAILED) if FAILED else "all checks passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
