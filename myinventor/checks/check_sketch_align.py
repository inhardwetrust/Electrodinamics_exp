# check_sketch_align.py
#
# Alignment hints (dashed inference lines) in sketch mode, real mouse
# events on a sketch on XY looked at from the top; offsets given in pixels
# (ALIGN_PX = 6):
#   - align(): x and y independently, the nearest source wins, the sketch
#     origin counts, excluded and fixed axes are left alone;
#   - Line: a cursor 3 px off the horizontal through the last point puts the
#     next point on it exactly (the same y text), a dashed guide shows from
#     that point meanwhile; 12 px off: no hint; the vertical through the
#     origin; two hints at once (x from one point, y from another);
#   - the guide is dashed: dashes of GUIDE_DASH_PX, gaps of GUIDE_GAP_PX;
#   - Point aligns too;
#   - dragging a point near another point's vertical: its x becomes that x
#     exactly, the guide shows while dragging and goes on release; a formula
#     axis stays and only the free one aligns;
#   - snapping onto a point wins over the hints (no guide); the chain's
#     first click, not made yet, is a snap target too (a click 3 px beside
#     it ends the chain, no tiny line).
#
#   python checks/check_sketch_align.py

import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "checks"))
sys.stdout.reconfigure(encoding="utf-8")

TMP = tempfile.mkdtemp()
os.environ["MYINVENTOR_SETTINGS"] = os.path.join(TMP, "settings.json")     # never the user's settings

import numpy as np                                                    # noqa: E402
from vispy import app                                                 # noqa: E402

app.use_app("pyside6")
app.use_app().create()

sys.path.insert(0, os.path.join(ROOT, "checks"))
from vispy_errors import install as _watch_vispy  # noqa: E402

VISPY_ERRORS = _watch_vispy()

from PySide6.QtCore import QEvent, Qt                                # noqa: E402
from PySide6.QtTest import QTest                                      # noqa: E402

from check_sketch_line import click, hover, pixel, send, settle, shield      # noqa: E402
from view.geometry_view import GUIDE_DASH_PX, GUIDE_GAP_PX           # noqa: E402
from view.main_window import MainWindow                               # noqa: E402
from view.picking import _project                                     # noqa: E402
from view.sketch_tools import align                                   # noqa: E402

FAILED = []


