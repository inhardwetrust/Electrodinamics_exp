# check_move.py
#
# Placement (model/transforms.py Move, [[transform]]) on three 2 mm cubes:
# A at x 0..2, B at 5..7, C at 10..12 (y 0..2, z 0..2):
#   - offset (1, 2, 3): A at (1, 2, 3)..(3, 4, 5);
#   - turned 90 around Z Axis, then moved by (10, 0, 0): x 8..10, y 0..2;
#   - turned 180 around a sketch line y = 5 along x ("S:L1"): y 8..10, z -2..0;
#   - a group (A, B) moved by z 10: both, the volumes kept, C stays;
#   - a body of the group after the group: it fails, naming the order;
#     add_transform puts a new one of a body before its group's; Move Up /
#     Down that would break it: refused;
#   - renaming a body / a group / a parameter renames them in the
#     transforms; the parameter's users list the transform;
#   - the file: a TOML round trip; refused: an unknown type, a name taken by
#     a feature; a missing body fails the transform;
#   - STEP: the moved bodies where they were moved; the report's line;
#   - the window: a group selected in the browser, Move: its panel takes
#     group:G, Offset Z 5: a preview of A and B moved; Apply: Placement /
#     Move1, the bodies there; C selected in 3D, Rotate: the axis asked,
#     a click on Z Axis takes it, 90, Apply: C turned; Edit; Move Up that
#     breaks the order: refused; Delete;
#   - picking targets while Move runs (clicks on Solid Bodies rows): Clear
#     empties them, a body taken, its group takes the place of the group's
#     bodies, a body of the group drops the group, a second click drops it;
#     applied with none: the transform fails (red); a new Move of a body of a
#     moved group says it goes before the group's; editing a transform
#     below the group's: a body of the group refused.
#
#   python checks/check_move.py [--no-window]

import io
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
from geometry.step_io import export_part                              # noqa: E402
from model.part import PartError                                      # noqa: E402
from model.part_file import PartFileError, dumps, from_data          # noqa: E402
from model.rebuild import rebuild                                     # noqa: E402
from model.sketch import Sketch, SketchFrame                          # noqa: E402
from model.transforms import Move                                     # noqa: E402

FAILED = []


