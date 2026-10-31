# check_project.py
#
# Sketch > Project Geometry (geometry/project.py, Sketch.add_projection,
# view/sketch_tools.py ProjectTool), into an empty sketch "Target" on the XY
# plane (z = 0), from bodies above it:
#   - Plate: 20 x 10 x 2 with a hole r 2 at (5, 5): a click on its top face
#     (the tool, the face lit under the cursor first) gives four lines on its
#     corners and the circle exactly; Target then has the profile of the
#     plate with its hole (200 - 4 pi) and the disc (4 pi); one undo step;
#     the same face again adds nothing;
#   - Rod: a cylinder r 3 along y (on the XZ plane, center x 30, z 3, 10
#     long): its side face: its end circles are seen edge-on, so they come
#     in as the segments x 27..33 at y 0 and y 10;
#   - Tab: a block with a round end (an arc, center (10, 25), r 5, -90..90):
#     its top face gives that arc exactly and the three lines, the ends joined;
#   - Tilt: a cylinder r 3 on a work plane turned 45 degrees around X: its
#     end cap projects to an ellipse (semi-axes 3 and 3 cos 45): a closed
#     spline whose points lie on it, within the tolerance;
#   - Construction on: the projected geometry is construction.
#
#   python checks/check_project.py

import math
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "checks"))
sys.stdout.reconfigure(encoding="utf-8")
TMP = tempfile.mkdtemp()
os.environ["MYCAD_SETTINGS"] = os.path.join(TMP, "settings.json")     # never the user's settings

import numpy as np                                                    # noqa: E402

from model.extrude import Extrude                                     # noqa: E402
from model.part import Part                                           # noqa: E402
from model.part_file import dumps                                     # noqa: E402
from model.sketch import Sketch                                       # noqa: E402
from model.workplane import WorkPlane                                 # noqa: E402

FAILED = []
RECT = ("L1", "L2", "L3", "L4")


