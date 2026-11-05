# check_polyline_reference.py
#
# A polyline's reference point and relative mode (reference = "P1",
# relative = true: its other points' x, y are offsets from P1):
#   - the file: written only when set, read back the same; strict (a
#     reference not among its sketch points, relative without one, a point
#     following two references, references round in a circle);
#   - switching relative on / off, changing the reference: every point stays
#     where it is (exact places); a point keeps its own formula plus the
#     reference's place as a number (12 - 10 = 2, "(w * 5) - (10)"), never the
#     reference's formula: a reference at a parameter (lineorig, the user's
#     featuretest) moves the polyline; the old reference becomes relative,
#     the new one absolute;
#   - moving the reference moves the whole polyline, and a line ending on one
#     of its points follows;
#   - deleting the reference (or the polyline): the points left become
#     absolute, where they were; the polyline split at a segment: the half
#     with the reference keeps it;
#   - extending and inserting into a relative polyline: the new point stays
#     where it was put (its offset written);
#   - a second relative polyline on a shared point with another reference:
#     refused, nothing changed; one hanging on another (its reference a point
#     of the first): moves with it; round in a circle: refused;
#   - the window: Reference of W1 on a point (one per polyline: ticking
#     another moves it), Relative on the polyline (off without a reference),
#     the reference drawn as an X at 45 degrees (in sketch mode), the fields
#     read "x (from P1)" with the place in grey; dragging the
#     reference moves the polyline, dragging the whole polyline moves it once;
#     the Polyline tool extends a relative one; a pasted relative polyline is
#     relative to its own copy of the reference.
#
#   python checks/check_polyline_reference.py

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

import numpy as np                                                    # noqa: E402
from PySide6.QtTest import QTest                                      # noqa: E402
from PySide6.QtWidgets import QCheckBox, QFormLayout, QLabel          # noqa: E402

from model.features import FeatureError                               # noqa: E402
from model.part_file import PartFileError, dumps, from_data           # noqa: E402
from model.rebuild import rebuild                                     # noqa: E402
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
name = "w"
formula = "4"

