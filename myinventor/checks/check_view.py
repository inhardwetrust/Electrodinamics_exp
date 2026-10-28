# check_view.py
#
# The window, the browser and the 3D view.
#   - a cube body (OCP -> numpy): volume, area, bounds, 6 CAD faces with
#     their triangles, 12 edges, 8 corners;
#   - the origin: 7 datums in Inventor's order, right-handed planes;
#   - a new part: the browser shows Part1 > Origin with the 7 datums; the
#     view shows the origin (not the background in the middle), hiding them
#     all leaves the background;
#   - datum picking from the top, perspective and Ortho: the middle is the
#     Center Point, a point on the X axis is the X Axis, a point inside the
#     XY square is the XY Plane, a point outside it is nothing;
#   - the mouse: hovering a datum (no button) pre-highlights it and marks
#     its row; a click selects it (tree row, info); a click on nothing clears;
#     hovering a row highlights its datum, a hidden one too;
#   - a body in front of a plane hides it (the click selects the body);
#     X-ray picks the plane through it;
#   - every standard view finds its orientation;
#   - Measure: a click near a corner picks the vertex exactly, |AB| in the
#     status bar; leaving Measure clears the points;
#   - navigation: a middle drag rotates (pivot kept) and picks nothing,
#     Ctrl + middle pans exactly with the cursor, a right drag zooms; a
#     left drag (plain, Shift, Ctrl) moves nothing;
#   - the window's size goes to the redirected settings file on close.
#
#   python checks/check_view.py

import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

SETTINGS = os.path.join(tempfile.mkdtemp(), "settings.json")
os.environ["MYINVENTOR_SETTINGS"] = SETTINGS            # never the user's settings

import numpy as np                                                    # noqa: E402
from vispy import app                                                 # noqa: E402

app.use_app("pyside6")
app.use_app().create()

sys.path.insert(0, os.path.join(ROOT, "checks"))
from vispy_errors import install as _watch_vispy  # noqa: E402

VISPY_ERRORS = _watch_vispy()

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt              # noqa: E402
from PySide6.QtGui import QMouseEvent                                 # noqa: E402
from PySide6.QtTest import QTest                                      # noqa: E402
from PySide6.QtWidgets import QApplication                            # noqa: E402

from geometry.body import Body                                        # noqa: E402
from geometry.tessellate import body_from_shape, make_box             # noqa: E402
from model.part import origin_datums                                  # noqa: E402
from model.rebuild import Result                                      # noqa: E402
from view.geometry_view import BACKGROUND, HOVER_COLOR, STANDARD_VIEWS   # noqa: E402
from view.main_window import MainWindow                               # noqa: E402
from view.picking import _project                                     # noqa: E402

MM = 1e-3
FAILED = []
ORIGIN_NAMES = ["YZ Plane", "XZ Plane", "XY Plane", "X Axis", "Y Axis", "Z Axis", "Center Point"]


