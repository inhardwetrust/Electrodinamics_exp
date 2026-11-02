# check_sketch_props.py
#
# Sketch entities' coordinates and sizes as formulas in the properties
# (below left), in view/formula_field.py fields. Parameters: width = 10,
# wall = 2. A sketch: line L1 from P1 (0, 0) to P2 (5, 0), circle C1 around
# P3 (20, 0), radius 1.
#   - a point: rows x, y; "width / 2" in y: P2 at y 5, the field shows
#     "= 5 mm", one undo step;
#   - a line: Start x / y, End x / y (its points);
#   - a circle: Center x / y, Radius; "wall" in Radius: radius 2;
#   - a formula that does not evaluate ("nope + 1"): a red frame, the
#     error in the tooltip, not applied;
#   - a plain number: no value shown;
#   - completing names: "2 * wi" + width taken -> "2 * width", the cursor
#     after it, Enter applies; typing "wa" opens the popup with "wall", and
#     the Enter that takes it in the popup does not apply yet.
#
#   python checks/check_sketch_props.py

import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "checks"))
sys.stdout.reconfigure(encoding="utf-8")
TMP = tempfile.mkdtemp()
os.environ["MYCAD_SETTINGS"] = os.path.join(TMP, "settings.json")     # never the user's settings

from model.part import Part                                           # noqa: E402
from model.part_file import dumps                                     # noqa: E402
from model.sketch import Sketch                                       # noqa: E402

FAILED = []


def check(label, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {label} {detail}")
    if not ok:
        FAILED.append(label)


def main():
    from vispy import app

    app.use_app("pyside6")
    app.use_app().create()
    from vispy_errors import install as _watch_vispy

    vispy_errors = _watch_vispy()
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QFormLayout

    from check_sketch_line import settle, shield
    from view.formula_field import FormulaField
    from view.main_window import MainWindow

    part = Part()
    part.add_parameter("width", "10")
    part.add_parameter("wall", "2")
    s = part.add_feature(Sketch("Sketch1", plane="XY Plane"))
    s.add_line(s.add_point("0", "0"), s.add_point("5", "0"))
    s.add_circle(s.add_point("20", "0"), "1")
    path = os.path.join(TMP, "props.part.toml")
    with open(path, "w", encoding="utf-8") as f:
        f.write(dumps(part))

    window = MainWindow()
    window.ask_save_changes = lambda: "discard"
    errors = []
    window.show_error = errors.append
    window.resize(1200, 800)
    window.show()
    shield(window)
    settle(200)
    window.open_file(path)
    settle()
    window.edit_sketch("Sketch1")
    settle()
    view = window.view

    def select(name):
        view.select_keys([("entity", "Sketch1", name)])
        window._on_view_select(("entity", "Sketch1", name))
        settle(30)
        out, form = {}, window.props_form
        for i in range(form.rowCount()):
            label = form.itemAt(i, QFormLayout.ItemRole.LabelRole)
            field = form.itemAt(i, QFormLayout.ItemRole.FieldRole)
            if label is not None and field is not None:
                out[label.widget().text()] = field.widget()
        return out

    def enter(field, text):
        field.setFocus()
        field.setText(text)
        field.editingFinished.emit()
        settle()

    def point(name):
        return [round(float(v), 9) for v in window.result.outputs["Sketch1"].points[name]]

    rows = select("P2")
    check("a point: rows x, y (formula fields)", {"x:", "y:"} <= set(rows)
          and all(isinstance(rows[k], FormulaField) for k in ("x:", "y:")), str(list(rows)))
    steps = len(window.doc._undo)
    enter(rows["y:"], "width / 2")
    rows = select("P2")
    check("  y = width / 2: P2 at (5, 5), one undo step", window.part.feature("Sketch1").point("P2").y == "width / 2"
          and point("P2") == [5, 5] and len(window.doc._undo) == steps + 1, str(point("P2")))
    check("  the field shows = 5 mm", rows["y:"].shown_value() == "= 5 mm", rows["y:"].shown_value())
    check("  a plain number shows no value", rows["x:"].shown_value() == "")

    rows = select("L1")
    check("a line: Start x / y, End x / y", {"Start x:", "Start y:", "End x:", "End y:"} <= set(rows), str(list(rows)))
    check("  End y holds P2's formula", rows["End y:"].text() == "width / 2")
    enter(rows["Start x:"], "-wall")
    check("  Start x = -wall: P1 at (-2, 0)", point("P1") == [-2, 0], str(point("P1")))

    rows = select("C1")
    check("a circle: Center x / y, Radius", {"Center x:", "Center y:", "Radius:"} <= set(rows), str(list(rows)))
    enter(rows["Radius:"], "wall")
    check("  Radius = wall: 2", window.part.feature("Sketch1").circle("C1").radius == "wall"
          and abs(window.result.outputs["Sketch1"].radii["C1"] - 2) < 1e-12)

    rows = select("C1")
    steps = len(window.doc._undo)
    field = rows["Radius:"]
    field.setText("nope + 1")
    check("a formula that does not evaluate: red, the error in the tooltip", "c81e1e" in field.styleSheet()
          and "nope" in field.toolTip(), field.toolTip())
    field.editingFinished.emit()
    settle()
    check("  not applied", window.part.feature("Sketch1").circle("C1").radius == "wall"
          and len(window.doc._undo) == steps and "not applied" in window.statusBar().currentMessage(),
          window.statusBar().currentMessage())

    rows = select("P3")
    field = rows["x:"]
    finished = []
    field.editingFinished.connect(lambda: finished.append(field.text()))
    field.setFocus()
    field.setText("2 * wi")
    field.setCursorPosition(len("2 * wi"))
    field.insert_name("width")
    check("completing: 2 * wi -> 2 * width, the cursor after it", field.text() == "2 * width"
          and field.cursorPosition() == len("2 * width"), field.text())
    QTest.keyClick(field, Qt.Key_Return)
    settle()
    check("  Enter applies: P3 at x 20", point("P3") == [20, 0]
          and window.part.feature("Sketch1").point("P3").x == "2 * width", str(point("P3")))
    rows = select("P3")
    field = rows["x:"]
    field.setFocus()
    field.setText("")
    QTest.keyClicks(field, "wa")
    settle(50)
    popup, model = field.completer_.popup(), field.completer_.completionModel()
    shown = [model.index(i, 0).data() for i in range(model.rowCount())]
    finished = []
    field.editingFinished.connect(lambda: finished.append(field.text()))
    check("typing 'wa' opens the popup with wall", popup.isVisible() and shown == ["wall"], str(shown))
    QTest.keyClick(popup, Qt.Key_Down)
    QTest.keyClick(popup, Qt.Key_Return)
    settle(50)
    check("  Enter in the popup: wall, not applied yet", field.text() == "wall" and not popup.isVisible()
          and finished == [] and window.part.feature("Sketch1").point("P3").x == "2 * width",
          f"{field.text()!r} {finished}")
    field.setText("2 * width")
    window.undo()
    settle()
    check("undo: P3 x back to 20 as text", window.part.feature("Sketch1").point("P3").x == "20")
    check("no error dialogs", not errors, str(errors))
    window.close()
    check("no errors in VisPy callbacks", not vispy_errors, f"{vispy_errors[:3]}")
    print("FAILED: " + ", ".join(FAILED) if FAILED else "all checks passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
