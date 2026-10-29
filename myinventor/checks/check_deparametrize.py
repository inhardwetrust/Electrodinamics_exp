# check_deparametrize.py
#
# Deparametrize: the formulas of sketch entities become plain numbers (the
# geometry stays where it is; it no longer follows the parameters and can be
# dragged). On examples/patch_antenna.part.toml, copied to a temp folder:
#   - model: formula_slots lists what is set by formulas, per selection: a
#     point (its x, y), a line (its end points), a polyline or one of its
#     segments (its points, widths, default width, bend radii), a circle
#     (its center and radius), the whole sketch; plain numbers are not listed;
#   - deparametrize: every one becomes its value (12 digits); the rebuilt
#     bodies are the same (exact volumes, bounds); a parameter change then
#     moves only what still has formulas; a formula with an error is left
#     and reported; the file round-trips;
#   - window (real mouse events): selecting the patch's edge shows
#     "Deparametrize (4)" (its two corners, x and y); a click: one undo
#     step, the corner now drags; Undo puts the formulas back; the sketch's
#     row in the tree offers the whole sketch; a sketch without formulas
#     shows no button.
#
#   python checks/check_deparametrize.py

import math
import os
import shutil
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

from PySide6.QtWidgets import QFormLayout, QPushButton                # noqa: E402

from check_sketch_drag import drag                                    # noqa: E402
from check_sketch_line import click, settle, shield                   # noqa: E402
from geometry import kernel                                           # noqa: E402
from model.part_file import dumps, from_data, load                    # noqa: E402
from model.rebuild import rebuild                                     # noqa: E402
from view.main_window import MainWindow                               # noqa: E402
from view.picking import _project                                     # noqa: E402

FAILED = []
MM = 1e-3
EXAMPLE = os.path.join(ROOT, "examples", "patch_antenna.part.toml")