def check(label, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {label} {detail}")
    if not ok:
        FAILED.append(label)


def check_align():
    points = {"A": np.array([5.0, 2.0]), "B": np.array([9.0, 7.0])}
    xy, guides = align((5.2, 6.9), points, 0.3)
    check("align: x from A, y from B", np.allclose(xy, (5, 7)) and len(guides) == 2, f"{xy}")
    xy, guides = align((5.4, 3.0), points, 0.3)
    check("align: out of reach, nothing", np.allclose(xy, (5.4, 3.0)) and not guides)
    xy, _ = align((0.1, 4.0), points, 0.3)
    check("align: the sketch origin counts", np.allclose(xy, (0, 4)))
    xy, guides = align((5.1, 2.1), points, 0.3, exclude={"A"})
    check("align: an excluded point does not count", np.allclose(xy, (5.1, 2.1)) and not guides)
    xy, _ = align((5.1, 6.95), points, 0.3, fixed=(True, False))
    check("align: a fixed axis stays", np.allclose(xy, (5.1, 7.0)))
    xy, _ = align((5.25, 0), {"A": np.array([5.0, 9.0]), "C": np.array([5.3, 9.0])}, 0.3)
    check("align: the nearest source wins", np.isclose(xy[0], 5.3))


def main():
    check_align()
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
    settle()
    frame = view.sketch_frame
    px = view._mm_per_px()                                   # mm per pixel
    sketch = lambda: window.part.feature("Sketch1")         # noqa: E731

    def at(xy, dx=0.0, dy=0.0):
        """The pixel of sketch point xy, moved dx, dy pixels (dy up on the screen = +y)."""
        return pixel(view, frame, (xy[0] + dx * px, xy[1] + dy * px))

    window.line_action.trigger()
    click(native, at((2, 2)))
    hover(native, at((9, 2), dy=3))
    settle()
    preview = view.tool.preview
    first = preview[0]                                      # the chain's first click (not made yet)
    check("Line: 3 px off the horizontal through the last point: on it", np.isclose(preview[1][1], first[1]),
          f"{preview}")
    check("  a guide from that point", len(view.guides) == 1 and np.allclose(view.guides[0][0], first)
          and view.guide_lines.visible)
    # dashes: each dash GUIDE_DASH_PX long, starts GUIDE_DASH_PX + GUIDE_GAP_PX apart (pixels)
    dashes = view.guide_lines.pos.reshape(-1, 2, 3)
    ends = _project(view._transform(), dashes.reshape(-1, 3))[0].reshape(-1, 2, 2)
    lengths = np.linalg.norm(ends[:, 1] - ends[:, 0], axis=1)
    steps = np.linalg.norm(np.diff(ends[:, 0], axis=0), axis=1)
    check("  dashed: 6 px dashes every 10 px", len(dashes) > 3 and np.allclose(lengths[:-1], GUIDE_DASH_PX, atol=0.1)
          and np.allclose(steps, GUIDE_DASH_PX + GUIDE_GAP_PX, atol=0.1),
          f"{len(dashes)} dashes, {np.round(lengths[:3], 2)}, {np.round(steps[:3], 2)}")
    click(native, at((9, 2), dy=3))
    p1, p2 = sketch().points[:2]
    check("  the new point takes the same y text exactly", p2.y == p1.y, f"{p1} {p2}")
    xy = {q.name: np.array([float(q.x), float(q.y)]) for q in sketch().points}
    hover(native, at((9, 5), dx=12))
    settle()
    check("12 px off: no hint", not view.guides and not np.isclose(view.tool.preview[1][0], 9.0))
    hover(native, at((0, 6), dx=-4))
    settle()
    check("the vertical through the sketch origin", np.isclose(view.tool.preview[1][0], 0.0) and view.guides)
    hover(native, at((2, 7), dx=4, dy=-3))
    settle()
    preview = view.tool.preview
    check("two hints at once: x from P1 ... ", np.isclose(preview[1][0], xy["P1"][0]) and len(view.guides) >= 1,
          f"{preview}")
    click(native, at((2, 7)))                               # P3 on P1's vertical
    xy = {q.name: np.array([float(q.x), float(q.y)]) for q in sketch().points}
    hover(native, at((9, 7), dx=-4, dy=3))
    settle()
    preview = view.tool.preview
    check("  ... and y from another: x of P2, y of P3", np.allclose(preview[1], (xy["P2"][0], xy["P3"][1]))
          and len(view.guides) == 2,
          f"{preview} {view.guides}")
    hover(native, at((9, 2), dx=3))
    settle()
    check("snapping onto a point wins (no guide)", view.tool.preview[2] == ("point", "P2") and not view.guides)
    QTest.keyClick(native, Qt.Key_Escape)                   # end that chain
    counts = (len(sketch().points), len(sketch().lines))
    click(native, at((-6, -6)))
    click(native, at((-6, -6), dx=3, dy=2))
    check("a click 3 px beside the chain's first click (not made yet) ends it, no tiny line",
          (len(sketch().points), len(sketch().lines)) == counts and view.tool.last is None)
    QTest.keyClick(native, Qt.Key_Escape)
    settle()
    check("leaving the tool clears the guides", view.tool is None and not view.guide_lines.visible)

    window.point_action.trigger()
    click(native, at((12, 4), dy=2))
    check("Point puts its point ...", sketch().points[-1].explicit)
    click(native, at((12, 7), dy=-4))
    p = sketch().points[-1]
    check("  ... a point 4 px under P3's horizontal takes its y", p.y == sketch().point("P3").y, f"{p}")
    QTest.keyClick(native, Qt.Key_Escape)
    settle()

    # --- dragging a point
    names = [q.name for q in sketch().points]
    target = names[-1]                                       # the last explicit point, at (12, 7)
    start, end = at((12, 7)), at((9, 0), dx=3)               # near P2's vertical (x = 9)
    send(native, QEvent.MouseMove, start)
    send(native, QEvent.MouseButtonPress, start, Qt.LeftButton, Qt.LeftButton)
    for i in range(1, 7):
        send(native, QEvent.MouseMove, start + (end - start) * i / 6, Qt.NoButton, Qt.LeftButton)
    settle()
    during = list(view.guides)
    send(native, QEvent.MouseButtonRelease, end, Qt.LeftButton, Qt.NoButton)
    settle()
    q = sketch().point(target)
    check("drag near P2's vertical: its x exactly", q.x == sketch().point("P2").x, f"{q}")
    check("  the guide shows while dragging, goes on release", len(during) >= 1 and not view.guides
          and not view.guide_lines.visible, f"{during}")
    window.part.feature("Sketch1").point(target).x = "6 + 6"
    window._commit()
    settle()
    start, end = at((12, 0)), at((9, 7), dx=3, dy=2)          # near P3's horizontal (y = 7), x near 9
    send(native, QEvent.MouseMove, start)
    send(native, QEvent.MouseButtonPress, start, Qt.LeftButton, Qt.LeftButton)
    for i in range(1, 7):
        send(native, QEvent.MouseMove, start + (end - start) * i / 6, Qt.NoButton, Qt.LeftButton)
    send(native, QEvent.MouseButtonRelease, end, Qt.LeftButton, Qt.NoButton)
    settle()
    q = sketch().point(target)
    check("a formula x stays, the free y aligns (P3's y)", q.x == "6 + 6" and q.y == sketch().point("P3").y,
          f"{q}")
    check("no error dialogs", not errors, f"{errors}")
    window.close()
    check("no errors in VisPy callbacks", not VISPY_ERRORS, f"{VISPY_ERRORS[:3]}")
    print("FAILED: " + ", ".join(FAILED) if FAILED else "all checks passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
