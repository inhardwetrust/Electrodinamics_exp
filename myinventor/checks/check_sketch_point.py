# check_sketch_point.py
#
# Explicit sketch points (Sketch > Point):
#   - model: `explicit` round-trips through TOML (written only when true), a
#     non-boolean is refused; deleting a line keeps an explicit end point
#     (a plain end point goes); deleting an explicit point takes its lines;
#   - the Point tool (real mouse events, a sketch on XY from the top): a
#     click puts an explicit point under the cursor (one undo step), a click
#     on the sketch origin puts one at exactly (0, 0), a click on an
#     existing point puts nothing; Line turns Point off and the other way
#     round; Esc leaves the tool;
#   - the look: a dot in a gapped cross, in the sketch's plane: each arm runs
#     from CROSS_GAP_PX to CROSS_ARM_PX pixels from the point, also after a
#     zoom; shown outside sketch mode, where a plain line end is not;
#   - behaviour: hover turns the point green, a click selects it (info says
#     "sketch point"), a left drag moves it, Delete removes it; Line snaps
#     to it and starts there; outside sketch mode hovering it highlights
#     the sketch.
#
#   python checks/check_sketch_point.py

import os
import sys
import tempfile
import tomllib

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

from PySide6.QtCore import Qt                                        # noqa: E402
from PySide6.QtTest import QTest                                      # noqa: E402

from check_sketch_drag import drag                                    # noqa: E402
from check_sketch_line import click, hover, pixel, settle, shield            # noqa: E402
from model.part import Part                                           # noqa: E402
from model.part_file import PartFileError, dumps, from_data           # noqa: E402
from model.sketch import Sketch                                       # noqa: E402
from view.geometry_view import CROSS_ARM_PX, CROSS_GAP_PX, HOVER_COLOR   # noqa: E402
from view.main_window import MainWindow                               # noqa: E402
from view.picking import _project                                     # noqa: E402

FAILED = []


