# check_sketch.py
#
# Sketches (model/sketch.py and the window's sketch mode):
#   - model: the frame of each origin plane (u x v = normal, to_world),
#     Sketch1 / Sketch2 names, a file round trip, a sketch on an axis or on
#     another sketch fails in the rebuild with the reason, a missing plane is
#     refused on load, a used feature cannot be deleted;
#   - the ribbon: File, 3D Model, Sketch; Sketch disabled outside sketch mode;
#   - right click on each origin plane > New Sketch: the sketch is added
#     (browser row, file), sketch mode starts (Sketch tab, grid on the plane),
#     and the camera looks at the plane: u to the right, v up the screen,
#     the normal toward the viewer; Finish Sketch leaves it;
#   - Start 2D Sketch: with a plane selected it starts there; else the
#     origin planes are shown and only planes are picked (a click on an axis
#     inside a plane picks the plane), the click starts the sketch; a row
#     selected in the browser does it too; Esc cancels;
#   - each sketch shows its origin point in 3D: hover turns it green and
#     marks its row, a click selects the sketch, hovering its row highlights
#     it; the Center Point wins where they coincide; a failed sketch shows
#     none (its row red); picking a plane ignores them;
#   - double click / Edit Sketch re-enters; Delete removes; undo of the
#     creation leaves sketch mode; New leaves it; save and open keep them.
#
#   python checks/check_sketch.py

import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

TMP = tempfile.mkdtemp()
os.environ["MYINVENTOR_SETTINGS"] = os.path.join(TMP, "settings.json")     # never the user's settings

import numpy as np                                                    # noqa: E402
from vispy import app                                                 # noqa: E402

app.use_app("pyside6")
app.use_app().create()

from PySide6.QtCore import QEvent, QPointF, Qt                      # noqa: E402
from PySide6.QtGui import QMouseEvent                                 # noqa: E402
from PySide6.QtWidgets import QApplication                            # noqa: E402
from PySide6.QtTest import QTest                                      # noqa: E402

from model.part import Part, PartError                                # noqa: E402
from model.part_file import PartFileError, dumps, from_data, load     # noqa: E402
from model.rebuild import rebuild                                     # noqa: E402
from model.sketch import Sketch                                       # noqa: E402
from view.geometry_view import HOVER_COLOR                            # noqa: E402
from view.main_window import MainWindow                               # noqa: E402
from view.picking import _project                                     # noqa: E402

FAILED = []
PLANES = ("XY Plane", "XZ Plane", "YZ Plane")


