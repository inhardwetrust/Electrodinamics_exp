# check_extrude_bodies.py
#
# Extrude (and Revolve) joining / cutting / intersecting several bodies:
# `body = ["Top", "Bottom"]`, each body by the same tool. The part: Top
# (x 0..10), Bottom (x 20..30), Far (x 50..60), each 10 x 10 x 1 on XY; a
# slot x 4..26, y 4..6 swept by 1 reaches Top and Bottom (12 mm³ of each).
#   - the model: Cut [Top, Bottom]: each 100 - 12 = 88; Intersect: each
#     12; Join of two, or a New solid of two: fails, naming why; Cut [Top,
#     Far] (Far is not reached): fails, and Top stays whole (all or nothing);
#   - the file: a list when several, a one-name list read as the name;
#     renaming Top renames it in the list; the report lists both;
#   - the window: Extrude starts on Join of the last body; Cut; clicks on
#     the browser's rows take and drop bodies ("2 selected", both selected
#     in 3D); a group's row takes all its bodies, again drops them; Clear
#     drops all (Apply off); Apply cuts both; editing it holds both; Join
#     keeps the last one; New solid takes none.
#
#   python checks/check_extrude_bodies.py

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

from cli import report, text                                          # noqa: E402
from model.extrude import Extrude                                     # noqa: E402
from model.features import FeatureError                               # noqa: E402
from model.part import Part                                           # noqa: E402
from model.part_file import dumps, load                               # noqa: E402
from model.rebuild import rebuild                                     # noqa: E402
from model.sketch import Sketch                                       # noqa: E402

FAILED = []


