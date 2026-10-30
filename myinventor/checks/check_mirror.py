# check_mirror.py
#
# Mirror (model/transforms.py, [[transform]] type = "mirror"): each target
# mirrored in place, in a plane parallel to YZ / XZ / XY through its own
# center. A is an L (a 2 mm cube at 0..2 with a 1 x 2 x 2 bar on it at
# x 0..1, y 2..4: its center of mass at x 5/6, y 5/3), B and C 2 mm cubes:
#   - YZ through A's center (x 1): its center of mass to x 7/6, its bounds,
#     volume and validity kept; XZ (y 2): y 7/3; XY: z stays (A is
#     symmetric in z); mirrored twice: back;
#   - a group (A, B) mirrored in YZ as one, through their common center
#     (x 3.5): A to x 5..7, B to 0..2;
#   - a body of the group mirrored after the group: it fails (the order);
#   - the file: a TOML round trip; a plane that is not one refused; the
#     report's line; STEP: the mirrored solid reads back with its volume;
#   - the window: a group selected, Mirror: its panel (targets group:G, the
#     plane), XZ, a preview, Apply: Placement / Mirror1, the bodies
#     mirrored; Edit: the plane filled in, YZ, Apply.
#
#   python checks/check_mirror.py [--no-window]

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
from model.extrude import Extrude                                     # noqa: E402
from model.part_file import PartFileError, dumps, from_data          # noqa: E402
from model.rebuild import rebuild                                     # noqa: E402
from model.sketch import Sketch                                       # noqa: E402
from model.transforms import Mirror                                   # noqa: E402

FAILED = []


