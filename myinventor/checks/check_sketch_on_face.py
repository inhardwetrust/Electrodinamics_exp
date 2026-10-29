# check_sketch_on_face.py
#
# Faces of bodies: hover, selection, and sketches on flat faces by meaning.
# The part: Sketch1 on XY, a 4 x 5 rectangle (L1 bottom, L2 right, L3 top,
# L4 left), Extrusion1 by h = 3 -> Solid1.
#   - face frames (ExtrudeResult.face_frame): "end" at z = h, normal +Z;
#     "start" at z = 0, normal -Z; "side.L2" on x = 4, normal +X; u x v =
#     normal for each; a cut's walls face into the pocket, its "end" (the
#     pocket's floor) back up; a cut has no "start";
#   - a sketch on "Extrusion1:end" with a circle r = 1, joined up by 2: the
#     volume 60 + 2 pi, the boss on top (z up to h + 2); h = 4: the sketch
#     and the boss follow (z up to 6);
#   - a sketch on "Extrusion1:side.L2" with a 1 x 1 square cut 1 deep into
#     the body: 60 + 2 pi - 1 and the pocket at x 3..4;
#   - wrong references: "Extrusion1:side.L9" fails the sketch (no face),
#     "Extrusion9:end" is refused on load, a sketch on a cut's "start" fails;
#     a TOML round trip keeps the reference;
#   - the window (real mouse events): hovering the top face shows it green,
#     a click selects it (blue; the info names it Extrusion1:end); Start 2D
#     Sketch with it selected makes Sketch2 on Extrusion1:end, looking at it
#     (its normal toward the viewer); Start 2D Sketch with nothing selected,
#     then a click on the right wall: Sketch3 on Extrusion1:side.L2; a curved
#     face (a cylinder's wall) is refused with the reason.
#
#   python checks/check_sketch_on_face.py

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

from check_extrude import rect                                        # noqa: E402
from check_sketch_line import click, hover, settle, shield           # noqa: E402
from geometry import kernel                                           # noqa: E402
from model.extrude import Extrude                                     # noqa: E402
from model.part import Part                                           # noqa: E402
from model.part_file import PartFileError, dumps, from_data           # noqa: E402
from model.rebuild import rebuild                                     # noqa: E402
from model.sketch import Sketch                                       # noqa: E402
from view.main_window import MainWindow                               # noqa: E402
from view.picking import _project                                     # noqa: E402

FAILED = []
MM = 1e-3
RECT = ("L1", "L2", "L3", "L4")


