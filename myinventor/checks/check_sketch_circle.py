# check_sketch_circle.py
#
# Sketch circles (center point + radius formula):
#   - model: C1.. names, a TOML round trip ([[feature.circle]]), refused
#     files (a missing center, a radius that is not a formula, an unknown
#     key), the radius formula evaluated (a renamed parameter reaches it), a
#     radius <= 0 fails the sketch naming the circle; delete: a circle takes
#     its center unless a line uses it or it is explicit, deleting the
#     center takes the circle;
#   - the Circle tool (real mouse events, a sketch on XY from the top):
#     a click puts the center, the preview circle follows the cursor (its
#     radius the distance), a second click makes the circle (3-4-5: r = 5),
#     one undo step; the center snaps to an existing point (no new point);
#     Esc drops a center, again leaves the tool; Circle turns Line off;
#   - drawn: the circle's pieces lie at the radius from the center;
#   - selection: hover on the rim turns the circle green, a click selects
#     it (info: radius, diameter); a box left to right around it takes it, a
#     box over part of it does not, right to left over part of it does;
#   - dragging: by the rim, alone, the radius follows the cursor (the center
#     stays); by the center, the circle moves (radius kept); in a
#     selection with a point both move; a formula radius stays;
#   - Delete removes it (its unused center too).
#
#   python checks/check_sketch_circle.py

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

from check_sketch_drag import drag                                    # noqa: E402
from check_sketch_line import click, hover, pixel, settle, shield    # noqa: E402
from check_sketch_select import box, shift_click                      # noqa: E402
from model.part import Part                                           # noqa: E402
from model.part_file import PartFileError, dumps, from_data           # noqa: E402
from model.rebuild import rebuild                                     # noqa: E402
from model.sketch import Sketch                                       # noqa: E402
from view.geometry_view import HOVER_COLOR                            # noqa: E402
from view.main_window import MainWindow                               # noqa: E402

FAILED = []