def check(label, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {label} {detail}")
    if not ok:
        FAILED.append(label)


def l_part():
    from check_groups import three_boxes

    part = three_boxes()
    s = part.add_feature(Sketch("SX", plane="XY Plane"))
    p = [s.add_point(x, y) for x, y in (("0", "2"), ("1", "2"), ("1", "4"), ("0", "4"))]
    for a, b in zip(p, p[1:] + p[:1]):
        s.add_line(a, b)
    part.add_feature(Extrude("EX", profiles=[("L1", "L2", "L3", "L4")], sketch="SX", distance="2", operation="join",
                             body="A"))
    return part


def mirrored(part, *transforms):
    part.transforms = list(transforms)
    return rebuild(part)


def check_model():
    from OCP.BRepCheck import BRepCheck_Analyzer

    part = l_part()
    before = rebuild(part)
    c0 = kernel.centroid(before.shapes["A"])
    check("A, an L: its center of mass at (5/6, 5/3, 1)", np.allclose(c0, [5 / 6, 5 / 3, 1]), f"{c0}")
    result = mirrored(part, Mirror("M", targets=["A"], plane="YZ"))
    c = kernel.centroid(result.shapes["A"])
    check("YZ through its center (x 1): x to 7/6; bounds, volume, validity kept",
          np.allclose(c, [7 / 6, 5 / 3, 1]) and np.allclose(kernel.bounds(result.shapes["A"]), [[0, 0, 0], [2, 4, 2]])
          and math.isclose(kernel.volume(result.shapes["A"]), 12, rel_tol=1e-9)
          and BRepCheck_Analyzer(result.shapes["A"]).IsValid(), f"{c} {result.feature_errors}")
    c = kernel.centroid(mirrored(part, Mirror("M", targets=["A"], plane="XZ")).shapes["A"])
    check("XZ through its center (y 2): y to 7/3", np.allclose(c, [5 / 6, 7 / 3, 1]), f"{c}")
    c = kernel.centroid(mirrored(part, Mirror("M", targets=["A"], plane="XY")).shapes["A"])
    check("XY: z stays (A is symmetric in z)", np.allclose(c, c0), f"{c}")
    c = kernel.centroid(mirrored(part, Mirror("M", targets=["A"], plane="YZ"),
                                 Mirror("N", targets=["A"], plane="YZ")).shapes["A"])
    check("mirrored twice: back", np.allclose(c, c0), f"{c}")

    part.add_to_group("G", ["A", "B"])
    result = mirrored(part, Mirror("M", targets=["group:G"], plane="YZ"))
    check("a group (A, B) in YZ as one, through x 3.5: A to x 5..7, B to 0..2",
          np.allclose(kernel.bounds(result.shapes["A"])[:, 0], [5, 7])
          and np.allclose(kernel.bounds(result.shapes["B"])[:, 0], [0, 2])
          and np.allclose(kernel.bounds(result.shapes["C"])[:, 0], [10, 12]), f"{result.feature_errors}")
    error = mirrored(part, Mirror("M", targets=["group:G"]), Mirror("N", targets=["A"])).feature_errors.get("N", "")
    check("a body of the group mirrored after the group: it fails", "before its group" in error, error)

    part.transforms = [Mirror("M", targets=["group:G"], plane="XZ")]
    text = dumps(part)
    check("TOML round trip", from_data(tomllib.loads(text)).to_dict() == part.to_dict() and 'plane = "XZ"' in text)
    try:
        from_data(tomllib.loads(text.replace('plane = "XZ"', 'plane = "ZZ"')))
        check("refused: a plane that is not one", False)
    except PartFileError as error:
        check("refused: a plane that is not one", "plane" in str(error), str(error))
    import cli

    result = rebuild(part)
    check("the report: mirrored in XZ through its center",
          "placement M (mirror group:G): mirrored in XZ through its center" in cli.text(cli.report(part, result)))
    path = os.path.join(TMP, "mirrored.step")
    export_part(path, part, result)
    from OCP.STEPControl import STEPControl_Reader

    reader = STEPControl_Reader()
    reader.ReadFile(path)
    reader.TransferRoots()
    check("STEP: the mirrored solids read back with their volume (12 + 8 + 8)",
          math.isclose(kernel.volume(reader.OneShape()), 28, rel_tol=1e-9))


def check_window():
    from vispy import app

    app.use_app("pyside6")
    app.use_app().create()
    from vispy_errors import install as _watch_vispy

    vispy_errors = _watch_vispy()
    from check_sketch_line import settle
    from view.main_window import MainWindow

    part = l_part()
    part.add_to_group("G", ["A", "B"])
    path = os.path.join(TMP, "l.part.toml")
    with open(path, "w", encoding="utf-8") as f:
        f.write(dumps(part))
    window = MainWindow()
    window.ask_save_changes = lambda: "discard"
    errors = []
    window.show_error = errors.append
    window.resize(1200, 800)
    window.show()
    settle(300)
    window.open_file(path)
    settle()
    window.tree.setCurrentItem(window._rows[("group", "G")])
    settle()
    window.mirror_action.trigger()
    settle()
    panel = window.move_panel
    check("a group selected, Mirror: its panel takes group:G, the plane YZ", panel is not None
          and window._moving["targets"] == ["group:G"] and panel.plane.currentText() == "YZ")
    panel.plane.setCurrentText("XZ")
    settle()
    check("  XZ: a preview", window.view.preview_body is not None)
    panel.apply_button.click()
    settle()
    c = kernel.centroid(window.result.shapes["A"])
    check("Apply: Placement / Mirror1; A mirrored in XZ through the group's center (y 2): y 7/3",
          window._rows.get(("transform", "Mirror1")) is not None and np.allclose(c[1], 7 / 3), f"{c}")
    window.start_move(edit="Mirror1")
    settle()
    panel = window.move_panel
    check("Edit: the plane filled in (XZ)", panel.plane.currentText() == "XZ")
    panel.plane.setCurrentText("YZ")
    panel.apply_button.click()
    settle()
    check("  YZ: A to x 5..7", np.allclose(kernel.bounds(window.result.shapes["A"])[:, 0], [5, 7])
          and window.part.transform("Mirror1")["plane"] == "YZ")
    check("no error dialogs", not errors, f"{errors}")
    window.close()
    check("no errors in VisPy callbacks", not vispy_errors, f"{vispy_errors[:3]}")


def main():
    check_model()
    if "--no-window" not in sys.argv:
        check_window()
    print("FAILED: " + ", ".join(FAILED) if FAILED else "all checks passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