def check(label, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {label} {detail}")
    if not ok:
        FAILED.append(label)


def rectangle(s, x0, y0, w, h):
    p = [s.add_point(str(x), str(y)) for x, y in ((x0, y0), (x0 + w, y0), (x0 + w, y0 + h), (x0, y0 + h))]
    for a, b in zip(p, p[1:] + p[:1]):
        s.add_line(a, b)


def the_part():
    part = Part()
    s = part.add_feature(Sketch("PlateSketch", plane="XY Plane"))
    rectangle(s, 0, 0, 20, 10)
    s.add_circle(s.add_point("5", "5"), "2")
    part.add_feature(Extrude("PlateExt", profiles=[RECT], sketch="PlateSketch", distance="2", body="Plate"))
    s = part.add_feature(Sketch("RodSketch", plane="XZ Plane"))
    s.add_circle(s.add_point("30", "-3"), "3")             # XZ: sketch y is -Z
    part.add_feature(Extrude("RodExt", profiles=[("C1",)], sketch="RodSketch", distance="10", body="Rod"))
    s = part.add_feature(Sketch("TabSketch", plane="XY Plane"))
    p1, p2, p3 = s.add_point("0", "20"), s.add_point("10", "20"), s.add_point("10", "25")
    arc = s.add_arc(kind="center", center=p3, radius="5", start_angle="-90", end_angle="90")
    p4 = s.add_point("0", "30")
    s.add_line(p1, p2)
    s.add_line(f"{arc}.end", p4)
    s.add_line(p4, p1)
    part.add_feature(Extrude("TabExt", profiles=[("A1", "L1", "L2", "L3")], sketch="TabSketch", distance="3",
                             body="Tab"))
    part.add_feature(WorkPlane("Tilted", base="XY Plane", axis="X Axis", angle="45"))
    s = part.add_feature(Sketch("TiltSketch", plane="Tilted"))
    s.add_circle(s.add_point("50", "0"), "3")
    part.add_feature(Extrude("TiltExt", profiles=[("C1",)], sketch="TiltSketch", distance="5", body="Tilt"))
    part.add_feature(Sketch("Target", plane="XY Plane"))
    return part


def main():
    from vispy import app

    app.use_app("pyside6")
    app.use_app().create()
    from vispy_errors import install as _watch_vispy

    vispy_errors = _watch_vispy()
    from PySide6.QtCore import QEvent, Qt

    from check_sketch_line import settle, shield
    from check_view import mouse
    from cli import report
    from geometry import kernel
    from geometry.project import face_of
    from model.rebuild import rebuild
    from view.main_window import MainWindow
    from view.picking import _project

    window = MainWindow()
    window.ask_save_changes = lambda: "discard"
    errors = []
    window.show_error = errors.append
    window.resize(1200, 800)
    window.show()
    shield(window)
    settle(300)
    path = os.path.join(TMP, "project.part.toml")
    with open(path, "w", encoding="utf-8") as f:
        f.write(dumps(the_part()))
    window.open_file(path)
    settle()
    check("the part builds", not window.result.feature_errors, str(window.result.feature_errors))

    def face_index(body, test):
        """The index of the face of `body` whose bounds pass test(lo, hi)."""
        shape = window.result.shapes[body]
        for k in range(200):
            try:
                lo, hi = kernel.bounds(face_of(shape, k))
            except IndexError:
                return None
            if test(lo, hi):
                return k
        return None

    top = face_index("Plate", lambda lo, hi: abs(lo[2] - 2) < 1e-9 and abs(hi[2] - 2) < 1e-9)
    window.edit_sketch("Target")
    settle()
    window.project_action.setChecked(True)
    settle()
    native = window.view.canvas.native
    target = window.result.outputs["Target"]
    pixel, _ = _project(window.view._transform(), np.array([[15.0, 8.0, 2.0]]))
    pos = pixel[0]
    mouse(native, QEvent.MouseMove, pos, Qt.NoButton)
    settle(50)
    check("the tool: the Plate's top face lit under the cursor", window.view.hovered == ("face", "Plate", top),
          f"{window.view.hovered} want face {top}")
    steps = len(window.doc._undo)
    mouse(native, QEvent.MouseButtonPress, pos, Qt.LeftButton)
    mouse(native, QEvent.MouseButtonRelease, pos, Qt.NoButton)
    settle()
    sketch = window.part.feature("Target")
    built = window.result.outputs["Target"]
    ends = sorted(tuple(sorted((tuple(np.round(built.points[l.start], 9)), tuple(np.round(built.points[l.end], 9)))))
                  for l in sketch.lines)
    want = sorted(tuple(sorted(e)) for e in (((0, 0), (20, 0)), ((20, 0), (20, 10)), ((20, 10), (0, 10)),
                                             ((0, 10), (0, 0))))
    check("a click on the top face: its four edges as lines on the corners", ends == want, str(ends))
    circle = sketch.circles[0] if sketch.circles else None
    check("  the hole as a circle, exactly: center (5, 5), r 2", circle is not None and circle.radius == "2"
          and np.allclose(built.points[circle.center], [5, 5], atol=1e-12), str(circle))
    check("  one undo step", len(window.doc._undo) == steps + 1)
    data = report(window.part, rebuild(window.part, folder=TMP))
    profiles = next(f for f in data["features"] if f["name"] == "Target")["profiles"]
    areas = sorted(round(p["area"], 2) for p in profiles)
    check("  Target's profiles: the plate with its hole and the disc", len(profiles) == 2
          and np.allclose(areas, sorted([round(200 - 4 * math.pi, 2), round(4 * math.pi, 2)]), atol=0.05),
          str(areas))
    before = (len(sketch.lines), len(sketch.circles), len(sketch.points))
    window.project_face(("face", "Plate", top))
    settle()
    sketch = window.part.feature("Target")
    check("the same face again: nothing new", (len(sketch.lines), len(sketch.circles), len(sketch.points)) == before,
          f"{before} -> {(len(sketch.lines), len(sketch.circles), len(sketch.points))}")

    side = face_index("Rod", lambda lo, hi: hi[1] - lo[1] > 9.9 and hi[0] - lo[0] > 5.9)
    names = window.project_face(("face", "Rod", side))
    settle()
    sketch, built = window.part.feature("Target"), window.result.outputs["Target"]
    segments = []
    for name in names:
        line = next((l for l in sketch.lines if l.name == name), None)
        if line is not None:
            a, b = built.points[line.start], built.points[line.end]
            segments.append((round(min(a[0], b[0]), 9), round(max(a[0], b[0]), 9), round(a[1], 9), round(b[1], 9)))
    check("Rod's side: its end circles edge-on, as segments x 27..33 at y 0 and y 10",
          (27.0, 33.0, 0.0, 0.0) in segments and (27.0, 33.0, 10.0, 10.0) in segments, str(segments))

    tab = face_index("Tab", lambda lo, hi: abs(lo[2] - 3) < 1e-9 and abs(hi[2] - 3) < 1e-9)
    names = window.project_face(("face", "Tab", tab))
    settle()
    sketch, built = window.part.feature("Target"), window.result.outputs["Target"]
    arcs = [a for a in sketch.arcs if a.name in names]
    arc = arcs[0] if arcs else None
    check("Tab's top: the arc exactly, center (10, 25), r 5, -90..90", arc is not None and arc.radius == "5"
          and np.allclose(built.points[arc.center], [10, 25], atol=1e-12)
          and (float(arc.start_angle), float(arc.end_angle)) == (-90.0, 90.0), str(arc))
    lines = [l for l in sketch.lines if l.name in names]
    line_ends = [built.points[p] for l in lines for p in (l.start, l.end)]
    arc_ends = [built.points[f"{arc.name}.{e}"] for e in ("start", "end")] if arc is not None else []
    check("  and three lines, two of them on the arc's own ends (not on new points there)", len(lines) == 3
          and len(arc_ends) == 2 and all(any(np.linalg.norm(e - q) < 1e-9 for q in line_ends) for e in arc_ends)
          and sum(p in (f"{arc.name}.start", f"{arc.name}.end") for l in lines for p in (l.start, l.end)) == 2,
          str([(l.start, l.end) for l in lines]))

    cap = face_index("Tilt", lambda lo, hi: (hi - lo)[0] > 5.9 and (hi - lo)[0] < 6.1 and (hi - lo)[2] < 4.3
                     and lo[2] > 0.5)
    window.construction_action.setChecked(True)
    settle()
    names = window.project_face(("face", "Tilt", cap))
    settle()
    sketch, built = window.part.feature("Target"), window.result.outputs["Target"]
    splines = [w for w in sketch.splines if w.name in names]
    spline = splines[0] if splines else None
    ok = spline is not None and spline.points[0] == spline.points[-1] and spline.construction
    if ok:
        xy = np.array([built.points[p] for p in spline.points])
        b = 3 * math.cos(math.radians(45))
        yc = 0.5 * (xy[:, 1].min() + xy[:, 1].max())
        off = np.abs(((xy[:, 0] - 50) / 3) ** 2 + ((xy[:, 1] - yc) / b) ** 2 - 1)
        ok = off.max() < 1e-6 and np.isclose(xy[:, 0].max() - xy[:, 0].min(), 6, atol=0.05)
        detail = f"{len(xy)} points, off {off.max():.2g}"
    else:
        detail = str(names)
    check("Tilt's end cap (construction on): a closed construction spline on the ellipse 3 x 3 cos 45", ok, detail)
    window.construction_action.setChecked(False)

    window.project_action.setChecked(False)
    window.finish_sketch()
    settle()
    check("no error dialogs", not errors, str(errors))
    window.close()
    check("no errors in VisPy callbacks", not vispy_errors, f"{vispy_errors[:3]}")
    print("FAILED: " + ", ".join(FAILED) if FAILED else "all checks passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