def check(label, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {label} {detail}")
    if not ok:
        FAILED.append(label)


def boxes():
    from check_groups import three_boxes

    return three_boxes()


def placed(part, body):
    result = rebuild(part)
    return (kernel.bounds(result.shapes[body]) if body in result.shapes else None), result


def check_moves():
    part = boxes()
    part.add_transform(Move("M", targets=["A"], offset=["1", "2", "3"]))
    bounds, result = placed(part, "A")
    check("offset (1, 2, 3)", np.allclose(bounds, [[1, 2, 3], [3, 4, 5]]), f"{bounds} {result.feature_errors}")

    part = boxes()
    part.add_transform(Move("M", targets=["A"], offset=["10", "0", "0"], axis="Z Axis", angle="90"))
    bounds, _ = placed(part, "A")
    check("turned 90 around Z, then moved by x 10: x 8..10", np.allclose(bounds, [[8, 0, 0], [10, 2, 2]], atol=1e-9),
          f"{bounds}")

    part = boxes()
    s = part.add_feature(Sketch("S", plane="XY Plane"))
    s.add_line(s.add_point("0", "5"), s.add_point("1", "5"), construction=True)
    part.add_transform(Move("M", targets=["A"], axis="S:L1", angle="180"))
    bounds, result = placed(part, "A")
    check("turned 180 around a sketch line y = 5 (S:L1): y 8..10, z -2..0",
          np.allclose(bounds, [[0, 8, -2], [2, 10, 0]], atol=1e-9) and "S" in part.transform("M").references(),
          f"{bounds} {result.feature_errors}")

    part = boxes()
    part.add_to_group("G", ["A", "B"])
    part.add_transform(Move("M", targets=["group:G"], offset=["0", "0", "10"]))
    result = rebuild(part)
    check("a group moved by z 10: A and B, volumes kept, C stays",
          all(np.isclose(kernel.bounds(result.shapes[b])[0][2], 10) for b in "AB")
          and np.isclose(kernel.bounds(result.shapes["C"])[0][2], 0)
          and all(math.isclose(kernel.volume(result.shapes[b]), 8, rel_tol=1e-9) for b in "ABC"))


def check_order():
    part = boxes()
    part.add_to_group("G", ["A", "B"])
    part.transforms = [Move("Group", targets=["group:G"], offset=["0", "0", "10"]),
                       Move("Late", targets=["A"], offset=["1", "0", "0"])]
    error = rebuild(part).feature_errors.get("Late", "")
    check("a body of a group moved after the group: it fails, naming the order", "before its group" in error, error)
    part.transforms = [Move("Group", targets=["group:G"], offset=["0", "0", "10"])]
    at = part.add_transform(Move("Pin", targets=["A"], offset=["1", "0", "0"]))
    part.add_transform(Move("Other", targets=["C"], offset=["0", "1", "0"]))
    check("add_transform: a body of the group before the group's, another one last",
          at == 0 and [t.name for t in part.transforms] == ["Pin", "Group", "Other"])
    result = rebuild(part)
    check("  A: its own move, then the group's (1, 0, 10)", np.allclose(kernel.bounds(result.shapes["A"])[0], [1, 0, 10]))
    try:
        part.move_transform("Pin", 1)
        check("Move Down past its group: refused", False)
    except PartError as error:
        check("Move Down past its group: refused", True, str(error))
    part.move_transform("Other", -1)
    check("  Move Up of another one: done", [t.name for t in part.transforms] == ["Pin", "Other", "Group"])


def check_renames_and_file():
    part = boxes()
    part.add_parameter("d", "4")
    part.add_to_group("G", ["A"])
    part.add_transform(Move("M", targets=["group:G", "B"], offset=["d", "0", "0"]))
    part.rename_body("B", "Beta")
    part.rename_group("G", "Gamma")
    part.rename_parameter("d", "shift")
    t = part.transform("M")
    check("renaming a body, a group, a parameter renames them in the transform",
          t.targets == ["group:Gamma", "Beta"] and t["offset"][0] == "shift"
          and part.parameter_users("shift") == ["M"], f"{t.to_dict()}")
    text = dumps(part)
    check("TOML round trip", from_data(tomllib.loads(text)).to_dict() == part.to_dict() and "[[transform]]" in text)
    for label, row in (("an unknown type", 'name = "X"\ntype = "spin"\ntargets = ["A"]\n'),
                       ("a name taken by a feature", 'name = "EA"\ntype = "move"\ntargets = ["A"]\n')):
        try:
            from_data(tomllib.loads(dumps(boxes()) + "\n[[transform]]\n" + row))
            check(f"refused: {label}", False)
        except PartFileError as error:
            check(f"refused: {label}", True, str(error))
    part = boxes()
    part.add_transform(Move("M", targets=["Z"], offset=["1", "0", "0"]))
    error = rebuild(part).feature_errors.get("M", "")
    check("a missing body: the transform fails", "no body 'Z'" in error, error)

    part = boxes()
    part.add_transform(Move("M", targets=["A"], offset=["0", "0", "7"]))
    result = rebuild(part)
    path = os.path.join(TMP, "moved.step")
    export_part(path, part, result)
    from OCP.STEPControl import STEPControl_Reader

    reader = STEPControl_Reader()
    reader.ReadFile(path)
    reader.TransferRoots()
    lo, hi = kernel.bounds(reader.OneShape())
    check("STEP: the moved body where it was moved (z up to 9)", np.isclose(hi[2], 9) and np.isclose(lo[2], 0))
    import cli

    data = cli.report(part, result)
    check("the report: placement M (move A): offset (0, 0, 7) mm",
          "placement M (move A): offset (0, 0, 7) mm" in cli.text(data), cli.text(data)[-200:])


def check_window():
    from vispy import app

    app.use_app("pyside6")
    app.use_app().create()
    from vispy_errors import install as _watch_vispy

    vispy_errors = _watch_vispy()
    from check_sketch_line import click, pixel, settle, shield
    from view.main_window import MainWindow

    part = boxes()
    part.add_to_group("G", ["A", "B"])
    path = os.path.join(TMP, "boxes.part.toml")
    with open(path, "w", encoding="utf-8") as f:
        f.write(dumps(part))
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
    window.tree.setCurrentItem(window._rows[("group", "G")])
    settle()
    window.move_action.trigger()
    settle()
    panel = window.move_panel
    check("a group selected, Move: the panel takes group:G", panel is not None
          and window._moving["targets"] == ["group:G"])
    panel.offset[2].setText("5")
    settle()
    preview = view.preview_body
    zs = preview.vertices[:, 2] / 1e-3 if preview is not None else np.zeros(1)
    check("  Offset Z 5: a preview of A and B moved (z 5..7, x 0..7)", preview is not None
          and np.isclose(zs.min(), 5) and np.isclose(zs.max(), 7)
          and np.isclose(preview.vertices[:, 0].max() / 1e-3, 7))
    panel.apply_button.click()
    settle()
    row = window._rows.get(("transform", "Move1"))
    check("Apply: Placement / Move1, A and B there, the preview gone", row is not None
          and row.parent().text(0) == "Placement" and view.preview_body is None
          and np.isclose(kernel.bounds(window.result.shapes["B"])[0][2], 5))

    view.select(("body", "C"))
    window._on_view_select(("body", "C"))
    window.rotate_action.trigger()
    settle()
    panel = window.move_panel
    check("C selected, Rotate: the axis asked at once", panel is not None and view.axis_mode
          and window._moving["targets"] == ["C"] and panel.angle.text() == "90")
    xz = SketchFrame(np.zeros(3), np.array([1.0, 0, 0]), np.array([0, 0, -1.0]), np.array([0, 1.0, 0]))
    click(native, pixel(view, xz, (0, -3)))
    settle()
    check("  a click on Z Axis takes it", panel.axis == "Z Axis" and not view.axis_mode, f"{panel.axis}")
    panel.apply_button.click()
    settle()
    check("  Apply: C turned 90 around Z (x -2..0, y 10..12)",
          np.allclose(kernel.bounds(window.result.shapes["C"]), [[-2, 10, 0], [0, 12, 2]], atol=1e-9))
    window.start_move(edit="Move1")
    settle()
    window.move_panel.offset[2].setText("6")
    window.move_panel.apply_button.click()
    settle()
    check("Edit Move1: z 6", np.isclose(kernel.bounds(window.result.shapes["A"])[0][2], 6)
          and window.part.transform("Move1")["offset"][2] == "6")
    view.select(("body", "A"))
    window._on_view_select(("body", "A"))
    window.move_action.trigger()
    settle()
    window.move_panel.offset[0].setText("1")
    window.move_panel.apply_button.click()
    settle()
    names = [t.name for t in window.part.transforms]
    check("a Move of A (in G): put before G's move", names.index("Move3") < names.index("Move1"), f"{names}")
    steps = len(window.doc._undo)
    window.reorder_transform("Move3", 1)
    check("  Move Down past G's: refused (the status bar says why)", len(window.doc._undo) == steps
          and "before its group" in window.statusBar().currentMessage())
    window.delete_transform("Move3")
    settle()
    check("Delete: gone", "Move3" not in [t.name for t in window.part.transforms]
          and ("transform", "Move3") not in window._rows)
    check_picking(window, settle)
    check("no error dialogs", not errors, f"{errors}")
    window.close()
    check("no errors in VisPy callbacks", not vispy_errors, f"{vispy_errors[:3]}")


def check_picking(window, settle):
    def click(key):
        window.tree.itemClicked.emit(window._rows[key], 0)
        settle(30)

    view = window.view
    view.select(None)
    window._on_view_select(None)
    window.move_action.trigger()
    settle()
    panel = window.move_panel
    targets = lambda: window._moving["targets"]           # noqa: E731
    check("picking: Move with nothing selected opens with no targets", panel is not None and targets() == [])
    click(("body", "C"))
    check("  a click on C takes it (highlighted)", targets() == ["C"] and view.selection == [("body", "C")])
    click(("body", "A"))
    click(("group", "G"))
    check("  A, then its group G: G takes A's place", targets() == ["C", "group:G"], f"{targets()}")
    click(("body", "B"))
    check("  then B (in G): G dropped, B taken", targets() == ["C", "B"], f"{targets()}")
    click(("body", "C"))
    check("  C again: dropped", targets() == ["B"])
    check("  B is in G, moved by Move1: the panel says it goes before it", "before Move1" in panel.placed.text(),
          panel.placed.text())
    panel.cleared.emit()
    settle()
    check("  Clear: none, Apply still on", targets() == [] and panel.apply_button.isEnabled())
    panel.apply_button.click()
    settle()
    empty = window.part.transforms[-1].name
    check("  applied with none: the transform fails (red)", "no targets" in window.result.feature_errors.get(empty, ""),
          f"{window.result.feature_errors}")
    window.start_move(edit=empty)
    settle()
    click(("body", "A"))
    check("editing a transform below G's: a body of G refused, the status bar says why",
          window._moving["targets"] == [] and "before its group" in window.statusBar().currentMessage(),
          window.statusBar().currentMessage())
    click(("body", "C"))
    window.move_panel.apply_button.click()
    settle()
    check("  C taken instead, applied: no error", empty not in window.result.feature_errors
          and window.part.transform(empty).targets == ["C"])


def main():
    check_moves()
    check_order()
    check_renames_and_file()
    if "--no-window" not in sys.argv:
        check_window()
    print("FAILED: " + ", ".join(FAILED) if FAILED else "all checks passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
