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
#   patch_5g8_dual.step  the same patch fed at both radiating edges by
#                    inset lines (one copper layer over the ground): a T
#                    splits the 50 Ohm line into two 100 Ohm arms, one
#                    lambda_g / 2 longer (a meander) for the 180 degrees
#                    the two edges need; a symmetric E-plane pattern
#   patch_5g8_4layer.step  the patch on a 4-layer board (JLC 7628-like):
#                    patch on top, In1 left out under it, In2 the ground,
#                    the feed network on the bottom over In2 (0.21 mm):
#                    two vias in antiphase up to the patch, symmetric
#                    about its center, the network shielded by the ground
#
#   python tools/make_test_models.py [stem ...]     (none: all of them)

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse    # noqa: E402
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


# Dual inset feed (mm). Fed at both edges with equal currents, each feed
# point sees twice the single-feed resistance, so a 100 Ohm arm takes the
# inset of a 50 Ohm single feed: R_edge cos^2(pi d / L) = 50 with R_edge
# ~130 Ohm (from the probe runs) -> d ~ 3.2 mm. 100 Ohm on FR4 1.6 mm:
# 0.7 mm wide, eps_eff ~2.95, lambda_g / 2 = 15 mm at 5.8 GHz -> the meander
# goes 7.5 mm out and back. Arms stay 5 mm (~3h) off the patch's edges.
PATCH_5G8_DUAL = {"h": 1.6, "t": 0.035, "w": 15.9, "l": 11.1, "side": 40.0,
                  "inset": 3.2, "gap": 0.5, "arm_w": 0.7, "main_w": 3.1,
                  "clear": 5.0, "tee_y": -13.95, "meander": 7.5, "meander_y": (-2.0, -5.0)}


def _union(shapes):
    out = shapes[0]
    for shape in shapes[1:]:
        out = BRepAlgoAPI_Fuse(out, shape).Shape()
    return out


def _trace(points, width, z0, z1):
    """Boxes along an axis-parallel polyline (mm), squared corners."""
    half = width / 2.0
    parts = []
    for (xa, ya), (xb, yb) in zip(points, points[1:]):
        x0, x1 = sorted((xa, xb))
        y0, y1 = sorted((ya, yb))
        parts.append(box(x0 - half, y0 - half, z0, x1 + half, y1 + half, z1))
    return parts


def patch_5g8_dual():
    p = PATCH_5G8_DUAL
    h, t, side = p["h"], p["t"], p["side"]
    l2, w2 = p["l"] / 2, p["w"] / 2
    z0, z1 = h, h + t
    notch = p["arm_w"] / 2 + p["gap"]
    tip = l2 - p["inset"]                       # where an arm meets the patch (x = -tip, +tip)
    patch = box(-l2, -w2, z0, l2, w2, z1)
    for sign in (-1, 1):                        # the insets at both radiating edges
        a, b = sorted((sign * l2, sign * tip))
        patch = BRepAlgoAPI_Cut(patch, box(a, -notch, z0 - 0.01, b, notch, z1 + 0.01)).Shape()

    xa = l2 + p["clear"]                        # the arms' vertical runs, outside the patch
    ty, (m0, m1) = p["tee_y"], p["meander_y"]
    arm_a = [(-tip, 0.0), (-xa, 0.0), (-xa, ty), (0.0, ty)]
    arm_b = [(tip, 0.0), (xa, 0.0), (xa, m0), (xa + p["meander"], m0), (xa + p["meander"], m1),
             (xa, m1), (xa, ty), (0.0, ty)]
    main = [(0.0, ty), (0.0, -side / 2 + p["main_w"] / 2)]   # ends flush with the board edge
    top = _union([patch] + _trace(arm_a, p["arm_w"], z0, z1) + _trace(arm_b, p["arm_w"], z0, z1)
                 + _trace(main, p["main_w"], z0, z1))
    edge = -side / 2 + 0.5
    return [
        ("ground", box(-side / 2, -side / 2, -t, side / 2, side / 2, 0.0), COPPER),
        ("substrate", box(-side / 2, -side / 2, 0.0, side / 2, side / 2, h), FR4),
        ("top", top, COPPER),
        # the port at the board's edge, its small end "+" on the line
        ("feed", cone_z(0.0, edge, 0.0, h, 0.5, 0.4), (0.8, 0.8, 0.85)),
    ]


def patch_5g8_dual_project(path):
    """patch_5g8_dual.toml: as patch_5g8's (types, FR4 at 5.8 GHz, band, far field)."""
    project = Project(path)
    for body in read_step(path).bodies:
        project.set_type(body, {"ground": "pec", "top": "pec", "substrate": "dielectric",
                                "feed": "port"}[body.name])
    project.set_material("substrate", "FR4")
    project.set_value("substrate", "f_ref", 5.8)
    project.set_setting("simulation", "f_min", 4.5)
    project.set_setting("simulation", "f_max", 7.0)
    project.set_setting("mesh", "cells_per_wavelength", 40.0)
    project.set_setting("mesh", "cells_per_feature", 8.0)
    project.set_setting("farfield", "frequencies", "5.6, 5.7, 5.8")
    project.save()


