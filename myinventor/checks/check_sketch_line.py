# check_sketch_line.py
#
# Sketch geometry and the Line tool:
#   - model: points and lines, names P1.. / L1.., a TOML round trip
#     ([[feature.point]], [[feature.line]]), refused files (a line to a
#     missing point, duplicate names, a line from a point to itself, an
#     unknown key), formulas (a parameter renamed in a point's formula,
#     evaluated in the rebuild, an error naming the point), delete (a line
#     takes its unshared end points, a point takes its lines);
#   - the Line tool through real mouse events on a sketch on XY (looked at
#     from the top), in sketch coordinates checked against the pixels:
#       hover shows the marker and no line; after the first click the
#       preview runs from that point to the cursor; each further click makes
#       a line (one undo step); a click 3 px beside the first point snaps to
#       it and closes the triangle (3 points, 3 lines, closed chain); a click
#       on the last point ends a chain; a click on the sketch origin makes a
#       point at exactly (0, 0); Esc ends the chain, a second Esc the tool;
#       a middle drag meanwhile turns the camera and adds nothing; undo
#       removes the last line;
#   - selection in sketch mode: hovering a line turns that line green, a
#     click selects it (info: its length), Delete removes it; outside sketch
#     mode hovering a line highlights the whole sketch;
#   - the lines are saved and read back; the XZ plane: a click lands on
#     the plane (y = 0) where the ray meets it.
#
#   python checks/check_sketch_line.py

import os
import sys
import tempfile
import tomllib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

TMP = tempfile.mkdtemp()
os.environ["MYCAD_SETTINGS"] = os.path.join(TMP, "settings.json")     # never the user's settings

import numpy as np                                                    # noqa: E402
from vispy import app                                                 # noqa: E402

app.use_app("pyside6")
app.use_app().create()

sys.path.insert(0, os.path.join(ROOT, "checks"))
from vispy_errors import install as _watch_vispy  # noqa: E402

VISPY_ERRORS = _watch_vispy()

from PySide6.QtCore import QEvent, QPointF, Qt                      # noqa: E402
from PySide6.QtGui import QMouseEvent                                 # noqa: E402
from PySide6.QtTest import QTest                                      # noqa: E402
from PySide6.QtWidgets import QApplication                            # noqa: E402

from model.part import Part                                           # noqa: E402
from model.part_file import PartFileError, dumps, from_data, load     # noqa: E402
from model.rebuild import rebuild                                     # noqa: E402
from model.sketch import Sketch                                       # noqa: E402
from view.geometry_view import HOVER_COLOR                            # noqa: E402
from view.main_window import MainWindow                               # noqa: E402
from view.picking import _project                                     # noqa: E402

FAILED = []
MM = 1e-3


