# check_panel_formulas.py
#
# The numbers of the command panels are formula fields (view/formula_field.py),
# as a sketch entity's properties are:
#   - Extrude (Distance), Revolve (Angle), Move (Offset X / Y / Z, Angle),
#     Work Plane (Offset, Angle), the Sketch ribbon's polyline Width;
#   - each shows its value grey at its right (with mm / °), a formula that
#     does not evaluate gets a red frame and Apply goes off;
#   - parameter names are completed, fields too ("c." offers c.x, c.y); the
#     Enter that takes a completion does not apply the panel;
#   - Apply writes the formula itself into the feature (not its value), and
#     the geometry follows a parameter change afterwards;
#   - the Width follows the parameters after a rebuild; a Width that does not
#     evaluate gives a new polyline 1 mm, said so.
#
#   python checks/check_panel_formulas.py

import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

TMP = tempfile.mkdtemp()
os.environ["MYCAD_SETTINGS"] = os.path.join(TMP, "settings.json")     # never the user's settings

from vispy import app                                                 # noqa: E402

app.use_app("pyside6")
app.use_app().create()

sys.path.insert(0, os.path.join(ROOT, "checks"))
from vispy_errors import install as _watch_vispy  # noqa: E402

VISPY_ERRORS = _watch_vispy()

from PySide6.QtCore import Qt                                        # noqa: E402
from PySide6.QtTest import QTest                                      # noqa: E402

from geometry import kernel                                           # noqa: E402
from view.extrude_panel import ExtrudePanel                          # noqa: E402
from view.formula_field import FormulaField                           # noqa: E402
from view.main_window import MainWindow                               # noqa: E402

FAILED = []


