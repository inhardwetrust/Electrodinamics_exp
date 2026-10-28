# check_sketch_select.py
#
# Selecting several sketch entities (real mouse events, a sketch on XY from
# the top). The sketch: L1 from P1 (0, 0) to P2 (6, 0); L2 from P3 (6, 0),
# at the same place as P2, to P4 (6, 6); an explicit point P5 (10, 8).
#   - a click on P2 selects P2 and P3 (the same place); Shift + click adds
#     P5, again takes it out, adds L1; the info names them;
#   - a drag on a selected entity moves the whole selection (P2, P3, P5 by
#     the same amount, P1 and P4 stay), one undo step;
#   - a drag on an unselected one selects it alone and moves only it;
#   - points at one place dragged together take the alignment hints (both
#     land on the sketch origin's vertical);
#   - a click on empty space clears;
#   - a box left to right takes what lies wholly inside (P1, P2, P3, L1; not
#     L2), right to left also what crosses it (L2); the box shows while
#     drawn (dashed: many pieces) and goes on release; Shift + box adds; the
#     camera does not move;
#   - Delete removes the whole selection.
#
#   python checks/check_sketch_select.py

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

from PySide6.QtCore import QEvent, Qt                                # noqa: E402
from PySide6.QtTest import QTest                                      # noqa: E402

from check_sketch_drag import drag                                    # noqa: E402
from check_sketch_line import click, pixel, send, settle, shield             # noqa: E402
from view.main_window import MainWindow                               # noqa: E402

FAILED = []


