# check_sketch_drag.py
#
# Dragging sketch points and lines with the left button (real mouse events,
# a sketch on XY looked at from the top; positions checked in sketch mm
# against the pixels):
#   - a point dragged to a pixel lands there; the lines on it follow; one
#     undo step; undo puts it back;
#   - a line dragged moves both ends by the same amount, the third point
#     stays; the camera does not move;
#   - a point whose x is a formula: x stays (the status bar says so), y follows;
#   - Esc while dragging puts everything back, no undo step;
#   - a press and release without a move still selects (a click);
#   - a left drag on empty space, outside sketch mode, or with the Line
#     tool on moves no sketch point.
#
#   python checks/check_sketch_drag.py

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

from check_sketch_line import click, pixel, send, settle, shield             # noqa: E402
from view.main_window import MainWindow                               # noqa: E402

FAILED = []


def check(label, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {label} {detail}")
    if not ok:
        FAILED.append(label)


def drag(native, start, end, steps=6, escape=False):
    """A left drag from pixel `start` to `end` (moves spaced for VisPy); Esc before the release if asked."""
    start, end = np.asarray(start, float), np.asarray(end, float)
    send(native, QEvent.MouseMove, start)
    send(native, QEvent.MouseButtonPress, start, Qt.LeftButton, Qt.LeftButton)
    for i in range(1, steps + 1):
        send(native, QEvent.MouseMove, start + (end - start) * i / steps, Qt.NoButton, Qt.LeftButton)
    if escape:
        QTest.keyClick(native, Qt.Key_Escape)
    send(native, QEvent.MouseButtonRelease, end, Qt.LeftButton, Qt.NoButton)
    settle()


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

    window.part.add_parameter("a", "2")
    window.new_sketch("XY Plane")
    sketch = window.part.feature("Sketch1")
    p1, p2, p3 = sketch.add_point("0", "0"), sketch.add_point("8", "0"), sketch.add_point("4", "6")
    for start, end in ((p1, p2), (p2, p3), (p3, p1)):
        sketch.add_line(start, end)
    window._commit()
    settle()
    frame = view.sketch_frame
    camera = view.view.camera
    tolerance = camera.scale_factor / min(camera._viewbox.size)      # mm per pixel

    def points():
        result = window.result.outputs["Sketch1"]
        return {n: result.points[n].copy() for n in result.points}

    # --- a point
    steps = len(window.doc._undo)
    before_camera = camera.state()
    drag(native, pixel(view, frame, (8, 0)), pixel(view, frame, (10, 3)))
    got = points()
    check("a dragged point lands under the cursor", np.allclose(got["P2"], (10, 3), atol=tolerance),
          f"{got['P2']}")
    segments = window.result.outputs["Sketch1"].segments_mm()
    check("  the lines on it follow", np.allclose(segments[0][1][:2], got["P2"])
          and np.allclose(segments[1][0][:2], got["P2"]))
    check("  one undo step, the camera still", len(window.doc._undo) == steps + 1
          and all(np.allclose(before_camera[k], camera.state()[k]) for k in before_camera))
    check("  it is selected", view.selected == ("entity", "Sketch1", "P2"))
    window.undo()
    check("undo puts it back", np.allclose(points()["P2"], (8, 0)))

    # --- a line
    before = points()
    drag(native, pixel(view, frame, (4, 0)), pixel(view, frame, (5, -2)))     # L1 at its middle
    got = points()
    moved = got["P1"] - before["P1"]
    check("a dragged line: both ends move by the same amount", np.allclose(moved, (1, -2), atol=tolerance)
          and np.allclose(got["P2"] - before["P2"], moved, atol=1e-9), f"{moved}")
    check("  the third point stays", np.allclose(got["P3"], before["P3"]))

    # --- a formula stays
    window.part.feature("Sketch1").point("P3").x = "a * 2"
    window._commit()
    settle()
    drag(native, pixel(view, frame, (4, 6)), pixel(view, frame, (7, 8)))
    got = points()
    p3 = window.part.feature("Sketch1").point("P3")
    check("a formula x stays, y follows", p3.x == "a * 2" and np.allclose(got["P3"], (4, 8), atol=tolerance),
          f"{p3}")
    check("  the status bar says so", "P3.x" in window.statusBar().currentMessage(),
          window.statusBar().currentMessage())

    # --- Esc
    before, steps = points(), len(window.doc._undo)
    drag(native, pixel(view, frame, got["P2"]), pixel(view, frame, (12, 5)), escape=True)
    check("Esc while dragging puts it back, no undo step",
          all(np.allclose(points()[n], before[n]) for n in before) and len(window.doc._undo) == steps
          and not window.doc.part.feature("Sketch1").point("P2").x == "12")

    # --- a click still selects
    l1 = window.result.outputs["Sketch1"]
    click(native, pixel(view, frame, 0.5 * (l1.points["P1"] + l1.points["P2"])))
    check("a click without a move selects (no move)", view.selected == ("entity", "Sketch1", "L1")
          and all(np.allclose(points()[n], before[n]) for n in before), f"{view.selected}")

    # --- nothing moves
    before = points()
    rotation = camera.state()["rotation"]
    drag(native, pixel(view, frame, (-8, 8)), pixel(view, frame, (-4, 4)))
    check("a left drag on empty space: nothing moves",
          all(np.allclose(points()[n], before[n]) for n in before)
          and np.allclose(rotation, camera.state()["rotation"]))
    window.line_action.trigger()
    drag(native, pixel(view, frame, before["P3"]), pixel(view, frame, (0, 9)))
    check("with Line on a drag moves no point", all(np.allclose(points()[n], before[n]) for n in before))
    window.line_action.setChecked(False)
    window.finish_sketch()
    view.look_from("Top")
    settle()
    drag(native, pixel(view, frame, before["P3"]), pixel(view, frame, (0, 9)))
    check("outside sketch mode a drag moves no point", all(np.allclose(points()[n], before[n]) for n in before))
    check("no error dialogs", not errors, f"{errors}")
    window.close()
    check("no errors in VisPy callbacks", not VISPY_ERRORS, f"{VISPY_ERRORS[:3]}")
    print("FAILED: " + ", ".join(FAILED) if FAILED else "all checks passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
