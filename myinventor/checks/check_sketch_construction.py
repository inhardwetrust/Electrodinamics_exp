# check_sketch_construction.py
#
# Construction lines and circles (Sketch > Construction):
#   - model: `construction` round-trips through TOML (written only when
#     true), a non-boolean is refused;
#   - the toggle as a mode: off at first, new lines are normal; on (nothing
#     selected), new lines and circles are construction ones; off again,
#     normal again;
#   - the toggle with a selection: it shows the selected curves' type (on
#     only if all are construction); a click on a mixed selection makes them
#     all construction, the next one all normal (one undo step each); the
#     mode stays as it was; with the selection cleared the button shows the
#     mode again;
#   - drawn: construction ones as dashes CONSTRUCTION_DASH_PX long every
#     CONSTRUCTION_DASH_PX + CONSTRUCTION_GAP_PX pixels (a circle's dashes
#     on its radius), also after a zoom; normal ones solid; still hovered
#     and picked like the others.
#
#   python checks/check_sketch_construction.py

import os
import sys
import tempfile
import tomllib

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
from model.part import Part                                           # noqa: E402
from model.part_file import PartFileError, dumps, from_data           # noqa: E402
from model.sketch import Sketch                                       # noqa: E402
from view.geometry_view import CONSTRUCTION_DASH_PX, CONSTRUCTION_GAP_PX, HOVER_COLOR   # noqa: E402
from view.main_window import MainWindow                               # noqa: E402
from view.picking import _project                                     # noqa: E402

FAILED = []