def check(label, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {label} {detail}")
    if not ok:
        FAILED.append(label)


def bodies(part):
    result = rebuild(part)
    return {name: (kernel.volume(shape), kernel.bounds(shape)) for name, shape in result.shapes.items()}


def same_bodies(a, b):
    return a.keys() == b.keys() and all(math.isclose(a[n][0], b[n][0], rel_tol=1e-9)
                                        and np.allclose(a[n][1], b[n][1], atol=1e-9) for n in a)


def check_model():
    part = load(EXAMPLE)
    top = part.feature("TopSketch")
    labels = lambda names: [s[0] for s in top.formula_slots(names)]     # noqa: E731
    check("a point: its x and y", labels(["P2"]) == ["P2.x", "P2.y"], f"{labels(['P2'])}")
    check("a line: its end points' formulas", sorted(labels(["L2"])) == ["P2.x", "P2.y", "P3.x", "P3.y"],
          f"{labels(['L2'])}")
    feed = labels(["Feed.1"])
    check("a polyline's segment: the whole polyline (points, widths, default width, bend radius); "
          "plain numbers (F1.x = 0, F3 none: sub_w/2) not listed",
          "F1.x" not in feed and {"F1.y", "F2.y", "F3.x", "F3.y", "Feed.width", "Feed.width[F2]",
                                  "Feed.radius[F2]"} <= set(feed) and "Feed.radius[F1]" not in feed, f"{feed}")
    # 4 patch corners x 2, the feed: 5 widths / radius, F1.y, F2.y, F3.x, F3.y (F1.x is a plain 0)
    check("the whole sketch: every formula once", len(labels(None)) == len(set(labels(None))) == 4 * 2 + 5 + 4,
          f"{len(labels(None))} {labels(None)}")

    before = bodies(part)
    values = rebuild(part).values
    done, failed = top.deparametrize(values)
    check("deparametrized the whole sketch: no formula left, nothing failed",
          not top.formula_slots() and not failed and len(done) == 17, f"{done} {failed}")
    check("  plain numbers of the values", top.point("P2").x == "8" and top.point("F3").x == "20"
          and top.polyline("Feed").radii[1] == "6", f"{top.point('P2')} {top.polyline('Feed').radii}")
    check("  the same bodies, exactly", same_bodies(before, bodies(part)))
    part.parameter("patch_w").formula = "20"
    part.parameter("sub_w").formula = "50"
    after = bodies(part)
    check("  patch_w no longer moves the patch; sub_w still moves the substrate (another sketch)",
          math.isclose(after["Copper"][0], before["Copper"][0], rel_tol=1e-9)
          and math.isclose(after["Substrate"][1][1][0], 25, abs_tol=1e-9), f"{after}")
    check("  the file round-trips", from_data(tomllib.loads(dumps(part))).to_dict() == part.to_dict())

    part = load(EXAMPLE)
    top = part.feature("TopSketch")
    top.point("P2").x = "nope / 2"
    done, failed = top.deparametrize(rebuild(part).values, ["P2"])
    check("a formula with an error is left and reported, the rest done",
          done == ["P2.y"] and list(failed) == ["P2.x"] and top.point("P2").x == "nope / 2", f"{done} {failed}")


def deparam_button(window):
    form = window.props_form
    for i in range(form.rowCount()):
        for role in (QFormLayout.ItemRole.SpanningRole, QFormLayout.ItemRole.FieldRole):
            item = form.itemAt(i, role)
            if item is not None and isinstance(item.widget(), QPushButton) \
                    and item.widget().text().startswith("Deparametrize"):
                return item.widget()
    return None


def check_window():
    path = os.path.join(TMP, "antenna.part.toml")
    shutil.copy(EXAMPLE, path)
    window = MainWindow(path)
    window.ask_save_changes = lambda: "discard"
    errors = []
    window.show_error = errors.append
    window.resize(1200, 800)
    window.show()
    shield(window)
    settle(300)
    view, native = window.view, window.view.canvas.native
    window.edit_sketch("TopSketch")
    settle(100)
    frame = window.result.outputs["TopSketch"].frame

    def at(xy):
        world = frame.to_world(np.array([xy], float) * MM) / MM
        return _project(view._transform(), world)[0][0]

    click(native, at((8, 0)))                                # the patch's right edge, L2
    button = deparam_button(window)
    check("the patch's edge selected: Deparametrize (4)", button is not None and button.text()
          == "Deparametrize (4)" and "P2.x" in button.toolTip(), f"{button.text() if button else None}")
    drag(native, at((8, -6)), at((10, -4)))
    check("  before: its corner P2 stays when dragged", window.part.feature("TopSketch").point("P2").x
          == "patch_w/2")
    click(native, at((8, 0)))
    steps = len(window.doc._undo)
    deparam_button(window).click()
    settle()
    sketch = lambda: window.part.feature("TopSketch")       # noqa: E731
    check("  a click: P2, P3 plain numbers, one undo step, the button gone (nothing left to free)",
          (sketch().point("P2").x, sketch().point("P2").y, sketch().point("P3").x) == ("8", "-6", "8")
          and len(window.doc._undo) == steps + 1 and deparam_button(window) is None
          and "deparametrized 4" in window.statusBar().currentMessage(), window.statusBar().currentMessage())
    drag(native, at((8, -6)), at((10, -4)))
    moved = sketch().point("P2")
    check("  now P2 drags (to about (10, -4))", abs(float(moved.x) - 10) < 0.3 and abs(float(moved.y) + 4) < 0.3,
          f"{moved}")
    window.undo()
    window.undo()
    check("  Undo twice: the formulas back", (sketch().point("P2").x, sketch().point("P2").y)
          == ("patch_w/2", "-patch_l/2"))
    window.finish_sketch()
    settle()

    window.tree.setCurrentItem(window._rows[("feature", "TopSketch")])
    settle()
    button = deparam_button(window)
    check("the sketch's row: the whole sketch, Deparametrize (17)", button is not None
          and button.text() == "Deparametrize (17)" and "the whole sketch" in button.toolTip(),
          f"{button.text() if button else None}")
    window.tree.setCurrentItem(window._rows[("feature", "SubstrateSketch")])
    settle()
    window.deparametrize("SubstrateSketch")
    settle()
    window.tree.setCurrentItem(window._rows[("feature", "SubstrateSketch")])
    settle()
    check("a sketch without formulas (deparametrized): no button", deparam_button(window) is None)
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