[[feature]]
name = "S"
type = "sketch"
plane = "XY Plane"
[[feature.point]]
name = "P1"
x = "10"
y = "0"
[[feature.point]]
name = "P2"
x = "20"
y = "0"
[[feature.point]]
name = "P3"
x = "w * 5"
y = "5"
[[feature.point]]
name = "Q"
x = "30"
y = "10"
[[feature.polyline]]
name = "W1"
points = ["P1", "P2", "P3"]
widths = ["1", "1", "1"]
[[feature.line]]
name = "L1"
start = "P3"
end = "Q"
'''


def places(part, names=("P1", "P2", "P3", "Q")):
    out = rebuild(part).outputs["S"]
    return {n: tuple(round(float(v), 9) for v in out.points[n]) for n in names if n in out.points}


def raw(sketch, *names):
    return {n: (sketch.point(n).x, sketch.point(n).y) for n in names}


def refused(fn, words=""):
    try:
        fn()
    except (FeatureError, PartFileError) as error:
        return words in str(error)
    return False


V = {"w": 4.0}                                  # the parameters' values (places as numbers)
START = {"P1": (10, 0), "P2": (20, 0), "P3": (20, 5), "Q": (30, 10)}


def check_model():
    part = from_data(tomllib.loads(PART))
    s = part.feature("S")
    s.set_reference("W1", "P1", V)
    check("a reference alone: nothing moves, nothing rewritten", places(part) == START
          and raw(s, "P2")["P2"] == ("20", "0"))
    s.set_relative("W1", True, V)
    check("relative on: every point where it was", places(part) == START, f"{places(part)}")
    check("  numbers as numbers (20 - 10 = 10), formulas (w * 5) - (10)",
          raw(s, "P2", "P3") == {"P2": ("10", "0"), "P3": ("(w * 5) - (10)", "5")}, f"{raw(s, 'P2', 'P3')}")
    text = dumps(part)
    check("  written: reference, relative; read back the same", 'reference = "P1"' in text and "relative = true" in text
          and from_data(tomllib.loads(text)).to_dict() == part.to_dict())
    s.point("P1").x = "0"
    check("moving the reference moves the polyline; the line on P3 follows (Q stays)",
          places(part) == {"P1": (0, 0), "P2": (10, 0), "P3": (10, 5), "Q": (30, 10)}, f"{places(part)}")
    s.set_reference("W1", "P2", V)
    check("another reference: still where they were", places(part)["P3"] == (10, 5) and places(part)["P1"] == (0, 0))
    check("  P1 relative now (-10), P2 absolute (10), P3 from P2: (w * 5) - (20)",
          raw(s, "P1", "P2", "P3") == {"P1": ("-10", "0"), "P2": ("10", "0"), "P3": ("(w * 5) - (20)", "5")},
          f"{raw(s, 'P1', 'P2', 'P3')}")
    s.set_relative("W1", False, V)
    check("relative off: absolute, where they were, the formula back to (w * 5) - (10)",
          raw(s, "P1", "P3") == {"P1": ("0", "0"), "P3": ("(w * 5) - (10)", "5")} and places(part)["P3"] == (10, 5),
          f"{raw(s, 'P1', 'P3')}")

    s.set_relative("W1", True, V)
    s.extend_polyline("W1", s.add_point("40", "0"), V)
    new = s.polylines[0].points[-1]
    check("extending a relative polyline: the new point where it was put (offset 30 from P2)",
          places(part, (new,))[new] == (40, 0) and raw(s, new)[new] == ("30", "0"), f"{raw(s, new)}")
    inserted = s.add_point("15", "0")
    s.insert_into_polyline("W1", 1, inserted, "1", V)
    check("inserting: where it was put (offset 5)", places(part, (inserted,))[inserted] == (15, 0)
          and raw(s, inserted)[inserted] == ("5", "0"))
    before = places(part)
    s.delete("P2", V)
    check("deleting the reference: the others absolute, where they were; no reference",
          places(part, ("P1", "P3", "Q")) == {k: v for k, v in before.items() if k != "P2"}
          and s.polylines[0].reference == "" and not s.polylines[0].relative
          and raw(s, "P1")["P1"] == ("0", "0"), f"{raw(s, 'P1', 'P3')}")

    part = from_data(tomllib.loads(PART))
    s = part.feature("S")
    s.set_reference("W1", "P1", V)
    s.set_relative("W1", True, V)
    s.delete("W1", V)
    check("deleting a relative polyline: its points used elsewhere stay where they were (P3, absolute)",
          places(part, ("P3",))["P3"] == (20, 5) and raw(s, "P3")["P3"] == ("w * 5", "5"), f"{raw(s, 'P3')}")

    part = from_data(tomllib.loads(PART))
    s = part.feature("S")
    s.set_reference("W1", "P3", V)
    s.set_relative("W1", True, V)
    s.delete("W1.1", V)
    halves = {w.name: (w.points, w.reference, w.relative) for w in s.polylines}
    check("split at a segment: the half with the reference keeps it, the other absolute",
          list(halves.values()) == [(["P1"], "", False), (["P2", "P3"], "P3", True)]
          or (len(halves) == 1 and list(halves.values())[0] == (["P2", "P3"], "P3", True)), f"{halves}")
    check("  every point where it was", places(part, ("P2", "P3")) == {"P2": (20, 0), "P3": (20, 5)})

    # Two relative polylines on one point; one hanging on another; a circle.
    part = from_data(tomllib.loads(PART))
    s = part.feature("S")
    s.set_reference("W1", "P1", V)
    s.set_relative("W1", True, V)
    r = s.add_point("50", "0")
    w2 = s.add_polyline(r, "P2", "1")
    s.set_reference(w2, r, V)
    snapshot = s.to_dict()
    check("a second relative polyline on P2 with another reference: refused, nothing changed",
          refused(lambda: s.set_relative(w2, True, V), "two references") and s.to_dict() == snapshot)
    w3 = s.add_polyline("P3", s.add_point("60", "5"), "1")
    s.set_reference(w3, "P3", V)
    s.set_relative(w3, True, V)
    end = s.polylines[-1].points[-1]
    s.point("P1").x = "11"
    check("one hanging on another (W3's reference P3 is W1's): moving W1's reference moves W3",
          places(part, (end,))[end] == (61, 5), f"{places(part, (end,))}")
    x, y = s.add_point("0", "40"), s.add_point("5", "40")
    a = s.add_polyline(x, y, "1")
    s.set_reference(a, x, V)
    s.set_relative(a, True, V)                     # y follows x
    b = s.add_polyline(y, x, "1")
    s.set_reference(b, y, V)
    snapshot = s.to_dict()
    check("round in a circle (x follows y, y follows x): refused, nothing changed",
          refused(lambda: s.set_relative(b, True, V), "circle") and s.to_dict() == snapshot)

    bad = PART.replace('widths = ["1", "1", "1"]', 'widths = ["1", "1", "1"]\nreference = "Q"\nrelative = true')
    check("strict: a reference not among its points refused", refused(lambda: from_data(tomllib.loads(bad)),
                                                                        "not one of its"))
    bad = PART.replace('widths = ["1", "1", "1"]', 'widths = ["1", "1", "1"]\nrelative = true')
    check("strict: relative without a reference refused", refused(lambda: from_data(tomllib.loads(bad)),
                                                                     "needs a reference"))
    check_parametric_reference()


def check_parametric_reference():
    """The user's featuretest (2026-11-05): the reference at a parameter's place; the polyline must follow it."""
    text = PART.replace('[[parameter]]\nname = "w"\nformula = "4"\n',
                        '[[parameter]]\nname = "w"\nformula = "4"\n\n[[parameter]]\nname = "lineorig"\n'
                        '[parameter.fields]\nx = "10"\ny = "0"\n')
    text = text.replace('name = "P1"\nx = "10"\ny = "0"', 'name = "P1"\nx = "lineorig.x"\ny = "lineorig.y"')
    part = from_data(tomllib.loads(text))
    s = part.feature("S")
    values = rebuild(part).values
    s.set_reference("W1", "P1", values)
    s.set_relative("W1", True, values)
    check("a reference at a parameter (lineorig): the offsets hold no lineorig (P2: 10, P3: (w * 5) - (10))",
          raw(s, "P2", "P3") == {"P2": ("10", "0"), "P3": ("(w * 5) - (10)", "5")}, f"{raw(s, 'P2', 'P3')}")
    part.set_formula("lineorig.x", "100")
    part.set_formula("lineorig.y", "7")
    check("  lineorig moved by (90, 7): the whole polyline follows",
          places(part, ("P1", "P2", "P3")) == {"P1": (100, 7), "P2": (110, 7), "P3": (110, 12)},
          f"{places(part, ('P1', 'P2', 'P3'))}")
    s.set_relative("W1", False, rebuild(part).values)
    check("  relative off there: absolute numbers where they are (P2 110, 7), P3 keeps w: (w * 5) + (90)",
          raw(s, "P2", "P3") == {"P2": ("110", "7"), "P3": ("(w * 5) + (90)", "12")}, f"{raw(s, 'P2', 'P3')}")
    check("  places unchanged", places(part, ("P2", "P3")) == {"P2": (110, 7), "P3": (110, 12)})


def props(window):
    out, form = {}, window.props_form
    for i in range(form.rowCount()):
        for role in (QFormLayout.ItemRole.SpanningRole, QFormLayout.ItemRole.FieldRole):
            item = form.itemAt(i, role)
            if item is None or item.widget() is None:
                continue
            widget = item.widget()
            if isinstance(widget, QCheckBox):
                out[widget.text()] = widget
            elif role == QFormLayout.ItemRole.FieldRole:
                label = form.itemAt(i, QFormLayout.ItemRole.LabelRole)
                key = label.widget().text() if label is not None and label.widget() is not None else ""
                out[key or f"row{i}"] = widget
    return out


def check_window():
    path = os.path.join(TMP, "ref.part.toml")
    with open(path, "w", encoding="utf-8") as f:
        f.write(PART)
    window = MainWindow(path)
    errors = []
    window.show_error = errors.append
    window.ask_save_changes = lambda: "discard"
    window.show()
    settle(200)
    window.edit_sketch("S")
    settle()
    view = window.view

    def select(*names):
        view.select_keys([("entity", "S", n) for n in names])
        window._on_view_select(view.selected)
        settle()
        return props(window)

    fields = select("W1")
    check("W1 selected: Relative, off without a reference", "Relative (no reference point)" in fields
          and not fields["Relative (no reference point)"].isEnabled())
    fields = select("P1")
    box = fields.get("Reference of W1")
    check("P1 selected: Reference of W1, unticked", box is not None and not box.isChecked(), f"{list(fields)}")
    box.setChecked(True)
    settle()
    check("  ticked: P1 is W1's reference (one undo step)", window.part.feature("S").polylines[0].reference == "P1")
    fields = select("P2")
    fields["Reference of W1"].setChecked(True)
    settle()
    check("  P2's ticked: it takes it from P1", window.part.feature("S").polylines[0].reference == "P2")
    fields = select("P1")
    check("  P1's box unticked now", not fields["Reference of W1"].isChecked())
    fields["Reference of W1"].setChecked(True)
    settle()
    fields = select("W1")
    relative = fields.get("Relative to P1")
    check("W1: Relative to P1, on offer", relative is not None and relative.isEnabled() and not relative.isChecked())
    relative.setChecked(True)
    settle()
    check("  ticked: relative, every point where it was", window.part.feature("S").polylines[0].relative
          and places(window.part) == START)
    marks = view._sketch_geometry["S"].xmark_segments
    p1 = np.array([10.0, 0.0, 0.0])
    directions = [(b - a) / np.linalg.norm(b - a) for a, b in marks] if len(marks) else []
    check("the reference P1 drawn as an X: two strokes through it at 45 degrees to the sketch's x, y",
          len(marks) == 2 and all(np.allclose((a + b) / 2, p1, atol=1e-9) for a, b in marks)
          and all(abs(abs(d[0]) - abs(d[1])) < 1e-9 and abs(d[2]) < 1e-12 for d in directions)
          and view._sketch_geometry["S"].xmarks.visible, f"{marks}")
    fields = select("P2")
    check("P2's fields: x (from P1) = 10, the place in grey", "x (from P1):" in fields
          and fields["x (from P1):"].text() == "10"
          and any(isinstance(v, QLabel) and "(20, 0) mm in the sketch" in v.text() for v in fields.values()),
          f"{list(fields)}")

    # Drag the reference: the polyline goes with it.
    start = places(window.part)
    window._drag_entity([("entity", "S", "P1")], np.array([0.0, 3.0]), "start")
    window._drag_entity([("entity", "S", "P1")], np.array([0.0, 3.0]), "move")
    window._drag_entity([("entity", "S", "P1")], np.array([0.0, 3.0]), "end")
    settle()
    now = places(window.part)
    check("dragging the reference 3 up: the whole polyline 3 up, Q stays",
          all(abs(now[n][1] - start[n][1] - 3) < 1e-9 for n in ("P1", "P2", "P3")) and now["Q"] == start["Q"], f"{now}")
    window._drag_entity([("entity", "S", "W1")], np.array([2.0, 0.0]), "start")
    window._drag_entity([("entity", "S", "W1")], np.array([2.0, 0.0]), "move")
    window._drag_entity([("entity", "S", "W1")], np.array([2.0, 0.0]), "end")
    settle()
    moved = places(window.part)
    check("dragging the whole polyline 2 right: each point 2 right, once",
          all(abs(moved[n][0] - now[n][0] - 2) < 1e-9 for n in ("P1", "P2", "P3")), f"{moved}")

    # The Polyline tool extends it.
    window._trace = "W1"
    sketch = window.part.feature("S")
    last = sketch.polylines[0].points[-1]
    window._add_trace(("point", last), ("xy", (40.0, 8.0)))
    settle()
    new = window.part.feature("S").polylines[0].points[-1]
    check("the Polyline tool extends it: the new point where it was clicked (40, 8)",
          places(window.part, (new,))[new] == (40, 8) and new in window.part.feature("S").bases(), f"{new}")

    # Copy, paste: relative to the copy's own reference.
    select("W1")
    window.copy_selection()
    window._paste(np.array([0.0, -20.0]))
    settle()
    copy = window.part.feature("S").polylines[-1]
    pts = places(window.part, tuple(copy.points))
    orig = places(window.part, tuple(window.part.feature("S").polylines[0].points))
    check("pasted: relative to its own copy of the reference, the same shape 20 lower",
          copy.relative and copy.reference not in window.part.feature("S").polylines[0].points
          and [pts[p] for p in copy.points] == [(x, round(y - 20, 9)) for x, y in
                                                [orig[p] for p in window.part.feature("S").polylines[0].points]],
          f"{copy}")
    window.finish_sketch()
    settle()
    check("out of sketch mode: no X (as the other points' dots)", not view._sketch_geometry["S"].xmarks.visible
          if "S" in view._sketch_geometry else True)
    window.close()
    check("no error dialogs", not errors, f"{errors}")


def main():
    check_model()
    check_window()
    check("no errors in VisPy callbacks", not VISPY_ERRORS, f"{VISPY_ERRORS[:3]}")
    print("FAILED: " + ", ".join(FAILED) if FAILED else "all checks passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