# 4-layer, feed behind the ground (mm). z: bottom copper -0.035..0,
# prepreg 0..0.21, In2 ground 0.21..0.225 (antipads around the vias), the
# patch's dielectric 0.225..1.5 (core + prepreg, In1 left out under the
# antenna), the patch 1.5..1.535. On the bottom, over In2: the 50 Ohm input
# (0.41 mm) from the board's edge, a quarter-wave 35 Ohm (0.74 mm, 7 mm)
# to the T, two 50 Ohm arms to the vias; arm B lambda_g / 2 longer (14.2 mm:
# a meander 7.1 mm out and back). Each via sees twice its single-feed
# resistance: 50 Ohm arms want the 25 Ohm single-feed point.
# Tuned by sweeps (30 cells per wavelength, 4 per feature): L 11.0 / 11.4 x
# vias at 1.5 / 2.0 / 2.5 mm -> 11.4 and 2.0 (S11 -23.5 dB at 5.76 GHz);
# the meander 6.3 / 7.1 / 7.9 mm tilted the beam 3 degrees to -x, 3 to -x,
# 1 to +x -> 7.7 for 180 degrees, and L 11.25 to bring S11 back to 5.8.
PATCH_5G8_4L = {"w": 15.9, "l": 11.25, "side": 40.0, "via_x": 2.0, "via_r": 0.15, "antipad": 0.45,
                "h_feed": 0.21, "t_ground": 0.015, "h_patch": 1.275, "t": 0.035,
                "w50": 0.41, "w35": 0.74, "quarter": 7.0, "tee_y": -9.0,
                "meander": 7.7, "meander_y": (-3.0, -6.0)}


def patch_5g8_4layer():
    p = PATCH_5G8_4L
    side, t = p["side"], p["t"]
    z_ground0 = p["h_feed"]
    z_ground1 = z_ground0 + p["t_ground"]
    z_patch = z_ground1 + p["h_patch"]
    l2, w2, xv = p["l"] / 2, p["w"] / 2, p["via_x"]

    ground = box(-side / 2, -side / 2, z_ground0, side / 2, side / 2, z_ground1)
    for x in (-xv, xv):
        ground = BRepAlgoAPI_Cut(ground, rod_z(x, 0.0, z_ground0 - 0.01, z_ground1 + 0.01, p["antipad"])).Shape()

    ty, (m0, m1), xm = p["tee_y"], p["meander_y"], xv + p["meander"]
    arm_a = [(-xv, 0.0), (-xv, ty), (0.0, ty)]
    arm_b = [(xv, 0.0), (xv, m0), (xm, m0), (xm, m1), (xv, m1), (xv, ty), (0.0, ty)]
    quarter = [(0.0, ty), (0.0, ty - p["quarter"])]
    feed_in = [(0.0, ty - p["quarter"]), (0.0, -side / 2 + p["w50"] / 2)]
    network = _union(_trace(arm_a, p["w50"], -t, 0.0) + _trace(arm_b, p["w50"], -t, 0.0)
                     + _trace(quarter, p["w35"], -t, 0.0) + _trace(feed_in, p["w50"], -t, 0.0))
    vias = _union([rod_z(x, 0.0, -t, z_patch, p["via_r"]) for x in (-xv, xv)])
    edge = -side / 2 + 0.3
    return [
        ("ground_in2", ground, COPPER),
        ("dielectric", box(-side / 2, -side / 2, 0.0, side / 2, side / 2, z_patch), FR4),
        ("patch", box(-l2, -w2, z_patch, l2, w2, z_patch + t), COPPER),
        ("network", network, COPPER),
        ("vias", vias, (0.9, 0.75, 0.3)),
        # the port at the board's edge between the bottom line ("+", small end) and In2
        ("feed", cone_z(0.0, edge, 0.0, z_ground0, 0.15, 0.2), (0.8, 0.8, 0.85)),
    ]


def patch_5g8_4layer_project(path):
    """patch_5g8_4layer.toml: as patch_5g8's (types, FR4 at 5.8 GHz, band, far field)."""
    project = Project(path)
    for body in read_step(path).bodies:
        project.set_type(body, {"ground_in2": "pec", "patch": "pec", "network": "pec", "vias": "pec",
                                "dielectric": "dielectric", "feed": "port"}[body.name.split("#")[0]])
    project.set_material("dielectric", "FR4")
    project.set_value("dielectric", "f_ref", 5.8)
    project.set_setting("simulation", "f_min", 4.5)
    project.set_setting("simulation", "f_max", 7.0)
    project.set_setting("simulation", "accuracy", "-50")
    project.set_setting("simulation", "padding", 26.0)            # lambda / 2: the far-field box away from the board
    project.set_setting("mesh", "cells_per_wavelength", 40.0)
    project.set_setting("mesh", "cells_per_feature", 8.0)
    project.set_setting("farfield", "frequencies", "5.6, 5.7, 5.8, 5.9, 6.0")
    project.save()


def main():
    models = os.path.join(ROOT, "models")
    os.makedirs(models, exist_ok=True)

    wanted = sys.argv[1:]           # stems to write; none: all
    for stem, build in (("dipole_2g4", dipole), ("patch_2g4", patch), ("patch_5g8", patch_5g8),
                        ("patch_5g8_dual", patch_5g8_dual), ("patch_5g8_4layer", patch_5g8_4layer)):
        if wanted and stem not in wanted:
            continue
        path = os.path.join(models, stem + ".step")
        write_step(path, build())
        print("wrote", path)
        if stem == "patch_5g8":
            patch_5g8_project(path)
        if stem == "patch_5g8_dual":
            patch_5g8_dual_project(path)
        if stem == "patch_5g8_4layer":
            patch_5g8_4layer_project(path)


if __name__ == "__main__":
    main()
