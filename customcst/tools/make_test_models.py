# make_test_models.py
#
# Writes the test STEP files into models/ (dimensions in mm, 2.4 GHz):
#   dipole_2g4.step  half-wave dipole: two copper rods, 2 mm feed gap
#   patch_2g4.step   patch on FR4: substrate (solid), ground (zero-thickness
#                    sheet), patch (35 um copper solid), coax probe pin
#
#   python tools/make_test_models.py

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace           # noqa: E402
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder  # noqa: E402
from OCP.gp import gp_Ax2, gp_Dir, gp_Pln, gp_Pnt                # noqa: E402

from geometry.step_io import write_step                           # noqa: E402

COPPER = (0.85, 0.55, 0.25)
FR4 = (0.25, 0.55, 0.3)


def box(x0, y0, z0, x1, y1, z1):
    return BRepPrimAPI_MakeBox(gp_Pnt(x0, y0, z0), gp_Pnt(x1, y1, z1)).Shape()


def rod_z(x, y, z0, z1, radius):
    axis = gp_Ax2(gp_Pnt(x, y, z0), gp_Dir(0, 0, 1))
    return BRepPrimAPI_MakeCylinder(axis, radius, z1 - z0).Shape()


def rect_sheet_z(x0, y0, x1, y1, z):
    plane = gp_Pln(gp_Pnt(0, 0, z), gp_Dir(0, 0, 1))
    return BRepBuilderAPI_MakeFace(plane, x0, x1, y0, y1).Face()


def dipole():
    # lambda0 = 124.9 mm; total length 58 mm ~ 0.46 lambda, radius 1 mm.
    gap, arm, radius = 2.0, 28.0, 1.0
    return [
        ("arm_top", rod_z(0, 0, gap / 2, gap / 2 + arm, radius), COPPER),
        ("arm_bottom", rod_z(0, 0, -gap / 2 - arm, -gap / 2, radius), COPPER),
    ]


def patch():
    # FR4 eps_r 4.3, h 1.6 mm: W = 38.4 mm, L = 29.8 mm (transmission-line model).
    h, t = 1.6, 0.035
    w, l, side = 38.4, 29.8, 70.0
    feed_x = -6.0
    return [
        ("substrate", box(-side / 2, -side / 2, 0, side / 2, side / 2, h), FR4),
        ("ground", rect_sheet_z(-side / 2, -side / 2, side / 2, side / 2, 0.0), COPPER),
        ("patch", box(-l / 2, -w / 2, h, l / 2, w / 2, h + t), COPPER),
        ("feed_pin", rod_z(feed_x, 0, 0.0, h, 0.65), (0.8, 0.8, 0.85)),
    ]


def main():
    models = os.path.join(ROOT, "models")
    os.makedirs(models, exist_ok=True)

    for stem, build in (("dipole_2g4", dipole), ("patch_2g4", patch)):
        path = os.path.join(models, stem + ".step")
        write_step(path, build())
        print("wrote", path)


if __name__ == "__main__":
    main()