def check(label, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {label} {detail}")
    if not ok:
        FAILED.append(label)


def check_model():
    part = Part()
    sketch = part.add_feature(Sketch("Sketch1", plane="XY Plane"))
    p1 = sketch.add_point("0", "0", explicit=True)
    p2, p3 = sketch.add_point("5", "0"), sketch.add_point("5", "5")
    sketch.add_line(p1, p2)
    sketch.add_line(p2, p3)
    text = dumps(part)
    back = from_data(tomllib.loads(text))
    check("explicit round-trips, written only when true", back.to_dict() == part.to_dict()
          and text.count("explicit = true") == 1 and "explicit = false" not in text)
    try:
        from_data(tomllib.loads(text.replace("explicit = true", 'explicit = "yes"')))
        check("a non-boolean explicit is refused", False)
    except PartFileError as error:
        check("a non-boolean explicit is refused", "explicit" in str(error), str(error))
    sketch.delete("L1")
    check("deleting a line keeps its explicit end (P1), the shared one (P2)",
          [p.name for p in sketch.points] == ["P1", "P2", "P3"])
    sketch.add_line("P1", "P3")
    sketch.delete("P1")
    check("deleting an explicit point takes its lines", [line.name for line in sketch.lines] == ["L2"]
          and [p.name for p in sketch.points] == ["P2", "P3"])


def arm_pixels(view, visual):
    """Each cross arm's two ends: their distances (px) from the point's pixel."""
    transform = view._transform()
    centers = visual.point_pos[np.array(visual.explicit, bool)]
    segments = visual.cross_segments.reshape(len(centers), 4, 2, 3)
    out = []
    for center, arms in zip(centers, segments):
        c = _project(transform, center[None])[0][0]
        ends = _project(transform, arms.reshape(-1, 3))[0]
        out.append(np.linalg.norm(ends - c, axis=1))
    return np.concatenate(out)


def check_window():
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
    camera = view.view.camera
    tolerance = camera.scale_factor / min(camera._viewbox.size)
    sketch = lambda: window.part.feature("Sketch1")                     # noqa: E731

    window.point_action.trigger()
    check("Point on", view.tool is not None and view.tool.name == "Point")
    steps = len(window.doc._undo)
    click(native, pixel(view, frame, (4, 3)))
    p = sketch().points[0] if sketch().points else None
    check("a click puts an explicit point under the cursor", p is not None and p.explicit
          and np.allclose((float(p.x), float(p.y)), (4, 3), atol=tolerance), f"{p}")
    check("  one undo step", len(window.doc._undo) == steps + 1)
    click(native, pixel(view, frame, (0, 0)) + 2)
    check("a click on the sketch origin: a point at exactly (0, 0)",
          [(q.x, q.y) for q in sketch().points][-1:] == [("0", "0")])
    click(native, pixel(view, frame, (4, 3)) + 2)
    check("a click on an existing point puts nothing", len(sketch().points) == 2)
    window.line_action.trigger()
    check("Line turns Point off", view.tool.name == "Line" and not window.point_action.isChecked())
    window.point_action.trigger()
    check("Point turns Line off", view.tool.name == "Point" and not window.line_action.isChecked())
    QTest.keyClick(native, Qt.Key_Escape)
    settle()
    check("Esc leaves the tool", view.tool is None and not window.point_action.isChecked()
          and native.isVisible())

    # --- the look
    visual = view._sketch_geometry["Sketch1"]
    distances = arm_pixels(view, visual)
    expected = np.tile([CROSS_GAP_PX, CROSS_ARM_PX], len(distances) // 2)
    check("a gapped cross: arms from 3 px to 9 px", visual.crosses.visible and len(distances) == 16
          and np.allclose(distances, expected, atol=0.2), f"{np.round(distances, 2).tolist()}")
    camera.scale_factor *= 3
    camera.view_changed()
    settle()
    distances = arm_pixels(view, visual)
    check("  the same pixels after a zoom", np.allclose(distances, expected, atol=0.2),
          f"{np.round(distances, 2).tolist()}")
    window._look_at_sketch()
    settle()

    # --- behaviour in sketch mode
    at = pixel(view, frame, (4, 3))
    hover(native, at)
    settle()
    key = ("entity", "Sketch1", "P1")
    check("hover: the point green", view.hovered == key and view._sketch_geometry["Sketch1"].shown_colors[key]
          == HOVER_COLOR, f"{view.hovered}")
    click(native, at)
    check("a click selects it (sketch point)", view.selected == key and "sketch point" in window.info.text())
    drag(native, at, pixel(view, frame, (6, -2)))
    p1 = sketch().point("P1")
    check("a left drag moves it", np.allclose((float(p1.x), float(p1.y)), (6, -2), atol=tolerance), f"{p1}")
    window.line_action.trigger()
    click(native, pixel(view, frame, (6, -2)) + 3)
    click(native, pixel(view, frame, (10, -2)))
    QTest.keyClick(native, Qt.Key_Escape)
    QTest.keyClick(native, Qt.Key_Escape)
    settle()
    lines = sketch().lines
    check("Line snaps to it and starts there", len(lines) == 1 and lines[0].start == "P1", f"{lines}")
    window.finish_sketch()
    view.look_from("Top")
    settle()
    visual = view._sketch_geometry["Sketch1"]
    check("outside sketch mode: crosses shown, the line's plain end not",
          visual.crosses.visible and len(visual.cross_segments) == 8
          and visual.dots.visible and len(visual.dots._data) == 2)
    hover(native, pixel(view, frame, (6, -2)))
    settle()
    check("  hovering the point highlights the sketch", view.hovered == ("feature", "Sketch1"))

    window.edit_sketch("Sketch1")
    settle()
    click(native, pixel(view, frame, (0, 0)))
    QTest.keyClick(native, Qt.Key_Delete)
    settle()
    check("Delete removes the selected point", [q.name for q in sketch().points] == ["P1", "P3"],
          f"{[q.name for q in sketch().points]}")
    check("no error dialogs", not errors, f"{errors}")
    window.close()


def main():
    check_model()
    check_window()
    check("no errors in VisPy callbacks", not VISPY_ERRORS, f"{VISPY_ERRORS[:3]}")
    print("FAILED: " + ", ".join(FAILED) if FAILED else "all checks passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
