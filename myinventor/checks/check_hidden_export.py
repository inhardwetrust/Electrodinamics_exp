# check_hidden_export.py
#
# A body's visibility is the part's (saved, undone) and STEP takes only the
# visible bodies:
#   - the document: set_body_visible, [[body]] visible = false written only
#     when hidden, read strictly, kept through a rename;
#   - export_part: the hidden ones left out (read back: only the visible
#     names), said in the note; every body hidden: an error, nothing written;
#   - the CLI: --export-step without the hidden, the report's "visible",
#     the text "hidden (not in STEP)";
#   - a hidden body is still built and still changed by later features (a
#     cut through it): only drawing and STEP skip it;
#   - the window: a body's eye off hides it (one undo step, no rebuild),
#     Export and STEP on Save leave it out, undo shows it again, a file with
#     visible = false opens with it hidden (view and eye);
#   - the browser fades the features about hidden bodies only (Part.faded_features:
#     all their bodies hidden; a sketch: all its users faded).
#
#   python checks/check_hidden_export.py

import io
import json
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

from PySide6.QtCore import Qt                                        # noqa: E402
from PySide6.QtTest import QTest                                      # noqa: E402

import cli                                                            # noqa: E402
from geometry import kernel                                           # noqa: E402
from geometry.step_io import export_part, read_bodies                 # noqa: E402
from model.part_file import PartFileError, dumps, from_data, load    # noqa: E402
from model.rebuild import rebuild                                     # noqa: E402
from view.main_window import FADED_COLOR, MainWindow                  # noqa: E402

FAILED = []


