# check_step_export.py
#
# STEP export (geometry/step_io.py), read back by customcst's own reader
# (customcst/geometry/step_io.py read_step, run in its own process: both
# projects have a `geometry` package).
# The part: Board, a 10 x 10 x 5 block (blue), and Post, a cylinder r = 2
# by 3 on Board's top face (no color).
#   - write_step: customcst reads two bodies, Board and Post, as solids,
#     their volumes (500; 12 pi within the tessellation), Board's color
#     exactly, Post on top (z up to 8); no temporary file is left;
#   - a body of two separate solids (a join of disjoint profiles) reads back
#     as one body;
#   - the window:
#       File > Export (Ctrl+E) asks for a path and writes there;
#       with no body it says so;
#       a failed feature is mentioned (its body as it was is written);
#       STEP on Save is off at first: Save writes no STEP; on (an app
#       setting: the part stays unmodified, no undo step): Save (Ctrl+S)
#       writes name.step next to name.part.toml, without a question; a new
#       Save overwrites it (the new size read back); the setting is kept in
#       the settings file.
#
#   python checks/check_step_export.py

import json
import math
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "checks"))
sys.stdout.reconfigure(encoding="utf-8")

TMP = tempfile.mkdtemp()
SETTINGS = os.path.join(TMP, "settings.json")
os.environ["MYCAD_SETTINGS"] = SETTINGS                 # never the user's settings

from vispy import app                                                 # noqa: E402

app.use_app("pyside6")
app.use_app().create()

from vispy_errors import install as _watch_vispy                      # noqa: E402

VISPY_ERRORS = _watch_vispy()

from PySide6.QtCore import Qt                                        # noqa: E402
from PySide6.QtTest import QTest                                      # noqa: E402

from check_extrude import rect                                        # noqa: E402
from check_sketch_line import settle, shield                          # noqa: E402
from app_settings import settings_path                                # noqa: E402
from geometry.step_io import write_step                               # noqa: E402
from model.extrude import Extrude                                     # noqa: E402
from model.part import Part, color_rgb                                # noqa: E402
from model.rebuild import rebuild                                     # noqa: E402
from model.sketch import Sketch                                       # noqa: E402
from view.main_window import MainWindow                               # noqa: E402

FAILED = []
CUSTOMCST = os.path.join(os.path.dirname(ROOT), "customcst")
SQUARE = ("L1", "L2", "L3", "L4")


