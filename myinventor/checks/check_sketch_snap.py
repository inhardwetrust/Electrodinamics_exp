# check_sketch_snap.py
#
# Points landing on lines and circles (curve_snap; real mouse events, a
# sketch on XY from the top). The sketch: a circle C1 around P1 (0, 0) with
# r = 5, a line L1 from P2 (-8, -7) to P3 (8, -7).
#   - curve_snap(): the foot on a line (clamped to the segment), the point
#     on a circle along the radius, nothing out of reach, the nearest wins,
#     excluded entities do not attract;
#   - Line: an end 3 px off the circle lands on it (|P - center| = r), the
#     marker is green; an end 3 px off the line lands on it (y = -7);
#   - Point: a point 3 px off the line lands on it;
#   - Circle: a center 3 px off the line lands on it;
#   - an existing point still wins over the curve through it;
#   - 15 px away nothing snaps;
#   - dragging a point 3 px off the circle puts it on the circle; a point is
#     not pulled onto its own line;
#   - the other way round, radius_snap(): a circle dragged by its rim goes
#     through a point that comes within SNAP_PX of it, wherever on the circle
#     (the radius = the distance to the point; a green dot shows it while
#     dragging), the sketch origin too, not its own center; far from points
#     the radius follows the cursor; the Circle tool's second click too.
#
#   python checks/check_sketch_snap.py

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
from vispy import app                                                 # noqa: E402

app.use_app("pyside6")
app.use_app().create()

from vispy_errors import install as _watch_vispy                      # noqa: E402

VISPY_ERRORS = _watch_vispy()

from PySide6.QtCore import Qt                                        # noqa: E402
from PySide6.QtTest import QTest                                      # noqa: E402

from check_sketch_drag import drag                                    # noqa: E402
from check_sketch_line import click, hover, pixel, settle, shield    # noqa: E402
from model.part import Part                                           # noqa: E402
from model.rebuild import rebuild                                     # noqa: E402
from model.sketch import Sketch                                       # noqa: E402
from view.main_window import MainWindow                               # noqa: E402
from check_sketch_drag import send                                    # noqa: E402
from PySide6.QtCore import QEvent                                    # noqa: E402
from view.sketch_tools import SNAP_COLOR, curve_snap, radius_snap     # noqa: E402

FAILED = []


