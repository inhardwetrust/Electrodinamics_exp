# check_sketch_history_view.py
#
# While a sketch is edited, the sketches drawn in 3D are those up to it in
# the history (what comes later does not exist yet); As placed (the
# retrospective view) shows them all, the later ones as they were when the
# sketch was opened, moved with the body they lie on:
#   - out of sketch mode: the sketches no extrusion used (Sketch2, Sketch3);
#   - editing Sketch1 at once (the window still holds the whole result) and
#     after an edit (rebuilt up to it): Sketch1 only;
#   - As placed on: Sketch1, Sketch2, Sketch3, Sketch2 on the placed board's
#     top (the board moved 5 up); off: Sketch1 again;
#   - editing Sketch2: Sketch2 (Sketch1 is used, Sketch3 later); Sketch3:
#     Sketch2 and Sketch3; Finish: as before.
#
#   python checks/check_sketch_history_view.py

import os
import sys
import tempfile

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

from PySide6.QtTest import QTest                                      # noqa: E402

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


def square(prefix, x0, y0, size):
    corners = ((x0, y0), (x0 + size, y0), (x0 + size, y0 + size), (x0, y0 + size))
    text = "".join(f'[[feature.point]]\nname = "{prefix}{i}"\nx = "{x}"\ny = "{y}"\n' for i, (x, y) in enumerate(corners))
    text += "".join(f'[[feature.line]]\nname = "{prefix}L{i}"\nstart = "{prefix}{i}"\nend = "{prefix}{(i + 1) % 4}"\n'
                    for i in range(4))
    return text


PART = f'''format = "mycad-part"
version = 1

[[feature]]
name = "Sketch1"
type = "sketch"
plane = "XY Plane"
{square("P", 0, 0, 10)}
[[feature]]
name = "Board"
type = "extrude"
sketch = "Sketch1"
profiles = [["PL0", "PL1", "PL2", "PL3"]]
distance = "2"
operation = "new"
body = "Board"

[[feature]]
name = "Sketch2"
type = "sketch"
plane = "Board:end"
{square("Q", 2, 2, 3)}
[[feature]]
name = "Sketch3"
type = "sketch"
plane = "XY Plane"
{square("R", 20, 0, 4)}
[[transform]]
name = "Lift"
type = "move"
targets = ["Board"]
offset = ["0", "0", "5"]
'''


def shown(window):
    return list(window.view._sketch_geometry)


def main():
    path = os.path.join(TMP, "history.part.toml")
    with open(path, "w", encoding="utf-8") as f:
        f.write(PART)
    window = MainWindow(path)
    errors = []
    window.show_error = errors.append
    window.ask_save_changes = lambda: "discard"
    window.show()
    settle(200)
    check("the part builds", not window.result.feature_errors, f"{window.result.feature_errors}")
    check("out of sketch mode: the unused sketches", shown(window) == ["Sketch2", "Sketch3"], f"{shown(window)}")

    window.edit_sketch("Sketch1")
    settle()
    check("editing Sketch1 at once: Sketch1 only (Sketch2, Sketch3 come later)", shown(window) == ["Sketch1"],
          f"{shown(window)}")
    window._add_point((5.0, 5.0))
    settle()
    check("  after an edit (rebuilt up to it): still Sketch1 only", window.result.upto == "Sketch1"
          and shown(window) == ["Sketch1"], f"{shown(window)}")
    window.placed_check.setChecked(True)
    settle()
    check("As placed: every sketch", shown(window) == ["Sketch1", "Sketch2", "Sketch3"], f"{shown(window)}")
    frame = window._shown_output("Sketch2").frame
    check("  Sketch2 on the placed board's top (z 2 + 5 = 7 mm)", abs(frame.origin[2] - 7e-3) < 1e-9,
          f"{frame.origin}")
    window.placed_check.setChecked(False)
    settle()
    check("As placed off: Sketch1 only again", shown(window) == ["Sketch1"], f"{shown(window)}")
    window.finish_sketch()
    settle()
    check("Finish: the unused sketches", shown(window) == ["Sketch2", "Sketch3"], f"{shown(window)}")

    window.edit_sketch("Sketch2")
    settle()
    check("editing Sketch2: Sketch2 (Sketch1 is used, Sketch3 later)", shown(window) == ["Sketch2"], f"{shown(window)}")
    window.finish_sketch()
    settle()
    window.edit_sketch("Sketch3")
    settle()
    check("editing Sketch3: Sketch2 and Sketch3", shown(window) == ["Sketch2", "Sketch3"], f"{shown(window)}")
    window.finish_sketch()
    settle()
    check("Finish: as before", shown(window) == ["Sketch2", "Sketch3"], f"{shown(window)}")

    window.close()
    check("no error dialogs", not errors, f"{errors}")
    check("no errors in VisPy callbacks", not VISPY_ERRORS, f"{VISPY_ERRORS[:3]}")
    print("FAILED: " + ", ".join(FAILED) if FAILED else "all checks passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
