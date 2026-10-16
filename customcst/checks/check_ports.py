# check_ports.py
#
# Point queries and port extraction, on geometry built here (no user files):
#   - points_inside matches the analytic inside test of a cylinder;
#   - surface_distances matches the analytic distance to a box;
#   - a cone port between two plates: polarity from the radii, the gap is
#     exactly the free space between the copper (also when the axis lies on
#     the plates' end faces), errors for cylinder / same conductor / no
#     contact.
#
#   python checks/check_ports.py

import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

import numpy as np                                                       # noqa: E402
from OCP.BRepPrimAPI import (                                            # noqa: E402
    BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCone, BRepPrimAPI_MakeCylinder,
)
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt                                # noqa: E402

from geometry.queries import points_inside, surface_distances            # noqa: E402
from geometry.step_io import read_step, write_step                       # noqa: E402
from project.ports import port_geometry                                  # noqa: E402

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


def cylinder_z(x, y, z0, z1, r):
    return BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(x, y, z0), gp_Dir(0, 0, 1)), r, z1 - z0).Shape()


def main():
    folder = tempfile.mkdtemp()
    try:
        # Plates: gnd z 0..0.035, strip z 1.635..1.67 (gap 1.6 mm), both end at x = 10.
        shapes = [
            ("gnd", box(-10, -5, 0, 10, 5, 0.035), None),
            ("strip", box(-10, -1.5, 1.635, 10, 1.5, 1.67), None),
            ("port_inside", cone_z(0, 0, 0, 1.67, 0.8, 0.5), None),        # through the copper
            ("port_edge", cone_z(10, 0, 0, 1.67, 0.8, 0.5), None),         # on the end faces
            ("port_flipped", cone_z(-5, 0, 0, 1.67, 0.5, 0.8), None),      # + on the ground
            ("port_cylinder", cylinder_z(5, 0, 0, 1.67, 0.5), None),
            ("port_short", cone_z(-8, 0, 0.0, 0.03, 0.8, 0.5), None),     # both ends in gnd
            ("port_floating", cone_z(-8, 3, 0.5, 1.0, 0.8, 0.5), None),   # touches nothing
            ("rod", cylinder_z(0, 20, 0, 10, 2.0), None),
        ]
        path = os.path.join(folder, "ports.step")
        write_step(path, shapes)
        g = read_step(path, linear_deflection=1e-6)
        conductors = [g["gnd"], g["strip"]]

        # --- queries -------------------------------------------------------
        rng = np.random.default_rng(1)
        pts = rng.uniform([-3, 17, -1], [3, 23, 11], size=(4000, 3)) * MM
        r = np.hypot(pts[:, 0], pts[:, 1] - 20 * MM)
        exact = (r < 2 * MM) & (pts[:, 2] > 0) & (pts[:, 2] < 10 * MM)
        # Tessellated cylinder: ignore points within the deflection of the wall.
        clear = (np.abs(r - 2 * MM) > 2e-6) & (np.abs(pts[:, 2]) > 1e-9)
        got = points_inside(g["rod"], pts)
        check("points_inside vs analytic cylinder", np.array_equal(got[clear], exact[clear]),
              f"{np.count_nonzero(got[clear] != exact[clear])} mismatches of {clear.sum()}")

        pts = rng.uniform([-12, -7, -2], [12, 7, 2], size=(500, 3)) * MM
        lo, hi = np.array([-10, -5, 0]) * MM, np.array([10, 5, 0.035]) * MM
        outside = np.linalg.norm(np.maximum(lo - pts, 0) + np.maximum(pts - hi, 0), axis=1)
        inside_d = np.min(np.concatenate((pts - lo, hi - pts), axis=1), axis=1)
        exact_d = np.where(outside > 0, outside, inside_d)
        d = surface_distances(g["gnd"], pts)
        check("surface_distances vs analytic box", np.allclose(d, exact_d, atol=1e-12),
              f"max err {np.max(np.abs(d - exact_d)):.1e} m")

        # --- ports ---------------------------------------------------------
        for name in ("port_inside", "port_edge"):
            p = port_geometry(g[name], conductors)
            ok = p.ok and p.minus_body == "gnd" and p.plus_body == "strip"
            check(f"{name}: - on gnd, + on strip", ok, p.error or "")
            if p.ok:
                check(f"{name}: gap = 1.6 mm", abs(p.gap_length - 1.6 * MM) < 1e-3 * MM,
                      f"{p.gap_length / MM:.6f} mm")
                check(f"{name}: gap starts at the gnd top",
                      abs(p.gap_minus[2] - 0.035 * MM) < 1e-3 * MM, f"z = {p.gap_minus[2] / MM:.6f} mm")

        p = port_geometry(g["port_flipped"], conductors)
        check("flipped cone: + on gnd", p.ok and p.plus_body == "gnd", p.error or "")

        expected_errors = {
            "port_cylinder": "same radius",
            "port_short": "same conductor",
            "port_floating": "touches no PEC",
            "gnd": "two circular ends",
        }
        for name, needle in expected_errors.items():
            p = port_geometry(g[name], conductors)
            check(f"{name}: error", (p.error or "").find(needle) >= 0, p.error or "no error")

        p = port_geometry(g["port_inside"], [g["gnd"]])
        check("conductor not PEC -> error", not p.ok, p.error or "no error")
    finally:
        shutil.rmtree(folder)

    print("FAILED: " + ", ".join(FAILED) if FAILED else "all checks passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
