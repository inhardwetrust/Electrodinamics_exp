# make_test_models.py
#
# Writes the test STEP files into models/ (dimensions in mm, 2.4 GHz):
#   dipole_2g4.step  half-wave dipole: two copper rods, 2 mm feed gap
#   patch_2g4.step   patch on FR4: substrate (solid), ground (zero-thickness
#                    sheet), patch (35 um copper solid), coax probe pin
#   patch_5g8.step   patch on FR4 1.6 mm for 5.8 GHz, 40 x 40 mm board, fed
#                    by a lumped port (a cone ground -> patch) at the probe
#                    point; patch_5g8.toml sets it up for a run with the
#                    far field (the antenna example)
#
#   python tools/make_test_models.py [stem ...]     (none: all of them)

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace           # noqa: E402
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCone, BRepPrimAPI_MakeCylinder  # noqa: E402
from OCP.gp import gp_Ax2, gp_Dir, gp_Pln, gp_Pnt                # noqa: E402

from geometry.step_io import read_step, write_step                # noqa: E402
from project.project_file import Project                          # noqa: E402

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


def cone_z(x, y, z0, z1, r0, r1):
    axis = gp_Ax2(gp_Pnt(x, y, z0), gp_Dir(0, 0, 1))
    return BRepPrimAPI_MakeCone(axis, r0, r1, z1 - z0).Shape()


# Transmission-line model, eps_r 4.3, h 1.6 mm, 5.8 GHz: W = 15.9 mm,
# L = 11.9 mm, the probe 4.2 mm in from the edge for 50 Ohm. On a mesh of
# 30 cells per wavelength, 8 per feature that resonated at 5.33 GHz with
# 25 Ohm and the probe's +40 Ohm reactance: tuned to L = 11.1 mm and the
# probe 3.15 mm from the center (S11 about -22 dB near 5.8 GHz).
PATCH_5G8 = {"h": 1.6, "t": 0.035, "w": 15.9, "l": 11.1, "side": 40.0, "feed_x": -3.15}


def patch_5g8():
    p = PATCH_5G8
    h, t, side = p["h"], p["t"], p["side"]
    return [
        ("ground", box(-side / 2, -side / 2, -t, side / 2, side / 2, 0.0), COPPER),
        ("substrate", box(-side / 2, -side / 2, 0.0, side / 2, side / 2, h), FR4),
        ("patch", box(-p["l"] / 2, -p["w"] / 2, h, p["l"] / 2, p["w"] / 2, h + t), COPPER),
        # a port is a truncated cone, its small end "+" on the patch
        ("feed", cone_z(p["feed_x"], 0.0, 0.0, h, 0.5, 0.4), (0.8, 0.8, 0.85)),
    ]


def patch_5g8_project(path):
    """patch_5g8.toml: types, FR4 with its loss at 5.8 GHz, the band, the far field."""
    project = Project(path)
    for body in read_step(path).bodies:
        project.set_type(body, {"ground": "pec", "patch": "pec", "substrate": "dielectric",
                                "feed": "port"}[body.name])
    project.set_material("substrate", "FR4")
    project.set_value("substrate", "f_ref", 5.8)           # its tan d at the band, not at 3 GHz
    project.set_setting("simulation", "f_min", 4.5)
    project.set_setting("simulation", "f_max", 7.0)
    project.set_setting("mesh", "cells_per_wavelength", 40.0)
    project.set_setting("mesh", "cells_per_feature", 10.0)
    project.set_setting("farfield", "frequencies", "5.7, 5.8, 5.9")
    project.save()


def main():
    models = os.path.join(ROOT, "models")
    os.makedirs(models, exist_ok=True)

    wanted = sys.argv[1:]           # stems to write; none: all
    for stem, build in (("dipole_2g4", dipole), ("patch_2g4", patch), ("patch_5g8", patch_5g8)):
        if wanted and stem not in wanted:
            continue
        path = os.path.join(models, stem + ".step")
        write_step(path, build())
        print("wrote", path)
        if stem == "patch_5g8":
            patch_5g8_project(path)


if __name__ == "__main__":
    main()