def check(label, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {label} {detail}")
    if not ok:
        FAILED.append(label)


def settle(ms=30):
    for _ in range(5):
        app.process_events()
    QTest.qWait(ms)


def rect(name, x0, y0, x1, y1):
    points = "".join(f'[[feature.point]]\nname = "{name}{i}"\nx = "{x}"\ny = "{y}"\n'
                     for i, (x, y) in enumerate(((x0, y0), (x1, y0), (x1, y1), (x0, y1))))
    lines = "".join(f'[[feature.line]]\nname = "{name}L{i}"\nstart = "{name}{i}"\nend = "{name}{(i + 1) % 4}"\n'
                    for i in range(4))
    return points + lines, [f"{name}L{i}" for i in range(4)]


A, A_LOOP = rect("A", 0, 0, 10, 10)
B, B_LOOP = rect("B", 20, 0, 30, 10)
H, H_LOOP = rect("H", 22, 2, 24, 4)

PART = f'''format = "mycad-part"
version = 1

[[feature]]
name = "Sketch1"
type = "sketch"
plane = "XY Plane"
{A}{B}
[[feature]]
name = "Sketch2"
type = "sketch"
plane = "XY Plane"
{H}
[[feature]]
name = "ExtA"
type = "extrude"
sketch = "Sketch1"
profiles = [{json.dumps(A_LOOP)}]
distance = "2"
operation = "new"
body = "Board"

[[feature]]
name = "ExtB"
type = "extrude"
sketch = "Sketch1"
profiles = [{json.dumps(B_LOOP)}]
distance = "2"
operation = "new"
body = "Shield"

[[feature]]
name = "Hole"
type = "extrude"
sketch = "Sketch2"
profiles = [{json.dumps(H_LOOP)}]
distance = "2"
operation = "cut"
body = "Shield"

[[body]]
name = "Shield"
color = "#c0c0c0"
visible = false
'''


def step_names(path):
    return sorted({row[0] for row in read_bodies(path)})


def check_document():
    part = from_data(tomllib.loads(PART))
    check("read: Shield hidden, Board shown", not part.body_visible("Shield") and part.body_visible("Board"))
    check("  written as visible = false", 'visible = false' in dumps(part)
          and from_data(tomllib.loads(dumps(part))).to_dict() == part.to_dict())
    part.set_body_visible("Board", False)
    part.set_body_visible("Board", True)
    check("shown again: nothing written for it (the default)", "Board" not in part.bodies)
    check("faded: ExtB and Hole (Shield hidden), Sketch2 (only Hole uses it); not Sketch1 (ExtA too)",
          part.faded_features() == {"ExtB", "Hole", "Sketch2"}, f"{sorted(part.faded_features())}")
    part.set_body_visible("Board", False)
    check("  Board hidden too: everything", part.faded_features() == {"Sketch1", "ExtA", "ExtB", "Sketch2", "Hole"},
          f"{sorted(part.faded_features())}")
    part.set_body_visible("Board", True)
    bad = PART.replace("visible = false", 'visible = "no"')
    try:
        from_data(tomllib.loads(bad))
        refused = False
    except PartFileError as error:
        refused = "visible" in str(error)
    check("strict reading: visible = \"no\" refused", refused)
    part.rename_body("Shield", "Can")
    check("a rename keeps it hidden (and its color)", not part.body_visible("Can") and part.body_color("Can") == "#c0c0c0")


def check_export(path):
    part = load(path)
    result = rebuild(part, folder=TMP)
    volume = kernel.volume(result.shapes["Shield"])
    check("a hidden body is still built and cut (10x10x2 - 2x2x2 = 192)", abs(volume - 192) < 1e-6, f"{volume}")
    step = os.path.join(TMP, "out.step")
    note = export_part(step, part, result)
    check("export_part: only the visible bodies", step_names(step) == ["Board"], f"{step_names(step)}")
    check("  the note says which were left out", "without the hidden Shield" in note, note)
    part.set_body_visible("Board", False)
    stamp = os.path.getmtime(step)
    try:
        export_part(step, part, result)
        error = ""
    except ValueError as e:
        error = str(e)
    check("every body hidden: an error, nothing written", "hidden" in error and os.path.getmtime(step) == stamp, error)

    out = io.StringIO()
    step2 = os.path.join(TMP, "cli.step")
    code = cli.run(path, True, True, step2, out=out)
    data = json.loads(out.getvalue())
    visible = {b["name"]: b["visible"] for b in data["bodies"]}
    check("CLI: the report has visible per body", visible == {"Board": True, "Shield": False}, f"{visible}")
    check("  --export-step without the hidden", code == 0 and step_names(step2) == ["Board"], f"{step_names(step2)}")
    out = io.StringIO()
    cli.run(path, True, False, None, out=out)
    check("  the text: hidden (not in STEP)", "Shield: volume 192 mm3" in out.getvalue()
          and "hidden (not in STEP)" in out.getvalue(), out.getvalue()[-400:])


def check_window(path):
    window = MainWindow(path)
    errors = []
    window.show_error = errors.append
    window.ask_save_changes = lambda: "discard"
    window.show()
    settle(200)
    check("opened: Shield hidden in the view, its eye off", window.view._visible.get("Shield") is False
          and window._rows[("body", "Shield")].checkState(0) == Qt.Unchecked
          and window.view._visible.get("Board") is True)

    def grey(name):
        return window._rows[("feature", name)].foreground(0).color() == FADED_COLOR

    check("the browser: ExtB, Hole, Sketch2 grey; ExtA, Sketch1 not",
          all(grey(n) for n in ("ExtB", "Hole", "Sketch2")) and not any(grey(n) for n in ("ExtA", "Sketch1"))
          and "hidden" in window._rows[("feature", "ExtB")].toolTip(0))
    result, steps = window.result, len(window.doc._undo)
    window._rows[("body", "Board")].setCheckState(0, Qt.Unchecked)
    settle()
    check("Board's eye off: hidden, saved with the part, one undo step, no rebuild",
          not window.part.body_visible("Board") and window.view._visible.get("Board") is False
          and len(window.doc._undo) == steps + 1 and window.result is result and window.doc.modified)
    check("  ExtA and Sketch1 grey now", grey("ExtA") and grey("Sketch1"))
    step = os.path.join(TMP, "window.step")
    try:
        window.export_step(step)
        error = ""
    except ValueError as e:
        error = str(e)
    check("  Export with every body hidden: refused, said why", "hidden" in error, error)
    window._rows[("body", "Shield")].setCheckState(0, Qt.Checked)
    settle()
    note = window.export_step(step)
    check("Shield shown, Board hidden: Export writes Shield only", step_names(step) == ["Shield"]
          and "without the hidden Board" in note, f"{step_names(step)} {note}")
    window.app_settings.step_on_save = True
    saved = os.path.join(TMP, "saved.part.toml")
    window.write_quiet(saved)
    check("Save with STEP on Save: the file keeps it, the STEP leaves Board out",
          not load(saved).body_visible("Board") and step_names(window.step_path()) == ["Shield"],
          f"{step_names(window.step_path())}")
    window.undo()
    window.undo()
    settle()
    check("undone twice: as opened (Board shown, Shield hidden)", window.part.body_visible("Board")
          and not window.part.body_visible("Shield") and window.view._visible.get("Board") is True
          and window.view._visible.get("Shield") is False
          and window._rows[("body", "Board")].checkState(0) == Qt.Checked)
    window.close()
    check("no error dialogs", not errors, f"{errors}")


def main():
    path = os.path.join(TMP, "hidden.part.toml")
    with open(path, "w", encoding="utf-8") as f:
        f.write(PART)
    check_document()
    check_export(path)
    check_window(path)
    check("no errors in VisPy callbacks", not VISPY_ERRORS, f"{VISPY_ERRORS[:3]}")
    print("FAILED: " + ", ".join(FAILED) if FAILED else "all checks passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
