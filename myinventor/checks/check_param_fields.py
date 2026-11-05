# check_param_fields.py
#
# Parameters with fields (mycenter with x, y, read as mycenter.x):
#   - formulas: name.field parsed and evaluated, only one level, the bare
#     name of one with fields is an error listing its fields, fields use
#     each other and plain parameters, cycles through fields are found;
#   - renames: a parameter (its fields follow: c.x -> k.x), a field (c.x ->
#     c.u), a plain x never touches c.x;
#   - users and deletes: a used field or parameter is refused, naming users;
#   - the file: [parameter.fields] read strictly, written, read back the same;
#   - geometry: a block placed by a field moves when the field changes
#     (exact bounds), the report lists the fields, the CLI's --set c.x=...;
#   - the window: the parameter tree (a row per field, the values together
#     on the parameter's row), With Fields (Point 2D), Add Field, renaming
#     and editing cells, delete refused when used, a formula field completes
#     "c." with its fields, set_parameters_quiet with a field (the control
#     channel's set).
#
#   python checks/check_param_fields.py

import io
import json
import os
import sys
import tempfile
import tomllib

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

from PySide6.QtTest import QTest                                      # noqa: E402

import cli                                                            # noqa: E402
from model.expressions import ExpressionError, evaluate, evaluate_parameters, names, rename  # noqa: E402
from model.part import PartError                                      # noqa: E402
from model.part_file import PartFileError, dumps, from_data, load, save  # noqa: E402
from model.rebuild import rebuild                                     # noqa: E402
from view.formula_field import FormulaField                           # noqa: E402
from view.main_window import MainWindow                               # noqa: E402
from view.parameters_panel import COMMENT, FORMULA, NAME, VALUE      # noqa: E402

FAILED = []


