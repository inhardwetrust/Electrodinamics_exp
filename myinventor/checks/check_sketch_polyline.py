# check_sketch_polyline.py
#
# Wide polylines (a center line with a width at every point):
#   - the outline (model/traces.py): straight, flat ends: L x w; round ends:
#     + pi w^2 / 4 (exact in the solid); tapering 1 -> 3: the trapezoid; a
#     right-angle turn, mitered: (L1 + L2) w;
#   - model: a TOML round trip; refused files (a missing point, widths not
#     one per point, a bad end); a new point gets the polyline's width text,
#     and a later change of that width leaves the points' widths alone;
#     deleting a middle point joins the pieces; deleting a center segment
#     splits it (each piece keeps its own ends, the cut ends flat; a piece of
#     one point goes, with its point); deleting the polyline takes its
#     points; a width <= 0 fails the sketch naming the polyline; a width
#     formula follows a parameter;
#   - extruded: exact volumes, round ends included;
#   - the window (real mouse events, a sketch on XY from the top): the
#     Polyline tool with the Width field at 2 draws W1 through three clicks;
#     its outline is solid, its center line dashed; hovering the outline
#     takes the polyline, the center line a segment; a point's width set in
#     the properties widens the outline there; the end set round; a dragged
#     point carries the outline; the middle point's bend: round (half the
#     width: the outer corner rounded off), a bigger radius cuts less,
#     chamfer cuts it straight; a double click on the center line puts a
#     point there (on the line, its width the line's: 3 between 2 and 4);
#     Delete on the middle point joins it, on a
#     center segment splits it; the Construction toggle leaves polylines
#     alone; Extrude takes the polyline's outline.
#
#   python checks/check_sketch_polyline.py

import math
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

from PySide6.QtCore import QEvent, QPoint, Qt                       # noqa: E402
from PySide6.QtTest import QTest                                      # noqa: E402
from PySide6.QtWidgets import QComboBox, QFormLayout, QLineEdit      # noqa: E402

from check_sketch_drag import drag, send                              # noqa: E402
from check_sketch_line import click, hover, pixel, settle, shield    # noqa: E402
from geometry import kernel                                           # noqa: E402
from model.extrude import Extrude                                     # noqa: E402
from model.part import Part                                           # noqa: E402
from model.part_file import PartFileError, dumps, from_data           # noqa: E402
from model.profiles import _area                                      # noqa: E402
from model.rebuild import rebuild                                     # noqa: E402
from model.sketch import Sketch                                       # noqa: E402
from model.traces import outline                                      # noqa: E402
from view.main_window import MainWindow                               # noqa: E402

FAILED = []


