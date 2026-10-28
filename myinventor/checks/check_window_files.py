# check_window_files.py
#
# Files and parameters through the window (dialogs answered by the check):
#   - a new window: Part1, not modified, Undo off;
#   - Parameters tab: Add (p1 = 0), editing a formula cell rebuilds (the
#     value shows), a bad formula shows "error" with the reason, a rename
#     rewrites the users, a taken name is refused and the cell goes back,
#     deleting a used parameter is refused; the title gets '*';
#   - undo / redo through the window;
#   - a datum's visibility is one undo step and saved;
#   - Save (asks for a path when new): the file reads back the same, the
#     title and the browser take the file's name, it joins the recent files;
#   - New with changes: Cancel keeps the part, Discard drops it;
#   - Open: the part comes back; a broken file is refused with a message,
#     the current part stays and the file is untouched;
#   - a file changed on disk is not overwritten without asking.
#
#   python checks/check_window_files.py

import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

TMP = tempfile.mkdtemp()
os.environ["MYINVENTOR_SETTINGS"] = os.path.join(TMP, "settings.json")     # never the user's settings

from vispy import app                                                 # noqa: E402

app.use_app("pyside6")
app.use_app().create()

from PySide6.QtCore import Qt                                        # noqa: E402
from PySide6.QtTest import QTest                                      # noqa: E402

from model.part_file import load                                      # noqa: E402
from view.main_window import MainWindow                               # noqa: E402
from view.parameters_panel import FORMULA, NAME, VALUE               # noqa: E402

FAILED = []


def check(label, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {label} {detail}")
    if not ok:
        FAILED.append(label)


def settle(ms=30):
    for _ in range(5):
        app.process_events()
    QTest.qWait(ms)


def cell(window, name, column):
    table = window.parameters.table
    return table.item(window.parameters.row_of(name), column)


def edit(window, name, column, text):
    """As a user would: the cell's text changes, the panel reports it after the editor closes."""
    cell(window, name, column).setText(text)
    settle()


def main():
    window = MainWindow()
    answers = {"save_changes": "cancel", "save_path": None, "overwrite": False}
    errors = []
    window.ask_save_changes = lambda: answers["save_changes"]
    window.ask_save_path = lambda: answers["save_path"]
    window.ask_overwrite_changed = lambda: answers["overwrite"]
    window.show_error = errors.append
    window.show()
    settle(200)

    check("new: Part1, not modified, no undo",
          window.windowTitle().startswith("Part1 —") and not window.doc.modified
          and not window.undo_action.isEnabled(), window.windowTitle())

    # --- parameters
    window.parameters.add_requested.emit()
    settle()
    check("Add: p1 = 0", [(p.name, p.formula) for p in window.part.parameters] == [("p1", "0")])
    check("  the title gets '*', Undo on", window.windowTitle().startswith("Part1* —")
          and window.undo_action.isEnabled(), window.windowTitle())
    edit(window, "p1", NAME, "L")
    edit(window, "L", FORMULA, "11.46")
    window.parameters.add_requested.emit()
    settle()
    edit(window, "p1", NAME, "h")
    edit(window, "h", FORMULA, "L / 10")
    check("a formula is evaluated", cell(window, "h", VALUE).text() == "1.146", cell(window, "h", VALUE).text())
    edit(window, "h", FORMULA, "L / nope")
    value = cell(window, "h", VALUE)
    check("a bad formula: 'error' with the reason", value.text() == "error" and "nope" in value.toolTip(),
          value.toolTip())
    edit(window, "h", FORMULA, "L / 10")
    edit(window, "L", NAME, "length")
    check("rename rewrites the users", window.part.parameter("h").formula == "length / 10")
    edit(window, "h", NAME, "length")
    check("a taken name is refused, the cell goes back",
          [p.name for p in window.part.parameters] == ["length", "h"] and cell(window, "h", NAME) is not None
          and "already exists" in window.statusBar().currentMessage(), window.statusBar().currentMessage())
    window.parameters.delete_requested.emit(["length"])
    settle()
    check("deleting a used parameter is refused", errors and "h" in errors[-1]
          and [p.name for p in window.part.parameters] == ["length", "h"], f"{errors}")

    # --- undo / redo
    window.undo()
    check("undo: the rename is undone", [p.name for p in window.part.parameters] == ["L", "h"]
          and cell(window, "h", FORMULA).text() == "L / 10")
    window.redo()
    check("redo: renamed again", [p.name for p in window.part.parameters] == ["length", "h"])

    # --- datum visibility
    row = window._rows[("datum", "XY Plane")]
    row.setCheckState(0, Qt.Checked)
    settle()
    check("a datum shown: saved in the part", window.part.datum("XY Plane").visible)
    window.undo()
    check("  one undo step hides it again", not window.part.datum("XY Plane").visible
          and window._rows[("datum", "XY Plane")].checkState(0) == Qt.Unchecked)
    window.redo()

    # --- save
    path = os.path.join(TMP, "patch.part.toml")
    answers["save_path"] = path
    check("Save (new part): asks for the path, writes", window.save() and os.path.isfile(path))
    back = load(path)
    check("the file reads back the same", back.to_dict() == window.part.to_dict())
    check("the title and the browser take the file's name",
          window.windowTitle().startswith("patch —") and window.tree.topLevelItem(0).text(0) == "patch",
          window.windowTitle())
    check("it joins the recent files", window.app_settings.recent_files[:1] == [os.path.abspath(path)])

    # --- New with changes
    edit(window, "length", FORMULA, "12")
    answers["save_changes"] = "cancel"
    window.new_part()
    check("New, Cancel: the part stays", window.part.parameters and window.doc.modified)
    answers["save_changes"] = "discard"
    window.new_part()
    check("New, Discard: an empty Part1", not window.part.parameters and window.doc.name == "Part1"
          and not window.doc.modified)

    # --- open
    check("Open: the part comes back", window.open_file(path) and window.part.to_dict() == back.to_dict()
          and window.doc.name == "patch")
    broken = os.path.join(TMP, "broken.part.toml")
    text = 'format = "myinventor-part"\nversion = 1\n[[parameter]]\nname = "1a"\nformula = "1"\n'
    with open(broken, "w", encoding="utf-8") as f:
        f.write(text)
    count = len(errors)
    opened = window.open_file(broken)
    with open(broken, encoding="utf-8") as f:
        untouched = f.read() == text
    check("a broken file is refused with a message", not opened and len(errors) == count + 1
          and "parameter 1" in errors[-1], errors[-1] if errors else "")
    check("  the current part stays, the file is untouched", window.doc.name == "patch" and untouched)

    # --- changed on disk
    edit(window, "length", FORMULA, "13")
    os.utime(path, ns=(1, 1))
    answers["overwrite"] = False
    check("changed on disk: No keeps the file", not window.save() and load(path).to_dict() == back.to_dict())
    answers["overwrite"] = True
    check("  Yes overwrites it", window.save() and load(path).parameter("length").formula == "13")

    answers["save_changes"] = "discard"
    window.close()
    print("FAILED: " + ", ".join(FAILED) if FAILED else "all checks passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
