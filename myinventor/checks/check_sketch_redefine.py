# check_sketch_redefine.py
#
# A sketch as a whole: its origin on its plane, Duplicate, Redefine.
#   - origin = ["dx", "1"] (formulas, mm along the plane's axes): the whole
#     sketch moves (the pad extruded from it follows, exact bounds); the
#     default is not written; a parameter rename reaches it, it counts as a
#     user; a formula that fails fails the sketch naming "origin x";
#   - Duplicate: a copy right after the original, every entity, plane and
#     origin, a free name, independent (no extrusion uses it, changing it
#     leaves the original);
#   - Redefine: onto a face (Board:end), a work plane, back onto XY; refused
#     onto a face of a feature below it (its own extrusion), an axis, a
#     sketch;
#   - the window: Duplicate and Redefine... in a sketch's right-click menu;
#     Redefine by a click on the board's top face: the origin goes to the
#     face's center (10, 5), the pad follows; a click on a face below it is
#     refused, said why; Esc cancels; Origin X / Y under the browser move
#     the sketch (one undo step);
#   - a Redefine that breaks its feature (a cut moved off the board) is
#     allowed: the feature fails, and what depends on it (a sketch on its
#     face, a feature from that sketch) fails too, red in the browser, the
#     status bar says what broke; the rest builds; Redefine back fixes all.
#
#   python checks/check_sketch_redefine.py

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
from PySide6.QtWidgets import QFormLayout                             # noqa: E402

from geometry import kernel                                           # noqa: E402
from model.part import PartError                                      # noqa: E402
from model.part_file import dumps, from_data                          # noqa: E402
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


def rect(prefix, x0, y0, x1, y1):
    corners = ((x0, y0), (x1, y0), (x1, y1), (x0, y1))
    text = "".join(f'[[feature.point]]\nname = "{prefix}{i}"\nx = "{x}"\ny = "{y}"\n' for i, (x, y) in enumerate(corners))
    text += "".join(f'[[feature.line]]\nname = "{prefix}L{i}"\nstart = "{prefix}{i}"\nend = "{prefix}{(i + 1) % 4}"\n'
                    for i in range(4))
    return text


PART = f'''format = "mycad-part"
version = 1

[[parameter]]
name = "dx"
formula = "3"

[[feature]]
name = "Sketch1"
type = "sketch"
plane = "XY Plane"
{rect("B", 0, 0, 20, 10)}
[[feature]]
name = "Board"
type = "extrude"
sketch = "Sketch1"
profiles = [["BL0", "BL1", "BL2", "BL3"]]
distance = "2"
operation = "new"
body = "Board"

[[feature]]
name = "WorkPlane1"
type = "workplane"
base = "XY Plane"
offset = "5"

[[feature]]
name = "Sketch2"
type = "sketch"
plane = "XY Plane"
{rect("P", 2, 2, 4, 4)}
[[feature]]
name = "PadExt"
type = "extrude"
sketch = "Sketch2"
profiles = [["PL0", "PL1", "PL2", "PL3"]]
distance = "1"
operation = "new"
body = "Pad"

[[feature]]
name = "Sketch3"
type = "sketch"
plane = "XY Plane"
{rect("R", 30, 0, 32, 2)}'''


def pad_bounds(part):
    result = rebuild(part, folder=TMP)
    if "Pad" not in result.shapes:
        return None, result
    return np.asarray(kernel.bounds(result.shapes["Pad"])), result


def near(bounds, expected):
    return bounds is not None and np.allclose(bounds, expected, atol=1e-9)


def refused(fn, words):
    try:
        fn()
    except PartError as error:
        return words in str(error)
    return False