def check(label, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {label} {detail}")
    if not ok:
        FAILED.append(label)


def settle(ms=30):
    for _ in range(5):
        app.process_events()
    QTest.qWait(ms)


def send(native, kind, pos, button=Qt.NoButton, buttons=Qt.NoButton, modifier=Qt.NoModifier):
    QTest.qWait(20)                 # VisPy drops a move < 10 ms after the previous one
    local = QPointF(float(pos[0]), float(pos[1]))
    QApplication.sendEvent(native, QMouseEvent(kind, local, native.mapToGlobal(local), button, buttons, modifier))


def shield(window):
    """
    The canvas ignores the real mouse (the check's window may open under the
    user's cursor, and real moves would mix with the synthetic ones); the
    events a check sends straight to it with sendEvent still arrive.
    """
    window.view.canvas.native.setAttribute(Qt.WA_TransparentForMouseEvents)


def hover(native, pos):
    send(native, QEvent.MouseMove, pos)


def click(native, pos):
    hover(native, pos)
    send(native, QEvent.MouseButtonPress, pos, Qt.LeftButton, Qt.LeftButton)
    send(native, QEvent.MouseButtonRelease, pos, Qt.LeftButton, Qt.NoButton)
    settle()


def check_model():
    part = Part()
    part.add_parameter("a", "10")
    sketch = part.add_feature(Sketch("Sketch1", plane="XY Plane"))
    p1, p2, p3 = sketch.add_point("0", "0"), sketch.add_point("a", "0"), sketch.add_point("a", "a / 2")
    l1, l2, l3 = sketch.add_line(p1, p2), sketch.add_line(p2, p3), sketch.add_line(p3, p1)
    check("names P1..3, L1..3", [p1, p2, p3, l1, l2, l3] == ["P1", "P2", "P3", "L1", "L2", "L3"])
    text = dumps(part)
    back = from_data(tomllib.loads(text))
    check("TOML round trip", back.to_dict() == part.to_dict() and "[[feature.point]]" in text
          and "[[feature.line]]" in text)
    result = rebuild(part).outputs["Sketch1"]
    check("rebuild evaluates the formulas", np.allclose(result.points["P3"], [10, 5]))
    check("segments in the scene (XY: x, y)", np.allclose(result.segments_mm()[1], [[10, 0, 0], [10, 5, 0]]))
    part.rename_parameter("a", "w")
    check("a renamed parameter reaches point formulas", sketch.point("P3").y == "w / 2")
    sketch.point("P3").x = "nope + 1"
    errors = rebuild(part).feature_errors
    check("a bad point formula fails the sketch, naming the point", "point P3" in errors.get("Sketch1", ""),
          f"{errors}")
    sketch.point("P3").x = "w"

    head = 'format = "mycad-part"\nversion = 1\n[[feature]]\nname = "S"\ntype = "sketch"\nplane = "XY Plane"\n'
    pts = '[[feature.point]]\nname = "P1"\nx = "0"\ny = 0\n[[feature.point]]\nname = "P2"\nx = "1"\ny = "0"\n'
    broken = {
        "a line to a missing point": pts + '[[feature.line]]\nname = "L1"\nstart = "P1"\nend = "P9"\n',
        "duplicate names": pts + '[[feature.line]]\nname = "P1"\nstart = "P1"\nend = "P2"\n',
        "a line from a point to itself": pts + '[[feature.line]]\nname = "L1"\nstart = "P1"\nend = "P1"\n',
        "an unknown key": pts.replace('y = 0', 'y = 0\nz = 1'),
    }
    for label, body in broken.items():
        try:
            from_data(tomllib.loads(head + body))
            check(f"refused: {label}", False)
        except PartFileError as error:
            check(f"refused: {label}", "S" in str(error), str(error))
    ok = from_data(tomllib.loads(head + pts))
    check("a number as a point formula is read as text", ok.features[0].point("P1").y == "0")

    sketch.delete("L1")
    check("deleting L1 keeps P1, P2 (used by L3, L2)", [p.name for p in sketch.points] == ["P1", "P2", "P3"]
          and [line.name for line in sketch.lines] == ["L2", "L3"])
    sketch.delete("L2")
    check("deleting L2 drops P2 (no other line)", [p.name for p in sketch.points] == ["P1", "P3"])
    sketch.delete("P1")
    check("deleting P1 drops its line L3", not sketch.lines and [p.name for p in sketch.points] == ["P3"])


def pixel(view, frame, xy):
    world = frame.to_world(np.array([xy], float) * MM) / MM
    return _project(view._transform(), world)[0][0]


def check_tool():
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
    sketch = lambda: window.part.feature("Sketch1")                     # noqa: E731  (undo replaces the part)
    mm_per_px = view.view.camera.scale_factor / min(view.view.camera._viewbox.size)
    tolerance = mm_per_px                                               # a click lands within a pixel

    window.line_action.trigger()
    check("Line on: a tool, Sketch tab", view.tool is not None and window.line_action.isChecked())
    a, b, c = (3.0, 2.0), (9.0, 2.0), (6.0, 7.0)
    hover(native, pixel(view, frame, a))
    settle()
    tool = view.tool
    check("hover: the marker, no preview line", tool.marker.visible and not tool.line.visible
          and np.allclose(tool.preview[1], a, atol=tolerance), f"{tool.preview}")
    click(native, pixel(view, frame, a))
    check("first click: nothing made yet", not sketch().points)
    hover(native, pixel(view, frame, b))
    settle()
    start, end, _ = tool.preview
    check("the preview runs from the first point to the cursor", tool.line.visible
          and np.allclose(start, a, atol=tolerance) and np.allclose(end, b, atol=tolerance), f"{tool.preview}")
    undo_steps = len(window.doc._undo)
    click(native, pixel(view, frame, b))
    points = {p.name: (float(p.x), float(p.y)) for p in sketch().points}
    check("second click: P1, P2 and L1", list(points) == ["P1", "P2"] and len(sketch().lines) == 1
          and np.allclose(points["P1"], a, atol=tolerance) and np.allclose(points["P2"], b, atol=tolerance),
          f"{points}")
    check("  one undo step", len(window.doc._undo) == undo_steps + 1)
    check("  new points are rounded to 1 um", all(len(t.split(".")[-1]) <= 3 for p in sketch().points
                                                   for t in (p.x, p.y)), f"{[(p.x, p.y) for p in sketch().points]}")
    click(native, pixel(view, frame, c))
    near_first = pixel(view, frame, a) + 3
    click(native, near_first)
    lines = [(line.start, line.end) for line in sketch().lines]
    check("a click 3 px beside the first point closes the triangle", lines == [("P1", "P2"), ("P2", "P3"),
                                                                             ("P3", "P1")]
          and len(sketch().points) == 3, f"{lines}")
    check("  and ends the chain (the tool stays)", tool.last is None and view.tool is tool)

    # a chain ended by a click on its last point; a point on the sketch origin
    click(native, pixel(view, frame, (0.0, 0.0)))
    click(native, pixel(view, frame, (-5.0, 0.0)))
    click(native, pixel(view, frame, (-5.0, 0.0)))
    origin_points = [p for p in sketch().points if (p.x, p.y) == ("0", "0")]
    check("a click on the sketch origin: a point at exactly (0, 0)", len(origin_points) == 1)
    check("a click on the last point ends the chain", tool.last is None and len(sketch().lines) == 4)

    # Esc: the chain, then the tool; a middle drag adds nothing
    click(native, pixel(view, frame, (-5.0, -5.0)))
    before = (len(sketch().points), len(sketch().lines))
    rotation = view.view.camera.state()["rotation"]
    p = pixel(view, frame, (0.0, -6.0))
    send(native, QEvent.MouseButtonPress, p, Qt.MiddleButton, Qt.MiddleButton)
    for i in range(1, 6):
        send(native, QEvent.MouseMove, p + (10 * i, 0), Qt.NoButton, Qt.MiddleButton)
    send(native, QEvent.MouseButtonRelease, p + (50, 0), Qt.MiddleButton, Qt.NoButton)
    settle()
    check("a middle drag turns the camera, adds nothing",
          not np.allclose(rotation, view.view.camera.state()["rotation"])
          and (len(sketch().points), len(sketch().lines)) == before)
    window._look_at_sketch()
    settle()
    QTest.keyClick(native, Qt.Key_Escape)
    settle()
    check("Esc ends the chain", tool.last is None and view.tool is tool and window.line_action.isChecked())
    QTest.keyClick(native, Qt.Key_Escape)
    settle()
    check("Esc again leaves the tool", view.tool is None and not window.line_action.isChecked())
    QTest.keyClick(native, Qt.Key_Escape)                 # nothing left to cancel
    settle()
    check("Esc never closes the 3D view", native.isVisible() and window.centralWidget() is native)

    window.undo()
    check("undo removes the last line", len(sketch().lines) == 3, f"{len(sketch().lines)}")
    window.redo()

    # --- selection in sketch mode
    mid = pixel(view, frame, (6.0, 2.0))                                # on L1
    hover(native, mid)
    settle()
    visual = view._sketch_geometry["Sketch1"]
    key = ("entity", "Sketch1", "L1")
    check("hover on a line: that line green", view.hovered == key and visual.shown_colors[key] == HOVER_COLOR
          and visual.shown_colors[("entity", "Sketch1", "L2")] != HOVER_COLOR, f"{view.hovered}")
    click(native, mid)
    shown = window.info.text().rsplit("length ", 1)[-1].split(" mm")[0]
    check("a click selects it, the info shows its length", view.selected == key
          and abs(float(shown) - 6.0) < 2 * tolerance, window.info.text())
    QTest.keyClick(native, Qt.Key_Delete)
    settle()
    check("Delete removes L1 (its points stay: used by L2, L3)", "L1" not in [ln.name for ln in sketch().lines]
          and len(sketch().points) == 5 and view.selected is None)

    window.finish_sketch()
    view.look_from("Top")
    settle()
    on_l2 = pixel(view, frame, (7.5, 4.5))
    hover(native, on_l2)
    settle()
    check("outside sketch mode a line highlights the whole sketch", view.hovered == ("feature", "Sketch1")
          and all(c == HOVER_COLOR for c in view._sketch_geometry["Sketch1"].shown_colors.values()),
          f"{view.hovered}")

    # --- file, XZ
    path = os.path.join(TMP, "lines.part.toml")
    window.ask_save_path = lambda: path
    window.save()
    check("saved and read back", load(path).to_dict() == window.part.to_dict())

    window.new_sketch("XZ Plane")
    settle()
    frame = view.sketch_frame
    window.line_action.trigger()
    click(native, pixel(view, frame, (2.0, 3.0)))
    click(native, pixel(view, frame, (8.0, 3.0)))
    s2 = window.part.feature("Sketch2")
    world = window.result.outputs["Sketch2"].segments_mm()[0]
    check("XZ: the points land on the plane (y = 0, z = -sketch y)",
          np.allclose(world[:, 1], 0) and np.allclose(world[:, 2], -3.0, atol=tolerance)
          and np.allclose(world[:, 0], [2, 8], atol=tolerance), f"{world.tolist()} {[(p.x, p.y) for p in s2.points]}")
    check("no error dialogs", not errors, f"{errors}")
    window.close()


def main():
    check_model()
    check_tool()
    check("no errors in VisPy callbacks", not VISPY_ERRORS, f"{VISPY_ERRORS[:3]}")
    print("FAILED: " + ", ".join(FAILED) if FAILED else "all checks passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
