# check_param_colors.py
#
# A parameter's color (the user marks the inputs; the intermediate ones
# stay white):
#   - the model: set / clear, a field's name colors its parameter, a bad
#     color refused, a rename keeps it;
#   - the file: `color` read strictly, written only when set, read back the
#     same; a bad one refused naming the parameter;
#   - the window: the rows tinted (a parameter with fields: its row and its
#     fields'), dark text on a light color, light on a dark one; the right
#     click menu (Color... picks for every selected parameter in one undo
#     step, the picker starting at the current color; Clear Color only when
#     one has a color); a cancelled picker changes nothing.
#
#   python checks/check_param_colors.py

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

from PySide6.QtCore import Qt                                         # noqa: E402
from PySide6.QtGui import QColor                                      # noqa: E402
from PySide6.QtTest import QTest                                      # noqa: E402

from model.part import PartError                                      # noqa: E402
from model.part_file import PartFileError, dumps, from_data, load, save  # noqa: E402
from view.main_window import MainWindow                               # noqa: E402
from view.parameters_panel import COMMENT, NAME, VALUE                # noqa: E402

FAILED = []


def check(label, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {label} {detail}")
    if not ok:
        FAILED.append(label)


def settle(ms=30):
    for _ in range(5):
        app.process_events()
    QTest.qWait(ms)


def raises(fn, kind=Exception):
    try:
        fn()
    except kind as error:
        return str(error)
    return None


PART = '''format = "mycad-part"
version = 1

[[parameter]]
name = "a"
formula = "10"
color = "#FFE082"

[[parameter]]
name = "c"
comment = "a point"
color = "#1a237e"
[parameter.fields]
x = "a"
y = "a / 2"

[[parameter]]
name = "b"
formula = "a + c.x"
'''


def check_model():
    path = os.path.join(TMP, "colors.part.toml")
    with open(path, "w", encoding="utf-8") as f:
        f.write(PART)
    part = load(path)
    check("read: colors lower-cased, none where not given",
          [p.color for p in part.parameters] == ["#ffe082", "#1a237e", ""])
    check("a field's color is its parameter's", part.parameter_color("c.y") == "#1a237e"
          and part.parameter_color("b") is None)
    part.set_parameter_color("c.x", "#00FF00")
    check("set through a field: the parameter", part.parameter("c").color == "#00ff00")
    check("a bad color refused, naming it", "c" in (raises(lambda: part.set_parameter_color("c", "green"), PartError) or ""))
    part.set_parameter_color("a", None)
    check("cleared", part.parameter("a").color == "" and "color" not in part.to_dict()["parameter"][0])
    part.rename_parameter("c", "k")
    check("a rename keeps it", part.parameter("k").color == "#00ff00")
    text = dumps(part)
    check("written only when set", text.count("color = ") == 1, text[:400])
    saved = os.path.join(TMP, "saved.part.toml")
    save(saved, part)
    check("read back the same", load(saved).to_dict() == part.to_dict())
    bad = PART.replace('"#FFE082"', '"yellow"')
    error = raises(lambda: from_data(__import__("tomllib").loads(bad)), (PartFileError, PartError))
    check("a file with a bad color refused, naming the parameter", error and "a" in error and "yellow" in error, error)
    bad = PART.replace('color = "#FFE082"', 'color = 3')
    check("a color that is no string refused", raises(lambda: from_data(__import__("tomllib").loads(bad)),
                                                      (PartFileError, PartError)) is not None)
    return path


def check_window(path):
    window = MainWindow(path)
    window.show_error = lambda text: None
    window.show()
    settle(200)
    panel = window.parameters

    def bg(name, column=NAME):
        brush = panel.item_of(name).background(column)
        return brush.color().name() if brush.style() != Qt.NoBrush else None

    def fg(name):
        return panel.item_of(name).foreground(VALUE).color().name()

    check("a's row tinted in every column", all(bg("a", c) == "#ffe082" for c in range(4)))
    check("  dark text on a light color", fg("a") == "#000000")
    check("c and its fields tinted, light text on dark", bg("c") == bg("c.x") == bg("c.y") == "#1a237e"
          and fg("c.x") == "#ffffff" and panel.item_of("c").text(COMMENT) == "a point")
    check("b stays white", bg("b") is None)

    actions = dict(panel.context_actions(panel.item_of("b")))
    check("right click on b: Color..., no Clear Color (it has none)", list(actions) == ["Color..."])
    check("  the clicked row becomes the selection", panel.selected_names() == ["b"])

    asked = []
    window.ask_color = lambda initial, title: asked.append((initial.name(), title)) or None
    actions["Color..."]()
    settle()
    check("a cancelled picker changes nothing", window.part.parameter("b").color == "" and not window.doc.modified)
    check("  it started at the default and named b", asked == [("#ffe082", "Color of b")], f"{asked}")

    panel.item_of("c.y").setSelected(True)
    actions = dict(panel.context_actions(panel.item_of("c.y")))
    check("right click in a selection keeps it, Clear Color offered",
          sorted(panel.selected_names()) == ["b", "c.y"] and "Clear Color" in actions, f"{panel.selected_names()}")
    asked.clear()
    window.ask_color = lambda initial, title: asked.append((initial.name(), title)) or QColor("#80cbc4")
    actions["Color..."]()
    settle()
    check("Color... on b and c.y: both parameters", window.part.parameter("b").color == "#80cbc4"
          and window.part.parameter("c").color == "#80cbc4" and bg("b") == bg("c.x") == "#80cbc4")
    check("  the picker started at the first one's color", asked and asked[0][0] == "#1a237e", f"{asked}")
    window.undo()
    settle()
    check("undo: one step back for both", window.part.parameter("b").color == ""
          and window.part.parameter("c").color == "#1a237e" and bg("b") is None)

    panel.tree.clearSelection()
    actions = dict(panel.context_actions(panel.item_of("a")))
    actions["Clear Color"]()
    settle()
    check("Clear Color: back to white", window.part.parameter("a").color == "" and bg("a") is None
          and fg("a") != "#ffffff")
    check("no menu off the rows", panel.context_actions(None) == [])

    panel.item_of("b").setText(NAME, "bb")
    settle()
    window.set_parameter_color(["bb"], "#ff8a65")
    settle()
    check("set_parameter_color on a renamed one, its tint follows the refill", bg("bb") == "#ff8a65")
    window.ask_save_changes = lambda: "discard"
    window.close()


def main():
    path = check_model()
    check_window(path)
    check("no errors in VisPy callbacks", not VISPY_ERRORS, f"{VISPY_ERRORS[:3]}")
    print("FAILED: " + ", ".join(FAILED) if FAILED else "all checks passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