def check_model():
    part = from_data(tomllib.loads(PART))
    check("origin by default: (0, 0), not written", part.feature("Sketch2")["origin"] == ["0", "0"]
          and "origin" not in dumps(part))
    part.feature("Sketch2").values["origin"] = ["dx", "1"]
    bounds, _ = pad_bounds(part)
    check("origin [dx, 1]: the pad moves by (3, 1)", near(bounds, [[5, 3, 0], [7, 5, 1]]), f"{bounds}")
    check("  written, read back the same", 'origin = ["dx", "1"]' in dumps(part)
          and from_data(tomllib.loads(dumps(part))).to_dict() == part.to_dict())
    check("  dx's users: Sketch2", part.parameter_users("dx") == ["Sketch2"], f"{part.parameter_users('dx')}")
    part.rename_parameter("dx", "shift")
    check("  a rename reaches it", part.feature("Sketch2")["origin"] == ["shift", "1"])
    part.feature("Sketch2").values["origin"] = ["nope", "0"]
    _, result = pad_bounds(part)
    check("  a formula that fails: Sketch2 fails, naming origin x", "origin x" in result.feature_errors.get("Sketch2", ""),
          f"{result.feature_errors.get('Sketch2')}")
    part.feature("Sketch2").values["origin"] = ["0", "0"]

    copy = part.duplicate_sketch("Sketch2")
    names = [f.name for f in part.features]
    original = part.feature("Sketch2")
    check("Duplicate: Sketch4 right after Sketch2", copy == "Sketch4" and names.index(copy) == names.index("Sketch2") + 1,
          f"{names}")
    duplicate = part.feature(copy)
    same = {k: v for k, v in original.to_dict().items() if k != "name"} == \
        {k: v for k, v in duplicate.to_dict().items() if k != "name"}
    check("  every entity, its plane and origin", same)
    check("  independent: nothing uses it", part.feature_users(copy) == [])
    duplicate.points[0].x = "-5"
    check("  changing it leaves the original", original.points[0].x == "2")
    bounds, result = pad_bounds(part)
    check("  the copy builds", copy in result.outputs and not result.feature_errors, f"{result.feature_errors}")

    part.redefine_sketch("Sketch2", "Board:end")
    bounds, _ = pad_bounds(part)
    check("Redefine onto Board:end: the pad on the board (z 2..3)", near(bounds, [[2, 2, 2], [4, 4, 3]]), f"{bounds}")
    part.redefine_sketch("Sketch2", "WorkPlane1", ["1", "0"])
    bounds, _ = pad_bounds(part)
    check("  onto WorkPlane1, origin (1, 0): z 5..6, x 3..5", near(bounds, [[3, 2, 5], [5, 4, 6]]), f"{bounds}")
    check("  onto its own extrusion's face: refused (below it)",
          refused(lambda: part.redefine_sketch("Sketch2", "PadExt:end"), "not above"))
    check("  onto an axis: refused", refused(lambda: part.redefine_sketch("Sketch2", "X Axis"), "not a plane"))
    check("  onto a sketch: refused", refused(lambda: part.redefine_sketch("Sketch2", "Sketch1"), "not a plane"))
    check("  onto a sketch below: refused", refused(lambda: part.redefine_sketch("Sketch2", "Sketch3"), "not above"))
    part.redefine_sketch("Sketch2", "XY Plane")
    bounds, _ = pad_bounds(part)
    check("  back onto XY: where it was", near(bounds, [[2, 2, 0], [4, 4, 1]]), f"{bounds}")


def props(window):
    out, form = {}, window.props_form
    for i in range(form.rowCount()):
        label = form.itemAt(i, QFormLayout.ItemRole.LabelRole)
        field = form.itemAt(i, QFormLayout.ItemRole.FieldRole)
        if label is not None and field is not None:
            out[label.widget().text()] = field.widget()
    return out


def face_of(window, body, normal, level, axis=2):
    """The ("face", body, i) whose triangles all face `normal` at coordinate `axis` = level (mm)."""
    visual = window.view._bodies[body].body
    for i in np.unique(visual.faces):
        triangles = window.view.face_triangles(("face", body, int(i)))
        n = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
        n /= np.linalg.norm(n, axis=1, keepdims=True)
        if np.allclose(n, normal, atol=1e-6) and np.allclose(triangles[..., axis], level, atol=1e-6):
            return ("face", body, int(i))
    return None


