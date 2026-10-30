# check_import_faces.py
#
# Sketches on the planar faces of an imported body, and modeling shown
# without the placement. The import: check_import's STEP (Substrate
# 10 x 10 x 1, Copper 4 x 4 x 0.5 on it, Pieces).
#   - its planar faces are named "face.<body>.<face>", each with its center
#     and outward normal: the substrate's top at (5, 5, 1), +z;
#   - a sketch on it ("Board:face.1.k"), a 2 x 2 square extruded out by 2
#     and joined: 100 + 8, up to z 3;
#   - the window, the import's group moved up by 10 (placement): shown at
#     z 10..; Start 2D Sketch with nothing selected: the bodies unplaced
#     (z 0..) while picking; a click on the substrate's top face names it
#     (sketch_connector:face.1.k), the sketch on it at z 1, the bodies still
#     unplaced while it is edited; Finish Sketch: placed again; a placed
#     body's face selected, then Start 2D Sketch: picked again (unplaced),
#     not taken as shown; Extrude: unplaced while it runs, placed after;
#   - the right-click menu in 3D: a placed body's face: pick it again; with
#     the placement gone, its face: New Sketch (on that face), Work Plane;
#     XY Plane: the same; a real right click on the face reaches the menu,
#     a right drag (zoom) does not.
#
#   python checks/check_import_faces.py [--no-window]

import math
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

from geometry import kernel                                           # noqa: E402
from model.extrude import Extrude                                     # noqa: E402
from model.rebuild import rebuild                                     # noqa: E402
from model.sketch import Sketch                                       # noqa: E402
from model.transforms import Move                                     # noqa: E402

FAILED = []