def check(label, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {label} {detail}")
    if not ok:
        FAILED.append(label)


def customcst_reads(path):
    """{name: {kind, volume mm^3, color, top mm}} as customcst's read_step sees the file."""
    code = (
        "import json, sys; sys.path.insert(0, '.'); from geometry.step_io import read_step; "
        f"g = read_step({path!r}); "
        "print(json.dumps({b.name: {'kind': b.kind, 'volume': b.volume / 1e-9, 'color': b.color, "
        "'top': float(b.bounds[1][2]) / 1e-3} for b in g.bodies}))"
    )
    out = subprocess.run([sys.executable, "-c", code], cwd=CUSTOMCST, capture_output=True, text=True, timeout=120)
    if out.returncode != 0:
        raise RuntimeError(out.stderr[-2000:])
    return json.loads(out.stdout.strip().splitlines()[-1])


def build(part):
    s = part.add_feature(Sketch("Sketch1", plane="XY Plane"))
    rect(s, 0, 0, 10, 10)
    part.add_feature(Extrude("Extrusion1", profiles=[SQUARE], sketch="Sketch1", distance="5", body="Board"))
    s2 = part.add_feature(Sketch("Sketch2", plane="Extrusion1:end"))
    s2.add_circle(s2.add_point("5", "5"), "2")
    part.add_feature(Extrude("Extrusion2", profiles=[("C1",)], sketch="Sketch2", distance="3", body="Post"))
    part.set_body_color("Board", "#2080e0")
    return part


def bodies_of(part):
    result = rebuild(part)
    return [(n, shape, color_rgb(part.body_color(n)) if part.body_color(n) else None)
            for n, shape in result.shapes.items()]


def check_writer():
    part = build(Part())
    path = os.path.join(TMP, "direct.step")
    write_step(path, bodies_of(part))
    got = customcst_reads(path)
    check("customcst reads two bodies, Board and Post, solids", sorted(got) == ["Board", "Post"]
          and all(b["kind"] == "solid" for b in got.values()), f"{got}")
    check("  Board: 500 mm³, blue (#2080e0) exactly", math.isclose(got["Board"]["volume"], 500, rel_tol=1e-9)
          and all(abs(a - b) < 1e-6 for a, b in zip(got["Board"]["color"], color_rgb("#2080e0"))),
          f"{got['Board']}")
    check("  Post: 12 pi (the tessellation within 1 %), no color, on top (z to 8)",
          math.isclose(got["Post"]["volume"], 12 * math.pi, rel_tol=1e-2) and got["Post"]["color"] is None
          and math.isclose(got["Post"]["top"], 8.0, abs_tol=1e-6), f"{got['Post']}")
    check("  no temporary file left", sorted(os.listdir(TMP)) == ["direct.step"], f"{os.listdir(TMP)}")

    part = Part()
    s = part.add_feature(Sketch("Sketch1", plane="XY Plane"))
    rect(s, 0, 0, 2, 2)
    s.add_circle(s.add_point("10", "10"), "1")
    part.add_feature(Extrude("Two", profiles=[SQUARE, ("C1",)], sketch="Sketch1", distance="1", body="Pads"))
    path = os.path.join(TMP, "two.step")
    write_step(path, bodies_of(part))
    got = customcst_reads(path)
    check("a body of two separate solids reads back as one body", list(got) == ["Pads"]
          and math.isclose(got["Pads"]["volume"], 4 + math.pi, rel_tol=1e-2), f"{got}")


def main():
    check_writer()
    window = MainWindow()
    asked = []
    window.ask_save_changes = lambda: "discard"
    window.ask_overwrite_changed = lambda: asked.append("overwrite") or True
    errors = []
    window.show_error = errors.append
    window.resize(1200, 800)
    window.show()
    settle(300)
    shield(window)
    native = window.view.canvas.native

    # --- Export with no body
    window.ask_export_path = lambda: os.path.join(TMP, "empty.step")
    QTest.keyClick(native, Qt.Key_E, Qt.ControlModifier)
    settle()
    check("Export (Ctrl+E) with no body: it says so, writes nothing", errors and "no body" in errors[-1]
          and not os.path.exists(os.path.join(TMP, "empty.step")), f"{errors}")

    build(window.part)
    window._commit()
    settle()
    exported = os.path.join(TMP, "exported.step")
    window.ask_export_path = lambda: exported
    QTest.keyClick(native, Qt.Key_E, Qt.ControlModifier)
    settle()
    check("Export writes where the dialog says", os.path.exists(exported)
          and sorted(customcst_reads(exported)) == ["Board", "Post"])

    window.part.feature("Sketch2").circle("C1").radius = "0"
    window._commit()
    window.export_dialog()
    check("  a failed feature is mentioned", "without the failed" in window.statusBar().currentMessage()
          and "Extrusion2" in window.statusBar().currentMessage(), window.statusBar().currentMessage())
    window.undo()

    # --- STEP on Save
    folder = os.path.join(TMP, "work")
    os.makedirs(folder)
    part_path = os.path.join(folder, "board.part.toml")
    step = os.path.join(folder, "board.step")
    window.ask_save_path = lambda: part_path
    check("STEP on Save is off at first", not window.step_on_save_action.isChecked()
          and not window.app_settings.step_on_save)
    QTest.keyClick(native, Qt.Key_S, Qt.ControlModifier)
    settle()
    check("Save (Ctrl+S): the part written, no STEP", os.path.exists(part_path) and not os.path.exists(step))
    modified, steps = window.doc.modified, len(window.doc._undo)
    window.step_on_save_action.trigger()
    with open(settings_path(), encoding="utf-8") as f:      # the helpers' imports may have redirected it
        kept = json.load(f).get("step_on_save")
    check("on: an app setting (kept in the settings file), the part not modified, no undo step",
          window.app_settings.step_on_save and kept is True and window.doc.modified == modified
          and len(window.doc._undo) == steps)
    QTest.keyClick(native, Qt.Key_S, Qt.ControlModifier)
    settle()
    check("Save: name.step next to the part", os.path.exists(step) and sorted(customcst_reads(step)) == ["Board", "Post"]
          and "board.step" in window.statusBar().currentMessage(), window.statusBar().currentMessage())
    window.part.feature("Extrusion1").values["distance"] = "7"
    window._commit()
    QTest.keyClick(native, Qt.Key_S, Qt.ControlModifier)
    settle()
    got = customcst_reads(step)
    check("a new Save overwrites it without a question (Board now 700)", math.isclose(got["Board"]["volume"], 700,
                                                                                      rel_tol=1e-9)
          and not asked, f"{got['Board']['volume']} {asked}")
    check("  Post moved up with the face (z to 10)", math.isclose(got["Post"]["top"], 10.0, abs_tol=1e-6))
    check("no other error dialogs", len(errors) == 1, f"{errors}")
    window.close()
    check("no errors in VisPy callbacks", not VISPY_ERRORS, f"{VISPY_ERRORS[:3]}")
    print("FAILED: " + ", ".join(FAILED) if FAILED else "all checks passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
