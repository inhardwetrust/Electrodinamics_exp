# check_extrude.py
#
# The first feature that makes a body: Extrude.
#   - profiles (model/profiles.py): a rectangle is one region (area 20); with
#     a diagonal, two triangles; a circle in a rectangle, a ring (the
#     rectangle with a round hole) and a disc; three nested loops; a dangling
#     line is left out; an open polyline has none; coincident ends of
#     separate chains close a loop; a construction side opens it;
#   - the kernel (OCP, exact volumes): 4 x 5 by 3 = 60; the ring
#     (20 - pi) * 3; the disc 3 pi; both fused into one solid of 60; a
#     negative distance goes the other way; on XZ the body grows along +Y;
#   - the feature: a TOML round trip (profiles as lists of names), refused
#     files (no profiles, a profile not a list); the rebuild makes Solid1
#     (its volume from the triangles agrees), the distance formula follows a
#     parameter, a broken loop fails it naming the profile;
#   - the window (real mouse events): Extrude opens the panel and profile
#     picking; a sketch with one region has it taken at once; with a ring
#     and a disc: hover shows a region (green), a click takes it (blue),
#     a second region adds, a click takes one back; Apply is off without
#     profiles or with a bad distance; Apply makes Extrusion1 and Solid1
#     (volume 60 for both), closes the panel, one undo step; the sketch
#     moves under Extrusion1 in the browser and is hidden in 3D; Solid
#     Bodies (1); Esc cancels; undo takes the body back; save and open keep it.
#
#   python checks/check_extrude.py

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

from PySide6.QtCore import Qt                                        # noqa: E402
from PySide6.QtTest import QTest                                      # noqa: E402

from check_sketch_line import click, hover, pixel, settle, shield    # noqa: E402
from geometry import kernel                                           # noqa: E402
from model.extrude import Extrude                                     # noqa: E402
from model.part import Part                                           # noqa: E402
from model.part_file import PartFileError, dumps, from_data, load     # noqa: E402
from model.profiles import find_regions                               # noqa: E402
from model.rebuild import rebuild                                     # noqa: E402
from model.sketch import Sketch                                       # noqa: E402
from view.main_window import MainWindow                               # noqa: E402

FAILED = []
MM = 1e-3