def check(label, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {label} {detail}")
    if not ok:
        FAILED.append(label)


def rectangle(sketch, x0, y0, x1, y1):
    p = [sketch.add_point(str(x), str(y)) for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]
    return tuple(sorted(sketch.add_line(a, b) for a, b in zip(p, p[1:] + p[:1])))


def the_part(slot=True):
    """Top, Bottom, Far; and the slot's sketch (its profile key), not yet extruded."""
    part = Part()
    for body, x in (("Top", 0), ("Bottom", 20), ("Far", 50)):
        s = part.add_feature(Sketch(f"{body}Sketch", plane="XY Plane"))
        key = rectangle(s, x, 0, x + 10, 10)
        part.add_feature(Extrude(f"{body}Ext", profiles=[key], sketch=s.name, distance="1", body=body))
    s = part.add_feature(Sketch("SlotSketch", plane="XY Plane"))
    return part, rectangle(s, 4, 4, 26, 6)


def volumes(result):
    from geometry import kernel

    return {name: round(kernel.volume(shape), 6) for name, shape in result.shapes.items()}


def model_checks():
    part, key = the_part()
    part.add_feature(Extrude("Via", profiles=[key], sketch="SlotSketch", distance="1", operation="cut",
                             body=["Top", "Bottom"]))
    result = rebuild(part, tessellate=False)
    v = volumes(result)
    check("Cut [Top, Bottom]: each 88, Far 100", not result.feature_errors
          and (v["Top"], v["Bottom"], v["Far"]) == (88, 88, 100), f"{result.feature_errors} {v}")
    check("  its result: body Top, bodies [Top, Bottom]", result.outputs["Via"].body == "Top"
          and result.outputs["Via"].bodies == ["Top", "Bottom"])
    part.feature("Via").values["operation"] = "intersect"
    v = volumes(rebuild(part, tessellate=False))
    check("Intersect [Top, Bottom]: each 12", (v["Top"], v["Bottom"]) == (12, 12), str(v))
    for operation, why in (("join", "join takes one body"), ("new", "a new solid takes one body")):
        part.feature("Via").values["operation"] = operation
        error = rebuild(part, tessellate=False).feature_errors.get("Via", "")
        check(f"{operation.capitalize()} of two fails: {why}", why in error, error)
    part.feature("Via").values.update(operation="cut", body=["Top", "Far"])
    result = rebuild(part, tessellate=False)
    v = volumes(result)
    error = result.feature_errors.get("Via", "")
    check("Cut [Top, Far]: fails at Far, Top whole (all or nothing)", "cut Far" in error and v["Top"] == 100,
          f"{error} {v}")

    part.feature("Via").values["body"] = ["Top", "Bottom"]
    written = dumps(part)
    check("the file: body = [\"Top\", \"Bottom\"]", 'body = ["Top", "Bottom"]' in written)
    path = os.path.join(TMP, "back.part.toml")
    with open(path, "w", encoding="utf-8") as f:
        f.write(written.replace('body = ["Top", "Bottom"]', 'body = ["Top"]'))
    check("  a one-name list reads as the name", load(path).feature("Via")["body"] == "Top")
    try:
        Extrude("Bad", profiles=[key], sketch="SlotSketch", distance="1", operation="cut", body=[])
        check("  an empty list is refused", False)
    except FeatureError as error:
        check("  an empty list is refused", "list" in str(error), str(error))
    part.rename_body("Top", "Upper")
    check("renaming Top: Upper in the list", part.feature("Via")["body"] == ["Upper", "Bottom"]
          and "Upper" in part.body_names(), str(part.feature("Via")["body"]))
    result = rebuild(part, tessellate=False)
    data = report(part, result)
    row = next(f for f in data["features"] if f["name"] == "Via")
    check("the report: body [Upper, Bottom]; the text names both", row["body"] == ["Upper", "Bottom"]
          and "cut body 'Upper', 'Bottom'" in text(data), str(row["body"]))


def window_checks():
    from vispy import app

    app.use_app("pyside6")
    app.use_app().create()
    from vispy_errors import install as _watch_vispy

    vispy_errors = _watch_vispy()
    from check_sketch_line import settle, shield
    from geometry import kernel
    from view.main_window import MainWindow

    part, key = the_part()
    part.add_to_group("Pair", ["Top", "Bottom"])
    path = os.path.join(TMP, "bodies.part.toml")
    with open(path, "w", encoding="utf-8") as f:
        f.write(dumps(part))
    window = MainWindow()
    window.ask_save_changes = lambda: "discard"
    errors = []
    window.show_error = errors.append
    window.resize(1200, 800)
    window.show()
    shield(window)
    settle(200)
    window.open_file(path)
    settle()
    view = window.view

    def click(key):
        window._row_clicked(window._rows[key])
        settle(30)

    window.start_extrude()
    settle()
    panel = window.extrude_panel
    check("Extrude starts on Join of the last body (Far)", panel.operation_key() == "join"
          and panel.body_names() == ["Far"] and panel.body.text() == "Far", panel.body.text())
    click(("body", "Top"))
    check("  Join: a click on Top replaces it", panel.body_names() == ["Top"])
    panel.operation.setCurrentIndex(2)                       # Cut
    click(("body", "Bottom"))
    check("Cut: a click on Bottom adds it: 2 selected, both selected in 3D", panel.body_names() == ["Top", "Bottom"]
          and panel.body.text() == "2 selected" and set(view.selection) == {("body", "Top"), ("body", "Bottom")},
          f"{panel.body.text()} {view.selection}")
    click(("body", "Top"))
    check("  a click on Top again drops it", panel.body_names() == ["Bottom"])
    click(("group", "Pair"))
    check("a group's row takes all its bodies", sorted(panel.body_names()) == ["Bottom", "Top"])
    click(("group", "Pair"))
    check("  again: drops them", panel.body_names() == [])
    view.set_profile_selection([("profile", "SlotSketch", key)])
    window._profiles_picked(view.profile_selection)
    click(("body", "Far"))
    panel.body_clear_button.click()
    settle(30)
    check("Clear drops all; Apply off", panel.body_names() == [] and not panel.apply_button.isEnabled()
          and view.selection == [])
    click(("body", "Top"))
    click(("body", "Bottom"))
    panel.distance.setText("1")
    check("  Top and Bottom taken: Apply on", panel.apply_button.isEnabled())
    panel.apply_button.click()
    settle()
    feature = window.part.feature("Extrusion1")
    v = {n: round(kernel.volume(s), 6) for n, s in window.result.shapes.items()}
    check("Apply: Extrusion1 cuts [Top, Bottom]: each 88", feature["body"] == ["Top", "Bottom"]
          and (v["Top"], v["Bottom"], v["Far"]) == (88, 88, 100) and not window.result.feature_errors, str(v))
    check("  the 3D selection cleared after", view.selection == [] or view.selection == [None], str(view.selection))
    window.start_extrude(edit="Extrusion1")
    settle()
    panel = window.extrude_panel
    check("editing it: the panel holds Cut of both", panel.operation_key() == "cut"
          and panel.body_names() == ["Top", "Bottom"])
    panel.operation.setCurrentIndex(1)                       # Join
    check("  Join keeps the last one (Bottom)", panel.body_names() == ["Bottom"] and panel.body.text() == "Bottom")
    panel.operation.setCurrentIndex(0)                       # New solid
    click(("body", "Top"))
    check("  New solid: a click takes nothing; Apply on", panel.body_names() is None
          and panel.apply_button.isEnabled())
    panel.reject()
    settle()
    check("Cancel: unchanged", window.part.feature("Extrusion1")["body"] == ["Top", "Bottom"])
    window._show_info(("feature", "Extrusion1"))
    info = window.info.text()
    check("the info names both, with their volumes", "cuts Top, Bottom" in info and "Top now: 88" in info
          and "Bottom now: 88" in info, info)
    check("no error dialogs", not errors, str(errors))
    window.close()
    check("no errors in VisPy callbacks", not vispy_errors, f"{vispy_errors[:3]}")


def main():
    model_checks()
    window_checks()
    print("FAILED: " + ", ".join(FAILED) if FAILED else "all checks passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
