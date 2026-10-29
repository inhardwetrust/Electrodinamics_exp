# check_sketch_clipboard.py
#
# Copy, cut and paste of sketch geometry (Ctrl+C / Ctrl+X / Ctrl+V as key
# presses; real mouse events; a sketch on XY from the top). The sketch: L1
# from P1 (0, 0) to P2 (4, 0), L2 from P2 to P3 (0, 3); a construction
# circle C1 around P4 (8, 8) with radius "a" (a = 1.5); an explicit point
# P5 (10, 0).
#   - nothing selected: Copy and Cut are off, Ctrl+C copies nothing;
#   - Ctrl+C on L1 and C1: the clipboard holds L1, C1 and their points
#     (P1, P2, P4), the anchor at the middle of their extent (circle
#     included); Paste turns on;
#   - Ctrl+V: the copy hangs on the cursor (the preview = the copy moved so
#     its anchor is under the cursor); a click puts it there: new points,
#     a line of the same length, a circle with the same radius formula and
#     construction flag, all moved by (click - anchor); one undo step; the
#     pasted is selected; the tool is left;
#   - the anchor snaps like a new point (3 px beside P3: exactly on P3);
#   - Esc while pasting: nothing is added;
#   - Ctrl+X on P5: it is gone (one undo step); Ctrl+V puts an explicit
#     point back elsewhere;
#   - a paste into another sketch (on XZ) lands in its own axes;
#   - undo takes a paste back.
#
#   python checks/check_sketch_clipboard.py

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

from check_sketch_line import click, hover, pixel, settle, shield    # noqa: E402
from check_sketch_select import shift_click                           # noqa: E402
from view.main_window import MainWindow                               # noqa: E402

FAILED = []


