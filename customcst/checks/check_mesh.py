# check_mesh.py
#
# Level 2 checks on a microstrip built here (the same as the user's test line,
# Z up): 35 um copper merges onto the substrate faces, body faces and port
# axes are lines, the step limits (lambda/N per material, cells per feature)
# and the grading ratio hold, and the domain is the model box + padding.
# A 4-layer stack (0.2 mm FR4, 15 um inner ground, 1.07 mm FR4) next to a
# round PEC body lying along y (an SMA): the z fixpoints come only from
# planar CAD faces and body extents (the cylinder's tessellation has facets
# normal to z, which chained the merge), and the inner ground sits exactly
# on the thinner layer's face.
#
#   python checks/check_mesh.py

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

from geometry.step_io import read_step, write_step                       # noqa: E402
from mesh.rectilinear import build_grid                                  # noqa: E402
from project.project_file import Project                                 # noqa: E402
from project.settings import C0                                          # noqa: E402

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


def has_line(lines, value, tol=1e-9):
    return bool(np.min(np.abs(lines - value)) < tol)


def stack_with_cylinder(folder):
    """See the header; the round body is a tube along y (an SMA shell) whose bore has facets normal to z."""
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder

    def tube(phi):
        x_dir = gp_Dir(np.cos(np.radians(phi)), 0, np.sin(np.radians(phi)))
        axes = gp_Ax2(gp_Pnt(0, -12, -0.5), gp_Dir(0, 1, 0), x_dir)
        outer = BRepPrimAPI_MakeCylinder(axes, 2.6, 8.0).Shape()
        inner = BRepPrimAPI_MakeCylinder(axes, 0.65, 8.0).Shape()
        return BRepAlgoAPI_Cut(outer, inner).Shape()

    def bore_facets(body):
        v = body.vertices[body.triangles]
        normal = np.cross(v[:, 1] - v[:, 0], v[:, 2] - v[:, 0])
        lo, hi = body.bounds
        z = v[:, 0, 2]
        return (~body.planar & (np.abs(normal[:, 2]) >= (1 - 1e-9) * np.linalg.norm(normal, axis=1))
                & (z > lo[2] + 1e-9) & (z < hi[2] - 1e-9))

    path = os.path.join(folder, "stack.step")
    for phi in range(0, 30):                    # turn the seam until the bore has a flat-on-top facet
        write_step(path, [("rod", tube(phi), None)])
        if bore_facets(read_step(path)["rod"]).any():
            break
    write_step(path, [("fr4_thin", box(-8, -4, 0, 8, 4, 0.2), None),
                      ("l2", box(-8, -4, 0.2, 8, 4, 0.2152), None),
                      ("fr4_thick", box(-8, -4, 0.2152, 8, 4, 1.2802), None),
                      ("l3", box(-8, -4, 1.2802, 8, 4, 1.2954), None),
                      ("top", box(-0.5, -4, -0.035, 0.5, 4, 0), None),
                      ("rod", tube(phi), None)])
    g = read_step(path)
    project = Project(path)
    for name in ("l2", "l3", "top", "rod"):
        project.set_type(g[name], "pec")
    for name in ("fr4_thin", "fr4_thick"):
        project.set_type(g[name], "dielectric")
        project.set_value(name, "eps_r", 4.3)
    project.set_setting("simulation", "f_max", 6.0)
    mesh = build_grid(g, project, {})

    body = g["rod"]
    facets = bore_facets(body)
    z_facets = np.unique(np.round(body.vertices[body.triangles][facets, 0, 2] / MM, 4))
    check("stack: the tube's bore has facets normal to z (the test means something)", facets.any(),
          f"{int(facets.sum())} facets at z {z_facets} mm")

    allowed = {0.0, 0.2, 0.2152, 1.2802, 1.2954, -0.035}
    lo, hi = body.bounds
    allowed |= {round(lo[2] / MM, 4), round(hi[2] / MM, 4)}
    allowed |= {round(c / MM, 4) for c in mesh.grid.bounds[0][2:3]} | {round(c / MM, 4) for c in mesh.grid.bounds[1][2:3]}
    fixed = {round(c / MM, 4) for c in mesh.fixpoints[2]}
    check("stack: z fixpoints only from planar faces and extents", fixed <= allowed,
          f"extra {sorted(fixed - allowed)}")
    check("stack: inner ground on the 0.2 mm layer's face", has_line(mesh.grid.lines[2], 0.2 * MM),
          f"z lines near it {np.round(mesh.grid.lines[2][np.abs(mesh.grid.lines[2] - 0.2 * MM) < 0.2 * MM] / MM, 4)} mm")


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
        project.set_setting("simulation", "padding", 10.0)

        ports = project.ports(g)
        check("ports valid", all(p.ok for p in ports.values()))

        result = build_grid(g, project, ports)
        check("grid built", result.ok, result.error)
        x, y, z = result.grid.lines
        mesh = project.settings["mesh"]
        f_max = project.settings["simulation"]["f_max"] * 1e9
        n = mesh["cells_per_wavelength"]

        # Thin copper -> on the substrate faces; the substrate stays 1.6 mm.
        check("line at substrate bottom (gnd merged onto it)", has_line(z, 0.035 * MM))
        check("line at substrate top (strip merged onto it)", has_line(z, 1.635 * MM))
        check("no line at the outer copper faces",
              not has_line(z, 0.0, 0.01 * MM) and not has_line(z, 1.67 * MM, 0.01 * MM))
        merged = dict((round(k / MM, 6), d) for k, d in result.merged[2])
        check("merge report", 0.035 in merged and 1.635 in merged, str(merged))

        check("strip edges are lines", has_line(y, -1.5 * MM) and has_line(y, 1.5 * MM))
        check("port axis is a line (y = 0, x = ±25)",
              has_line(y, 0.0) and has_line(x, 25 * MM) and has_line(x, -25 * MM))

        lo, hi = result.grid.bounds
        check("domain = box + 10 mm padding",
              np.allclose(lo / MM, [-35, -25, -10]) and np.allclose(hi / MM, [35, 25, 11.67]),
              f"{lo / MM} .. {hi / MM}")

        for axis, lines in enumerate((x, y, z)):
            check(f"{'xyz'[axis]}: strictly increasing", bool(np.all(np.diff(lines) > 0)))

        # Step limits.
        h_air = C0 / f_max / n
        h_fr4 = C0 / (f_max * np.sqrt(4.3)) / n
        all_steps = np.concatenate([np.diff(l) for l in (x, y, z)])
        check("every step <= lambda0 / N", all_steps.max() <= h_air * (1 + 1e-9),
              f"max {all_steps.max() / MM:.3f} mm, limit {h_air / MM:.3f} mm")

        inside = (x[:-1] >= -25 * MM) & (x[1:] <= 25 * MM)
        hx = np.diff(x)[inside]
        check("steps along the board <= lambda_FR4 / N", hx.max() <= h_fr4 * (1 + 1e-9),
              f"max {hx.max() / MM:.3f} mm, limit {h_fr4 / MM:.3f} mm")

        hz = np.diff(z)[(z[:-1] >= 0.035 * MM - 1e-12) & (z[1:] <= 1.635 * MM + 1e-12)]
        check("substrate: >= cells_per_feature cells across 1.6 mm",
              len(hz) >= mesh["cells_per_feature"], f"{len(hz)} cells")

        for axis, lines in enumerate((x, y, z)):
            h = np.diff(lines)
            ratio = np.max(np.maximum(h[1:] / h[:-1], h[:-1] / h[1:]))
            check(f"{'xyz'[axis]}: grading ratio <= {mesh['max_ratio']} (+5%)",
                  ratio <= mesh["max_ratio"] * 1.05, f"{ratio:.3f}")

        # Auto padding = lambda0 / 4 at the center frequency (3 GHz -> 25 mm).
        project.set_setting("simulation", "padding", 0.0)
        lo_auto, _ = build_grid(g, project, ports).grid.bounds
        check("auto padding = 25 mm at 1-5 GHz", np.isclose(-lo_auto[2] / MM, 24.983, atol=1e-3),
              f"{-lo_auto[2] / MM:.3f} mm")

        stack_with_cylinder(folder)
    finally:
        shutil.rmtree(folder)

    print("FAILED: " + ", ".join(FAILED) if FAILED else "all checks passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