def check(label, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {label} {detail}")
    if not ok:
        FAILED.append(label)


def settle(ms=30):
    for _ in range(5):
        app.process_events()
    QTest.qWait(ms)


def check_model():
    part = Part()
    for plane in PLANES:
        part.add_feature(Sketch(part.free_feature_name("Sketch"), plane=plane))
    check("names Sketch1..3", [f.name for f in part.features] == ["Sketch1", "Sketch2", "Sketch3"])
    result = rebuild(part)
    frames = [result.outputs[f.name] for f in part.features]
    check("frames: u x v = normal = the plane's normal",
          all(np.allclose(np.cross(f.u, f.v), f.normal) for f in frames)
          and [tuple(f.normal) for f in frames] == [(0, 0, 1), (0, 1, 0), (1, 0, 0)])
    check("to_world: (x, y) along u, v", np.allclose(frames[1].to_world([[2, 3]]), [[2, 0, -3]]))

    back = from_data(__import__("tomllib").loads(dumps(part)))
    check("file round trip", back.to_dict() == part.to_dict() and back.features == part.features)

    part.add_feature(Sketch("OnAxis", plane="X Axis"))
    part.add_feature(Sketch("OnSketch", plane="Sketch1"))
    errors = rebuild(part).feature_errors
    check("a sketch on an axis fails: not a plane", "work axis" in errors.get("OnAxis", ""), f"{errors}")
    check("a sketch on a sketch fails: not a plane", "not a plane" in errors.get("OnSketch", ""), f"{errors}")
    message = None
    try:
        part.delete_feature("Sketch1")
    except PartError as error:
        message = str(error)
    check("a used feature cannot be deleted", message is not None and "OnSketch" in message, message)
    text = 'format = "myinventor-part"\nversion = 1\n[[feature]]\nname = "S"\ntype = "sketch"\nplane = "AB Plane"\n'
    try:
        from_data(__import__("tomllib").loads(text))
        refused = None
    except PartFileError as error:
        refused = str(error)
    check("a missing plane is refused on load", refused is not None and "AB Plane" in refused, refused)


def hover(native, pos):
    """A mouse move without a button (QTest.mouseMove does not reach the canvas in Qt 6)."""
    local = QPointF(float(pos[0]), float(pos[1]))
    event = QMouseEvent(QEvent.MouseMove, local, native.mapToGlobal(local), Qt.NoButton, Qt.NoButton,
                        Qt.NoModifier)
    QApplication.sendEvent(native, event)


def screen_moves(view, frame, step=5.0):
    """Screen motion (px, y down) and depth change of a step along u, v and the normal."""
    transform = view._transform()
    origin = frame.origin / 1e-3
    points = np.array([origin, origin + step * frame.u, origin + step * frame.v, origin + step * frame.normal])
    h = transform.map(points)
    xyz = h[:, :3] / h[:, 3:4]
    return xyz[1:] - xyz[0]


def check_window():
    window = MainWindow()
    window.ask_save_changes = lambda: "discard"
    errors = []
    window.show_error = errors.append
    window.resize(1200, 800)
    window.show()
    settle(300)
    view = window.view
    ribbon = window.ribbon

    titles = [ribbon.tabText(i) for i in range(ribbon.count())]
    check("ribbon tabs: File, 3D Model, Sketch", titles == ["File", "3D Model", "Sketch"], f"{titles}")
    check("  3D Model shown, Sketch disabled", ribbon.current_title() == "3D Model"
          and not ribbon.isTabEnabled(2))

    # --- right click > New Sketch on each plane
    for plane in PLANES:
        actions = dict(window.context_actions(window._rows[("datum", plane)]))
        check(f"{plane}: the menu has New Sketch", "New Sketch" in actions, f"{list(actions)}")
        actions["New Sketch"]()
        settle()
        name = window.editing
        feature = window.part.feature(name) if name else None
        check(f"{plane}: {name} created and edited", feature is not None and feature["plane"] == plane
              and ("feature", name) in window._rows)
        check(f"{plane}: Sketch tab on, grid shown", ribbon.current_title() == "Sketch" and ribbon.isTabEnabled(2)
              and view.sketch_grid.visible and view.sketch_frame is not None)
        moves = screen_moves(view, view.sketch_frame)
        u, v, n = moves
        check(f"{plane}: u right, v up, normal toward the viewer",
              u[0] > 10 and abs(u[1]) < 0.5 and v[1] < -10 and abs(v[0]) < 0.5
              and abs(n[0]) < 0.5 and abs(n[1]) < 0.5 and n[2] < 0, f"{np.round(moves, 3).tolist()}")
        window.finish_sketch_action.trigger()
        check(f"{plane}: Finish Sketch leaves it", window.editing is None and not view.sketch_grid.visible
              and ribbon.current_title() == "3D Model" and not ribbon.isTabEnabled(2))
    check("browser: Sketch1..3 under the part", [window.tree.topLevelItem(0).child(i).text(0) for i in (1, 2, 3)]
          == ["Sketch1", "Sketch2", "Sketch3"])

    # --- a sketch's origin point in 3D
    check("each sketch shows its origin point", sorted(view._sketch_points) == ["Sketch1", "Sketch2", "Sketch3"]
          and all(v.line.visible for v in view._sketch_points.values()))
    view.look_from("Top")
    settle()
    middle = _project(view._transform(), np.zeros((1, 3)))[0][0]
    native = view.canvas.native
    hover(native, middle)
    settle()
    row = window._rows[("feature", "Sketch1")]
    check("hover on the point: Sketch1 green, its row marked",
          view.hovered == ("feature", "Sketch1")
          and view._sketch_points["Sketch1"].shown_color == HOVER_COLOR
          and row.background(0).color().green() > 150, f"{view.hovered}")
    QTest.qWait(20)
    hover(native, (5, 5))
    settle()
    check("hover off: plain again", view.hovered is None and row.background(0).style() == Qt.NoBrush)
    view._click_select(view.key_at(middle))
    settle()
    check("a click selects the sketch (browser, info)", view.selected == ("feature", "Sketch1")
          and window.tree.currentItem() is row and "on XY Plane" in window.info.text(), window.info.text())
    window._row_hovered(window._rows[("feature", "Sketch2")])
    check("hovering a sketch's row highlights its point", view.hovered == ("feature", "Sketch2"))
    window._row_hovered(None)
    window._rows[("datum", "Center Point")].setCheckState(0, Qt.Checked)
    check("the Center Point wins where it coincides", view.key_at(middle) == ("datum", "Center Point"))
    window._rows[("datum", "Center Point")].setCheckState(0, Qt.Unchecked)
    window.part.add_feature(Sketch("Bad", plane="X Axis"))
    window._commit()
    check("a failed sketch: no point, its row red", "Bad" not in view._sketch_points
          and window._rows[("feature", "Bad")].foreground(0).color().red() > 150)
    window.delete_feature("Bad")
    window.tree.setCurrentItem(None)
    view.select(None)

    # --- Start 2D Sketch with a plane selected
    window.tree.setCurrentItem(window._rows[("datum", "XZ Plane")])
    window.start_sketch_action.trigger()
    check("Start 2D Sketch with XZ selected: Sketch4 on it", window.editing == "Sketch4"
          and window.part.feature("Sketch4")["plane"] == "XZ Plane")
    check("  Start 2D Sketch is off in sketch mode", not window.start_sketch_action.isEnabled())
    window.finish_sketch()

    # --- Start 2D Sketch with nothing selected: pick a plane
    window._on_view_select(None)
    view.select(None)
    window.start_sketch_action.trigger()
    check("nothing selected: waits for a plane, the planes are shown", window.picking_plane
          and all(view._datums[p].fill.visible for p in PLANES)
          and not view._datums["X Axis"].line.visible)
    view.look_from("Top")
    settle()
    on_axis = _project(view._transform(), np.array([[6.0, 0.0, 0.0]]))[0][0]
    key = view.key_at(on_axis)
    check("  a click on the X axis inside XY picks the plane", key == ("datum", "XY Plane"), f"{key}")
    middle = _project(view._transform(), np.zeros((1, 3)))[0][0]
    check("  the sketches' points are not picked meanwhile", view.key_at(middle) == ("datum", "XY Plane"),
          f"{view.key_at(middle)}")
    QTest.keyClick(view.canvas.native, Qt.Key_Escape)
    settle()
    check("  Esc cancels: planes hidden again", not window.picking_plane
          and not view._datums["XY Plane"].fill.visible and window.editing is None)
    window.start_sketch_action.trigger()
    view._click_select(view.key_at(on_axis))            # what a click does
    settle()
    check("  the click starts Sketch5 on XY", window.editing == "Sketch5" and not window.picking_plane
          and window.part.feature("Sketch5")["plane"] == "XY Plane"
          and not view._datums["XY Plane"].fill.visible)
    window.finish_sketch()
    window.start_sketch_action.trigger()
    window.tree.setCurrentItem(window._rows[("datum", "YZ Plane")])
    check("  a plane row selected in the browser starts Sketch6 on YZ", window.editing == "Sketch6"
          and window.part.feature("Sketch6")["plane"] == "YZ Plane")
    window.finish_sketch()

    # --- edit, delete, undo
    window._row_double_clicked(window._rows[("feature", "Sketch2")])
    check("double click edits Sketch2", window.editing == "Sketch2"
          and np.allclose(view.sketch_frame.normal, (0, 1, 0)))
    actions = dict(window.context_actions(window._rows[("feature", "Sketch2")]))
    check("  its menu: Delete (no Edit while editing it)", list(actions) == ["Delete"], f"{list(actions)}")
    actions["Delete"]()
    settle()
    check("Delete removes it and leaves sketch mode", "Sketch2" not in [f.name for f in window.part.features]
          and window.editing is None and ("feature", "Sketch2") not in window._rows)
    window.undo()
    check("undo brings it back", "Sketch2" in [f.name for f in window.part.features])

    window._rows[("datum", "XY Plane")]
    dict(window.context_actions(window._rows[("datum", "XY Plane")]))["New Sketch"]()
    created = window.editing
    window.undo()
    check("undo of the creation leaves sketch mode", window.editing is None
          and created not in [f.name for f in window.part.features] and not view.sketch_grid.visible)

    # --- save, open, new
    path = os.path.join(TMP, "sketches.part.toml")
    window.ask_save_path = lambda: path
    window.save()
    names = [f.name for f in load(path).features]
    check("saved and read back", names == [f.name for f in window.part.features] and len(names) == 6, f"{names}")
    window.edit_sketch("Sketch1")
    window.new_part()
    check("New leaves sketch mode", window.editing is None and not window.part.features
          and not ribbon.isTabEnabled(2))
    window.open_file(path)
    check("Open: the sketches are back in the browser",
          all(("feature", n) in window._rows for n in names))
    check("no error dialogs", not errors, f"{errors}")
    window.close()


def main():
    check_model()
    check_window()
    print("FAILED: " + ", ".join(FAILED) if FAILED else "all checks passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