def check(label, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {label} {detail}")
    if not ok:
        FAILED.append(label)


def settle(ms=30):
    for _ in range(5):
        app.process_events()
    QTest.qWait(ms)


PART = '''format = "mycad-part"
version = 1

[[parameter]]
name = "h"
formula = "1.6"

[[parameter]]
name = "c"
[parameter.fields]
x = "4"
y = "2"

[[feature]]
name = "Sketch1"
type = "sketch"
plane = "XY Plane"
[[feature.point]]
name = "P1"
x = "0"
y = "0"
[[feature.point]]
name = "P2"
x = "10"
y = "0"
[[feature.point]]
name = "P3"
x = "10"
y = "10"
[[feature.point]]
name = "P4"
x = "0"
y = "10"
[[feature.line]]
name = "L1"
start = "P1"
end = "P2"
[[feature.line]]
name = "L2"
start = "P2"
end = "P3"
[[feature.line]]
name = "L3"
start = "P3"
end = "P4"
[[feature.line]]
name = "L4"
start = "P4"
end = "P1"

[[feature]]
name = "Board"
type = "extrude"
sketch = "Sketch1"
profiles = [["L1", "L2", "L3", "L4"]]
distance = "h"
operation = "new"
body = "Board"

[[feature]]
name = "WorkPlane1"
type = "workplane"
base = "XY Plane"
offset = "c.x"

[[transform]]
name = "Move1"
type = "move"
targets = ["Board"]
offset = ["c.x", "0", "0"]
'''


def completes(field, typed, expected, label):
    """Typing `typed` pops up `expected`; Enter on the popup takes the first, the panel stays open."""
    field.setFocus()
    field.setText("")
    QTest.keyClicks(field, typed)
    settle()
    completer = field.completer_
    shown = [completer.completionModel().index(i, 0).data() for i in range(completer.completionCount())]
    check(f"{label}: '{typed}' offers {expected}", completer.popup().isVisible() and shown == expected, f"{shown}")
    QTest.keyClick(completer.popup(), Qt.Key_Return)
    settle()
    check(f"  Enter takes {expected[0]}", field.text() == expected[0] and not completer.popup().isVisible(),
          field.text())


def formula_field(field, label, value, unit):
    check(f"{label}: a formula field", isinstance(field, FormulaField))
    check(f"  its value shown: {value} {unit}", field.shown_value() == f"= {value} {unit}", field.shown_value())


def check_extrude(window):
    window.start_extrude(edit="Board")
    settle()
    panel = window.extrude_panel
    field = panel.distance
    formula_field(field, "Extrude Distance", "1.6", "mm")
    completes(field, "c.", ["c.x", "c.y"], "Extrude Distance")
    check("  the panel is still open (that Enter did not apply)", window.extrude_panel is panel)
    field.setText("c.x + nope")
    settle()
    check("  a bad formula: red frame, Apply off, the error below",
          "c81e1e" in field.styleSheet() and not panel.apply_button.isEnabled() and "nope" in panel.value.text())
    field.setText("c.x / 2")
    settle()
    check("  a good one: Apply on, no error line", panel.apply_button.isEnabled() and not panel.value.isVisibleTo(panel))
    panel.apply_button.click()
    settle()
    check("Apply: the formula itself in the feature", window.part.feature("Board")["distance"] == "c.x / 2")
    window.set_parameters_quiet({"c.x": "6"})
    settle()
    lo, hi = kernel.bounds(window.result.shapes["Board"])
    check("  the board follows c.x (3 thick, moved 6)", abs(hi[2] - lo[2] - 3) < 1e-9 and abs(lo[0] - 6) < 1e-9,
          f"{lo} {hi}")


def check_work_plane(window):
    window.start_work_plane(edit="WorkPlane1")
    settle()
    panel = window.workplane_panel
    formula_field(panel.offset, "Work Plane Offset", "6", "mm")
    check("Work Plane Angle: a formula field, degrees", isinstance(panel.angle, FormulaField) and panel.angle.unit == "°")
    completes(panel.offset, "c.", ["c.x", "c.y"], "Work Plane Offset")
    panel.offset.setText("h +")
    settle()
    check("  a bad formula: red frame, Apply off", "c81e1e" in panel.offset.styleSheet()
          and not panel.apply_button.isEnabled())
    panel.offset.setText("c.y * 3")
    settle()
    panel.apply_button.click()
    settle()
    check("Apply: offset = 'c.y * 3'", window.part.feature("WorkPlane1")["offset"] == "c.y * 3")


def check_move(window):
    window.start_move(edit="Move1")
    settle()
    panel = window.move_panel
    formula_field(panel.offset[0], "Move Offset X", "6", "mm")
    check("Move Offset Y / Z and Angle: formula fields",
          all(isinstance(f, FormulaField) for f in panel.offset[1:] + [panel.angle]) and panel.angle.unit == "°")
    panel.offset[2].setFocus()
    panel.offset[2].setText("")
    QTest.keyClicks(panel.offset[2], "c")
    settle()
    completer = panel.offset[2].completer_
    shown = [completer.completionModel().index(i, 0).data() for i in range(completer.completionCount())]
    check("Move Offset Z: 'c' offers c.x, c.y", completer.popup().isVisible() and shown == ["c.x", "c.y"], f"{shown}")
    completer.popup().hide()
    panel.offset[2].setText("h * 2")
    settle()
    check("  its value: = 3.2 mm", panel.offset[2].shown_value() == "= 3.2 mm", panel.offset[2].shown_value())
    panel.offset[1].setText("1/0")
    settle()
    check("  division by zero: red frame, Apply off", "c81e1e" in panel.offset[1].styleSheet()
          and not panel.apply_button.isEnabled())
    panel.offset[1].setText("-c.y")
    settle()
    panel.apply_button.click()
    settle()
    check("Apply: offset = ['c.x', '-c.y', 'h * 2']",
          window.part.transform("Move1")["offset"] == ["c.x", "-c.y", "h * 2"], f"{window.part.transform('Move1')['offset']}")
    lo, _ = kernel.bounds(window.result.shapes["Board"])
    check("  the board placed there (6, -2, 3.2)", all(abs(a - b) < 1e-9 for a, b in zip(lo, (6, -2, 3.2))), f"{lo}")


def check_revolve_angle():
    panel = ExtrudePanel(None, {"a": 90.0}, distance="a * 2", kind="revolve", title="Revolve")
    formula_field(panel.distance, "Revolve Angle", "180", "°")
    panel.distance.setText("a * 5")
    check("  450°: refused (Apply off, said why)", not panel.apply_button.isEnabled() and panel.value.text() != "")
    panel.close()


def check_width(window):
    field = window.trace_width
    check("Sketch Width: a formula field", isinstance(field, FormulaField))
    field.setText("h / 2")
    check("  its value from the parameters: = 0.8 mm", field.shown_value() == "= 0.8 mm", field.shown_value())
    window.set_parameters_quiet({"h": "3"})
    settle()
    check("  follows a rebuild: = 1.5 mm", field.shown_value() == "= 1.5 mm", field.shown_value())
    check("  completes fields", "c.x" in field.completer_.model().stringList())

    window.new_sketch("XY Plane")
    settle()
    sketch_name = window.editing
    field.setText("nope")
    window._set_trace_tool(True)
    window._add_trace(("free", (20.0, 0.0)), ("free", (30.0, 0.0)))
    settle()
    trace = window.part.feature(sketch_name).polylines[-1]
    check("a Width that does not evaluate: the polyline gets 1 mm, said so",
          trace.width == "1" and "does not evaluate" in window.statusBar().currentMessage(),
          f"{trace.width!r} {window.statusBar().currentMessage()!r}")
    field.setText("h / 2")
    window._trace = None
    window._add_trace(("free", (20.0, 5.0)), ("free", (30.0, 5.0)))
    settle()
    trace = window.part.feature(sketch_name).polylines[-1]
    check("  a good one: the polyline's width is the formula", trace.width == "h / 2", f"{trace.width!r}")
    window._set_trace_tool(False)
    window.finish_sketch()
    settle()


def main():
    path = os.path.join(TMP, "panels.part.toml")
    with open(path, "w", encoding="utf-8") as f:
        f.write(PART)
    window = MainWindow(path)
    errors = []
    window.show_error = errors.append
    window.ask_save_changes = lambda: "discard"
    window.show()
    settle(200)
    check("the part builds", not window.result.feature_errors, f"{window.result.feature_errors}")
    check_extrude(window)
    check_work_plane(window)
    check_move(window)
    check_revolve_angle()
    check_width(window)
    window.close()
    check("no error dialogs", not errors, f"{errors}")
    check("no errors in VisPy callbacks", not VISPY_ERRORS, f"{VISPY_ERRORS[:3]}")
    print("FAILED: " + ", ".join(FAILED) if FAILED else "all checks passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
