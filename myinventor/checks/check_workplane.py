# check_workplane.py
#
# Work planes (model/workplane.py): a sketch on one, a 2 x 3 rectangle
# extruded by 1, lands where the plane is:
#   - Offset f = 5 from XY: z 5..6; f = 8: it follows;
#   - Offset 2 from a box's top face (E:end at z 1): z 3..4;
#   - Offset 1 from another work plane (a chain): z 6..7;
#   - Angle 90 around Y Axis from XY: the plane x = 0 facing +x (the box
#     x 0..1, z -2..0);
#   - Angle 90 around a sketch line (A:L1, the diagonal x = y) from XY: its
#     normal (1, -1, 0) / sqrt 2, the box against it;
#   - refused: an axis not in the base (Z Axis on XY), a base that is not a
#     plane (X Axis), an offset and an axis together;
#   - the file: a TOML round trip (visible = false kept); the report;
#   - the window: Work Plane on the selected XY Plane: the panel, a preview
#     (forced, shown at the offset), Offset 7, Apply: WorkPlane1 at z 7, a
#     ticked browser row (unticking hides it, one undo step), New Sketch on
#     it lies at z 7; Edit Work Plane: Angle, Pick, a click on Y Axis,
#     30 degrees, Apply: turned 30 around Y;
#   - Three Points (model): Center Point, a sketch point (10, 0, 0), (0, 10,
#     h): through them, normal (0, -h, 10) / |..|; the sketch point moved:
#     the plane follows; three on a line: it fails; a TOML round trip;
#   - the window: Work Plane with nothing selected: the panel at once, the
#     base asked (a click on XZ Plane takes it); Three Points: Pick, three
#     clicks (a sketch point, the origin's center, a body's corner): Apply:
#     through them; Edit: the same panel, the points listed.
#
#   python checks/check_workplane.py [--no-window]

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

from geometry import kernel                                           # noqa: E402
from model.extrude import Extrude                                     # noqa: E402
from model.features import FeatureError                               # noqa: E402
from model.part import Part                                           # noqa: E402
from model.part_file import dumps, from_data                          # noqa: E402
from model.rebuild import rebuild                                     # noqa: E402
from model.sketch import Sketch, SketchFrame                          # noqa: E402
from model.workplane import WorkPlane                                 # noqa: E402

FAILED = []
RECT = ("L1", "L2", "L3", "L4")