def check(label, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {label} {detail}")
    if not ok:
        FAILED.append(label)


def check_model():
    part = Part()
    s = part.add_feature(Sketch("Sketch1", plane="XY Plane"))
    s.add_line(s.add_point("0", "0"), s.add_point("1", "0"), construction=True)
    s.add_line("P1", s.add_point("0", "1"))
    s.add_circle("P1", "2", construction=True)
    text = dumps(part)
    check("construction round-trips, written only when true",
          from_data(tomllib.loads(text)).to_dict() == part.to_dict() and text.count("construction = true") == 2
          and "construction = false" not in text)
    try:
        from_data(tomllib.loads(text.replace("construction = true", "construction = 1", 1)))
        check("a non-boolean construction is refused", False)
    except PartFileError as error:
        check("a non-boolean construction is refused", "construction" in str(error), str(error))


def dash_pixels(view, segments):
    """Each dash's length and the distance between dash starts, in pixels."""
    ends = _project(view._transform(), segments.reshape(-1, 3))[0].reshape(-1, 2, 2)
    return np.linalg.norm(ends[:, 1] - ends[:, 0], axis=1), np.linalg.norm(np.diff(ends[:, 0], axis=0), axis=1)


def main():
    check_model()
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
    sketch = lambda: window.part.feature("Sketch1")         # noqa: E731
    action = window.construction_action

    def line(a, b):
        window.line_action.setChecked(True)
        click(native, pixel(view, frame, a))
        click(native, pixel(view, frame, b))
        QTest.keyClick(native, Qt.Key_Escape)
        QTest.keyClick(native, Qt.Key_Escape)
        settle()
        return sketch().lines[-1]

    # --- the mode
    check("off at first", not action.isChecked() and not window.construction_mode)
    l1 = line((-8, 6), (8, 6))
    check("a new line is normal", not l1.construction)
    action.trigger()
    check("on with nothing selected: the mode", window.construction_mode and action.isChecked())
    l2 = line((-8, 2), (8, 2))
    window.circle_action.setChecked(True)
    click(native, pixel(view, frame, (0, -4)))
    click(native, pixel(view, frame, (3, -4)))
    QTest.keyClick(native, Qt.Key_Escape)
    QTest.keyClick(native, Qt.Key_Escape)
    settle()
    c1 = sketch().circles[-1]
    check("  new lines and circles are construction ones", l2.construction and c1.construction)
    action.trigger()
    l3 = line((-8, -9), (8, -9))
    check("off again: normal again", not window.construction_mode and not l3.construction)

    # --- the selection
    click(native, pixel(view, frame, (0, 2)))                                  # L2 (construction)
    check("a construction line selected: the button is on", action.isChecked())
    click(native, pixel(view, frame, (0, 6)))                                  # L1 (normal)
    check("a normal one: off", not action.isChecked())
    shift_click(native, pixel(view, frame, (0, 2)))
    shift_click(native, pixel(view, frame, (3, -4)))                           # C1's rim
    check("a mixed selection (L1, L2, C1): off", not action.isChecked() and len(window._selected_curves()) == 3,
          f"{window._selected_curves()}")
    steps = len(window.doc._undo)
    action.trigger()
    flags = {e.name: e.construction for e in sketch().lines + sketch().circles}
    check("  a click: all construction, the button on", flags["L1"] and flags["L2"] and flags["C1"]
          and action.isChecked() and len(window.doc._undo) == steps + 1, f"{flags}")
    action.trigger()
    flags = {e.name: e.construction for e in sketch().lines + sketch().circles}
    check("  again: all normal, the button off", not (flags["L1"] or flags["L2"] or flags["C1"])
          and not action.isChecked() and len(window.doc._undo) == steps + 2, f"{flags}")
    check("  the mode stays as it was (off)", not window.construction_mode)
    action.trigger()                                                           # all three construction again
    click(native, pixel(view, frame, (-12, -12)))
    check("selection cleared: the button shows the mode (off)", not action.isChecked() and not view.selection)

    # --- drawn
    visual = view._sketch_geometry["Sketch1"]
    lengths, steps_px = dash_pixels(view, visual.dash_segments["L2"])
    period = CONSTRUCTION_DASH_PX + CONSTRUCTION_GAP_PX
    check("a construction line: dashes 8 px every 13 px", len(lengths) > 10
          and np.allclose(lengths[:-1], CONSTRUCTION_DASH_PX, atol=0.1) and np.allclose(steps_px, period, atol=0.1),
          f"{len(lengths)} {np.round(lengths[:3], 2)} {np.round(steps_px[:3], 2)}")
    ring = visual.dash_segments["C1"].reshape(-1, 3)
    center = window.result.outputs["Sketch1"].world_mm(sketch().circle("C1").center)
    radius = window.result.outputs["Sketch1"].radii["C1"]
    check("  a construction circle: dashes on its radius", len(visual.dash_segments["C1"]) > 5
          and np.allclose(np.linalg.norm(ring - center, axis=1), radius, rtol=1e-3))
    check("  the normal one (L3) is solid, not dashed", "L3" not in visual.dash_segments
          and visual.lines.visible)
    camera = view.view.camera
    camera.scale_factor *= 2.5
    camera.view_changed()
    settle()
    visual = view._sketch_geometry["Sketch1"]
    lengths, steps_px = dash_pixels(view, visual.dash_segments["L2"])
    check("  the same pixels after a zoom", np.allclose(lengths[:-1], CONSTRUCTION_DASH_PX, atol=0.1)
          and np.allclose(steps_px, period, atol=0.1), f"{np.round(lengths[:3], 2)}")
    window._look_at_sketch()
    settle()
    hover(native, pixel(view, frame, (0.3, 2)))
    settle()
    key = ("entity", "Sketch1", "L2")
    check("a construction line hovers like the others", view.hovered == key
          and view._sketch_geometry["Sketch1"].shown_colors[key] == HOVER_COLOR, f"{view.hovered}")
    check("no error dialogs", not errors, f"{errors}")
    window.close()
    check("no errors in VisPy callbacks", not VISPY_ERRORS, f"{VISPY_ERRORS[:3]}")
    print("FAILED: " + ", ".join(FAILED) if FAILED else "all checks passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
