# check_body_color.py
#
# A body's color: a property of the body (Part.bodies, [[body]] in the
# file), not of the features making it.
#   - model: set / reset; '#RRGGBB' kept lower case; a bad color refused;
#     a TOML round trip ([[body]] name, color), written only when set;
#     refused files (a bad color, an unknown property, a body twice);
#   - rebuild: the body gets the color (0..1 RGB); a Cut / Join of it keeps
#     the color (it is the body's, not the feature's);
#   - the window: Solid1's row opens to a Color row whose icon is a swatch
#     of its color (the default one at first, "Color (default)"); a click on
#     it asks for a color (the check answers for the dialog) and paints the
#     body (the mesh's color in 3D, the swatch), one undo step; a cancelled
#     dialog changes nothing; undo brings the old color back; right click >
#     Default color resets it; save and open keep it.
#
#   python checks/check_body_color.py

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
from vispy import app                                                 # noqa: E402

app.use_app("pyside6")
app.use_app().create()

from vispy_errors import install as _watch_vispy                      # noqa: E402

VISPY_ERRORS = _watch_vispy()

from PySide6.QtGui import QColor                                     # noqa: E402

from check_extrude import rect                                        # noqa: E402
from check_sketch_line import settle, shield                          # noqa: E402
from model.extrude import Extrude                                     # noqa: E402
from model.part import Part, PartError                                # noqa: E402
from model.part_file import PartFileError, dumps, from_data, load     # noqa: E402
from model.rebuild import rebuild                                     # noqa: E402
from model.sketch import Sketch                                       # noqa: E402
from view.geometry_view import DEFAULT_COLORS                         # noqa: E402
from view.main_window import MainWindow                               # noqa: E402

FAILED = []
SQUARE = ("L1", "L2", "L3", "L4")


def check(label, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {label} {detail}")
    if not ok:
        FAILED.append(label)


def block(part):
    s1 = part.add_feature(Sketch("Sketch1", plane="XY Plane"))
    rect(s1, 0, 0, 10, 10)
    part.add_feature(Extrude("Extrusion1", profiles=[SQUARE], sketch="Sketch1", distance="5", body="Solid1"))
    return part


def check_model():
    part = block(Part())
    check("no color: nothing written", part.body_color("Solid1") is None and "[[body]]" not in dumps(part))
    part.set_body_color("Solid1", "#C87533")
    text = dumps(part)
    check("set: kept lower case, written as [[body]]", part.body_color("Solid1") == "#c87533"
          and '[[body]]\nname = "Solid1"\ncolor = "#c87533"' in text)
    check("a TOML round trip", from_data(tomllib.loads(text)).to_dict() == part.to_dict())
    try:
        part.set_body_color("Solid1", "orange")
        check("a bad color is refused", False)
    except PartError as error:
        check("a bad color is refused", "#rrggbb" in str(error), str(error))
    for label, bad in (("a bad color", 'color = "#12345"'), ("an unknown property", 'color = "#c87533"\nshine = 1'),
                       ("a body twice", 'color = "#c87533"\n\n[[body]]\nname = "Solid1"\ncolor = "#000000"')):
        try:
            from_data(tomllib.loads(text.replace('color = "#c87533"', bad)))
            check(f"refused: {label}", False)
        except PartFileError as error:
            check(f"refused: {label}", "body" in str(error), str(error))

    body = rebuild(part).bodies[0]
    check("rebuild: the body gets the color", np.allclose(body.color, (0xc8 / 255, 0x75 / 255, 0x33 / 255)),
          f"{body.color}")
    s2 = part.add_feature(Sketch("Sketch2", plane="XY Plane"))
    s2.add_circle(s2.add_point("5", "5"), "2")
    part.add_feature(Extrude("Hole", profiles=[("C1",)], sketch="Sketch2", distance="5", operation="cut",
                             body="Solid1"))
    body = rebuild(part).bodies[0]
    check("a cut of it keeps the color (the body's, not the feature's)",
          np.allclose(body.color, (0xc8 / 255, 0x75 / 255, 0x33 / 255)))
    part.set_body_color("Solid1", None)
    check("reset: no color, nothing written", part.body_color("Solid1") is None and "[[body]]" not in dumps(part))


def swatch(row):
    image = row.icon(0).pixmap(14, 14).toImage()
    c = QColor(image.pixel(7, 7))
    return np.array([c.redF(), c.greenF(), c.blueF()])


def main():
    check_model()
    window = MainWindow()
    window.ask_save_changes = lambda: "discard"
    errors = []
    window.show_error = errors.append
    window.resize(1200, 800)
    window.show()
    settle(300)
    shield(window)
    view = window.view
    block(window.part)
    window._commit()
    settle()

    body_row = window._rows[("body", "Solid1")]
    color_row = window._rows.get(("body_color", "Solid1"))
    check("Solid1 opens to a Color row (default)", color_row is not None and color_row.parent() is body_row
          and color_row.text(0) == "Color (default)")
    check("  its swatch shows the default color", np.allclose(swatch(color_row), DEFAULT_COLORS["solid"], atol=0.01),
          f"{swatch(color_row)}")
    asked = []
    window.ask_color = lambda initial, title: asked.append((initial.name(), title)) or None
    before = len(window.doc._undo)
    window._row_clicked(color_row)
    check("a click asks for a color, starting from the current one", len(asked) == 1
          and asked[0][1] == "Color of Solid1", f"{asked}")
    check("  cancelled: nothing changes (no color, no undo step)", window.part.body_color("Solid1") is None
          and len(window.doc._undo) == before)
    steps = len(window.doc._undo)
    window.ask_color = lambda initial, title: QColor("#2080e0")
    window._row_clicked(window._rows[("body_color", "Solid1")])
    settle()
    mesh_color = np.array(view._bodies["Solid1"].mesh.color.rgb)
    color_row = window._rows[("body_color", "Solid1")]
    check("a color picked: the body painted in 3D", window.part.body_color("Solid1") == "#2080e0"
          and np.allclose(mesh_color, (0x20 / 255, 0x80 / 255, 0xe0 / 255), atol=0.01), f"{mesh_color}")
    check("  the swatch shows it, one undo step", np.allclose(swatch(color_row), (0x20 / 255, 0x80 / 255, 0xe0 / 255),
                                                              atol=0.01)
          and color_row.text(0) == "Color" and len(window.doc._undo) == steps + 1)
    window.undo()
    check("undo: the default again", window.part.body_color("Solid1") is None
          and np.allclose(view._bodies["Solid1"].mesh.color.rgb, DEFAULT_COLORS["solid"], atol=0.01))
    window.redo()
    actions = dict(window.context_actions(window._rows[("body_color", "Solid1")]))
    check("right click: Pick a color..., Default color", list(actions) == ["Pick a color...", "Default color"])
    actions["Default color"]()
    check("  Default color resets it", window.part.body_color("Solid1") is None)
    window.undo()
    path = os.path.join(TMP, "colored.part.toml")
    window.ask_save_path = lambda: path
    window.save()
    window.new_part()
    window.open_file(path)
    check("save and open keep it", load(path).body_color("Solid1") == "#2080e0"
          and np.allclose(view._bodies["Solid1"].mesh.color.rgb, (0x20 / 255, 0x80 / 255, 0xe0 / 255), atol=0.01))
    check("no error dialogs", not errors, f"{errors}")
    window.close()
    check("no errors in VisPy callbacks", not VISPY_ERRORS, f"{VISPY_ERRORS[:3]}")
    print("FAILED: " + ", ".join(FAILED) if FAILED else "all checks passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