def check(label, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {label} {detail}")
    if not ok:
        FAILED.append(label)


def settle(ms=30):
    for _ in range(5):
        app.process_events()
    QTest.qWait(ms)


def raises(fn, kind=Exception):
    try:
        fn()
    except kind as error:
        return str(error)
    return None


PART = '''format = "mycad-part"
version = 1

[[parameter]]
name = "a"
formula = "2"
comment = "half the block"

[[parameter]]
name = "c"
comment = "the block's center"
[parameter.fields]
x = "10"
y = "c.x / 2"

[[feature]]
name = "Sketch1"
type = "sketch"
plane = "XY Plane"
[[feature.point]]
name = "P1"
x = "c.x - a"
y = "c.y - a"
[[feature.point]]
name = "P2"
x = "c.x + a"
y = "c.y - a"
[[feature.point]]
name = "P3"
x = "c.x + a"
y = "c.y + a"
[[feature.point]]
name = "P4"
x = "c.x - a"
y = "c.y + a"
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
name = "Block"
type = "extrude"
sketch = "Sketch1"
profiles = [["L1", "L2", "L3", "L4"]]
distance = "1"
operation = "new"
body = "Block"
'''


def check_formulas():
    check("names: a field as name.field", names("c.x + x + sin(c.y)") == {"c.x", "c.y", "x"})
    values = {"c.x": 3.0, "c.y": 4.0, "w": 1.0}
    check("a field evaluates", evaluate("hypot(c.x, c.y) + w", values) == 6.0)
    error = raises(lambda: evaluate("c + 1", values), ExpressionError)
    check("the bare name: an error listing its fields", error and "c.x" in error and "c.y" in error, error)
    error = raises(lambda: evaluate("c.z", values), ExpressionError)
    check("a missing field: unknown name 'c.z'", error and "'c.z'" in error, error)
    for text in ("c.x.y", "sqrt(c).x"):
        check(f"only name.field after a dot: {text!r} refused", raises(lambda: names(text), ExpressionError))
    values, errors = evaluate_parameters([("w", "10"), ("c.x", "w / 2"), ("c.y", "c.x - 3"),
                                          ("d.x", "d.y"), ("d.y", "d.x + 1")])
    check("fields use plain parameters and each other", values == {"w": 10, "c.x": 5, "c.y": 2}, f"{values}")
    check("a cycle through fields is found", "circular" in errors.get("d.x", "") and "d.y" in errors, f"{errors}")
    check("rename x leaves c.x alone", rename("c.x + x + c . x", "x", "w") == "c.x + w + c . x")
    check("rename c renames its fields' uses", rename("c.x + c . y + ac.x", "c", "k") == "k.x + k . y + ac.x",
          rename("c.x + c . y + ac.x", "c", "k"))
    check("rename a field", rename("c.x + c.xx + a.x", "c.x", "c.u") == "c.u + c.xx + a.x")


def check_document():
    part = from_data(tomllib.loads(PART))
    p = part.parameter("c")
    check("read: fields in order", p.has_fields and list(p.fields.items()) == [("x", "10"), ("y", "c.x / 2")])
    check("file: written and read back the same", from_data(tomllib.loads(dumps(part))).to_dict() == part.to_dict())
    check("  as [parameter.fields]", "[parameter.fields]\nx = \"10\"" in dumps(part), dumps(part)[:200])
    for bad, what in ((PART.replace('comment = "the block\'s center"', 'formula = "1"'), "both formula and fields"),
                      (PART.replace('x = "10"', 'x = true'), "a field not a formula"),
                      (PART.replace('x = "10"', '"1x" = "10"'), "a field not a name")):
        check(f"strict reading: {what} refused", raises(lambda: from_data(tomllib.loads(bad)), PartFileError))

    check("formula_rows: fields flattened", part.formula_rows()[:3] == [("a", "2"), ("c.x", "10"), ("c.y", "c.x / 2")])
    check("set_formula / formula_of a field", (part.set_formula("c.x", "12"), part.formula_of("c.x"))[1] == "12")
    error = raises(lambda: part.set_formula("c", "1"), PartError)
    check("the bare name holds no formula", error and "has fields" in error, error)
    check("users of c: the sketch (not its own fields)", part.parameter_users("c") == ["Sketch1"],
          f"{part.parameter_users('c')}")
    check("users of c.x: c.y and the sketch", part.parameter_users("c.x") == ["c.y", "Sketch1"],
          f"{part.parameter_users('c.x')}")
    check("deleting a used field is refused", raises(lambda: part.delete_parameter("c.x"), PartError))
    check("deleting a used parameter is refused", raises(lambda: part.delete_parameter("c"), PartError))

    part.rename_parameter("c.y", "v")
    sketch = part.feature("Sketch1")
    p1 = next(pt for pt in sketch.points if pt.name == "P1")
    check("rename a field: the field and every use", list(part.parameter("c").fields) == ["x", "v"]
          and p1.y == "c.v - a", f"{p1.y}")
    part.rename_parameter("c", "ctr")
    check("rename the parameter: its fields' uses follow",
          p1.x == "ctr.x - a" and part.formula_of("ctr.v") == "ctr.x / 2", f"{p1.x}, {part.formula_of('ctr.v')}")
    check("a field name taken is refused", raises(lambda: part.rename_parameter("ctr.v", "x"), PartError))
    check("add_field: the first free of x, y, z (y, renamed v)", part.add_field("ctr") == "y"
          and part.formula_of("ctr.y") == "0")
    check("  then z", part.add_field("ctr") == "z")
    part.delete_parameter("ctr.z")
    part.delete_parameter("ctr.y")
    check("an unused field deletes", list(part.parameter("ctr").fields) == ["x", "v"])
    check("a plain parameter has no fields to add", raises(lambda: part.add_field("a"), PartError))


def check_geometry():
    path = os.path.join(TMP, "block.part.toml")
    with open(path, "w", encoding="utf-8") as f:
        f.write(PART)
    part = load(path)
    result = rebuild(part, folder=TMP)
    data = cli.report(part, result)
    bounds = next(b["bounds"] for b in data["bodies"] if b["name"] == "Block")
    check("a block placed by c.x, c.y: bounds (8, 3)..(12, 7)",
          data["ok"] and all(abs(u - v) < 1e-6 for u, v in zip(bounds[0] + bounds[1], [8, 3, 0, 12, 7, 1])),
          f"{bounds}")
    c = next(p for p in data["parameters"] if p["name"] == "c")
    check("the report lists the fields with their values",
          c["fields"] == {"x": {"formula": "10", "value": 10.0}, "y": {"formula": "c.x / 2", "value": 5.0}}, f"{c}")
    out = io.StringIO()
    code = cli.run(path, True, True, None, out=out, formulas={"c.x": "20"})
    data = json.loads(out.getvalue())
    bounds = next(b["bounds"] for b in data["bodies"] if b["name"] == "Block")
    check("CLI --set c.x=20: the block moves (y follows c.x / 2)",
          code == 0 and abs(bounds[0][0] - 18) < 1e-6 and abs(bounds[0][1] - 8) < 1e-6, f"{bounds}")
    out = io.StringIO()
    check("CLI --set c=1: refused (exit 2)", cli.run(path, True, False, None, out=out, formulas={"c": "1"}) == 2,
          out.getvalue())
    out = io.StringIO()
    cli.run(path, True, False, None, out=out)
    check("the text report: a line per field", "  c.y = c.x / 2  ->  5" in out.getvalue(), out.getvalue()[:300])
    return path


def check_window(path):
    window = MainWindow(path)
    errors = []
    window.show_error = errors.append
    window.show()
    settle(200)
    panel = window.parameters
    c = panel.item_of("c")
    check("the tree: c with its fields under it", c is not None and c.childCount() == 2
          and panel.item_of("c.x").parent() is c and panel.item_of("c.y").text(NAME) == "y")
    check("  c's row: the values together, no formula", c.text(VALUE) == "(10, 5)" and c.text(FORMULA) == ""
          and c.text(COMMENT) == "the block's center", f"{c.text(VALUE)!r}")
    check("  a field's row: its formula and value",
          panel.item_of("c.y").text(FORMULA) == "c.x / 2" and panel.item_of("c.y").text(VALUE) == "5")

    panel.item_of("c.x").setText(FORMULA, "14")
    settle()
    check("editing a field's formula rebuilds", window.part.formula_of("c.x") == "14"
          and panel.item_of("c.y").text(VALUE) == "7" and panel.item_of("c").text(VALUE) == "(14, 7)")
    lo = window.full_result().shapes and cli.report(window.part, window.full_result())["bodies"][0]["bounds"][0]
    check("  the block follows", abs(lo[0] - 12) < 1e-6 and abs(lo[1] - 5) < 1e-6, f"{lo}")
    panel.item_of("c.x").setText(FORMULA, "c.y")
    settle()
    check("a cycle: 'error' with the reason", panel.item_of("c.x").text(VALUE) == "error"
          and "circular" in panel.item_of("c.x").toolTip(VALUE) and panel.item_of("c").text(VALUE) == "error")
    window.undo()
    settle()
    check("undo: one step back", window.part.formula_of("c.x") == "14")

    panel.item_of("c.y").setText(NAME, "v")
    settle()
    p1 = next(pt for pt in window.part.feature("Sketch1").points if pt.name == "P1")
    check("renaming a field's cell renames its uses", panel.item_of("c.v") is not None and p1.y == "c.v - a")
    panel.item_of("c").setText(NAME, "ctr")
    settle()
    check("renaming the parameter's cell: its fields follow", panel.item_of("ctr.x") is not None
          and p1.x == "ctr.x - a", p1.x)
    panel.item_of("ctr.x").setText(NAME, "v")
    settle()
    check("a taken field name is refused, said so", panel.item_of("ctr.x") is not None
          and "already" in window.statusBar().currentMessage(), window.statusBar().currentMessage())

    panel.item_of("ctr.v").setSelected(True)
    check("Add Field: on when a field is selected", panel.add_field.isEnabled())
    panel.add_field.click()
    settle()
    check("  adds y = 0 (the first free), its name being edited", window.part.formula_of("ctr.y") == "0"
          and panel.tree.currentItem() is panel.item_of("ctr.y"))
    panel.tree.closePersistentEditor(panel.tree.currentItem(), NAME)
    settle()
    panel.delete_requested.emit(["ctr.x"])
    settle()
    check("deleting a used field is refused, naming its users", errors and "Sketch1" in errors[-1], f"{errors}")
    panel.delete_requested.emit(["ctr.y"])
    settle()
    check("an unused field deletes", panel.item_of("ctr.y") is None and "y" not in window.part.parameter("ctr").fields)

    panel.add_fields_requested.emit(["x", "y"])
    settle()
    new = window.part.parameters[-1]
    check("With Fields > Point 2D: p1 with x, y = 0", new.name == "p1" and new.fields == {"x": "0", "y": "0"}
          and panel.item_of("p1").text(VALUE) == "(0, 0)")
    panel.tree.clearSelection()
    panel.item_of("a").setSelected(True)
    check("Add Field: off on a plain parameter", not panel.add_field.isEnabled())

    error = window.set_parameters_quiet({"ctr.x": "30"})
    lo = cli.report(window.part, window.full_result())["bodies"][0]["bounds"][0]
    check("set ctr.x=30 (the control channel's set): rebuilt", error is None and abs(lo[0] - 28) < 1e-6, f"{error}")
    error = window.set_parameters_quiet({"ctr": "1"})
    check("  set ctr=1: refused", error and "ctr" in error, error)

    field = FormulaField("", window.result.values)
    field.show()
    field.setFocus()
    QTest.keyClicks(field, "ctr.")
    settle()
    popup = field.completer_.popup()
    shown = [field.completer_.completionModel().index(i, 0).data() for i in range(field.completer_.completionCount())]
    check("a formula field completes 'ctr.' with its fields", popup.isVisible() and shown == ["ctr.v", "ctr.x"],
          f"{shown}")
    popup.hide()
    field.setText("ctr.x + a")
    check("  and evaluates a field", field.accepted() and "32" in field.shown_value(), field.shown_value())
    field.close()

    saved = os.path.join(TMP, "saved.part.toml")
    save(saved, window.part)
    check("saved and read back the same", load(saved).to_dict() == window.part.to_dict())
    window.ask_save_changes = lambda: "discard"
    window.close()


def main():
    check_formulas()
    check_document()
    path = check_geometry()
    check_window(path)
    check("no errors in VisPy callbacks", not VISPY_ERRORS, f"{VISPY_ERRORS[:3]}")
    print("FAILED: " + ", ".join(FAILED) if FAILED else "all checks passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
