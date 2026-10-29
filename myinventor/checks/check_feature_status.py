# check_feature_status.py
#
# Features that cannot be built, and showing a used sketch from the browser.
# The part: Sketch1 on XY, a 10 x 10 square; Extrusion1 by h = 5 -> Solid1;
# Sketch2 on XY, a circle r = 2 at (5, 5); Extrusion2 cuts it from Solid1.
#   - a cut that does not reach the body fails ("removes nothing") and the
#     body stays; a cut through all of it fails ("removes all");
#   - in the window each failure turns the feature's row red, its tooltip
#     and the info give the reason, and the status bar names it right after
#     the edit; undo makes it black again:
#       a line of Sketch1 deleted (the profile is gone; Extrusion2, cutting
#       Solid1 which no longer exists, fails too);
#       Sketch2's radius made 0 (the sketch fails, Extrusion2 "uses Sketch2");
#       the distance parameter h broken ("distance uses h, which has an error");
#       the cut moved off the body (removes nothing);
#   - a used sketch (hidden in 3D): hovering its row shows it green while
#     the mouse is there; selecting the row shows it blue; selecting
#     something else hides it again.
#
#   python checks/check_feature_status.py

import os
import sys
import tempfile

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

from check_extrude import rect                                        # noqa: E402
from check_sketch_line import settle, shield                          # noqa: E402
from model.extrude import Extrude                                     # noqa: E402
from model.part import Part                                           # noqa: E402
from model.rebuild import rebuild                                     # noqa: E402
from model.sketch import Sketch                                       # noqa: E402
from view.geometry_view import HOVER_COLOR, SELECTED_COLOR            # noqa: E402
from view.main_window import MainWindow                               # noqa: E402

FAILED = []
SQUARE = ("L1", "L2", "L3", "L4")


def check(label, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {label} {detail}")
    if not ok:
        FAILED.append(label)


def build(part):
    part.add_parameter("h", "5")
    s1 = part.add_feature(Sketch("Sketch1", plane="XY Plane"))
    rect(s1, 0, 0, 10, 10)
    part.add_feature(Extrude("Extrusion1", profiles=[SQUARE], sketch="Sketch1", distance="h", body="Solid1"))
    s2 = part.add_feature(Sketch("Sketch2", plane="XY Plane"))
    s2.add_circle(s2.add_point("5", "5"), "2")
    part.add_feature(Extrude("Extrusion2", profiles=[("C1",)], sketch="Sketch2", distance="h", operation="cut",
                             body="Solid1"))
    return part


def check_kernel():
    part = build(Part())
    part.feature("Sketch2").point("P1").x = "30"             # the circle off the block
    result = rebuild(part)
    check("a cut that does not reach the body fails: removes nothing",
          "removes nothing" in result.feature_errors.get("Extrusion2", ""), f"{result.feature_errors}")
    check("  the body stays (500)", abs(result.bodies[0].volume / 1e-9 - 500) < 1e-6)
    part = build(Part())
    part.feature("Sketch2").circle("C1").radius = "20"
    result = rebuild(part)
    check("a cut through all of it fails: removes all",
          "removes all" in result.feature_errors.get("Extrusion2", ""), f"{result.feature_errors}")


def main():
    check_kernel()
    window = MainWindow()
    window.ask_save_changes = lambda: "discard"
    errors = []
    window.show_error = errors.append
    window.resize(1200, 800)
    window.show()
    settle(300)
    shield(window)
    view = window.view
    build(window.part)
    window._commit()
    settle()
    check("the part builds: nothing red", not window.result.feature_errors, f"{window.result.feature_errors}")

    def red(name):
        row = window._rows[("feature", name)]
        return row.foreground(0).color().red() > 150 and row.foreground(0).color().green() < 100

    def tooltip(name):
        return window._rows[("feature", name)].toolTip(0)

    def message():
        return window.statusBar().currentMessage()

    cases = [
        ("a line of Sketch1 deleted", lambda p: p.feature("Sketch1").delete("L2"),
         {"Extrusion1": "not a closed region", "Extrusion2": "no body 'Solid1'"}),
        ("Sketch2's radius 0", lambda p: setattr(p.feature("Sketch2").circle("C1"), "radius", "0"),
         {"Sketch2": "radius must be > 0", "Extrusion2": "uses 'Sketch2'"}),
        ("the parameter h broken", lambda p: setattr(p.parameter("h"), "formula", "h2 * 2"),
         {"Extrusion1": "distance uses h, which has an error", "Extrusion2": "distance uses h"}),
        ("the cut moved off the body", lambda p: setattr(p.feature("Sketch2").point("P1"), "x", "40"),
         {"Extrusion2": "removes nothing"}),
    ]
    for label, edit, expected in cases:
        edit(window.part)
        window._commit()
        settle()
        got = {n: window.result.feature_errors.get(n, "") for n in expected}
        reds = all(red(n) for n in expected)
        reasons = all(text in got[n] and text in tooltip(n) for n, text in expected.items())
        first = next(iter(expected))
        check(f"{label}: red rows, the reasons in their tooltips", reds and reasons, f"{got}")
        check("  the status bar names it", f"{first} failed" in message(), message())
        window.tree.setCurrentItem(window._rows[("feature", first)])
        check("  the info gives the reason", expected[first] in window.info.text(), window.info.text())
        window.undo()
        settle()
        check("  undo: black again", not window.result.feature_errors and not any(red(n) for n in expected))

    # --- a used sketch from the browser
    window.tree.setCurrentItem(None)
    view.select(None)
    window._show_sketches()
    check("used sketches are hidden", "Sketch1" not in view._sketch_geometry and "Sketch2" not in view._sketch_geometry)
    row = window._rows[("feature", "Sketch1")]
    window._row_hovered(row)
    visual = view._sketch_geometry.get("Sketch1")
    check("hovering its row shows it, green", visual is not None
          and all(c == HOVER_COLOR for c in visual.shown_colors.values()))
    window._row_hovered(None)
    check("  the mouse gone: hidden again", "Sketch1" not in view._sketch_geometry)
    window.tree.setCurrentItem(row)
    visual = view._sketch_geometry.get("Sketch1")
    check("selecting its row shows it, blue", visual is not None
          and all(c == SELECTED_COLOR for c in visual.shown_colors.values()))
    window.tree.setCurrentItem(window._rows[("datum", "XY Plane")])
    check("  something else selected: hidden again", "Sketch1" not in view._sketch_geometry)
    check("no error dialogs", not errors, f"{errors}")
    window.close()
    check("no errors in VisPy callbacks", not VISPY_ERRORS, f"{VISPY_ERRORS[:3]}")
    print("FAILED: " + ", ".join(FAILED) if FAILED else "all checks passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