def check(label, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {label} {detail}")
    if not ok:
        FAILED.append(label)


def base(h="3"):
    part = Part()
    part.add_parameter("h", h)
    s1 = part.add_feature(Sketch("Sketch1", plane="XY Plane"))
    rect(s1, 0, 0, 4, 5)
    part.add_feature(Extrude("Extrusion1", profiles=[RECT], sketch="Sketch1", distance="h", body="Solid1"))
    return part


def right_handed(frame):
    return np.allclose(np.cross(frame.u, frame.v), frame.normal)


def check_model():
    result = rebuild(base())
    built = result.outputs["Extrusion1"]
    end, start, side = (built.face_frame(r) for r in ("end", "start", "side.L2"))
    check("end: z = 3, normal +Z", np.isclose(end.origin[2], 3 * MM) and np.allclose(end.normal, (0, 0, 1)))
    check("start: z = 0, normal -Z", np.isclose(start.origin[2], 0) and np.allclose(start.normal, (0, 0, -1)))
    check("side.L2: on x = 4, normal +X", np.isclose(side.origin[0], 4 * MM) and np.allclose(side.normal, (1, 0, 0)))
    check("each frame right-handed (u x v = normal)", all(right_handed(f) for f in (end, start, side)))

    part = base()
    s2 = part.add_feature(Sketch("Sketch2", plane="Extrusion1:end"))
    s2.add_circle(s2.add_point("2", "2.5"), "1")
    part.add_feature(Extrude("Boss", profiles=[("C1",)], sketch="Sketch2", distance="2", operation="join",
                             body="Solid1"))
    result = rebuild(part)
    shape = result.shapes["Solid1"]
    top = result.bodies[0].bounds[1][2] / MM
    check("a circle on the top face joined up by 2: 60 + 2 pi, the boss on top (z to 5)",
          math.isclose(kernel.volume(shape), 60 + 2 * math.pi, rel_tol=1e-9) and np.isclose(top, 5.0),
          f"{kernel.volume(shape):.6f}, top {top}")
    part.parameter("h").formula = "4"
    result = rebuild(part)
    top = result.bodies[0].bounds[1][2] / MM
    check("h = 4: the sketch and the boss follow (z to 6)", np.isclose(top, 6.0) and not result.feature_errors,
          f"{top} {result.feature_errors}")
    part.parameter("h").formula = "3"

    s3 = part.add_feature(Sketch("Sketch3", plane="Extrusion1:side.L2"))
    rect(s3, 1, 1, 2, 2)
    part.add_feature(Extrude("Pocket", profiles=[RECT], sketch="Sketch3", distance="-1", operation="cut",
                             body="Solid1"))
    result = rebuild(part)
    shape = result.shapes["Solid1"]
    pocket = result.outputs["Pocket"]
    check("a square on the right wall cut 1 deep: 60 + 2 pi - 1",
          math.isclose(kernel.volume(shape), 59 + 2 * math.pi, rel_tol=1e-9) and not result.feature_errors,
          f"{kernel.volume(shape):.6f} {result.feature_errors}")
    floor = pocket.face_frame("end")
    check("  the pocket's floor at x = 3, facing out (+X); its walls face into it",
          np.isclose(floor.origin[0], 3 * MM) and np.allclose(floor.normal, (1, 0, 0))
          and all(right_handed(pocket.face_frame(r)) for r in pocket.face_roles()))
    try:
        pocket.face_frame("start")
        check("a cut has no start face", False)
    except ValueError as error:
        check("a cut has no start face", "opening" in str(error), str(error))

    text = dumps(part)
    check("a TOML round trip keeps the reference", from_data(tomllib.loads(text)).to_dict() == part.to_dict()
          and 'plane = "Extrusion1:end"' in text)
    bad = base()
    bad.add_feature(Sketch("Bad", plane="Extrusion1:side.L9"))
    errors = rebuild(bad).feature_errors
    check("a face that is not there fails the sketch", "side.L9" in errors.get("Bad", ""), f"{errors}")
    try:
        from_data(tomllib.loads(text.replace('plane = "Extrusion1:end"', 'plane = "Extrusion9:end"')))
        check("a reference to a feature not above is refused", False)
    except PartFileError as error:
        check("a reference to a feature not above is refused", "Extrusion9" in str(error), str(error))


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
    rect(window.part.feature("Sketch1"), 0, 0, 4, 5)
    window._commit()
    window.extrude_action.trigger()
    window.extrude_panel.distance.setText("3")
    window.extrude_panel.apply_button.click()
    settle()
    view.reset_view()
    settle()

    def at(point):
        return _project(view._transform(), np.array([point], float))[0][0]

    top = at((2, 2.5, 3))
    hover(native, top)
    settle()
    key = view.hovered
    check("hover over the top face: it shows green", key is not None and key[:2] == ("face", "Solid1")
          and view.face_hover.visible and np.allclose(view.face_triangles(key)[..., 2], 3.0), f"{key}")
    def pixel_rgb(point):
        """The rendered color at a model point (the canvas may render at a higher resolution)."""
        image = view.canvas.render()
        xy = at(point) * image.shape[1] / view.canvas.size[0]
        return image[int(round(xy[1])), int(round(xy[0])), :3].astype(float) / 255.0

    green = pixel_rgb((2, 2.5, 3))
    check("  drawn: the top face's pixels are green", green[1] > green[0] + 0.2 and green[1] > green[2] + 0.2,
          f"{np.round(green, 2)}")
    click(native, top)
    check("a click selects it (blue), the info names it Extrusion1:end", view.selected == key
          and view.face_selected.visible and "Extrusion1:end" in window.info.text(), window.info.text())
    hover(native, (5, 5))
    settle()
    blue = pixel_rgb((2, 2.5, 3))
    check("  drawn: the top face's pixels are blue", blue[2] > blue[0] + 0.2 and blue[2] > blue[1] + 0.05,
          f"{np.round(blue, 2)}")
    window.start_sketch_action.trigger()
    settle()
    check("Start 2D Sketch on it: Sketch2 on Extrusion1:end, edited",
          window.editing == "Sketch2" and window.part.feature("Sketch2")["plane"] == "Extrusion1:end")
    frame = view.sketch_frame
    center = np.asarray(view.view.camera.center, float)         # off the view's center perspective shifts it
    a, b = _project(view._transform(), np.array([center, center + 5 * frame.normal]))[0]
    check("  looking at it (its normal points at the viewer)", np.linalg.norm(b - a) < 0.5, f"{b - a}")
    window.finish_sketch()
    view.look_from("Right")                                  # the wall x = 4 faces the viewer
    settle()

    window.start_sketch_action.trigger()
    check("Start 2D Sketch with nothing selected: picking", window.picking_plane)
    click(native, at((4, 2.5, 1.5)))                         # the right wall (x = 4)
    check("a click on the right wall: Sketch3 on Extrusion1:side.L2", window.editing == "Sketch3"
          and window.part.feature("Sketch3")["plane"] == "Extrusion1:side.L2",
          f"{window.editing} {window.part.features[-1]['plane'] if window.part.features[-1].TYPE == 'sketch' else ''}")
    check("  its frame faces +X", np.allclose(view.sketch_frame.normal, (1, 0, 0)))
    window.finish_sketch()

    # a cylinder: its wall is curved
    window.new_sketch("XY Plane")
    s = window.part.feature("Sketch4")
    s.add_circle(s.add_point("10", "10"), "1")
    window._commit()
    window.finish_sketch()
    window.extrude_action.trigger()
    window.extrude_panel.operation.setCurrentIndex(0)        # a new solid
    window.view.set_profile_selection([("profile", "Sketch4", ("C1",))])
    window._profiles_picked(window.view.profile_selection)
    window.extrude_panel.distance.setText("3")
    window.extrude_panel.apply_button.click()
    settle()
    view.reset_view()
    settle()
    wall = at((10, 9, 1.5))                                  # the cylinder's front, toward -Y
    click(native, wall)
    reference, why = window.face_reference(view.selected) if view.selected else (None, "nothing")
    check("a curved face is refused with the reason", view.selected is not None and view.selected[1] == "Solid2"
          and reference is None and "curved" in why, f"{view.selected} {why}")
    check("no error dialogs", not errors, f"{errors}")
    window.close()


def check_covered_sketch():
    """
    A sketch under what was extruded from it (examples/patch_antenna: the
    patch and feed sketched on the substrate's top, the copper 0.035 above
    them): while edited it is drawn over the bodies and its curves hover and
    select; a point set by formulas does not move when dragged, and says why.
    """
    import shutil

    from check_sketch_drag import drag

    path = os.path.join(TMP, "patch_antenna.part.toml")
    shutil.copy(os.path.join(ROOT, "examples", "patch_antenna.part.toml"), path)
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

    visual = view._sketch_geometry["TopSketch"]
    check("the edited sketch on the substrate's top is drawn over the copper", visual.on_top
          and not view._sketch_geometry["SubstrateSketch"].on_top if "SubstrateSketch" in view._sketch_geometry
          else visual.on_top)
    hover(native, at((8, 0)))                                # the patch's right edge, L2, under the copper
    settle()
    check("  its line under the copper hovers", view.hovered == ("entity", "TopSketch", "L2"), f"{view.hovered}")
    click(native, at((8, 0)))
    check("  and selects", view.selected == ("entity", "TopSketch", "L2"), f"{view.selected}")
    hover(native, at((10, -14)))                             # the feed's center line
    settle()
    check("  the feed polyline too", view.hovered is not None and view.hovered[2].startswith("Feed"),
          f"{view.hovered}")
    before = window.part.feature("TopSketch").point("P2")
    x0, y0 = before.x, before.y
    drag(native, at((8, -6)), at((10, -4)))                  # P2 = (patch_w/2, -patch_l/2)
    settle()
    moved = window.part.feature("TopSketch").point("P2")
    message = window.statusBar().currentMessage()
    check("a point set by formulas does not move when dragged, and says why",
          (moved.x, moved.y) == (x0, y0) and "set by a formula" in message, message)
    window.finish_sketch()
    settle()
    shown = view._sketch_geometry.get("TopSketch")
    check("  leaving the sketch: drawn with the bodies again", shown is None or not shown.on_top,
          f"editing {view.editing_sketch}, shown {shown is not None}, "
          f"on top {None if shown is None else shown.on_top}, same visual {shown is visual}")
    check("no error dialogs (the covered sketch)", not errors, f"{errors}")
    window.close()


def main():
    check_model()
    check_window()
    check_covered_sketch()
    check("no errors in VisPy callbacks", not VISPY_ERRORS, f"{VISPY_ERRORS[:3]}")
    print("FAILED: " + ", ".join(FAILED) if FAILED else "all checks passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