def check(label, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {label} {detail}")
    if not ok:
        FAILED.append(label)


def build(sketch):
    part = Part()
    part.add_feature(sketch)
    return rebuild(part).outputs[sketch.name]


def check_function():
    s = Sketch("S", plane="XY Plane")
    s.add_circle(s.add_point("0", "0"), "5")
    s.add_line(s.add_point("-8", "-7"), s.add_point("8", "-7"))
    result = build(s)
    xy, name = curve_snap((2.0, -6.8), result, 0.5)
    check("a line: the foot of the perpendicular", name == "L1" and np.allclose(xy, (2, -7)), f"{xy} {name}")
    xy, name = curve_snap((9.2, -7.1), result, 1.5)
    check("  clamped to the segment (its end)", name == "L1" and np.allclose(xy, (8, -7)), f"{xy}")
    xy, name = curve_snap((3.1, 4.1), result, 0.5)
    check("a circle: along the radius", name == "C1" and np.isclose(np.linalg.norm(xy), 5.0)
          and np.allclose(xy / np.linalg.norm(xy), np.array([3.1, 4.1]) / np.hypot(3.1, 4.1)), f"{xy}")
    check("out of reach: nothing", curve_snap((0.0, 0.0), result, 0.5) is None)
    xy, name = curve_snap((0.0, -5.9), result, 2.0)     # 0.9 from C1, 1.1 from L1
    check("the nearest wins", name == "C1", f"{name}")
    check("excluded entities do not attract", curve_snap((2.0, -6.8), result, 0.5, exclude={"L1"}) is None)
    points = {"A": np.array([3.0, 4.0]), "B": np.array([0.0, 7.0])}
    r, at = radius_snap((0, 0), 5.15, points, 0.2)
    check("radius_snap: through the point within reach", np.isclose(r, 5.0) and np.allclose(at, (3, 4)))
    check("radius_snap: out of reach, nothing", radius_snap((0, 0), 6.0, points, 0.2) is None)
    r, at = radius_snap((1, 1), 1.5, points, 0.2)
    check("radius_snap: the sketch origin counts", np.isclose(r, np.sqrt(2)) and np.allclose(at, (0, 0)))
    check("radius_snap: excluded points do not count", radius_snap((0, 0), 5.1, points, 0.2, exclude={"A"}) is None)


def main():
    check_function()
    window = MainWindow()
    window.ask_save_changes = lambda: "discard"
    errors = []
    window.show_error = errors.append
    window.resize(1200, 800)
    window.show()
    settle(300)
    shield(window)
    view = window.view
    native = view.canvas.native
    window.new_sketch("XY Plane")
    s = window.part.feature("Sketch1")
    s.add_circle(s.add_point("0", "0"), "5")
    s.add_line(s.add_point("-8", "-7"), s.add_point("8", "-7"))
    window._commit()
    settle()
    frame = view.sketch_frame
    px = view._mm_per_px()
    sketch = lambda: window.part.feature("Sketch1")         # noqa: E731

    def xy(name):
        p = sketch().point(name)
        return np.array([float(p.x), float(p.y)])

    def near(point, dx=0.0, dy=0.0):
        return pixel(view, frame, (point[0] + dx * px, point[1] + dy * px))

    on_circle = 5 * np.array([np.cos(0.6), np.sin(0.6)])
    window.line_action.trigger()
    click(native, near((-3, 9)))
    hover(native, near(on_circle * 1.0, dx=2.0, dy=2.0))     # ~3 px outside the circle
    settle()
    check("Line: the marker turns green on the circle", view.tool.preview[2] is not None
          and view.tool.curve == "C1" and np.allclose(view.tool.marker._data["a_bg_color"][0], SNAP_COLOR),
          f"{view.tool.preview} {view.tool.curve}")
    click(native, near(on_circle, dx=2.0, dy=2.0))
    end = xy(sketch().lines[-1].end)
    check("  the end lands on the circle (|P - center| = r)", np.isclose(np.linalg.norm(end), 5.0, atol=1e-3),
          f"{end} r={np.linalg.norm(end):.5f}")
    click(native, near((3, -7), dy=3))
    end = xy(sketch().lines[-1].end)
    check("  the next end lands on the line (y = -7)", np.isclose(end[1], -7.0, atol=1e-6)
          and np.isclose(end[0], 3.0, atol=2 * px), f"{end}")
    QTest.keyClick(native, Qt.Key_Escape)
    QTest.keyClick(native, Qt.Key_Escape)
    settle()

    window.point_action.trigger()
    click(native, near((-5, -7), dy=-3))
    p = xy(sketch().points[-1].name)
    check("Point: 3 px off the line lands on it", np.isclose(p[1], -7.0, atol=1e-6), f"{p}")
    click(native, near((-30, 30)))                           # 15+ px from everything
    p = xy(sketch().points[-1].name)
    check("far away nothing snaps", not np.isclose(p[1], -7.0) and np.allclose(p, (-30, 30), atol=2 * px), f"{p}")
    QTest.keyClick(native, Qt.Key_Escape)
    settle()

    window.circle_action.trigger()
    click(native, near((6, -7), dy=3))
    click(native, near((7, -6)))
    center = xy(sketch().circles[-1].center)
    check("Circle: a center 3 px off the line lands on it", np.isclose(center[1], -7.0, atol=1e-6), f"{center}")
    QTest.keyClick(native, Qt.Key_Escape)
    QTest.keyClick(native, Qt.Key_Escape)
    settle()

    window.line_action.trigger()
    click(native, near((8, -7), dx=3, dy=3))                 # by P3, the line's end
    click(native, near((12, -3)))
    QTest.keyClick(native, Qt.Key_Escape)
    QTest.keyClick(native, Qt.Key_Escape)
    settle()
    check("an existing point wins over the curve through it", sketch().lines[-1].start == "P3",
          f"{sketch().lines[-1]}")

    # --- dragging
    explicit = [p.name for p in sketch().points if p.explicit and np.allclose(xy(p.name), (-30, 30), atol=2 * px)][0]
    drag(native, near(xy(explicit)), near(-5 * np.array([np.cos(0.3), np.sin(0.3)]), dx=-2, dy=-2))
    p = xy(explicit)
    check("drag a point 3 px off the circle: on it", np.isclose(np.linalg.norm(p), 5.0, atol=1e-3),
          f"{p} r={np.linalg.norm(p):.5f}")
    line = sketch().lines[-1]                                 # from P3 to its end near (12, -3)
    start, end = xy(line.start), xy(line.end)
    middle = 0.5 * (start + end)
    drag(native, near(end), near(middle, dx=3))               # 3 px beside its own line
    moved = xy(line.end)
    d, m = end - start, moved - start
    gap = abs(d[0] * m[1] - d[1] * m[0]) / np.linalg.norm(d)     # from the line as it was
    check("a point is not pulled onto its own line", gap > 1.5 * px, f"{moved} gap {gap / px:.2f} px")

    # --- a circle's rim sticks to points
    window.new_part()
    window.new_sketch("XY Plane")
    s = window.part.feature("Sketch1")
    s.add_circle(s.add_point("2", "1"), "3")
    s.add_point("7", "4", explicit=True)
    window._commit()
    settle()
    frame = view.sketch_frame
    px = view._mm_per_px()
    to_p = float(np.hypot(5, 3))                               # from the center (2, 1) to P2 (7, 4)

    def radius():
        return window.result.outputs["Sketch1"].radii["C1"]

    def rim_drag(start, end, look=False):
        start, end = np.asarray(start, float), np.asarray(end, float)
        send(native, QEvent.MouseMove, start)
        send(native, QEvent.MouseButtonPress, start, Qt.LeftButton, Qt.LeftButton)
        for i in range(1, 7):
            send(native, QEvent.MouseMove, start + (end - start) * i / 6, Qt.NoButton, Qt.LeftButton)
        settle()
        during = (view.snap_marker.visible, None if view.snap_spot is None else view.snap_spot.copy())
        send(native, QEvent.MouseButtonRelease, end, Qt.LeftButton, Qt.NoButton)
        settle()
        return during

    shown, spot = rim_drag(near((5, 1)), near((2, 1 + to_p), dy=3))       # straight up, P2 is off to the right
    check("dragging the rim near the distance to a point: through it", np.isclose(radius(), to_p, atol=1e-3),
          f"r = {radius():.5f}, |P2 - C| = {to_p:.5f}")
    check("  a green dot on the point while dragging, gone after", shown and spot is not None
          and np.allclose(spot, (7, 4)) and not view.snap_marker.visible, f"{shown} {spot}")
    rim_drag(near((2, 1 + radius())), near((2, 5)))
    check("far from points the radius follows the cursor (4)", np.isclose(radius(), 4.0, atol=2 * px),
          f"{radius():.4f}")
    rim_drag(near((2, 5)), near((2, 1 + np.sqrt(5)), dy=-3))
    check("the sketch origin counts (r = |C|)", np.isclose(radius(), np.sqrt(5), atol=1e-3), f"{radius():.5f}")
    window.circle_action.trigger()
    click(native, near((-6, -6)))
    to_p2 = float(np.hypot(13, 10))
    click(native, near((-6 - to_p2, -6), dx=-3))               # to the left; P2 is up and to the right
    out = window.result.outputs["Sketch1"]
    r2 = out.radii.get("C2")
    actual = float(np.linalg.norm(out.points["P2"] - out.points[sketch().circle("C2").center])) if r2 else None
    check("Circle tool: the second click goes through a point too", r2 is not None and np.isclose(r2, actual, atol=1e-3),
          f"{r2} vs |P2 - center| = {actual}")
    QTest.keyClick(native, Qt.Key_Escape)
    QTest.keyClick(native, Qt.Key_Escape)
    settle()
    check("no error dialogs", not errors, f"{errors}")
    window.close()
    check("no errors in VisPy callbacks", not VISPY_ERRORS, f"{VISPY_ERRORS[:3]}")
    print("FAILED: " + ", ".join(FAILED) if FAILED else "all checks passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
