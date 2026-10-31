# check_placed_view.py
#
# "As placed" in sketch mode (MainWindow.set_placed_view): the bodies shown
# placed, a sketch on a placed body moved with it, Project Geometry from
# the placed faces. The part:
#   Board  20 x 20 x 1.6 on XY (x, y 0..20);
#   Block  2 x 2 x 1 drawn at the origin, moved by (10, 5, 1.6) onto the
#          board's top (placement: modeling has it at the origin);
#   Target an empty sketch on the board's top (BoardExt:end).
# Checks:
#   - the check box shows in sketch mode only; off: the Block at the origin
#     (modeling), its bottom projects to x 0..2, y 0..2;
#   - on: the Block shown at x 10..12; its bottom face (as placed) projects to
#     x 10..12, y 5..7 in Target: where it sits on the board;
#   - the board moved too (by (100, 0, 0)): on, Target is shown moved with
#     it (its origin at x 100); the Block stays at world x 10..12, so its
#     bottom lands at x -90..-88, y 5..7 of Target: where it is relative to
#     the board after the placement;
#   - off again: the bodies as modeling has them; no dialogs, no VisPy errors.
#
#   python checks/check_placed_view.py

import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "checks"))
sys.stdout.reconfigure(encoding="utf-8")
TMP = tempfile.mkdtemp()
os.environ["MYCAD_SETTINGS"] = os.path.join(TMP, "settings.json")     # never the user's settings

import numpy as np                                                    # noqa: E402

from model.extrude import Extrude                                     # noqa: E402
from model.part import Part                                           # noqa: E402
from model.part_file import dumps                                     # noqa: E402
from model.sketch import Sketch                                       # noqa: E402
from model.transforms import Move                                     # noqa: E402

FAILED = []
RECT = ("L1", "L2", "L3", "L4")


def check(label, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {label} {detail}")
    if not ok:
        FAILED.append(label)


def block(part, name, x0, y0, w, h, height):
    s = part.add_feature(Sketch(name + "Sketch", plane="XY Plane"))
    p = [s.add_point(str(x), str(y)) for x, y in ((x0, y0), (x0 + w, y0), (x0 + w, y0 + h), (x0, y0 + h))]
    for a, b in zip(p, p[1:] + p[:1]):
        s.add_line(a, b)
    part.add_feature(Extrude(name + "Ext", profiles=[RECT], sketch=name + "Sketch", distance=str(height), body=name))


def the_part():
    part = Part()
    block(part, "Board", 0, 0, 20, 20, 1.6)
    block(part, "Block", 0, 0, 2, 2, 1)
    part.add_feature(Sketch("Target", plane="BoardExt:end"))
    part.add_transform(Move("PlaceBlock", targets=["Block"], offset=["10", "5", "1.6"]))
    return part


def main():
    from vispy import app

    app.use_app("pyside6")
    app.use_app().create()
    from vispy_errors import install as _watch_vispy

    vispy_errors = _watch_vispy()
    from check_sketch_line import settle, shield
    from geometry import kernel
    from geometry.project import face_of
    from model.transforms import Move as MoveTransform
    from view.main_window import MainWindow

    window = MainWindow()
    window.ask_save_changes = lambda: "discard"
    errors = []
    window.show_error = errors.append
    window.resize(1200, 800)
    window.show()
    shield(window)
    settle(300)
    path = os.path.join(TMP, "placed.part.toml")
    with open(path, "w", encoding="utf-8") as f:
        f.write(dumps(the_part()))
    window.open_file(path)
    settle()

    def shown_x(body):
        v = window.view._bodies[body].body.vertices / 1e-3
        return round(float(v[:, 0].min()), 6), round(float(v[:, 0].max()), 6)

    def bottom(shapes, z):
        shape = shapes["Block"]
        for k in range(20):
            lo, hi = kernel.bounds(face_of(shape, k))
            if abs(lo[2] - z) < 1e-9 and abs(hi[2] - z) < 1e-9:
                return k
        return None

    def projected(names):
        sketch, built = window.part.feature("Target"), window.result.outputs["Target"]
        xy = np.array([built.points[p] for l in sketch.lines if l.name in names for p in (l.start, l.end)])
        return np.round([xy.min(axis=0), xy.max(axis=0)], 9).tolist() if len(xy) else None

    check("As placed hidden outside sketch mode", not window.placed_check.isVisibleTo(window))
    window.edit_sketch("Target")
    settle()
    check("sketch mode: As placed shown, off", window.placed_check.isVisibleTo(window)
          and not window.placed_check.isChecked())
    check("  off: the Block as modeling has it (x 0..2)", shown_x("Block") == (0.0, 2.0), str(shown_x("Block")))
    names = window.project_face(("face", "Block", bottom(window._frozen.shapes, 0.0)))
    settle()
    check("  its bottom projects to x 0..2, y 0..2", projected(names) == [[0, 0], [2, 2]], str(projected(names)))
    window.undo()
    settle()

    window.placed_check.setChecked(True)
    settle()
    check("on: the Block shown placed (x 10..12)", shown_x("Block") == (10.0, 12.0), str(shown_x("Block")))
    names = window.project_face(("face", "Block", bottom(window._frozen_placed.shapes, 1.6)))
    settle()
    check("  its bottom as placed projects to x 10..12, y 5..7 of Target", projected(names) == [[10, 5], [12, 7]],
          str(projected(names)))
    window.undo()
    settle()
    window.placed_check.setChecked(False)
    window.finish_sketch()
    settle()
    check("Finish Sketch: As placed hidden again", not window.placed_check.isVisibleTo(window))

    window.part.add_transform(MoveTransform("PlaceBoard", targets=["Board"], offset=["100", "0", "0"]))
    window._commit()
    settle()
    window.edit_sketch("Target")
    window.placed_check.setChecked(True)
    settle()
    frame = window.view.sketch_frame
    check("the board moved by 100: on, Target shown moved with it (origin x 100)",
          frame is not None and np.allclose(frame.origin / 1e-3, [100, 0, 1.6]), str(frame.origin / 1e-3))
    names = window.project_face(("face", "Block", bottom(window._frozen_placed.shapes, 1.6)))
    settle()
    check("  the Block's bottom (it stays at world x 10..12) lands at x -90..-88 of Target: where it is "
          "relative to the moved board", projected(names) == [[-90, 5], [-88, 7]], str(projected(names)))
    window.placed_check.setChecked(False)
    settle()
    check("off again: the bodies as modeling has them (Board x 0..20)", shown_x("Board") == (0.0, 20.0),
          str(shown_x("Board")))
    window.finish_sketch()
    settle()
    check("no error dialogs", not errors, str(errors))
    window.close()
    check("no errors in VisPy callbacks", not vispy_errors, f"{vispy_errors[:3]}")
    print("FAILED: " + ", ".join(FAILED) if FAILED else "all checks passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