def check_window():
    path = os.path.join(TMP, "redefine.part.toml")
    with open(path, "w", encoding="utf-8") as f:
        f.write(PART)
    window = MainWindow(path)
    errors = []
    window.show_error = errors.append
    window.ask_save_changes = lambda: "discard"
    window.show()
    settle(200)
    labels = [t for t, _ in window.context_actions(window._rows[("feature", "Sketch2")])]
    check("Sketch2's right-click menu: Edit, Duplicate, Redefine..., Delete",
          labels == ["Edit Sketch", "Duplicate", "Redefine...", "Delete"], f"{labels}")

    steps = len(window.doc._undo)
    dict(window.context_actions(window._rows[("feature", "Sketch2")]))["Duplicate"]()
    settle()
    check("Duplicate: Sketch4, one undo step, its row selected", "Sketch4" in [f.name for f in window.part.features]
          and len(window.doc._undo) == steps + 1 and window.tree.currentItem() is window._rows[("feature", "Sketch4")])
    check("  drawn (nothing uses it)", "Sketch4" in window.view._sketch_geometry, f"{list(window.view._sketch_geometry)}")
    window.undo()
    settle()

    top = face_of(window, "Board", [0, 0, 1], 2)
    check("the board's top face found", top is not None)
    window.start_redefine("Sketch2")
    settle()
    check("Redefine...: picking a plane", window.picking_plane and window._plane_purpose == "redefine")
    window._escape()
    settle()
    check("  Esc cancels: nothing changed", not window.picking_plane and window.part.feature("Sketch2")["plane"] == "XY Plane")

    pad_top = face_of(window, "Pad", [0, 0, 1], 1)
    window.start_redefine("Sketch2")
    settle()
    window._plane_picked(pad_top)
    settle()
    check("  a face of its own extrusion: refused, said why", window.part.feature("Sketch2")["plane"] == "XY Plane"
          and "not above" in window.statusBar().currentMessage(), window.statusBar().currentMessage())

    steps = len(window.doc._undo)
    window.start_redefine("Sketch2")
    settle()
    window._plane_picked(top)
    settle()
    sketch = window.part.feature("Sketch2")
    lo, hi = kernel.bounds(window.result.shapes["Pad"])
    check("a click on the board's top: Board:end, origin at the face's center (10, 5)",
          sketch["plane"] == "Board:end" and sketch["origin"] == ["10", "5"], f"{sketch['plane']} {sketch['origin']}")
    check("  the pad follows (x 12..14, y 7..9, z 2..3), one undo step",
          np.allclose([lo, hi], [[12, 7, 2], [14, 9, 3]], atol=1e-9) and len(window.doc._undo) == steps + 1,
          f"{lo} {hi}")

    window.select_row(("feature", "Sketch2"))
    settle()
    fields = props(window)
    check("Sketch2 selected: Origin X / Y below the browser", "Origin X:" in fields and "Origin Y:" in fields
          and fields["Origin X:"].text() == "10", f"{list(fields)}")
    field = fields["Origin X:"]
    field.setFocus()
    field.setText("dx")
    field.editingFinished.emit()
    settle()
    lo, _ = kernel.bounds(window.result.shapes["Pad"])
    check("  Origin X = dx: the pad at x 5", window.part.feature("Sketch2")["origin"] == ["dx", "5"]
          and abs(lo[0] - 5) < 1e-9, f"{lo}")

    window.start_redefine("Sketch2")
    settle()
    window._plane_picked(("datum", "WorkPlane1"))
    settle()
    lo, _ = kernel.bounds(window.result.shapes["Pad"])
    check("a click on WorkPlane1: onto it, origin (0, 0), z 5", window.part.feature("Sketch2")["plane"] == "WorkPlane1"
          and window.part.feature("Sketch2")["origin"] == ["0", "0"] and abs(lo[2] - 5) < 1e-9, f"{lo}")
    window.close()
    check("no error dialogs", not errors, f"{errors}")