def check(label, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {label} {detail}")
    if not ok:
        FAILED.append(label)


def settle(ms=50):
    for _ in range(5):
        app.process_events()
    QTest.qWait(ms)


def center_pixel(view):
    image = view.canvas.render()
    h, w = image.shape[:2]
    return image[h // 2, w // 2, :3].astype(float) / 255.0


def cube(lo=(0, 0, 0), hi=(20, 20, 20)):
    return body_from_shape("Cube", make_box(lo, hi))


def pixel_of(view, point_mm):
    xy, _ = _project(view._transform(), np.asarray(point_mm, float)[None])
    return xy[0]


def check_cube():
    body = cube()
    check("cube is a solid", body.kind == "solid")
    check("cube volume 8000 mm³", abs(body.volume / MM ** 3 - 8000.0) < 1e-6, f"{body.volume / MM ** 3:.9g}")
    check("cube area 2400 mm²", abs(body.area / MM ** 2 - 2400.0) < 1e-6, f"{body.area / MM ** 2:.9g}")
    lo, hi = body.bounds
    check("cube bounds 0..20 mm", np.allclose(lo, 0.0) and np.allclose(hi / MM, 20.0), f"{lo / MM} {hi / MM}")
    check("6 CAD faces", body.face_count == 6 and len(np.unique(body.faces)) == 6, f"{body.face_count}")
    flat = []
    for f in range(body.face_count):
        points = body.vertices[body.triangles[body.faces == f]].reshape(-1, 3)
        flat.append(int(np.sum(np.ptp(points, axis=0) < 1e-12)) == 1)
    check("each face's triangles lie in one side's plane", all(flat))
    check("12 edges, 8 corners", len(body.edges) == 12 and len(body.corners) == 8,
          f"{len(body.edges)} edges, {len(body.corners)} corners")
    try:
        Body("bad", "solid", body.vertices, body.triangles, body.normals, body.edges, faces=[0])
        check("a wrong face count is refused", False)
    except ValueError:
        check("a wrong face count is refused", True)


def check_origin():
    datums = origin_datums()
    check("origin: 7 datums in Inventor's order", [d.name for d in datums] == ORIGIN_NAMES)
    planes = {d.name: d for d in datums if d.kind == "plane"}
    normals = {name: tuple(planes[name].direction) for name in planes}
    check("plane normals +X, +Y, +Z", normals == {"YZ Plane": (1, 0, 0), "XZ Plane": (0, 1, 0), "XY Plane": (0, 0, 1)},
          f"{normals}")
    check("planes are right-handed (u x v = normal)",
          all(np.allclose(np.cross(d.u, d.v), d.direction) for d in planes.values()))


def mouse(native, kind, pos, buttons=Qt.NoButton, modifier=Qt.NoModifier, which=Qt.LeftButton):
    # QTest.mouseMove carries no pressed button in Qt 6: build the events.
    local = QPointF(float(pos[0]), float(pos[1]))
    button = Qt.NoButton if kind == QEvent.MouseMove else which
    event = QMouseEvent(kind, local, native.mapToGlobal(local), button, buttons, modifier)
    QApplication.sendEvent(native, event)


def check_window():
    window = MainWindow()
    window.ask_save_changes = lambda: "discard"          # the visibility edits leave it modified
    window.resize(1200, 800)
    window.show()
    settle(300)
    view = window.view
    native = view.canvas.native
    native.setAttribute(Qt.WA_TransparentForMouseEvents)    # the real mouse stays out (the check sends its own)

    # --- the browser of a new part
    root = window.tree.topLevelItem(0)
    origin = root.child(0)
    names = [origin.child(i).text(0) for i in range(origin.childCount())]
    check("browser: Part1 > Origin > the 7 datums",
          root.text(0) == "Part1" and origin.text(0) == "Origin" and names == ORIGIN_NAMES
          and root.childCount() == 1, f"{root.text(0)} {origin.text(0)} {names}")

    background = np.array(BACKGROUND[:3])
    check("the origin starts hidden (as in Inventor)",
          np.abs(center_pixel(view) - background).max() < 0.02
          and all(origin.child(i).checkState(0) == Qt.Unchecked for i in range(origin.childCount())))
    check("hidden datums are not picked", view.datum_at(np.array(view.canvas.size) / 2) is None)
    for i in range(origin.childCount()):
        origin.child(i).setCheckState(0, Qt.Checked)
    settle()
    check("all ticked: the origin is drawn in the middle", np.abs(center_pixel(view) - background).max() > 0.05)
    check("  and the part keeps the visibility", all(d.visible for d in window.part.origin))

    # --- datum picking
    for ortho in (False, True):
        label = "ortho" if ortho else "perspective"
        view.set_orthographic(ortho)
        view.look_from("Top")
        settle()
        got = {}
        for name, point in (("Center Point", (0, 0, 0)), ("X Axis", (6, 0, 0)), ("XY Plane", (5, 4, 0)),
                            (None, (14, 14, 0))):
            key = view.datum_at(pixel_of(view, point))
            got[point] = key[1] if key else None
        want = {(0, 0, 0): "Center Point", (6, 0, 0): "X Axis", (5, 4, 0): "XY Plane", (14, 14, 0): None}
        check(f"{label}: top view picks point, axis, plane, nothing", got == want, f"{got}")
    view.set_orthographic(False)

    # --- hover and selection with the mouse
    view.reset_view()
    settle()
    # A point inside the XY square whose pixel is clear of every axis on the screen.
    axes = [pixel_of(view, end) for d in np.eye(3) * 12 for end in (-d, d)]
    def clear_of_axes(xy):
        gaps = []
        for p, q in zip(axes[::2], axes[1::2]):
            s = np.clip(np.dot(xy - p, q - p) / np.dot(q - p, q - p), 0, 1)
            gaps.append(np.linalg.norm(p + s * (q - p) - xy))
        return min(gaps) > 20
    at = next(pixel_of(view, (x, y, 0)) for x in range(3, 10) for y in range(-9, 10)
              if clear_of_axes(pixel_of(view, (x, y, 0))))
    mouse(native, QEvent.MouseMove, at)
    settle()
    row = window._rows[("datum", "XY Plane")]
    check("hover over the XY plane: pre-highlighted",
          view.hovered == ("datum", "XY Plane")
          and np.allclose(view._datums["XY Plane"].line.color[:3], HOVER_COLOR[:3]), f"{view.hovered}")
    check("  and its row is marked", row.background(0).color().green() > 150)
    QTest.qWait(20)
    mouse(native, QEvent.MouseMove, (5, 5))
    settle()
    check("hover off: nothing highlighted", view.hovered is None and row.background(0).style() == Qt.NoBrush)

    z_axis = pixel_of(view, (0, 0, 9))
    QTest.mouseClick(native, Qt.LeftButton, Qt.NoModifier, QPoint(int(round(z_axis[0])), int(round(z_axis[1]))))
    settle()
    current = window.tree.currentItem()
    check("a click on the Z axis selects it",
          view.selected == ("datum", "Z Axis") and current is not None and current.text(0) == "Z Axis",
          f"{view.selected}")
    check("  the info shows its direction", "direction (0, 0, 1)" in window.info.text(), window.info.text())
    QTest.mouseClick(native, Qt.LeftButton, Qt.NoModifier, QPoint(5, 5))
    settle()
    check("a click on nothing clears the selection", view.selected is None and window.info.text() == "")

    window.tree.setCurrentItem(window._rows[("datum", "YZ Plane")])
    check("selecting a row selects the datum", view.selected == ("datum", "YZ Plane"))
    hidden = window._rows[("datum", "XZ Plane")]
    hidden.setCheckState(0, Qt.Unchecked)
    window._row_hovered(hidden)
    shown_while_hovered = view._datums["XZ Plane"].line.visible
    window._row_hovered(None)
    check("hovering a hidden datum's row shows it, leaving hides it again",
          shown_while_hovered and not view._datums["XZ Plane"].line.visible)
    hidden.setCheckState(0, Qt.Checked)

    # --- a body hides what is behind it
    window.show_result(Result(bodies=[cube((2, 2, 2), (8, 8, 8))]), reset_view=True)
    for datum in window.part.origin:
        window._rows[("datum", datum.name)].setCheckState(0, Qt.Checked)
    view.look_from("Top")
    settle()
    over = pixel_of(view, (5, 5, 8))
    check("a cube over the XY plane: the click selects the cube", view.key_at(over) == ("body", "Cube"),
          f"{view.key_at(over)}")
    view.set_xray(True)
    check("  X-ray: the XY plane behind it", view.key_at(over) == ("datum", "XY Plane"), f"{view.key_at(over)}")
    view.set_xray(False)
    body_rows = window.tree.topLevelItem(0).child(1)
    check("browser: Solid Bodies (1) > Cube",
          body_rows.text(0) == "Solid Bodies (1)" and body_rows.child(0).text(0) == "Cube")

    # --- standard views for every up axis (look_from raises if none fits)
    ok = True
    for up in STANDARD_VIEWS:
        view.set_up(up)
        for name in STANDARD_VIEWS[up]:
            try:
                view.look_from(name)
            except ValueError as error:
                ok = False
                print("   ", up, name, error)
    view.set_up("z")
    check("every standard view finds its orientation (up x, y, z)", ok)

    # --- Measure
    window.show_result(Result(bodies=[cube()]), reset_view=True)
    window.measure_action.trigger()
    check("Measure on", view.measure)
    for ortho in (False, True):
        view.set_orthographic(ortho)
        view.reset_view()
        settle()
        corner = np.array([20.0, 0.0, 20.0])                # visible in the default view
        pick = view.pick_at(pixel_of(view, corner) + 3)     # 3 px off: still snaps
        check(f"{'ortho' if ortho else 'perspective'}: a click near a corner picks the vertex",
              pick is not None and pick.kind == "vertex" and np.allclose(pick.point, corner), f"{pick}")
    view.set_orthographic(False)
    view.set_picks([view.pick_at(pixel_of(view, (20, 0, 20))), view.pick_at(pixel_of(view, (0, 0, 20)))])
    message = window.statusBar().currentMessage()
    check("status bar: |AB| = 20 mm", "|AB| = 20 mm" in message, message)

    camera = view.view.camera
    w, h = view.canvas.size
    start = np.array([w // 2, h // 2])

    def drag(button, modifier=Qt.NoModifier, dx=80, steps=8):
        mouse(native, QEvent.MouseButtonPress, start, button, modifier, button)
        for i in range(1, steps + 1):
            QTest.qWait(15)
            mouse(native, QEvent.MouseMove, start + (dx * i // steps, 0), button, modifier)
        mouse(native, QEvent.MouseButtonRelease, start + (dx, 0), Qt.NoButton, modifier, button)
        QTest.qWait(500)

    def unchanged(before, after):
        return all(np.allclose(before[k], after[k]) for k in before)

    view.set_picks([None, None])
    before = camera.state()
    drag(Qt.MiddleButton)
    after = camera.state()
    check("a middle drag rotates", not np.allclose(before["rotation"], after["rotation"]))
    check("  about the pivot (center unchanged)", np.allclose(before["center"], after["center"]))
    check("  and picks nothing", view.picks == [None, None])
    before = camera.state()
    drag(Qt.MiddleButton, Qt.ControlModifier)
    after = camera.state()
    moved = np.linalg.norm(np.subtract(after["center"], before["center"]))
    expected = 80 * camera.scale_factor / min(camera._viewbox.size)
    check("Ctrl + middle drag pans by the cursor's move at the pivot",
          abs(moved - expected) < 1e-6 * expected and np.allclose(before["rotation"], after["rotation"]),
          f"center moved {moved:.6g} mm, expected {expected:.6g}")
    for label, modifier in (("a left drag", Qt.NoModifier), ("a Shift + left drag", Qt.ShiftModifier),
                            ("a Ctrl + left drag", Qt.ControlModifier)):
        before = camera.state()
        drag(Qt.LeftButton, modifier)
        check(f"{label} moves nothing", unchanged(before, camera.state()) and view.picks == [None, None])
    QTest.keyPress(native, Qt.Key_Space)
    before = camera.state()
    drag(Qt.LeftButton)
    QTest.keyRelease(native, Qt.Key_Space)
    after = camera.state()
    moved = np.linalg.norm(np.subtract(after["center"], before["center"]))
    check("Space + left drag pans (a touchpad)", abs(moved - expected) < 1e-6 * expected
          and not camera.space_down, f"center moved {moved:.6g} mm")
    before = camera.state()
    mouse(native, QEvent.MouseButtonPress, start, Qt.RightButton, which=Qt.RightButton)
    for i in range(1, 9):
        QTest.qWait(15)
        mouse(native, QEvent.MouseMove, start + (0, 10 * i), Qt.RightButton)
    mouse(native, QEvent.MouseButtonRelease, start + (0, 80), which=Qt.RightButton)
    QTest.qWait(500)
    after = camera.state()
    check("a right drag zooms", after["distance"] != before["distance"]
          and np.allclose(before["rotation"], after["rotation"]), f"{before['distance']:.4g} -> {after['distance']:.4g}")

    view.reset_view()
    settle()
    QTest.mouseClick(native, Qt.LeftButton, Qt.NoModifier, QPoint(int(start[0]), int(start[1])))
    settle()
    check("Measure: a click picks point A", view.picks[0] is not None and view.picks[1] is None, f"{view.picks}")
    window.measure_action.trigger()
    check("Measure off clears the points", not view.measure and view.picks == [None, None])

    window.close()
    with open(SETTINGS, encoding="utf-8") as f:
        saved = json.load(f)
    check("closing saves the window to the redirected settings", saved.get("window", {}).get("width", 0) > 0,
          f"{saved.get('window')}")


def main():
    check_cube()
    check_origin()
    check_window()
    check("no errors in VisPy callbacks", not VISPY_ERRORS, f"{VISPY_ERRORS[:3]}")
    print("FAILED: " + ", ".join(FAILED) if FAILED else "all checks passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