def check(label, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {label} {detail}")
    if not ok:
        FAILED.append(label)


def box_on(part, plane, name="B"):
    """A 2 x 3 rectangle on `plane` extruded by 1: (bounds (2, 3) mm or None, the Result)."""
    s = part.add_feature(Sketch(f"S{name}", plane=plane))
    p = [s.add_point(x, y) for x, y in (("0", "0"), ("2", "0"), ("2", "3"), ("0", "3"))]
    for a, b in zip(p, p[1:] + p[:1]):
        s.add_line(a, b)
    part.add_feature(Extrude(f"E{name}", profiles=[RECT], sketch=f"S{name}", distance="1", body=name))
    result = rebuild(part)
    return (kernel.bounds(result.shapes[name]) if name in result.shapes else None), result


def check_offsets():
    part = Part()
    part.add_parameter("f", "5")
    part.add_feature(WorkPlane("WP", base="XY Plane", offset="f"))
    bounds, result = box_on(part, "WP")
    check("Offset f = 5 from XY: the box z 5..6", bounds is not None and np.allclose(bounds, [[0, 0, 5], [2, 3, 6]]),
          f"{bounds} {result.feature_errors}")
    part.parameter("f").formula = "8"
    result = rebuild(part)
    check("  f = 8: it follows (z 8..9)", np.allclose(kernel.bounds(result.shapes["B"]), [[0, 0, 8], [2, 3, 9]]))

    part = Part()
    _, _ = box_on(part, "XY Plane", "Base")                 # z 0..1: its end face at z 1
    part.add_feature(WorkPlane("WP", base="EBase:end", offset="2"))
    bounds, result = box_on(part, "WP")
    check("Offset 2 from a box's top face (EBase:end at z 1): z 3..4",
          bounds is not None and np.allclose(bounds, [[0, 0, 3], [2, 3, 4]]), f"{bounds} {result.feature_errors}")

    part = Part()
    part.add_feature(WorkPlane("WP", base="XY Plane", offset="5"))
    part.add_feature(WorkPlane("WP2", base="WP", offset="1"))
    bounds, result = box_on(part, "WP2")
    check("Offset 1 from another work plane (5 from XY): z 6..7",
          bounds is not None and np.allclose(bounds, [[0, 0, 6], [2, 3, 7]]), f"{bounds} {result.feature_errors}")


def check_angles():
    part = Part()
    part.add_feature(WorkPlane("WP", base="XY Plane", axis="Y Axis", angle="90"))
    bounds, result = box_on(part, "WP")
    normal = result.outputs["WP"].plane_frame().normal
    check("Angle 90 around Y Axis from XY: x = 0 facing +x, the box x 0..1, z -2..0",
          np.allclose(normal, [1, 0, 0]) and bounds is not None
          and np.allclose(bounds, [[0, 0, -2], [1, 3, 0]], atol=1e-9), f"{normal} {bounds}")

    part = Part()
    a = part.add_feature(Sketch("A", plane="XY Plane"))
    a.add_line(a.add_point("0", "0"), a.add_point("1", "1"))
    part.add_feature(WorkPlane("WP", base="XY Plane", axis="A:L1", angle="90"))
    bounds, result = box_on(part, "WP")
    normal = result.outputs["WP"].plane_frame().normal
    check("Angle 90 around a sketch line (A:L1, x = y) from XY: normal (1, -1, 0) / sqrt 2, A a reference",
          np.allclose(normal, np.array([1, -1, 0]) / math.sqrt(2)) and "A" in part.feature("WP").references()
          and bounds is not None and not result.feature_errors, f"{normal} {result.feature_errors}")


def check_refused():
    part = Part()
    part.add_feature(WorkPlane("WP", base="XY Plane", axis="Z Axis", angle="10"))
    error = rebuild(part).feature_errors.get("WP", "")
    check("refused: Z Axis is not in XY", "does not lie in" in error, error)
    part = Part()
    part.add_feature(WorkPlane("WP", base="X Axis", offset="1"))
    error = rebuild(part).feature_errors.get("WP", "")
    check("refused: X Axis is no plane", "not a plane" in error, error)
    try:
        WorkPlane("WP", base="XY Plane", offset="2", axis="Y Axis", angle="10")
        check("refused: an offset and an axis together", False)
    except FeatureError as error:
        check("refused: an offset and an axis together", True, str(error))


def check_points():
    part = Part()
    part.add_parameter("h", "5")
    s = part.add_feature(Sketch("A", plane="XY Plane"))
    s.add_point("10", "0", explicit=True)
    part.add_feature(WorkPlane("WP", points=["Center Point", "A:P1", ["0", "10", "h"]]))
    f = rebuild(part).outputs["WP"].plane_frame()
    want = np.array([0, -5, 10]) / math.sqrt(125)
    check("Three Points: through the center, A:P1 (10, 0, 0), (0, 10, h): x toward the second, normal (0, -h, 10)",
          np.allclose(f.origin, 0) and np.allclose(f.u, [1, 0, 0]) and np.allclose(f.normal, want)
          and "A" in part.feature("WP").references(), f"{f.normal}")
    s.point("P1").y = "10"
    f = rebuild(part).outputs["WP"].plane_frame()
    check("  A:P1 moved to (10, 10): the plane follows (x along (1, 1, 0))",
          np.allclose(f.u, np.array([1, 1, 0]) / math.sqrt(2)), f"{f.u}")
    text = dumps(part)
    check("  TOML round trip", from_data(tomllib.loads(text)).to_dict() == part.to_dict() and "points = [" in text)
    part.feature("WP").points = ["Center Point", ["1", "0", "0"], ["2", "0", "0"]]
    error = rebuild(part).feature_errors.get("WP", "")
    check("  three on a line: it fails", "one line" in error, error)


def check_points_window(window, settle):
    from check_sketch_line import click, pixel
    from model.sketch import SketchFrame

    view, native = window.view, window.view.canvas.native
    s = window.part.add_feature(Sketch("Pts", plane="XY Plane"))
    s.add_point("10", "0", explicit=True)
    from model.extrude import Extrude

    q = [s.add_point(x, y) for x, y in (("0", "20"), ("4", "20"), ("4", "24"), ("0", "24"))]
    for a, b in zip(q, q[1:] + q[:1]):
        s.add_line(a, b)
    window.part.add_feature(Extrude("Cube", profiles=[("L1", "L2", "L3", "L4")], sketch="Pts", distance="4",
                                    body="Cube"))
    window._commit()
    settle()
    view.select(None)
    window._on_view_select(None)
    window.workplane_action.trigger()
    settle()
    panel = window.workplane_panel
    check("Work Plane with nothing selected: the panel at once, the base asked (picking)",
          panel is not None and window.picking_plane and not panel.apply_button.isEnabled())
    window._plane_picked(("datum", "XZ Plane"))
    settle()
    check("  a click on XZ Plane: the base", panel.base == "XZ Plane" and not window.picking_plane
          and panel.apply_button.isEnabled(), f"{panel.base}")
    panel.kind.setCurrentText("Three Points")
    settle()
    check("  Three Points: Apply off until three are picked", not panel.apply_button.isEnabled())
    panel.pick_points.emit()
    settle()
    xy = SketchFrame(np.zeros(3), np.array([1.0, 0, 0]), np.array([0, 1.0, 0]), np.array([0, 0, 1.0]))
    top = SketchFrame(np.array([0, 0, 0.004]), np.array([1.0, 0, 0]), np.array([0, 1.0, 0]), np.array([0, 0, 1.0]))
    click(native, pixel(view, xy, (10, 0)))           # the sketch point Pts:P1
    click(native, pixel(view, xy, (0, 0)))            # the origin's center
    click(native, pixel(view, top, (4, 24)))          # the cube's top corner (4, 24, 4)
    settle()
    points = window._workplane["points"]
    check("  three clicks: a sketch point, the center, a body's corner (by its place)",
          len(points) == 3 and points[0] == "Pts:P1" and points[1] == "Center Point"
          and isinstance(points[2], list) and np.allclose([float(c) for c in points[2]], [4, 24, 4], atol=1e-6)
          and panel.apply_button.isEnabled(), f"{points}")
    panel.apply_button.click()
    settle()
    name = [f.name for f in window.part.features if f.TYPE == "workplane"][-1]
    f = window.result.outputs[name].plane_frame()
    normal = np.cross([-10, 0, 0], [-6, 24, 4])
    check("Apply: through them (origin at Pts:P1, x toward the center)",
          np.allclose(f.origin / 1e-3, [10, 0, 0]) and np.allclose(f.u, [-1, 0, 0])
          and np.allclose(f.normal, normal / np.linalg.norm(normal)), f"{f.origin} {f.normal}")
    window.start_work_plane(edit=name)
    settle()
    panel = window.workplane_panel
    check("Edit: the same panel, Three Points, the points listed", panel.is_points()
          and "P1 (Pts)" in panel.points_label.text() and panel.apply_button.isEnabled(), panel.points_label.text())
    panel.reject()
    settle()


def check_file():
    part = Part()
    part.add_feature(WorkPlane("WP", base="XY Plane", offset="3", visible=False))
    text = dumps(part)
    check("TOML round trip, visible = false kept", from_data(tomllib.loads(text)).to_dict() == part.to_dict()
          and "visible = false" in text)
    import cli

    data = cli.report(part, rebuild(part))
    check("the report: from XY Plane, offset 3 mm", "from XY Plane, offset 3 mm" in cli.text(data))


def check_window():
    from vispy import app

    app.use_app("pyside6")
    app.use_app().create()
    from vispy_errors import install as _watch_vispy

    vispy_errors = _watch_vispy()
    from PySide6.QtCore import Qt

    from check_sketch_line import click, pixel, settle, shield
    from view.main_window import MainWindow

    path = os.path.join(TMP, "empty.part.toml")
    with open(path, "w", encoding="utf-8") as f:
        f.write(dumps(Part()))
    window = MainWindow()
    window.ask_save_changes = lambda: "discard"
    errors = []
    window.show_error = errors.append
    window.resize(1200, 800)
    window.show()
    settle(300)
    shield(window)
    window.open_file(path)
    settle()
    view, native = window.view, window.view.canvas.native
    view.select(("datum", "XY Plane"))
    window.workplane_action.trigger()
    settle()
    panel = window.workplane_panel
    preview = next((d for d in view._datum_list if d.name == "WorkPlane1"), None)
    check("Work Plane on the selected XY Plane: the panel, a preview at the offset (10)",
          panel is not None and preview is not None and np.allclose(preview.origin, [0, 0, 0.010])
          and view._datum_visible.get("WorkPlane1"))
    panel.offset.setText("7")
    settle()
    preview = next(d for d in view._datum_list if d.name == "WorkPlane1")
    check("  Offset 7: the preview follows", np.allclose(preview.origin, [0, 0, 0.007]))
    panel.apply_button.click()
    settle()
    row = window._rows.get(("datum", "WorkPlane1"))
    check("Apply: WorkPlane1 at z 7, a ticked browser row", window.workplane_panel is None
          and window.part.feature("WorkPlane1")["offset"] == "7" and row is not None
          and row.checkState(0) == Qt.Checked and np.allclose(window._datum("WorkPlane1").origin, [0, 0, 0.007]))
    steps = len(window.doc._undo)
    row.setCheckState(0, Qt.Unchecked)
    settle()
    check("  unticked: hidden, one undo step", window.part.feature("WorkPlane1")["visible"] is False
          and not view._datum_visible.get("WorkPlane1") and len(window.doc._undo) == steps + 1)
    actions = dict(window.context_actions(window._rows[("datum", "WorkPlane1")]))
    check("  its menu: New Sketch, Edit Work Plane, Delete", list(actions) == ["New Sketch", "Edit Work Plane", "Delete"],
          f"{list(actions)}")
    actions["New Sketch"]()
    settle()
    frame = window.result.outputs[window.editing].frame if window.editing else None
    check("  New Sketch on it: at z 7", frame is not None and np.allclose(frame.origin, [0, 0, 0.007]))
    window.finish_sketch()
    settle()

    window.start_work_plane(edit="WorkPlane1")
    settle()
    panel = window.workplane_panel
    panel.kind.setCurrentText("Angle")
    settle()
    check("Edit Work Plane, Angle: Apply off until an axis is picked", not panel.apply_button.isEnabled())
    panel.pick_axis.emit()
    settle()
    xy = SketchFrame(np.zeros(3), np.array([1.0, 0, 0]), np.array([0, 1.0, 0]), np.array([0, 0, 1.0]))
    click(native, pixel(view, xy, (0, 5)))
    settle()
    check("  Pick, a click on Y Axis: taken", panel.axis == "Y Axis" and not view.axis_mode, f"{panel.axis}")
    panel.angle.setText("30")
    settle()
    panel.apply_button.click()
    settle()
    feature = window.part.feature("WorkPlane1")
    normal = window._datum("WorkPlane1").direction
    check("  Apply: turned 30 degrees around Y from XY (its sketch moves along)",
          (feature["axis"], feature["angle"], feature["offset"]) == ("Y Axis", "30", "0")
          and np.allclose(normal, [math.sin(math.radians(30)), 0, math.cos(math.radians(30))]),
          f"{feature.to_dict()} {normal}")
    check_points_window(window, settle)
    check("no error dialogs", not errors, f"{errors}")
    window.close()
    check("no errors in VisPy callbacks", not vispy_errors, f"{vispy_errors[:3]}")


def main():
    check_offsets()
    check_angles()
    check_refused()
    check_points()
    check_file()
    if "--no-window" not in sys.argv:
        check_window()
    print("FAILED: " + ", ".join(FAILED) if FAILED else "all checks passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