BREAK = f'''format = "mycad-part"
version = 1

[[feature]]
name = "Sketch1"
type = "sketch"
plane = "XY Plane"
{rect("B", 0, 0, 20, 10)}
[[feature]]
name = "Board"
type = "extrude"
sketch = "Sketch1"
profiles = [["BL0", "BL1", "BL2", "BL3"]]
distance = "2"
operation = "new"
body = "Board"

[[feature]]
name = "WorkPlane1"
type = "workplane"
base = "XY Plane"
offset = "5"

[[feature]]
name = "SketchH"
type = "sketch"
plane = "XY Plane"
{rect("H", 8, 3, 12, 7)}
[[feature]]
name = "Hole"
type = "extrude"
sketch = "SketchH"
profiles = [["HL0", "HL1", "HL2", "HL3"]]
distance = "2"
operation = "cut"
body = "Board"

[[feature]]
name = "SketchC"
type = "sketch"
plane = "Hole:end"
{rect("C", 9, 4, 11, 6)}
[[feature]]
name = "Plug"
type = "extrude"
sketch = "SketchC"
profiles = [["CL0", "CL1", "CL2", "CL3"]]
distance = "1"
operation = "new"
body = "Plug"
'''


def check_breakage():
    """A Redefine that breaks its feature: it and all that depends on it red until fixed."""
    from view.main_window import ERROR_COLOR

    path = os.path.join(TMP, "break.part.toml")
    with open(path, "w", encoding="utf-8") as f:
        f.write(BREAK)
    window = MainWindow(path)
    errors = []
    window.show_error = errors.append
    window.ask_save_changes = lambda: "discard"
    window.show()
    settle(200)
    volume = kernel.volume(window.result.shapes["Board"])
    check("break: the part builds, the hole cut (400 - 32 = 368)", not window.result.feature_errors
          and abs(volume - 368) < 1e-6, f"{window.result.feature_errors} {volume}")

    def red(name):
        return window._rows[("feature", name)].foreground(0).color() == ERROR_COLOR

    window.redefine_sketch("SketchH", "WorkPlane1")
    settle()
    failed = window.result.feature_errors
    check("SketchH onto WorkPlane1 (allowed): the cut no longer reaches the board, Hole fails",
          window.part.feature("SketchH")["plane"] == "WorkPlane1" and "removes nothing" in failed.get("Hole", ""),
          f"{failed}")
    check("  what depends on it fails too: SketchC (on Hole:end), Plug (from SketchC)",
          "uses 'Hole'" in failed.get("SketchC", "") and "uses 'SketchC'" in failed.get("Plug", ""), f"{failed}")
    check("  the others still build (Board, SketchH, WorkPlane1)",
          not {"Sketch1", "Board", "WorkPlane1", "SketchH"} & set(failed)
          and abs(kernel.volume(window.result.shapes["Board"]) - 400) < 1e-6)
    check("  red in the browser: Hole, SketchC, Plug; not SketchH, Board",
          red("Hole") and red("SketchC") and red("Plug") and not red("SketchH") and not red("Board"))
    check("  the reason in the tooltip", "removes nothing" in window._rows[("feature", "Hole")].toolTip(0))
    message = window.statusBar().currentMessage()
    check("  the status bar says what it broke", "breaks Hole, SketchC, Plug" in message, message)
    window.redefine_sketch("SketchH", "XY Plane")
    settle()
    check("fixed (Redefine back onto XY): all built, nothing red", not window.result.feature_errors
          and not any(red(n) for n in ("Hole", "SketchC", "Plug"))
          and abs(kernel.volume(window.result.shapes["Board"]) - 368) < 1e-6, f"{window.result.feature_errors}")
    window.undo()
    settle()
    check("undo: broken again (the same three)", set(window.result.feature_errors) == {"Hole", "SketchC", "Plug"},
          f"{set(window.result.feature_errors)}")
    window.undo()
    settle()
    check("undo again: as opened", not window.result.feature_errors)
    window.close()
    check("break: no error dialogs", not errors, f"{errors}")


def main():
    check_model()
    check_window()
    check_breakage()
    check("no errors in VisPy callbacks", not VISPY_ERRORS, f"{VISPY_ERRORS[:3]}")
    print("FAILED: " + ", ".join(FAILED) if FAILED else "all checks passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