def check(label, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {label} {detail}")
    if not ok:
        FAILED.append(label)


def top_face(planes, body=1):
    """The role of the substrate's top face (+z at z 1) among an import's face planes."""
    return next(role for role, (o, n) in planes.items()
                if role.startswith(f"face.{body}.") and np.allclose(n, [0, 0, 1]) and np.isclose(o[2], 1e-3))


def check_model():
    from check_import import import_part, write_source

    folder = os.path.join(TMP, "model")
    os.makedirs(os.path.join(folder, "board.files"))
    write_source(os.path.join(folder, "board.files", "board.step"))
    part = import_part(folder)
    result = rebuild(part, folder=folder)
    planes = result.outputs["Board"].face_planes()
    role = top_face(planes)
    frame = result.outputs["Board"].face_frame(role)
    check("the import's planar faces named, the substrate's top: its center (5, 5, 1), +z",
          np.allclose(frame.origin / 1e-3, [5, 5, 1]) and np.allclose(frame.normal, [0, 0, 1])
          and len([r for r in planes if r.startswith("face.1.")]) == 6, f"{role} {frame.origin}")
    s = part.add_feature(Sketch("S", plane=f"Board:{role}"))
    p = [s.add_point(x, y) for x, y in (("-1", "-1"), ("1", "-1"), ("1", "1"), ("-1", "1"))]
    for a, b in zip(p, p[1:] + p[:1]):
        s.add_line(a, b)
    part.add_feature(Extrude("E", profiles=[("L1", "L2", "L3", "L4")], sketch="S", distance="2", operation="join",
                             body="Board:Substrate"))
    result = rebuild(part, folder=folder)
    shape = result.shapes["Board:Substrate"]
    check("a sketch on it, a square extruded out by 2 and joined: 100 + 8, up to z 3",
          not result.feature_errors and math.isclose(kernel.volume(shape), 108, rel_tol=1e-9)
          and np.isclose(kernel.bounds(shape)[1][2], 3), f"{result.feature_errors}")


def z_range(window, body):
    visual = window.view._bodies[body]
    z = visual.body.vertices[:, 2] / 1e-3
    return float(z.min()), float(z.max())


def check_window():
    from vispy import app

    app.use_app("pyside6")
    app.use_app().create()
    from vispy_errors import install as _watch_vispy

    vispy_errors = _watch_vispy()
    from check_import import write_source
    from check_sketch_line import settle
    from view.main_window import MainWindow

    source = os.path.join(TMP, "src", "sketch connector.step")
    os.makedirs(os.path.dirname(source))
    write_source(source)
    window = MainWindow()
    window.ask_save_changes = lambda: "discard"
    errors = []
    window.show_error = errors.append
    window.resize(1200, 800)
    window.show()
    settle(300)
    folder = os.path.join(TMP, "win")
    os.makedirs(folder)
    window._write(os.path.join(folder, "board.part.toml"))
    name = window.import_step(source)
    window.part.add_transform(Move("Up", targets=[f"group:{name}"], offset=["0", "0", "10"]))
    window._commit()
    settle()
    substrate = f"{name}:Substrate"
    check("the import's group moved up by 10: shown at z 10..11", np.allclose(z_range(window, substrate), (10, 11)),
          f"{z_range(window, substrate)}")

    window.view.select(None)
    window.start_sketch()
    settle()
    check("Start 2D Sketch: the bodies unplaced while picking (z 0..1)", window.picking_plane
          and np.allclose(z_range(window, substrate), (0, 1)), f"{z_range(window, substrate)}")
    faces = window.view._bodies[substrate].body.face_count
    top = [k for k in range(faces) if (window.face_reference(("face", substrate, k))[0] or "").startswith(f"{name}:face.1.")
           and np.allclose(window.view.face_triangles(("face", substrate, k))[:, :, 2], 1)]
    check("  the substrate's top face as shown names an import face", len(top) == 1, f"{top}")
    window._plane_picked(("face", substrate, top[0]))
    settle()
    sketch = window.part.feature(window.editing) if window.editing else None
    frame = window.result.outputs[window.editing].frame if sketch is not None else None
    check("  a click on it: a sketch on that face at z 1, the bodies still unplaced",
          sketch is not None and sketch["plane"].startswith(f"{name}:face.1.") and np.isclose(frame.origin[2], 1e-3)
          and np.allclose(z_range(window, substrate), (0, 1)), f"{sketch['plane'] if sketch else None}")
    edited = window.editing
    window.finish_sketch()
    settle()
    check("Finish Sketch (nothing drawn): placed again (z 10..11)", np.allclose(z_range(window, substrate), (10, 11)))
    window.edit_sketch(edited)
    s = window.part.feature(edited)
    p = [s.add_point(x, y) for x, y in (("-1", "-1"), ("1", "-1"), ("1", "1"), ("-1", "1"))]
    for a, b in zip(p, p[1:] + p[:1]):
        s.add_line(a, b)
    window._commit()
    window.finish_sketch()
    settle()
    check("Finish Sketch (a square drawn): placed again", np.allclose(z_range(window, substrate), (10, 11)))
    window.view.select(("face", substrate, top[0]))
    window.start_sketch()
    settle()
    check("a placed body's face selected, then Start 2D Sketch: picked again, unplaced (not taken as shown)",
          window.picking_plane and window.editing is None and np.allclose(z_range(window, substrate), (0, 1)))
    window._escape()
    settle()
    check("  Esc: placed again", not window.picking_plane and np.allclose(z_range(window, substrate), (10, 11)))
    window.extrude_action.trigger()
    settle()
    unplaced = np.allclose(z_range(window, substrate), (0, 1))
    if window.extrude_panel is not None:
        window.extrude_panel.reject()
    settle()
    check("Extrude: unplaced while it runs, placed after", unplaced
          and np.allclose(z_range(window, substrate), (10, 11)))
    check_context_menu(window, substrate, top[0], name)
    check("no error dialogs", not errors, f"{errors}")
    window.close()
    check("no errors in VisPy callbacks", not vispy_errors, f"{vispy_errors[:3]}")


def check_context_menu(window, substrate, top, name):
    from PySide6.QtCore import QEvent, Qt

    from check_sketch_line import pixel, send, settle, shield
    from model.sketch import SketchFrame

    key = ("face", substrate, top)
    texts = [t for t, _ in window.view_context_actions(key)]
    check("right click on a placed body's face: only picking it again (as built)",
          texts == ["New Sketch (pick the face as built)..."], f"{texts}")
    window.delete_transform("Up")
    settle()
    actions = dict(window.view_context_actions(key))
    check("  the placement gone: New Sketch, Work Plane", list(actions) == ["New Sketch", "Work Plane"],
          f"{list(actions)}")
    actions["New Sketch"]()
    settle()
    sketch = window.part.feature(window.editing) if window.editing else None
    check("  New Sketch: a sketch on that face", sketch is not None and sketch["plane"].startswith(f"{name}:face.1."))
    window.finish_sketch()
    settle()
    check("XY Plane's menu: New Sketch, Work Plane",
          [t for t, _ in window.view_context_actions(("datum", "XY Plane"))] == ["New Sketch", "Work Plane"])
    shield(window)
    seen = []
    window.view.on_context = lambda k, pos: seen.append(k)
    native = window.view.canvas.native
    top_center = SketchFrame(np.array([0, 0, 0.001]), np.array([1.0, 0, 0]), np.array([0, 1.0, 0]),
                             np.array([0, 0, 1.0]))
    at = pixel(window.view, top_center, (1, 1))          # the substrate's top beside the copper
    send(native, QEvent.MouseMove, at)
    send(native, QEvent.MouseButtonPress, at, Qt.RightButton, Qt.RightButton)
    send(native, QEvent.MouseButtonRelease, at, Qt.RightButton, Qt.NoButton)
    settle()
    check("a real right click on the substrate's top: the menu asked for that face",
          len(seen) == 1 and seen[0] is not None and seen[0][:2] == ("face", substrate), f"{seen}")
    seen.clear()
    send(native, QEvent.MouseButtonPress, at, Qt.RightButton, Qt.RightButton)
    for i in range(1, 6):
        send(native, QEvent.MouseMove, at + np.array([0, 8 * i]), Qt.NoButton, Qt.RightButton)
    send(native, QEvent.MouseButtonRelease, at + np.array([0, 40]), Qt.RightButton, Qt.NoButton)
    settle()
    check("  a right drag (zoom): no menu", not seen, f"{seen}")


def main():
    check_model()
    if "--no-window" not in sys.argv:
        check_window()
    print("FAILED: " + ", ".join(FAILED) if FAILED else "all checks passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
