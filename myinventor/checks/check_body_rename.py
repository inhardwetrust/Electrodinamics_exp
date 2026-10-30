# check_body_rename.py
#
# Renaming a body (Part.rename_body, the browser's Rename):
#   - a board (Substrate, new) and copper made of two extrusions (new, then
#     join) and cut by a third, the copper colored: Copper renamed Patch:
#     every feature naming it, its color row; the same volumes; STEP names
#     the solid Patch; a TOML round trip;
#   - refused: an empty name, a taken one, a quote, a body that is not
#     there; the same name: nothing;
#   - the window: Rename... in the body's right-click menu, double click and
#     F2 ask for the name (ask_text replaced); one undo step; the row, the
#     selection and a hidden body's hiding follow the name; a taken name:
#     the status bar says why, nothing changes.
#
#   python checks/check_body_rename.py [--no-window]

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

from geometry import kernel                                           # noqa: E402
from geometry.step_io import export_part                              # noqa: E402
from model.extrude import Extrude                                     # noqa: E402
from model.part import Part, PartError                                # noqa: E402
from model.part_file import dumps, from_data                          # noqa: E402
from model.rebuild import rebuild                                     # noqa: E402
from model.sketch import Sketch                                       # noqa: E402

FAILED = []


def check(label, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {label} {detail}")
    if not ok:
        FAILED.append(label)


def board_part():
    """Substrate 20 x 20 x 1; Copper: 10 x 10 (new) joined with 4 x 4 beside it, minus a 2 x 2 hole: 112 - 4."""
    part = Part()
    for i, (x0, y0, w) in enumerate(((0, 0, 20), (2, 2, 10), (12, 5, 4), (5, 5, 2))):
        s = part.add_feature(Sketch(f"S{i}", plane="XY Plane"))
        p = [s.add_point(str(x), str(y)) for x, y in ((x0, y0), (x0 + w, y0), (x0 + w, y0 + w), (x0, y0 + w))]
        for a, b in zip(p, p[1:] + p[:1]):
            s.add_line(a, b)
    square = ("L1", "L2", "L3", "L4")
    part.add_feature(Extrude("Board", profiles=[square], sketch="S0", distance="1", body="Substrate"))
    part.add_feature(Extrude("Patch1", profiles=[square], sketch="S1", distance="2", body="Copper"))
    part.add_feature(Extrude("Patch2", profiles=[square], sketch="S2", distance="2", operation="join", body="Copper"))
    part.add_feature(Extrude("Hole", profiles=[square], sketch="S3", distance="2", operation="cut", body="Copper"))
    part.set_body_color("Copper", "#d4a017")
    return part


def volumes(part):
    result = rebuild(part)
    return {name: kernel.volume(shape) for name, shape in result.shapes.items()}, result


def check_model():
    part = board_part()
    before, _ = volumes(part)
    part.rename_body("Copper", "Patch")
    after, result = volumes(part)
    check("Copper renamed Patch: every feature naming it, its color; the same volumes",
          [f.values.get("body") for f in part.features[4:]] == ["Substrate", "Patch", "Patch", "Patch"]
          and part.body_color("Patch") == "#d4a017" and part.body_color("Copper") is None
          and not result.feature_errors and math.isclose(after["Patch"], before["Copper"], rel_tol=1e-12)
          and math.isclose(after["Patch"], 2 * (100 + 16 - 4), rel_tol=1e-9),
          f"{after} {result.feature_errors}")
    path = os.path.join(TMP, "board.step")
    export_part(path, part, result)
    text = open(path, encoding="utf-8", errors="replace").read()
    check("  STEP names the solid Patch (no Copper)", "'Patch'" in text and "Copper" not in text)
    check("  TOML round trip", from_data(tomllib.loads(dumps(part))).to_dict() == part.to_dict()
          and 'name = "Patch"' in dumps(part))
    for label, old, new in (("an empty name", "Patch", "  "), ("a taken name", "Patch", "Substrate"),
                            ("a quote", "Patch", "it's"), ("no such body", "Copper", "X")):
        try:
            part.rename_body(old, new)
            check(f"refused: {label}", False)
        except PartError as error:
            check(f"refused: {label}", True, str(error))
    data = part.to_dict()
    part.rename_body("Patch", " Patch ")
    check("the same name (spaces trimmed): nothing changes", part.to_dict() == data)


def check_window():
    from vispy import app

    app.use_app("pyside6")
    app.use_app().create()
    from vispy_errors import install as _watch_vispy

    vispy_errors = _watch_vispy()
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    from check_sketch_line import settle
    from view.main_window import MainWindow

    path = os.path.join(TMP, "board.part.toml")
    with open(path, "w", encoding="utf-8") as f:
        f.write(dumps(board_part()))
    window = MainWindow()
    window.ask_save_changes = lambda: "discard"
    errors = []
    window.show_error = errors.append
    asked = []
    answers = []
    window.ask_text = lambda title, label, text: (asked.append(text), answers.pop(0) if answers else None)[1]
    window.resize(1200, 800)
    window.show()
    settle(300)
    window.open_file(path)
    settle()
    row = window._rows[("body", "Copper")]
    actions = window.context_actions(row)
    check("the body's right-click menu: Rename...", [t for t, _ in actions] == ["Rename..."], f"{actions}")
    window.view.set_visible("Copper", False)
    row.setCheckState(0, Qt.Unchecked)
    window.view.select(("body", "Copper"))
    window._on_view_select(("body", "Copper"))
    steps = len(window.doc._undo)
    answers.append("Patch")
    actions[0][1]()
    settle()
    features = [f.values.get("body") for f in window.part.features[4:]]
    check("Rename... asked with the old name, renamed everywhere, one undo step",
          asked == ["Copper"] and features == ["Substrate", "Patch", "Patch", "Patch"]
          and len(window.doc._undo) == steps + 1 and ("body", "Patch") in window._rows
          and ("body", "Copper") not in window._rows, f"{asked} {features}")
    check("  the selection and the hiding follow the name", window.view.selected == ("body", "Patch")
          and window.view._visible.get("Patch") is False
          and window._rows[("body", "Patch")].checkState(0) == Qt.Unchecked)
    answers.append("Substrate")
    window._row_double_clicked(window._rows[("body", "Patch")])
    settle()
    check("double click asks too; a taken name: nothing changes, the status bar says why",
          asked[-1] == "Patch" and window.part.body_names() == ["Substrate", "Patch"]
          and "already exists" in window.statusBar().currentMessage(), window.statusBar().currentMessage())
    window.tree.setFocus()
    window.tree.setCurrentItem(window._rows[("body", "Substrate")])
    answers.append("Board body")
    QTest.keyClick(window.tree, Qt.Key_F2)
    settle()
    check("F2 on a body's row renames it", window.part.body_names() == ["Board body", "Patch"],
          f"{window.part.body_names()}")
    window.undo()
    window.undo()
    settle()
    check("undone twice: Copper again", window.part.body_names() == ["Substrate", "Copper"]
          and window.part.body_color("Copper") == "#d4a017")
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
