# check_sketch_mode_3d.py
#
# 3D while a sketch is edited (MainWindow._build):
#   - a 10 x 10 rectangle extruded by 2 (200); its sketch edited and a
#     corner moved to make it 10 x 15: the sketch (and its profile) follow
#     at once, the extrusion is not rebuilt (Result.upto), the body stays
#     200 in 3D, drawn a little see-through (alpha 0.8);
#   - Save as STEP meanwhile writes the whole part (300);
#   - Finish Sketch: 3D catches up (300), opaque again;
#   - undo while editing back past the sketch's creation leaves sketch mode
#     and rebuilds it all;
#   - a line of the outline deleted, then Finish Sketch: the extrusion fails;
#     the sketch stays selected and shown (the status bar says what to do),
#     and shown still when nothing is selected.
#
#   python checks/check_sketch_mode_3d.py

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

from geometry import kernel                                           # noqa: E402
from model.extrude import Extrude                                     # noqa: E402
from model.part import Part                                           # noqa: E402
from model.part_file import dumps                                     # noqa: E402
from model.sketch import Sketch                                       # noqa: E402

FAILED = []


def check(label, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {label} {detail}")
    if not ok:
        FAILED.append(label)


def square_part():
    part = Part()
    s = part.add_feature(Sketch("S", plane="XY Plane"))
    p = [s.add_point(x, y) for x, y in (("0", "0"), ("10", "0"), ("10", "10"), ("0", "10"))]
    for a, b in zip(p, p[1:] + p[:1]):
        s.add_line(a, b)
    part.add_feature(Extrude("E", profiles=[("L1", "L2", "L3", "L4")], sketch="S", distance="2", body="B"))
    return part


def alpha(window):
    return float(window.view._bodies["B"].mesh.color.rgba[3])


def main():
    from vispy import app

    app.use_app("pyside6")
    app.use_app().create()
    from vispy_errors import install as _watch_vispy

    vispy_errors = _watch_vispy()
    from check_sketch_line import settle
    from model.profiles import find_regions
    from view.main_window import MainWindow

    path = os.path.join(TMP, "square.part.toml")
    with open(path, "w", encoding="utf-8") as f:
        f.write(dumps(square_part()))
    window = MainWindow()
    window.ask_save_changes = lambda: "discard"
    errors = []
    window.show_error = errors.append
    window.resize(1200, 800)
    window.show()
    settle(300)
    window.open_file(path)
    settle()
    check("the part: 200, opaque", math.isclose(kernel.volume(window.result.shapes["B"]), 200, rel_tol=1e-9)
          and alpha(window) == 1.0, f"{alpha(window)}")

    window.edit_sketch("S")
    settle()
    check("  editing the sketch: the bodies a little see-through (0.8)", math.isclose(alpha(window), 0.8, abs_tol=1e-6),
          f"{alpha(window)}")
    sketch = window.part.feature("S")
    for name in ("P3", "P4"):
        sketch.point(name).y = "15"
    window._commit()
    settle()
    out = window.result.outputs["S"]
    regions = find_regions(out)
    check("a corner moved: the sketch and its profile follow (150), the extrusion is not rebuilt",
          window.result.upto == "S" and [round(r.area, 9) for r in regions] == [150.0]
          and window.result.outputs["E"] is window._frozen.outputs["E"], f"{window.result.upto}")
    check("  the body in 3D stays as it was (200), see-through",
          math.isclose(kernel.volume(window.result.shapes["B"]), 200, rel_tol=1e-9)
          and math.isclose(window.view._bodies["B"].body.volume * 1e9, 200, rel_tol=1e-6)
          and math.isclose(alpha(window), 0.8, abs_tol=1e-6))
    step = os.path.join(TMP, "square.step")
    window.export_step(step)
    from OCP.STEPControl import STEPControl_Reader

    reader = STEPControl_Reader()
    reader.ReadFile(step)
    reader.TransferRoots()
    check("  STEP meanwhile: the whole part as it is now (300)",
          math.isclose(kernel.volume(reader.OneShape()), 300, rel_tol=1e-9))

    window.finish_sketch()
    settle()
    check("Finish Sketch: 3D catches up (300), opaque again",
          window.result.upto is None and math.isclose(kernel.volume(window.result.shapes["B"]), 300, rel_tol=1e-9)
          and math.isclose(window.view._bodies["B"].body.volume * 1e9, 300, rel_tol=1e-6) and alpha(window) == 1.0,
          f"{alpha(window)}")

    window.new_sketch("XZ Plane")
    settle()
    window.undo()
    settle()
    check("undo past a new sketch's creation: out of sketch mode, all rebuilt",
          window.editing is None and window.result.upto is None and "Sketch1" not in window.result.outputs
          and alpha(window) == 1.0)
    window.edit_sketch("S")
    settle()
    window.part.feature("S").delete("L1")
    window._commit()
    window.finish_sketch()
    settle()
    message = window.statusBar().currentMessage()
    check("an open outline, Finish Sketch: the extrusion fails, the sketch stays selected and shown",
          "E" in window.result.feature_errors and window.view.selected == ("feature", "S")
          and "S" in window.view._sketch_geometry and window.tree.currentItem() is window._rows[("feature", "S")]
          and "S stays shown" in message, message)
    window.view.select(None)
    window._on_view_select(None)
    settle()
    check("  nothing selected: the sketch of the failed extrusion still shows", "S" in window.view._sketch_geometry)
    window.undo()
    settle()
    check("  undone: the body back, its sketch hidden again (used, its extrusion fine)",
          not window.result.feature_errors and "S" not in window.view._sketch_geometry)
    check("no error dialogs", not errors, f"{errors}")
    window.close()
    check("no errors in VisPy callbacks", not vispy_errors, f"{vispy_errors[:3]}")
    print("FAILED: " + ", ".join(FAILED) if FAILED else "all checks passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
