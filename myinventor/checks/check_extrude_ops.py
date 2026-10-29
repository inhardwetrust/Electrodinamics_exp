# check_extrude_ops.py
#
# Extrude's operations: New solid, Join, Cut, Intersect (exact OCP volumes).
# The part: Solid1, a 10 x 10 block 5 high (Sketch1, Extrusion1); Sketch2
# with a circle r = 2 at (5, 5) and a rectangle (8..14) x (2..6).
#   - model: cut the circle through it: 500 - 20 pi, still one body; join the
#     rectangle: + 80 (its part outside); intersect a circle r = 2 by 10 with
#     a fresh block: 20 pi; a second New solid: two bodies; errors name the
#     cause (no such body above, the name taken, the cut removes all, they do
#     not overlap) and leave the body as it was; a cut above the body it
#     cuts fails; a TOML round trip keeps the operation; an older file
#     without one reads as New;
#   - the window: with no body the panel offers New only; with Solid1 it
#     starts on Join of Solid1; Cut shows a red preview; Apply cuts (one
#     body, 500 - 20 pi); editing the cut rolls the model back (Solid1 uncut,
#     500, in the view), the panel holds Cut and Solid1; Cancel brings the
#     cut model back; a new distance applied changes the cut depth.
#
#   python checks/check_extrude_ops.py

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

from vispy import app                                                 # noqa: E402

app.use_app("pyside6")
app.use_app().create()

from vispy_errors import install as _watch_vispy                      # noqa: E402

VISPY_ERRORS = _watch_vispy()

from PySide6.QtCore import Qt                                        # noqa: E402
from PySide6.QtTest import QTest                                      # noqa: E402

from check_extrude import rect                                        # noqa: E402
from check_sketch_line import settle, shield                          # noqa: E402
from geometry import kernel                                           # noqa: E402
from model.extrude import Extrude                                     # noqa: E402
from model.part import Part                                           # noqa: E402
from model.part_file import dumps, from_data                          # noqa: E402
from model.rebuild import rebuild                                     # noqa: E402
from model.sketch import Sketch                                       # noqa: E402
from view.main_window import MainWindow                               # noqa: E402

FAILED = []
MM = 1e-3
BLOCK = ("L1", "L2", "L3", "L4")
CIRCLE = ("C1",)
BAR = ("L1", "L2", "L3", "L4")              # Sketch2's rectangle (names are per sketch)