def check(label, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {label} {detail}")
    if not ok:
        FAILED.append(label)


def shift_click(native, pos):
    send(native, QEvent.MouseMove, pos)
    send(native, QEvent.MouseButtonPress, pos, Qt.LeftButton, Qt.LeftButton, Qt.ShiftModifier)
    send(native, QEvent.MouseButtonRelease, pos, Qt.LeftButton, Qt.NoButton, Qt.ShiftModifier)
    settle()


def box(native, a, b, shift=False, view=None):
    """A left drag from pixel a to b on empty space; returns (box shown while drawn, its pieces)."""
    modifier = Qt.ShiftModifier if shift else Qt.NoModifier
    a, b = np.asarray(a, float), np.asarray(b, float)
    send(native, QEvent.MouseMove, a)
    send(native, QEvent.MouseButtonPress, a, Qt.LeftButton, Qt.LeftButton, modifier)
    for i in range(1, 6):
        send(native, QEvent.MouseMove, a + (b - a) * i / 5, Qt.NoButton, Qt.LeftButton, modifier)
    settle()
    shown = view.box_line.visible
    pieces = len(view.box_line.pos) // 2 if view.box_line.pos is not None else 0
    send(native, QEvent.MouseButtonRelease, b, Qt.LeftButton, Qt.NoButton, modifier)
    settle()
    return shown, pieces


def main():
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
    p1, p2, p3, p4 = s.add_point("0", "0"), s.add_point("6", "0"), s.add_point("6", "0"), s.add_point("6", "6")
    s.add_line(p1, p2)
    s.add_line(p3, p4)
    s.add_point("10", "8", explicit=True)
    window._commit()
    settle()
    frame = view.sketch_frame
    camera = view.view.camera
    tolerance = view._mm_per_px()
    sketch = lambda: window.part.feature("Sketch1")         # noqa: E731

    def names():
        return {k[2] for k in view.selection}

    def xy(name):
        p = sketch().point(name)
        return np.array([float(p.x), float(p.y)])

    # --- clicks
    click(native, pixel(view, frame, (6, 0)))
    check("a click on P2 selects P2 and P3 (the same place)", names() == {"P2", "P3"}, f"{names()}")
    shift_click(native, pixel(view, frame, (10, 8)))
    check("Shift + click adds P5", names() == {"P2", "P3", "P5"}, f"{names()}")
    shift_click(native, pixel(view, frame, (10, 8)))
    check("Shift + click again takes it out", names() == {"P2", "P3"}, f"{names()}")
    shift_click(native, pixel(view, frame, (3, 0)))
    check("Shift + click on L1 adds the line", names() == {"P2", "P3", "L1"}, f"{names()}")
    check("  the info names them", "3 selected" in window.info.text() and "L1" in window.info.text(),
          window.info.text())

    # --- drag the selection
    shift_click(native, pixel(view, frame, (3, 0)))          # L1 out again
    shift_click(native, pixel(view, frame, (10, 8)))         # P5 in
    steps = len(window.doc._undo)
    drag(native, pixel(view, frame, (10, 8)), pixel(view, frame, (11, 10)))
    moved = {n: xy(n) for n in ("P1", "P2", "P3", "P4", "P5")}
    check("a drag on a selected point moves the whole selection",
          np.allclose(moved["P5"] - (10, 8), (1, 2), atol=tolerance)
          and np.allclose(moved["P2"] - (6, 0), moved["P5"] - (10, 8), atol=1e-9)
          and np.allclose(moved["P3"], moved["P2"]), f"{moved}")
    check("  the others stay; one undo step", np.allclose(moved["P1"], (0, 0)) and np.allclose(moved["P4"], (6, 6))
          and len(window.doc._undo) == steps + 1)
    window.undo()
    settle()
    view.select_keys([("entity", "Sketch1", "P5")])
    drag(native, pixel(view, frame, (6, 3)), pixel(view, frame, (8, 3)))        # L2, not selected
    check("a drag on an unselected line selects it alone, moves only it",
          names() == {"L2"} and np.allclose(xy("P3"), (8, 0), atol=tolerance)
          and np.allclose(xy("P4"), (8, 6), atol=tolerance) and np.allclose(xy("P2"), (6, 0))
          and np.allclose(xy("P5"), (10, 8)), f"{names()} {xy('P3')} {xy('P2')}")
    window.undo()
    settle()

    # --- a coincident group takes the alignment hints
    click(native, pixel(view, frame, (6, 0)))
    drag(native, pixel(view, frame, (6, 0)), pixel(view, frame, (3 * tolerance, -4)))
    check("points at one place dragged together align (the origin's vertical: x = 0)",
          sketch().point("P2").x == sketch().point("P3").x == "0", f"{xy('P2')} {xy('P3')}")
    window.undo()
    settle()

    # --- a click on empty space
    click(native, pixel(view, frame, (-8, -8)))
    check("a click on empty space clears", not view.selection)

    # --- boxes
    rotation = camera.state()["rotation"]
    shown, pieces = box(native, pixel(view, frame, (-1, 1)), pixel(view, frame, (7, -1)), view=view)
    check("a box left to right: wholly inside (P1, P2, P3, L1)", names() == {"P1", "P2", "P3", "L1"},
          f"{names()}")
    check("  shown while drawn (solid: 4 pieces), gone after", shown and pieces == 4 and not view.box_line.visible,
          f"{pieces}")
    shown, pieces = box(native, pixel(view, frame, (7, -1)), pixel(view, frame, (-1, 1)), view=view)
    check("right to left also takes what crosses (L2)", names() == {"P1", "P2", "P3", "L1", "L2"}, f"{names()}")
    check("  drawn dashed", shown and pieces > 8, f"{pieces}")
    click(native, pixel(view, frame, (10, 8)))
    box(native, pixel(view, frame, (5, 7)), pixel(view, frame, (7, 5)), shift=True, view=view)
    check("Shift + box adds (P5 and P4)", names() == {"P5", "P4"}, f"{names()}")
    check("the camera did not move", np.allclose(rotation, camera.state()["rotation"]))

    # --- Delete
    click(native, pixel(view, frame, (10, 8)))
    shift_click(native, pixel(view, frame, (3, 0)))
    QTest.keyClick(native, Qt.Key_Delete)
    settle()
    check("Delete removes the whole selection (P5, L1 with its unshared ends P1, P2)",
          [p.name for p in sketch().points] == ["P3", "P4"] and [ln.name for ln in sketch().lines] == ["L2"],
          f"{[p.name for p in sketch().points]} {[ln.name for ln in sketch().lines]}")
    check("no error dialogs", not errors, f"{errors}")
    window.close()
    check("no errors in VisPy callbacks", not VISPY_ERRORS, f"{VISPY_ERRORS[:3]}")
    print("FAILED: " + ", ".join(FAILED) if FAILED else "all checks passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