def check(label, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {label} {detail}")
    if not ok:
        FAILED.append(label)


def rect(s, x0, y0, x1, y1):
    p = [s.add_point(str(x), str(y)) for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]
    for i in range(4):
        s.add_line(p[i], p[(i + 1) % 4])
    return p


def sketch_with(build, plane="XY Plane"):
    part = Part()
    s = part.add_feature(Sketch("S", plane=plane))
    build(s)
    return part, rebuild(part).outputs["S"]


def summary(result):
    return [(r.key, round(r.area, 4), [h.names for h in r.holes]) for r in find_regions(result)]


def ring(s):
    rect(s, 0, 0, 4, 5)
    s.add_circle(s.add_point("2", "2.5"), "1")


def check_profiles():
    _, out = sketch_with(lambda s: rect(s, 0, 0, 4, 5))
    check("a rectangle: one region, area 20", summary(out) == [(("L1", "L2", "L3", "L4"), 20.0, [])], f"{summary(out)}")

    def diagonal(s):
        p = rect(s, 0, 0, 4, 5)
        s.add_line(p[0], p[2])
    _, out = sketch_with(diagonal)
    check("with a diagonal: two triangles", [r[:2] for r in summary(out)]
          == [(("L1", "L2", "L5"), 10.0), (("L3", "L4", "L5"), 10.0)], f"{summary(out)}")
    _, out = sketch_with(ring)
    got = summary(out)
    check("a circle in a rectangle: a ring with the hole, and the disc",
          got[0][0] == ("L1", "L2", "L3", "L4") and got[0][2] == [("C1",)] and got[1][0] == ("C1",), f"{got}")

    def nested(s):
        rect(s, 0, 0, 10, 10)
        rect(s, 2, 2, 8, 8)
        s.add_circle(s.add_point("5", "5"), "1")
    _, out = sketch_with(nested)
    got = summary(out)
    check("three nested loops: each the hole of the next one out", [r[2] for r in got]
          == [[("L5", "L6", "L7", "L8")], [("C1",)], []], f"{got}")

    def dangling(s):
        p = rect(s, 0, 0, 4, 5)
        s.add_line(p[2], s.add_point("9", "9"))
    _, out = sketch_with(dangling)
    check("a dangling line is left out", [r[0] for r in summary(out)] == [("L1", "L2", "L3", "L4")])

    def open_chain(s):
        a, b, c = s.add_point("0", "0"), s.add_point("1", "0"), s.add_point("1", "1")
        s.add_line(a, b)
        s.add_line(b, c)
    _, out = sketch_with(open_chain)
    check("an open polyline: none", not summary(out))

    def chains(s):
        a, b, c = s.add_point("0", "0"), s.add_point("4", "0"), s.add_point("0", "3")
        b2, c2 = s.add_point("4", "0"), s.add_point("0", "3")
        s.add_line(a, b)
        s.add_line(b2, c)
        s.add_line(c2, a)
    _, out = sketch_with(chains)
    check("coincident ends of separate chains close a loop", [r[1] for r in summary(out)] == [6.0])

    def construction(s):
        rect(s, 0, 0, 4, 5)
        s.lines[0].construction = True
    _, out = sketch_with(construction)
    check("a construction side opens it", not summary(out))


def check_kernel():
    def volume(build, pick=None, distance=3.0, plane="XY Plane"):
        _, out = sketch_with(build, plane)
        regions = find_regions(out)
        if pick is not None:
            regions = [regions[i] for i in pick]
        shape = kernel.extrude(out.frame, regions, distance)
        return kernel.volume(shape), kernel.solid_count(shape), shape

    v, n, _ = volume(lambda s: rect(s, 0, 0, 4, 5))
    check("4 x 5 by 3: 60, one solid", math.isclose(v, 60.0, rel_tol=1e-9) and n == 1, f"{v} {n}")
    v, _, _ = volume(ring, [0])
    check("the ring: (20 - pi) * 3", math.isclose(v, (20 - math.pi) * 3, rel_tol=1e-9), f"{v}")
    v, _, _ = volume(ring, [1])
    check("the disc: 3 pi", math.isclose(v, 3 * math.pi, rel_tol=1e-9), f"{v}")
    v, n, _ = volume(ring)
    check("both fused: one solid of 60", math.isclose(v, 60.0, rel_tol=1e-9) and n == 1, f"{v} {n}")
    v, _, shape = volume(lambda s: rect(s, 0, 0, 4, 5), distance=-2)
    lo, hi = kernel.body("b", shape).bounds
    check("a negative distance goes the other way (z from -2 to 0)", math.isclose(v, 40.0, rel_tol=1e-9)
          and np.allclose([lo[2], hi[2]], [-2 * MM, 0]), f"{lo} {hi}")
    _, _, shape = volume(lambda s: rect(s, 0, 0, 4, 5), plane="XZ Plane")
    lo, hi = kernel.body("b", shape).bounds
    check("on XZ the body grows along +Y (0..3)", np.allclose([lo[1], hi[1]], [0, 3 * MM]), f"{lo} {hi}")


def check_feature():
    part = Part()
    part.add_parameter("h", "3")
    s = part.add_feature(Sketch("Sketch1", plane="XY Plane"))
    rect(s, 0, 0, 4, 5)
    part.add_feature(Extrude("Extrusion1", profiles=[["L4", "L1", "L3", "L2"]], sketch="Sketch1", distance="h",
                             body="Solid1"))
    text = dumps(part)
    back = from_data(tomllib.loads(text))
    check("TOML round trip (profiles as lists of names)", back.to_dict() == part.to_dict()
          and 'profiles = [["L1", "L2", "L3", "L4"]]' in text, text.split("[[feature]]")[-1])
    for label, bad in (("no profiles", "profiles = []"), ("a profile not a list", 'profiles = ["L1"]')):
        try:
            from_data(tomllib.loads(text.replace('profiles = [["L1", "L2", "L3", "L4"]]', bad)))
            check(f"refused: {label}", False)
        except PartFileError as error:
            check(f"refused: {label}", "Extrusion1" in str(error), str(error))
    result = rebuild(part)
    body = result.bodies[0] if result.bodies else None
    check("the rebuild makes Solid1 (60 mm³, the triangles agree)", body is not None and body.name == "Solid1"
          and math.isclose(body.volume / MM ** 3, 60.0, rel_tol=1e-6), f"{result.feature_errors}")
    part.parameter("h").formula = "5"
    check("the distance follows a parameter (100 mm³)",
          math.isclose(rebuild(part).bodies[0].volume / MM ** 3, 100.0, rel_tol=1e-6))
    s.delete("L2")
    errors = rebuild(part).feature_errors
    check("a broken loop fails it, naming the profile", "L1 L2 L3 L4" in errors.get("Extrusion1", ""), f"{errors}")


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

    # --- one region: taken at once
    window.new_sketch("XY Plane")
    rect(window.part.feature("Sketch1"), 0, 0, 4, 5)
    window._commit()
    window.extrude_action.trigger()
    settle()
    panel = window.extrude_panel
    check("Extrude: the panel opens, picking profiles", panel is not None and panel.isVisible() and view.profile_mode)
    check("  the only region is taken at once, Apply on", len(view.profile_selection) == 1
          and panel.apply_button.isEnabled(), f"{view.profile_selection}")
    QTest.keyClick(native, Qt.Key_Escape)
    settle()
    check("Esc cancels: no feature, the panel closed", window.extrude_panel is None and not view.profile_mode
          and [f.name for f in window.part.features] == ["Sketch1"] and not panel.isVisible())

    # --- a ring and a disc
    window.edit_sketch("Sketch1")
    s = window.part.feature("Sketch1")
    s.add_circle(s.add_point("2", "2.5"), "1")
    window._commit()
    frame = view.sketch_frame
    window.extrude_action.trigger()
    settle()
    panel = window.extrude_panel
    check("two regions: none taken, Apply off", not view.profile_selection and not panel.apply_button.isEnabled())
    ring_key = ("profile", "Sketch1", ("L1", "L2", "L3", "L4"))
    disc_key = ("profile", "Sketch1", ("C1",))
    hover(native, pixel(view, frame, (0.5, 0.5)))
    settle()
    check("hover over the ring: it shows (green)", view.hovered == ring_key
          and view.shown_profiles.get(ring_key) == "hover", f"{view.hovered}")
    click(native, pixel(view, frame, (0.5, 0.5)))
    hover(native, pixel(view, frame, (8, 8)))                 # off the sketch
    settle()
    check("a click takes it (blue), Apply on", view.profile_selection == [ring_key]
          and view.shown_profiles.get(ring_key) == "selected" and panel.apply_button.isEnabled())
    click(native, pixel(view, frame, (2, 2.5)))
    check("a click on the disc adds it", view.profile_selection == [ring_key, disc_key]
          and "2 selected" in panel.profiles.text())
    click(native, pixel(view, frame, (2, 2.5)))
    check("a click takes it back", view.profile_selection == [ring_key])
    click(native, pixel(view, frame, (2, 2.5)))
    panel.clear_button.click()
    settle()
    check("Clear: none selected, no preview, Apply and Clear off", not view.profile_selection
          and not panel.apply_button.isEnabled() and not panel.clear_button.isEnabled()
          and all(state != "selected" for state in view.shown_profiles.values()) and view.preview_body is None)
    click(native, pixel(view, frame, (0.5, 0.5)))
    click(native, pixel(view, frame, (2, 2.5)))
    check("  picked again: both, Clear on", view.profile_selection == [ring_key, disc_key]
          and panel.clear_button.isEnabled())
    panel.distance.setText("nope")
    check("a bad distance: Apply off, the error shown", not panel.apply_button.isEnabled()
          and "nope" in panel.value.text())
    panel.distance.setText("0")
    check("  0: Apply off", not panel.apply_button.isEnabled())
    panel.distance.setText("3")
    steps = len(window.doc._undo)
    panel.apply_button.click()
    settle()
    check("Apply: Extrusion1 making Solid1, the panel closed",
          [f.name for f in window.part.features] == ["Sketch1", "Extrusion1"]
          and window.extrude_panel is None and not view.profile_mode and not panel.isVisible())
    body = window.result.bodies[0] if window.result.bodies else None
    check("  Solid1: ring + disc by 3 = 60 mm³ (one solid)", body is not None and body.name == "Solid1"
          and math.isclose(body.volume / MM ** 3, 60.0, rel_tol=1e-6)
          and kernel.solid_count(window.result.outputs["Extrusion1"].shape) == 1)
    check("  one undo step", len(window.doc._undo) == steps + 1)
    root = window.tree.topLevelItem(0)
    rows = [root.child(i).text(0) for i in range(root.childCount())]
    extrusion = window._rows[("feature", "Extrusion1")]
    check("the browser: Solid Bodies (1) first, Sketch1 under Extrusion1",
          rows == ["Solid Bodies (1)", "Origin", "Extrusion1"] and extrusion.child(0).text(0) == "Sketch1", f"{rows}")
    check("the sketch is hidden in 3D, the body shown", "Sketch1" not in view._sketch_geometry
          and "Solid1" in view._bodies)
    window.edit_sketch("Sketch1")
    check("editing the used sketch shows it again", "Sketch1" in view._sketch_geometry)
    window.finish_sketch()

    path = os.path.join(TMP, "extruded.part.toml")
    window.ask_save_path = lambda: path
    window.save()
    check("saved and read back", load(path).to_dict() == window.part.to_dict())
    window.undo()
    check("undo takes the body back", not window.result.bodies and "Sketch1" in view._sketch_geometry)
    window.open_file(path)
    check("open: the body is rebuilt", [b.name for b in window.result.bodies] == ["Solid1"])
    check("no error dialogs", not errors, f"{errors}")
    window.close()


def main():
    check_profiles()
    check_kernel()
    check_feature()
    check_window()
    check("no errors in VisPy callbacks", not VISPY_ERRORS, f"{VISPY_ERRORS[:3]}")
    print("FAILED: " + ", ".join(FAILED) if FAILED else "all checks passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