def check(label, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {label} {detail}")
    if not ok:
        FAILED.append(label)


def props(window):
    """{label: field widget} of the properties under the info."""
    form, out = window.props_form, {}
    for i in range(form.rowCount()):
        label, field = form.itemAt(i, QFormLayout.LabelRole), form.itemAt(i, QFormLayout.FieldRole)
        if label is not None and field is not None:
            out[label.widget().text()] = field.widget()
    return out


def area(points, widths, start="flat", end="flat"):
    return _area(outline(points, widths, start, end)[1])


def check_outline():
    check("straight, flat: 10 x 1", math.isclose(area([[0, 0], [10, 0]], [1, 1]), 10.0, rel_tol=1e-12))
    v = area([[0, 0], [10, 0]], [1, 1], "round", "round")
    check("round ends: + pi / 4 (the polygon samples the arcs)", math.isclose(v, 10 + math.pi / 4, rel_tol=1e-3),
          f"{v}")
    check("tapering 1 -> 3: 20", math.isclose(area([[0, 0], [10, 0]], [1, 3]), 20.0, rel_tol=1e-12))
    check("a right-angle turn, mitered: (10 + 5) x 1",
          math.isclose(area([[0, 0], [10, 0], [10, 5]], [1, 1, 1]), 15.0, rel_tol=1e-12))
    turn, widths = [[0, 0], [10, 0], [10, 5]], [2, 2, 2]

    def bent(kind, radius=None, points=turn):
        return _area(outline(points, widths, bends=["normal", kind, "normal"], radii=[None, radius, None])[1])
    check("bend round, radius = half the width: the corner's square becomes a quarter disc (30 - 1 + pi / 4)",
          math.isclose(bent("round"), 29 + math.pi / 4, rel_tol=1e-3), f"{bent('round')}")
    check("bend chamfer, the same radius: half the square off (29.5)", math.isclose(bent("chamfer"), 29.5,
                                                                                    rel_tol=1e-12))
    cut = 1 - math.sqrt(1.2 ** 2 - 1)
    check("chamfer, radius 1.2: a smaller triangle off", math.isclose(bent("chamfer", 1.2), 30 - cut * cut / 2,
                                                                      rel_tol=1e-12))
    check("a radius past the corner: sharp (30); below half the width: as half the width",
          math.isclose(bent("round", 2.0), 30.0, rel_tol=1e-12) and math.isclose(bent("round", 0.5), bent("round")))
    check("the other way round (a right turn): the same", math.isclose(
        bent("round", None, [[0, 0], [10, 0], [10, -5]]), bent("round"), rel_tol=1e-12))


def sketch_part():
    part = Part()
    part.add_parameter("w", "1")
    s = part.add_feature(Sketch("S", plane="XY Plane"))
    p = [s.add_point(x, y) for x, y in (("0", "0"), ("10", "0"), ("10", "5"), ("20", "5"))]
    name = s.add_polyline(p[0], p[1], "w")
    s.extend_polyline(name, p[2])
    s.extend_polyline(name, p[3])
    return part, s


def check_model():
    part, s = sketch_part()
    w = s.polyline("W1")
    check("a new point gets the polyline's width text", w.widths == ["w", "w", "w", "w"])
    w.width = "3"
    check("a later change of that width leaves the points' widths alone", w.widths == ["w", "w", "w", "w"])
    w.end = "round"
    text = dumps(part)
    check("TOML round trip", from_data(tomllib.loads(text)).to_dict() == part.to_dict()
          and "[[feature.polyline]]" in text)
    head = 'format = "mycad-part"\nversion = 1\n[[feature]]\nname = "S"\ntype = "sketch"\nplane = "XY Plane"\n'
    pts = '[[feature.point]]\nname = "P1"\nx = "0"\ny = "0"\n[[feature.point]]\nname = "P2"\nx = "1"\ny = "0"\n'
    for label, body in (("a missing point", 'points = ["P1", "P9"]\nwidths = ["1", "1"]\n'),
                        ("widths not one per point", 'points = ["P1", "P2"]\nwidths = ["1"]\n'),
                        ("a bad end", 'points = ["P1", "P2"]\nwidths = ["1", "1"]\nend = "pointy"\n')):
        try:
            from_data(tomllib.loads(head + pts + '[[feature.polyline]]\nname = "W1"\n' + body))
            check(f"refused: {label}", False)
        except PartFileError as error:
            check(f"refused: {label}", "W1" in str(error), str(error))

    part.parameter("w").formula = "2"
    out = rebuild(part).outputs["S"]
    v = _area(out.outlines["W1"][1])
    check("a width formula follows a parameter (w = 2: 25 x 2 + the round end's half circle, pi / 2)",
          math.isclose(v, 50 + math.pi / 2, rel_tol=1e-3), f"{v}")
    s.polyline("W1").widths[1] = "-1"
    errors = rebuild(part).feature_errors
    check("a width <= 0 fails the sketch, naming the polyline", "polyline W1" in errors.get("S", ""), f"{errors}")
    s.polyline("W1").widths[1] = "w"

    part, s = sketch_part()
    s.delete("P2")
    check("deleting a middle point joins the pieces", s.polyline("W1").points == ["P1", "P3", "P4"]
          and len(s.polyline("W1").widths) == 3)
    part, s = sketch_part()
    s.polyline("W1").start, s.polyline("W1").end = "round", "round"
    s.delete("W1.2")
    a, b = s.polyline("W1"), s.polyline("W2")
    check("deleting a center segment splits it (each keeps its own ends, the cut ends flat)",
          a.points == ["P1", "P2"] and b.points == ["P3", "P4"] and (a.start, a.end) == ("round", "flat")
          and (b.start, b.end) == ("flat", "round"), f"{a} {b}")
    part, s = sketch_part()
    s.delete("W1.1")
    check("a piece of one point goes, with its point", [w.points for w in s.polylines] == [["P2", "P3", "P4"]]
          and "P1" not in [p.name for p in s.points], f"{[w.points for w in s.polylines]}")
    part, s = sketch_part()
    s.delete("W1")
    check("deleting the polyline takes its points", not s.polylines and not s.points)

    part, s = sketch_part()
    w = s.polyline("W1")
    check("bends and radii are not written while all are normal", "bends" not in dumps(part))
    w.bends[1], w.radii[1] = "round", "2"
    text = dumps(part)
    check("set: a TOML round trip with bends and radii", from_data(tomllib.loads(text)).to_dict() == part.to_dict()
          and 'bends = ["normal", "round", "normal", "normal"]' in text)
    s.insert_into_polyline("W1", 2, s.add_point("10", "2"), "w")
    check("a point put in: the lists stay aligned", w.points[2] == "P5" and len(w.widths) == len(w.bends)
          == len(w.radii) == 5 and w.bends[1] == "round" and w.bends[2] == "normal")
    s.delete("P2")
    check("a point deleted: its bend and radius go with it", w.bends == ["normal"] * 4 and w.radii == [""] * 4)

    for xy in (("5", "0.001"), ("1", "0.5")):     # nearly straight, nearly reversed (regression)
        part = Part()
        t = part.add_feature(Sketch("S", plane="XY Plane"))
        q = [t.add_point(*a) for a in (("0", "0"), ("10", "0"), xy)]
        t.extend_polyline(t.add_polyline(q[0], q[1], "1"), q[2])
        t.polyline("W1").bends[1] = "round"
        r = rebuild(part)
        check(f"a round bend at radius = half width builds ({xy})", "S" not in r.feature_errors,
              f"{r.feature_errors}")
    part, s = sketch_part()
    s.polyline("W1").widths[1] = "-1"
    r = rebuild(part)
    check("a polyline that fails keeps its sketch shown (failed, but with an output)",
          "S" in r.feature_errors and "S" in r.outputs and "W1" not in r.outputs["S"].outlines,
          f"{r.feature_errors}")

    part, s = sketch_part()
    s.add_circle("P3", "2")
    out = rebuild(part).outputs["S"]
    check("a circle beside a polyline: its radius survives the polyline's bend radii (regression)",
          isinstance(out.radii, dict) and out.radii.get("C1") == 2.0
          and len(out.circle_segments_mm("C1", 16)) > 0, f"{out.radii}")

    part, s = sketch_part()
    s.polyline("W1").bends[1:3] = ["round", "round"]
    part.add_feature(Extrude("E", profiles=[("W1",)], sketch="S", distance="1", body="Bent"))
    v = kernel.volume(rebuild(part).shapes["Bent"])
    check("extruded with round bends: exact (25 - 2 (1/4 - pi/16))", math.isclose(
        v, 25 - 2 * (0.25 - math.pi / 16), rel_tol=1e-9), f"{v}")

    part, s = sketch_part()
    s.polyline("W1").start = s.polyline("W1").end = "round"
    part.add_feature(Extrude("E", profiles=[("W1",)], sketch="S", distance="2", body="Trace"))
    result = rebuild(part)
    v = kernel.volume(result.shapes["Trace"])
    check("extruded: (10 + 5 + 10) x 1 + pi / 4, by 2, exactly (mitered turns, round ends)",
          math.isclose(v, (25 + math.pi / 4) * 2, rel_tol=1e-9), f"{v} {result.feature_errors}")


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
    px = view._mm_per_px()
    sketch = lambda: window.part.feature("Sketch1")         # noqa: E731

    window.trace_width.setText("2")
    window.trace_action.trigger()
    check("the Polyline tool", view.tool is not None and view.tool.name == "Polyline")
    for xy in ((0, 0), (12, 0), (12, 6)):
        click(native, pixel(view, frame, xy))
    QTest.keyClick(native, Qt.Key_Escape)
    QTest.keyClick(native, Qt.Key_Escape)
    settle()
    w = sketch().polylines[0] if sketch().polylines else None
    check("three clicks: W1 with three points, widths from the field (2)", w is not None and w.name == "W1"
          and len(w.points) == 3 and w.widths == ["2", "2", "2"] and not sketch().lines, f"{w}")
    visual = view._sketch_geometry["Sketch1"]
    check("its outline solid, its center line dashed", "W1" not in visual.construction
          and {"W1.1", "W1.2"} <= set(visual.dash_segments) and visual.lines.visible)
    hover(native, pixel(view, frame, (6, 1)))                # on the outline (1 mm off the center line)
    settle()
    check("hovering the outline takes the polyline", view.hovered == ("entity", "Sketch1", "W1"), f"{view.hovered}")
    hover(native, pixel(view, frame, (6, 0.0)))
    settle()
    check("hovering the center line takes a segment", view.hovered == ("entity", "Sketch1", "W1.1"),
          f"{view.hovered}")

    out = window.result.outputs["Sketch1"]
    before = _area(out.outlines["W1"][1])
    click(native, pixel(view, frame, (12, 0)))               # the middle point
    middle = view.selected[2] if view.selected else None
    rows = props(window)
    check("an inner point selected: its width, bend and radius on W1 in the properties", middle is not None
          and rows.get("Width on W1:") is not None and rows["Width on W1:"].text() == "2"
          and rows.get("Bend on W1:") is not None and rows["Bend on W1:"].currentText() == "normal"
          and rows.get("Radius on W1:") is not None and rows["Radius on W1:"].text() == "", f"{middle} {list(rows)}")
    steps = len(window.doc._undo)
    rows["Width on W1:"].setText("4")
    rows["Width on W1:"].editingFinished.emit()
    settle()
    after = _area(window.result.outputs["Sketch1"].outlines["W1"][1])
    check("set to 4: the outline widens there, one undo step", sketch().polyline("W1").widths[1] == "4"
          and after > before + 1 and len(window.doc._undo) == steps + 1, f"{before:.2f} -> {after:.2f}")

    # --- the bend at the middle point (a left turn: its outer corner on the right, at (14, -2))
    click(native, pixel(view, frame, (12, 0)))
    props(window)["Bend on W1:"].setCurrentText("round")
    settle()
    rounded = _area(window.result.outputs["Sketch1"].outlines["W1"][1])
    check("bend round (radius: half the width): the outer corner rounded off",
          sketch().polyline("W1").bends[1] == "round" and rounded < after - 0.5, f"{after:.3f} -> {rounded:.3f}")
    click(native, pixel(view, frame, (12, 0)))
    radius = props(window)["Radius on W1:"]
    radius.setText("2.5")
    radius.editingFinished.emit()
    settle()
    bigger = _area(window.result.outputs["Sketch1"].outlines["W1"][1])
    check("  a bigger radius cuts less", sketch().polyline("W1").radii[1] == "2.5" and rounded < bigger < after,
          f"{rounded:.3f} < {bigger:.3f} < {after:.3f}")
    click(native, pixel(view, frame, (12, 0)))
    props(window)["Bend on W1:"].setCurrentText("chamfer")
    settle()
    edges = window.result.outputs["Sketch1"].outlines["W1"][0]
    check("  chamfer: the cut is a straight edge (no arc but the ends')", sketch().polyline("W1").bends[1] == "chamfer"
          and all(e[0] == "line" for e in edges), f"{[e[0] for e in edges]}")
    click(native, pixel(view, frame, (12, 0)))
    props(window)["Bend on W1:"].setCurrentText("normal")
    radius = props(window)["Radius on W1:"]
    radius.setText("")
    radius.editingFinished.emit()
    settle()

    # --- a double click on the center line puts a point there
    count = len(sketch().polyline("W1").points)
    at = (6.0, 0.0)
    send(native, QEvent.MouseMove, pixel(view, frame, at))
    QTest.mouseDClick(native, Qt.LeftButton, Qt.NoModifier, QPoint(*map(int, np.round(pixel(view, frame, at)))))
    settle()
    w = sketch().polyline("W1")
    new = w.points[1] if len(w.points) == count + 1 else None
    out = window.result.outputs["Sketch1"]
    check("a double click on the center line puts a point there, on the line",
          new is not None and np.isclose(out.points[new][1], 0.0, atol=1e-9)
          and np.isclose(out.points[new][0], 6.0, atol=2 * px), f"{w.points}")
    check("  its width: the line's between its neighbours (2 and 4 -> 3)",
          new is not None and np.isclose(float(w.widths[1]), 3.0, atol=0.02) and view.selected[2] == new,
          f"{w.widths}")
    window.undo()

    click(native, pixel(view, frame, (6, 1.5)))              # the outline (now wider near the middle)
    widgets = [window.props_form.itemAt(i, QFormLayout.FieldRole).widget() for i in range(window.props_form.rowCount())
               if window.props_form.itemAt(i, QFormLayout.FieldRole) is not None]
    fields = [x for x in widgets if isinstance(x, QLineEdit)]
    combos = [x for x in widgets if isinstance(x, QComboBox)]
    check("the polyline selected: its width and its ends in the properties",
          view.selected == ("entity", "Sketch1", "W1") and len(fields) == 1 and fields[0].text() == "2"
          and [c.currentText() for c in combos] == ["flat", "flat"], f"{view.selected}")
    combos[-1].setCurrentText("round")
    settle()
    check("  the end set round", sketch().polyline("W1").end == "round")

    end_point = sketch().polyline("W1").points[-1]
    drag(native, pixel(view, frame, (12, 6)), pixel(view, frame, (16, 6)))
    out = window.result.outputs["Sketch1"]
    check("a dragged point carries the outline", np.isclose(out.points[end_point][0], 16, atol=2 * px)
          and out.outlines["W1"][1][:, 0].max() > 16.5, f"{out.points[end_point]}")

    window.construction_action.trigger()
    check("the Construction toggle leaves polylines alone (it sets the mode)", window.construction_mode
          and not hasattr(sketch().polyline("W1"), "construction"))
    window.construction_action.trigger()

    click(native, pixel(view, frame, (12, 0)))
    QTest.keyClick(native, Qt.Key_Delete)
    settle()
    check("Delete on the middle point joins it", len(sketch().polyline("W1").points) == 2)
    window.undo()
    out = window.result.outputs["Sketch1"]
    p2, p3 = sketch().polyline("W1").points[1:3]
    click(native, pixel(view, frame, 0.5 * (out.points[p2] + out.points[p3])))     # the second center segment
    check("  the second center segment selected", view.selected == ("entity", "Sketch1", "W1.2"), f"{view.selected}")
    QTest.keyClick(native, Qt.Key_Delete)
    settle()
    check("Delete on a center segment splits it into two polylines... of which one point goes",
          [w.points[:1] for w in sketch().polylines] == [["P1"]] and len(sketch().polylines) == 1
          and len(sketch().polylines[0].points) == 2, f"{[w.points for w in sketch().polylines]}")
    window.undo()

    window.finish_sketch()
    window.extrude_action.trigger()
    settle()
    check("Extrude takes the polyline's outline (the only profile)",
          window.view.profile_selection == [("profile", "Sketch1", ("W1",))], f"{window.view.profile_selection}")
    window.extrude_panel.distance.setText("1")
    window.extrude_panel.apply_button.click()
    settle()
    check("  a body", [b.name for b in window.result.bodies] == ["Solid1"] and not window.result.feature_errors,
          f"{window.result.feature_errors}")
    check("no error dialogs", not errors, f"{errors}")
    window.close()


def main():
    check_outline()
    check_model()
    check_window()
    check("no errors in VisPy callbacks", not VISPY_ERRORS, f"{VISPY_ERRORS[:3]}")
    print("FAILED: " + ", ".join(FAILED) if FAILED else "all checks passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