def check(label, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {label} {detail}")
    if not ok:
        FAILED.append(label)


def check_model():
    part = Part()
    part.add_parameter("d", "6")
    s = part.add_feature(Sketch("Sketch1", plane="XY Plane"))
    c1 = s.add_circle(s.add_point("1", "2"), "d / 2")
    check("names: C1", c1 == "C1")
    text = dumps(part)
    check("TOML round trip", from_data(tomllib.loads(text)).to_dict() == part.to_dict()
          and "[[feature.circle]]" in text)
    result = rebuild(part).outputs["Sketch1"]
    ring = result.circle_xy("C1")
    check("the radius formula is evaluated (d / 2 = 3)", np.isclose(result.radii["C1"], 3.0)
          and np.allclose(np.linalg.norm(ring - (1, 2), axis=1), 3.0))
    part.rename_parameter("d", "dia")
    check("a renamed parameter reaches the radius", s.circle("C1").radius == "dia / 2")
    s.circle("C1").radius = "-1"
    errors = rebuild(part).feature_errors
    check("a radius <= 0 fails the sketch, naming the circle", "circle C1" in errors.get("Sketch1", ""),
          f"{errors}")
    s.circle("C1").radius = "3"

    head = 'format = "mycad-part"\nversion = 1\n[[feature]]\nname = "S"\ntype = "sketch"\nplane = "XY Plane"\n'
    pt = '[[feature.point]]\nname = "P1"\nx = "0"\ny = "0"\n'
    for label, body in {"a missing center": pt + '[[feature.circle]]\nname = "C1"\ncenter = "P9"\nradius = "1"\n',
                        "a radius that is not a formula": pt + '[[feature.circle]]\nname = "C1"\ncenter = "P1"\n'
                                                               'radius = true\n',
                        "an unknown key": pt + '[[feature.circle]]\nname = "C1"\ncenter = "P1"\nradius = "1"\n'
                                               'width = 2\n'}.items():
        try:
            from_data(tomllib.loads(head + body))
            check(f"refused: {label}", False)
        except PartFileError as error:
            check(f"refused: {label}", "C1" in str(error) or "circle" in str(error), str(error))

    p2 = s.add_point("9", "9")
    s.add_line("P1", p2)
    s.delete("C1")
    check("deleting a circle keeps a center a line uses", [p.name for p in s.points] == ["P1", "P2"])
    c2 = s.add_circle(s.add_point("5", "5"), "1")
    s.delete(c2)
    check("deleting a circle takes its unused center", [p.name for p in s.points] == ["P1", "P2"])
    s.add_circle("P1", "2")
    s.delete("P1")
    check("deleting the center takes the circle (and the line)", not s.circles and not s.lines)


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
    tolerance = view._mm_per_px()
    sketch = lambda: window.part.feature("Sketch1")         # noqa: E731

    # --- the tool
    window.line_action.trigger()
    window.circle_action.trigger()
    check("Circle on, Line off", view.tool.name == "Circle" and not window.line_action.isChecked())
    click(native, pixel(view, frame, (2, 1)))
    check("first click: nothing made yet", not sketch().points)
    hover(native, pixel(view, frame, (5, 5)))
    settle()
    tool = view.tool
    check("the preview circle follows the cursor (radius = distance)", tool.line.visible
          and np.isclose(tool.radius, 5.0, atol=2 * tolerance), f"{tool.radius}")
    steps = len(window.doc._undo)
    click(native, pixel(view, frame, (5, 5)))
    result = window.result.outputs["Sketch1"]
    check("second click: C1 with r = 5 around P1 (2, 1)", [c.name for c in sketch().circles] == ["C1"]
          and np.isclose(result.radii["C1"], 5.0, atol=2 * tolerance)
          and np.allclose(result.points["P1"], (2, 1), atol=tolerance), f"{result.radii}")
    check("  one undo step", len(window.doc._undo) == steps + 1)
    visual = view._sketch_geometry["Sketch1"]
    pieces = visual.segments[np.array(visual.seg_owner) == "C1"].reshape(-1, 3)
    check("drawn: its pieces at the radius from the center",
          np.allclose(np.linalg.norm(pieces - visual.point_pos[0], axis=1), result.radii["C1"]))
    click(native, pixel(view, frame, (2, 1)) + 3)            # onto P1
    click(native, pixel(view, frame, (2, 3)))
    check("a center on an existing point: no new point", len(sketch().points) == 1
          and sketch().circles[-1].center == "P1", f"{sketch().circles}")
    click(native, pixel(view, frame, (-8, -8)))
    QTest.keyClick(native, Qt.Key_Escape)
    settle()
    check("Esc drops the center (the tool stays)", view.tool is not None and tool.center is None)
    QTest.keyClick(native, Qt.Key_Escape)
    settle()
    check("Esc again leaves the tool", view.tool is None and not window.circle_action.isChecked())
    window.undo()                                           # the second circle (r = 2) off
    settle()

    # --- selection
    rim = pixel(view, frame, (7, 1))                        # on C1 (center (2, 1), r = 5)
    hover(native, rim)
    settle()
    key = ("entity", "Sketch1", "C1")
    check("hover on the rim: the circle green", view.hovered == key
          and view._sketch_geometry["Sketch1"].shown_colors[key] == HOVER_COLOR, f"{view.hovered}")
    click(native, rim)
    shown = float(window.info.text().split("diameter ")[-1].split(" mm")[0])
    check("a click selects it (info: radius, diameter)", view.selection == [key]
          and np.isclose(shown, 2 * window.result.outputs["Sketch1"].radii["C1"], atol=1e-3), window.info.text())
    click(native, pixel(view, frame, (-9, -9)))
    box(native, pixel(view, frame, (-4, 7)), pixel(view, frame, (8, -5)), view=view)
    check("a box left to right around it takes it (and its center)", {k[2] for k in view.selection} == {"C1", "P1"},
          f"{view.selection}")
    box(native, pixel(view, frame, (-4, 7)), pixel(view, frame, (3, -5)), view=view)
    check("over part of it: not the circle", ("entity", "Sketch1", "C1") not in view.selection)
    box(native, pixel(view, frame, (3, -5)), pixel(view, frame, (-4, 7)), view=view)
    check("right to left over part of it: the circle", ("entity", "Sketch1", "C1") in view.selection)

    # --- dragging
    click(native, pixel(view, frame, (-9, -9)))
    center = window.result.outputs["Sketch1"].points["P1"].copy()
    drag(native, rim, pixel(view, frame, (2, 9)))           # the rim alone: the radius
    result = window.result.outputs["Sketch1"]
    expected = np.linalg.norm(np.array([2.0, 9.0]) - center)
    check("drag by the rim: the radius follows (the distance to the cursor), the center stays",
          np.isclose(result.radii["C1"], expected, atol=2 * tolerance) and np.allclose(result.points["P1"], center),
          f"{result.radii} vs {expected:.4g}, {result.points['P1']}")
    drag(native, pixel(view, frame, (2, 1)), pixel(view, frame, (4, 2)))    # the center
    result = window.result.outputs["Sketch1"]
    check("drag by the center: the circle moves, radius kept",
          np.allclose(result.points["P1"], center + (2, 1), atol=2 * tolerance)
          and np.isclose(result.radii["C1"], expected, atol=2 * tolerance), f"{result.points['P1']}")
    window.point_action.trigger()
    click(native, pixel(view, frame, (-6, -6)))
    QTest.keyClick(native, Qt.Key_Escape)
    settle()
    radius = sketch().circle("C1").radius
    click(native, pixel(view, frame, (12, 2)))              # the rim (center (4, 2), r = 8)
    shift_click(native, pixel(view, frame, (-6, -6)))
    drag(native, pixel(view, frame, (-6, -6)), pixel(view, frame, (-5, -4)))
    result = window.result.outputs["Sketch1"]
    check("a circle in a selection with a point: both move, radius kept",
          np.allclose(result.points["P1"], (5, 4), atol=2 * tolerance)
          and np.allclose(result.points["P2"], (-5, -4), atol=2 * tolerance)
          and sketch().circle("C1").radius == radius, f"{result.points}")
    window.part.feature("Sketch1").circle("C1").radius = "4 * 2"
    window._commit()
    settle()
    click(native, pixel(view, frame, (-9, -9)))
    drag(native, pixel(view, frame, (13, 4)), pixel(view, frame, (15, 4)))
    check("a formula radius stays", sketch().circle("C1").radius == "4 * 2"
          and "C1.radius" in window.statusBar().currentMessage(), window.statusBar().currentMessage())

    # --- delete
    click(native, pixel(view, frame, (13, 4)))
    QTest.keyClick(native, Qt.Key_Delete)
    settle()
    check("Delete removes the circle and its unused center", not sketch().circles
          and [p.name for p in sketch().points] == ["P2"], f"{[p.name for p in sketch().points]}")
    check("no error dialogs", not errors, f"{errors}")
    window.close()
    check("no errors in VisPy callbacks", not VISPY_ERRORS, f"{VISPY_ERRORS[:3]}")
    print("FAILED: " + ", ".join(FAILED) if FAILED else "all checks passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