def check(label, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {label} {detail}")
    if not ok:
        FAILED.append(label)


def base_part():
    part = Part()
    s1 = part.add_feature(Sketch("Sketch1", plane="XY Plane"))
    rect(s1, 0, 0, 10, 10)
    part.add_feature(Extrude("Extrusion1", profiles=[BLOCK], sketch="Sketch1", distance="5", body="Solid1"))
    s2 = part.add_feature(Sketch("Sketch2", plane="XY Plane"))
    s2.add_circle(s2.add_point("5", "5"), "2")
    rect(s2, 8, 2, 14, 6)
    return part


def volumes(part):
    result = rebuild(part)
    return {n: kernel.volume(shape) for n, shape in result.shapes.items()}, result.feature_errors


def add(part, name, profiles, operation, body, distance="5", sketch="Sketch2"):
    part.add_feature(Extrude(name, profiles=profiles, sketch=sketch, distance=distance, operation=operation,
                             body=body))


def check_model():
    part = base_part()
    add(part, "Cut1", [CIRCLE], "cut", "Solid1")
    v, errors = volumes(part)
    check("cut the circle through: 500 - 20 pi, one body", list(v) == ["Solid1"]
          and math.isclose(v["Solid1"], 500 - 20 * math.pi, rel_tol=1e-9) and not errors, f"{v} {errors}")
    add(part, "Join1", [BAR], "join", "Solid1")
    v, errors = volumes(part)
    check("join the rectangle: + 80 (its part outside)",
          math.isclose(v["Solid1"], 580 - 20 * math.pi, rel_tol=1e-9) and not errors, f"{v} {errors}")
    add(part, "New2", [CIRCLE], "new", "Solid2", distance="-3")
    v, _ = volumes(part)
    check("a second New solid: two bodies", list(v) == ["Solid1", "Solid2"]
          and math.isclose(v["Solid2"], 12 * math.pi, rel_tol=1e-9), f"{list(v)}")
    text = dumps(part)
    check("a TOML round trip keeps the operation", from_data(tomllib.loads(text)).to_dict() == part.to_dict()
          and 'operation = "cut"' in text)
    old = 'format = "mycad-part"\nversion = 1\n' + text.split("version = 1\n", 1)[1].replace(
        'operation = "new"\n', "", 1)
    check("an older file without the operation reads as New",
          from_data(tomllib.loads(old)).feature("Extrusion1")["operation"] == "new")

    part = base_part()
    add(part, "Meet", [CIRCLE], "intersect", "Solid1", distance="10")
    v, _ = volumes(part)
    check("intersect a circle by 10 with the block: 20 pi", math.isclose(v["Solid1"], 20 * math.pi, rel_tol=1e-9),
          f"{v}")

    part = base_part()
    cases = [("no such body", "join", "Solid9", "no body 'Solid9'"),
             ("the name taken", "new", "Solid1", "exists already")]
    for label, operation, body, expected in cases:
        part = base_part()
        add(part, "Bad", [CIRCLE], operation, body)
        v, errors = volumes(part)
        check(f"an error names it: {label}", expected in errors.get("Bad", "")
              and math.isclose(v["Solid1"], 500, rel_tol=1e-9), f"{errors}")
    part = base_part()
    s3 = part.add_feature(Sketch("Sketch3", plane="XY Plane"))
    rect(s3, -1, -1, 11, 11)
    add(part, "Bad", [BLOCK], "cut", "Solid1", sketch="Sketch3")
    v, errors = volumes(part)
    check("a cut removing all fails, the body stays", "removes all" in errors.get("Bad", "")
          and math.isclose(v["Solid1"], 500, rel_tol=1e-9), f"{errors}")
    part = base_part()
    s3 = part.add_feature(Sketch("Sketch3", plane="XY Plane"))
    rect(s3, 20, 20, 25, 25)
    add(part, "Bad", [BLOCK], "intersect", "Solid1", sketch="Sketch3")
    _, errors = volumes(part)
    check("an intersect that does not overlap fails", "do not overlap" in errors.get("Bad", ""), f"{errors}")
    part = base_part()
    cut = Extrude("Early", profiles=[BLOCK], sketch="Sketch1", distance="1", operation="cut", body="Solid1")
    part.features.insert(1, cut)                # above Extrusion1, which makes Solid1
    _, errors = volumes(part)
    check("a cut above the body it cuts fails", "no body 'Solid1' above" in errors.get("Early", ""), f"{errors}")


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
    rect(window.part.feature("Sketch1"), 0, 0, 10, 10)
    window._commit()
    window.extrude_action.trigger()
    panel = window.extrude_panel
    enabled = [panel.operation.model().item(i).isEnabled() for i in range(4)]
    check("no body yet: New only", panel.operation_key() == "new" and enabled == [True, False, False, False],
          f"{enabled}")
    panel.distance.setText("5")
    panel.apply_button.click()
    settle()

    window.new_sketch("XY Plane")
    s2 = window.part.feature("Sketch2")
    s2.add_circle(s2.add_point("5", "5"), "2")
    window._commit()
    window.extrude_action.trigger()
    settle()
    panel = window.extrude_panel
    check("with Solid1: starts on Join of Solid1", panel.operation_key() == "join" and panel.body_name() == "Solid1"
          and [panel.body.itemText(i) for i in range(panel.body.count())] == ["Solid1"])
    check("  the only region (the circle) is taken", view.profile_selection == [("profile", "Sketch2", CIRCLE)])
    panel.operation.setCurrentIndex(2)                       # Cut
    panel.distance.setText("5")
    check("Cut: a red preview", view.preview.visible and view.preview_cut)
    panel.apply_button.click()
    settle()
    v = kernel.volume(window.result.shapes["Solid1"])
    check("Apply cuts: one body, 500 - 20 pi", [b.name for b in window.result.bodies] == ["Solid1"]
          and math.isclose(v, 500 - 20 * math.pi, rel_tol=1e-9), f"{v}")
    check("  the browser: Solid Bodies (1)", window.tree.topLevelItem(0).child(0).text(0) == "Solid Bodies (1)")

    window._row_double_clicked(window._rows[("feature", "Extrusion2")])
    settle()
    panel = window.extrude_panel
    shown = view._bodies["Solid1"].body.volume / MM ** 3
    check("editing the cut rolls back: Solid1 uncut (500) in the view", math.isclose(shown, 500.0, rel_tol=1e-6),
          f"{shown}")
    check("  the panel holds Cut and Solid1", panel.operation_key() == "cut" and panel.body_name() == "Solid1")
    QTest.keyClick(native, Qt.Key_Escape)
    settle()
    shown = view._bodies["Solid1"].body.volume / MM ** 3
    check("Cancel brings the cut model back", math.isclose(shown, 500 - 20 * math.pi, rel_tol=2e-3), f"{shown}")
    window._row_double_clicked(window._rows[("feature", "Extrusion2")])
    window.extrude_panel.distance.setText("2")
    window.extrude_panel.apply_button.click()
    settle()
    v = kernel.volume(window.result.shapes["Solid1"])
    check("a new distance applied: a 2 mm deep pocket (500 - 8 pi)", math.isclose(v, 500 - 8 * math.pi, rel_tol=1e-9)
          and window.part.feature("Extrusion2")["operation"] == "cut", f"{v}")
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