def check(label, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {label} {detail}")
    if not ok:
        FAILED.append(label)


def key(native, letter):
    QTest.keyClick(native, letter, Qt.ControlModifier)
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
    window.part.add_parameter("a", "1.5")
    window.new_sketch("XY Plane")
    s = window.part.feature("Sketch1")
    p1, p2, p3 = s.add_point("0", "0"), s.add_point("4", "0"), s.add_point("0", "3")
    s.add_line(p1, p2)
    s.add_line(p2, p3)
    s.add_circle(s.add_point("8", "8"), "a", construction=True)
    s.add_point("10", "0", explicit=True)
    window._commit()
    settle()
    frame = view.sketch_frame
    px = view._mm_per_px()
    sketch = lambda: window.part.feature("Sketch1")         # noqa: E731

    def xy(name, sk="Sketch1"):
        return window.result.outputs[sk].points[name].copy()

    # --- nothing selected
    check("nothing selected: Copy and Cut off", not window.copy_action.isEnabled() and not window.cut_action.isEnabled())
    key(native, Qt.Key_C)
    check("  Ctrl+C copies nothing", window.clipboard is None and not window.paste_action.isEnabled())

    # --- copy
    click(native, pixel(view, frame, (2, 0)))                      # L1
    shift_click(native, pixel(view, frame, (9.5, 8)))              # C1's rim
    check("L1 and C1 selected: Copy on", window.copy_action.isEnabled(), f"{view.selection}")
    key(native, Qt.Key_C)
    clip = window.clipboard
    check("Ctrl+C: L1, C1 and their points", clip is not None and sorted(clip["points"]) == ["P1", "P2", "P4"]
          and clip["lines"] == [("P1", "P2", False)] and len(clip["circles"]) == 1
          and clip["circles"][0][2:] == ("a", True), f"{clip}")
    # extent: x from 0 to 9.5 (the circle's right), y from 0 to 9.5 -> the anchor (4.75, 4.75)
    check("  the anchor: the middle of the extent (circle included)", np.allclose(clip["anchor"], (4.75, 4.75)),
          f"{clip['anchor']}")
    check("  Paste on", window.paste_action.isEnabled())

    # --- paste
    count = (len(sketch().points), len(sketch().lines), len(sketch().circles))
    key(native, Qt.Key_V)
    check("Ctrl+V: the paste tool", view.tool is not None and view.tool.name == "Paste")
    target = (-10.0, 9.0)
    hover(native, pixel(view, frame, target))
    settle()
    tool = view.tool
    offset = tool.offset
    expected = np.array(target) - clip["anchor"]
    check("  the copy hangs on the cursor by its anchor", tool.shape.visible
          and np.allclose(offset, expected, atol=2 * px), f"{offset} vs {expected}")
    steps = len(window.doc._undo)
    click(native, pixel(view, frame, target))
    new = (len(sketch().points), len(sketch().lines), len(sketch().circles))
    check("a click pastes: 3 points, 1 line, 1 circle more", new == (count[0] + 3, count[1] + 1, count[2] + 1),
          f"{count} -> {new}")
    line, circle = sketch().lines[-1], sketch().circles[-1]
    moved = xy(line.start) - xy("P1")
    check("  moved by (click - anchor)", np.allclose(moved, expected, atol=2 * px), f"{moved}")
    check("  the line keeps its length", np.isclose(np.linalg.norm(xy(line.end) - xy(line.start)), 4.0, atol=1e-6))
    check("  the circle keeps its radius formula and construction flag", circle.radius == "a" and circle.construction
          and np.allclose(xy(circle.center) - xy("P4"), moved, atol=1e-6))
    check("  one undo step, the pasted selected, the tool left", len(window.doc._undo) == steps + 1
          and {k[2] for k in view.selection} == {line.name, circle.name, line.start, line.end, circle.center}
          and view.tool is None, f"{view.selection}")

    # --- the anchor snaps
    key(native, Qt.Key_V)
    click(native, pixel(view, frame, (0, 3)) + 3)                  # 3 px beside P3
    pasted = sketch().lines[-1]
    anchor_now = xy(pasted.start) + (clip["anchor"] - clip["points"]["P1"]["xy"])
    check("the anchor snaps like a point (exactly onto P3)", np.allclose(anchor_now, xy("P3"), atol=1e-3),
          f"{anchor_now} vs {xy('P3')}")
    window.undo()
    settle()

    # --- Esc
    before = (len(sketch().points), len(sketch().lines), len(sketch().circles))
    key(native, Qt.Key_V)
    hover(native, pixel(view, frame, (5, -8)))
    QTest.keyClick(native, Qt.Key_Escape)
    settle()
    check("Esc while pasting: nothing added, the tool left", view.tool is None
          and (len(sketch().points), len(sketch().lines), len(sketch().circles)) == before)

    # --- cut
    click(native, pixel(view, frame, (10, 0)))                     # P5
    steps = len(window.doc._undo)
    key(native, Qt.Key_X)
    check("Ctrl+X on P5: gone, one undo step", "P5" not in [p.name for p in sketch().points]
          and len(window.doc._undo) == steps + 1)
    key(native, Qt.Key_V)
    click(native, pixel(view, frame, (12, -4)))
    p = sketch().points[-1]
    check("  Ctrl+V puts an explicit point back elsewhere", p.explicit
          and np.allclose(xy(p.name), (12, -4), atol=2 * px), f"{p}")

    # --- another sketch
    click(native, pixel(view, frame, (2, 0)))                      # L1 again
    key(native, Qt.Key_C)
    window.finish_sketch()
    window.new_sketch("XZ Plane")
    settle()
    frame2 = view.sketch_frame
    key(native, Qt.Key_V)
    click(native, pixel(view, frame2, (3, 4)))
    s2 = window.part.feature("Sketch2")
    world = window.result.outputs["Sketch2"].segments_mm()
    check("a paste into a sketch on XZ lands in its own axes (y = 0, length 4)",
          len(s2.lines) == 1 and np.allclose(world[0][:, 1], 0) and
          np.isclose(np.linalg.norm(world[0][1] - world[0][0]), 4.0, atol=1e-6), f"{world.tolist()}")
    window.undo()
    check("undo takes a paste back", not window.part.feature("Sketch2").lines)
    check("no error dialogs", not errors, f"{errors}")
    window.close()
    check("no errors in VisPy callbacks", not VISPY_ERRORS, f"{VISPY_ERRORS[:3]}")
    print("FAILED: " + ", ".join(FAILED) if FAILED else "all checks passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
