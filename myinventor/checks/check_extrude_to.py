# check_extrude_to.py
#
# Extrude up to a plane and up to a body (extent = "plane" | "body", to = ...):
#   - up to a parallel work plane: exact (z 2..5 from the board's top, 12
#     mm3), its end face a sketch can sit on (z 5);
#   - up to an inclined work plane (30 deg around X): cut by it (the volume
#     of a wedge, 20 tan 30), no flat end ("end" refused, said why);
#   - up to a body: a post under a cylinder (r 3 along Y, axis at z 10)
#     stops on its curved underside (volume 2 (16 - (sqrt 8 + 9 asin 1/3)));
#     a profile half under it: fails (it would go on forever); one beside
#     it: fails (never meets); up to the body it cuts: refused;
#   - the other way: a plane below the sketch is reached downwards;
#   - the file: extent / to written only when not "distance" (distance then
#     not required, not written), read back the same; strict (`to` missing);
#   - renames follow (the work plane, the body); a used work plane cannot be
#     deleted; the report says "up to plane WorkPlane1";
#   - the window: Extents (Distance, To Plane, To Body) in the Extrude panel,
#     the target from a list (planes above, bodies there), Distance hidden,
#     Apply writes extent / to, the preview shows the sweep; Edit Extrusion
#     opens with them.
#
#   python checks/check_extrude_to.py

import io
import math
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

import numpy as np                                                    # noqa: E402
from PySide6.QtTest import QTest                                      # noqa: E402

import cli                                                            # noqa: E402
from geometry import kernel                                           # noqa: E402
from model.part import PartError                                      # noqa: E402
from model.part_file import PartFileError, dumps, from_data           # noqa: E402
from model.rebuild import rebuild                                     # noqa: E402
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


def rect(prefix, x0, y0, x1, y1):
    corners = ((x0, y0), (x1, y0), (x1, y1), (x0, y1))
    text = "".join(f'[[feature.point]]\nname = "{prefix}{i}"\nx = "{x}"\ny = "{y}"\n' for i, (x, y) in enumerate(corners))
    text += "".join(f'[[feature.line]]\nname = "{prefix}L{i}"\nstart = "{prefix}{i}"\nend = "{prefix}{(i + 1) % 4}"\n'
                    for i in range(4))
    return text


def loop(prefix):
    return f'[["{prefix}L0", "{prefix}L1", "{prefix}L2", "{prefix}L3"]]'


BASE = f'''format = "mycad-part"
version = 1

[[feature]]
name = "Sketch1"
type = "sketch"
plane = "XY Plane"
{rect("B", 0, 0, 20, 10)}
[[feature]]
name = "Board"
type = "extrude"
sketch = "Sketch1"
profiles = {loop("B")}
distance = "2"
operation = "new"
body = "Board"

[[feature]]
name = "WorkPlane1"
type = "workplane"
base = "XY Plane"
offset = "5"

[[feature]]
name = "Tilted"
type = "workplane"
base = "XY Plane"
axis = "X Axis"
angle = "30"

[[feature]]
name = "Below"
type = "workplane"
base = "XY Plane"
offset = "-3"

[[feature]]
name = "LidSketch"
type = "sketch"
plane = "XZ Plane"
[[feature.point]]
name = "C"
x = "10"
y = "-10"
[[feature.circle]]
name = "R"
center = "C"
radius = "3"

[[feature]]
name = "LidExt"
type = "extrude"
sketch = "LidSketch"
profiles = [["R"]]
distance = "10"
operation = "new"
body = "Lid"
'''


def with_features(text):
    return BASE + text


def build(text):
    part = from_data(tomllib.loads(text))
    return part, rebuild(part, folder=TMP)


PAD = f'''
[[feature]]
name = "PadSketch"
type = "sketch"
plane = "Board:end"
{rect("P", 2, 2, 4, 4)}
[[feature]]
name = "Pad"
type = "extrude"
sketch = "PadSketch"
profiles = {loop("P")}
extent = "plane"
to = "WorkPlane1"
operation = "new"
body = "Pad"

[[feature]]
name = "TopSketch"
type = "sketch"
plane = "Pad:end"
{rect("T", 2.5, 2.5, 3.5, 3.5)}
'''


def wedge(sign=""):
    return f'''
[[feature]]
name = "WedgeSketch"
type = "sketch"
plane = "XY Plane"
{rect("W", 0, 4, 2, 6)}
[[feature]]
name = "Wedge"
type = "extrude"
sketch = "WedgeSketch"
profiles = {loop("W")}
extent = "plane"
to = "Tilted"
operation = "new"
body = "Wedge"
'''


def post(x0, x1, name="Post", operation="new", body="Post", to="Lid"):
    return f'''
[[feature]]
name = "{name}Sketch"
type = "sketch"
plane = "Board:end"
{rect("Q", x0, 4, x1, 6)}
[[feature]]
name = "{name}"
type = "extrude"
sketch = "{name}Sketch"
profiles = {loop("Q")}
extent = "body"
to = "{to}"
operation = "{operation}"
body = "{body}"
'''


def check_model():
    part, result = build(with_features(PAD))
    lo, hi = kernel.bounds(result.shapes["Pad"]) if "Pad" in result.shapes else (None, None)
    check("up to a parallel work plane: z 2..5, 12 mm3", not result.feature_errors
          and np.allclose([lo[2], hi[2]], [2, 5]) and abs(kernel.volume(result.shapes["Pad"]) - 12) < 1e-9,
          f"{result.feature_errors}")
    check("  its end face carries a sketch (z 5)", abs(result.outputs["TopSketch"].frame.origin[2] - 5e-3) < 1e-12)
    text = dumps(part)
    check("  written: extent / to, no distance; read back the same",
          'extent = "plane"' in text and 'to = "WorkPlane1"' in text and "distance" not in text.split('name = "Pad"')[1]
          .split("[[feature]]")[0] and from_data(tomllib.loads(text)).to_dict() == part.to_dict())
    check("  a plain extrusion writes neither", 'extent' not in text.split('name = "Board"')[1].split("[[feature]]")[0])
    out = io.StringIO()
    path = os.path.join(TMP, "pad.part.toml")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    cli.run(path, True, False, None, out=out)
    check("  the report: up to plane WorkPlane1", "up to plane WorkPlane1" in out.getvalue())
    check("  WorkPlane1 is used: not deleted", isinstance(_raises(lambda: part.delete_feature("WorkPlane1")), PartError))
    part.rename_body("Lid", "Cover")
    old = part.feature("WorkPlane1")
    part.features[part.features.index(old)].name = "WP"
    for f in part.features:
        f.rename_reference("WorkPlane1", "WP")
    check("  renames follow (the work plane)", part.feature("Pad")["to"] == "WP")

    part, result = build(with_features(wedge()))
    volume = kernel.volume(result.shapes["Wedge"]) if "Wedge" in result.shapes else 0
    expected = 20 * math.tan(math.radians(30))
    check("up to an inclined plane (30 deg): the wedge, 20 tan 30", abs(volume - expected) < 1e-6,
          f"{volume} vs {expected} {result.feature_errors}")
    check("  no flat end: a sketch on Wedge:end refused, said why",
          "no flat end" in str(_raises(lambda: result.outputs["Wedge"].face_frame("end"))))
    check("  not offered as a face", "end" not in result.outputs["Wedge"].face_roles())

    part, result = build(with_features(post(9, 11)))
    volume = kernel.volume(result.shapes["Post"]) if "Post" in result.shapes else 0
    expected = 2 * (16 - (math.sqrt(8) + 9 * math.asin(1 / 3)))
    check("up to a body: the post stops on the cylinder's underside", abs(volume - expected) < 1e-6,
          f"{volume} vs {expected} {result.feature_errors}")
    lo, hi = kernel.bounds(result.shapes["Post"])
    check("  from z 2 up to 10 - sqrt(8) at its corners, 7 in the middle",
          abs(lo[2] - 2) < 1e-9 and abs(hi[2] - (10 - math.sqrt(8))) < 1e-6, f"{lo} {hi}")
    part.rename_body("Lid", "Cover")
    check("  renaming the body renames `to`", part.feature("Post")["to"] == "Cover")
    _, result = build(with_features(post(12, 14)))
    check("  a profile half under it: fails (it would go on forever)", "misses" in result.feature_errors.get("Post", ""),
          f"{result.feature_errors}")
    _, result = build(with_features(post(15, 17)))
    check("  a profile beside it: fails (never meets)", "never meets" in result.feature_errors.get("Post", ""),
          f"{result.feature_errors}")
    _, result = build(with_features(post(9, 11, name="Hole", operation="cut", body="Board", to="Board")))
    check("  up to the body it cuts: refused", "cannot go up to 'Board'" in result.feature_errors.get("Hole", ""),
          f"{result.feature_errors}")
    _, result = build(with_features(post(9, 11, to="Nope")))
    check("  up to a body that is not there: fails, naming it", "no body 'Nope'" in result.feature_errors.get("Post", ""))

    down = f'''
[[feature]]
name = "DownSketch"
type = "sketch"
plane = "XY Plane"
{rect("D", 30, 0, 32, 2)}
[[feature]]
name = "Down"
type = "extrude"
sketch = "DownSketch"
profiles = {loop("D")}
extent = "plane"
to = "Below"
operation = "new"
body = "Down"
'''
    _, result = build(with_features(down))
    lo, hi = kernel.bounds(result.shapes["Down"]) if "Down" in result.shapes else (None, None)
    check("a plane below the sketch: reached downwards (z -3..0)", lo is not None and np.allclose([lo[2], hi[2]], [-3, 0]),
          f"{result.feature_errors}")
    bad = with_features(down).replace('to = "Below"\n', "")
    check("strict: up to a plane without `to` refused", isinstance(_raises(lambda: from_data(tomllib.loads(bad))),
                                                                     PartFileError))


WINDOW = f'''
[[feature]]
name = "S"
type = "sketch"
plane = "Board:end"
{rect("Q", 9, 4, 11, 6)}'''


def check_window():
    path = os.path.join(TMP, "window.part.toml")
    with open(path, "w", encoding="utf-8") as f:
        f.write(with_features(WINDOW))
    window = MainWindow(path)
    errors = []
    window.show_error = errors.append
    window.ask_save_changes = lambda: "discard"
    window.show()
    settle(200)
    window.start_extrude()
    settle()
    panel = window.extrude_panel
    check("Extrude: Extents Distance / To Plane / To Body, Distance at first",
          [panel.extent.itemText(i) for i in range(panel.extent.count())] == ["Distance", "To Plane", "To Body"]
          and panel.extent_key() == "distance" and panel.distance.isVisibleTo(panel)
          and not panel.target.isVisibleTo(panel))
    panel.extent.setCurrentIndex(1)
    settle()
    targets = [panel.target.itemText(i) for i in range(panel.target.count())]
    check("  To Plane: the origin planes and the work planes; Distance hidden, To shown",
          targets == ["YZ Plane", "XZ Plane", "XY Plane", "WorkPlane1", "Tilted", "Below"]
          and not panel.distance.isVisibleTo(panel) and panel.target.isVisibleTo(panel), f"{targets}")
    panel.target.setCurrentIndex(targets.index("WorkPlane1"))
    settle()
    check("  the only profile taken, WorkPlane1: a preview, Apply on", window.view.preview_body is not None
          and panel.apply_button.isEnabled())
    panel.operation.setCurrentIndex(0)              # New solid
    settle()
    panel.apply_button.click()
    settle()
    feature = window.part.features[-1]
    shape = window.result.shapes.get(feature["body"])
    check("Apply: extent plane, to WorkPlane1, 2 x 2 x 3 = 12 mm3",
          feature["extent"] == "plane" and feature["to"] == "WorkPlane1"
          and shape is not None and abs(kernel.volume(shape) - 12) < 1e-9, f"{feature.to_dict()}")

    window.start_extrude(edit=feature.name)
    settle()
    panel = window.extrude_panel
    check("Edit: opens with To Plane, WorkPlane1", panel.extent_key() == "plane" and panel.target_name() == "WorkPlane1"
          and not panel.distance.isVisibleTo(panel))
    panel.extent.setCurrentIndex(2)
    settle()
    bodies = [panel.target.itemText(i) for i in range(panel.target.count())]
    check("  To Body: the bodies there are", bodies == ["Board", "Lid"], f"{bodies}")
    panel.target.setCurrentIndex(bodies.index("Lid"))
    settle()
    check("  Lid: a preview", window.view.preview_body is not None)
    panel.apply_button.click()
    settle()
    feature = window.part.feature(feature.name)
    shape = window.result.shapes.get(feature["body"])
    expected = 2 * (16 - (math.sqrt(8) + 9 * math.asin(1 / 3)))
    check("Apply: up to Lid, stopped on its underside", feature["extent"] == "body" and feature["to"] == "Lid"
          and shape is not None and abs(kernel.volume(shape) - expected) < 1e-6, f"{feature.to_dict()}")
    window.start_extrude(edit=feature.name)
    settle()
    panel = window.extrude_panel
    panel.extent.setCurrentIndex(0)
    settle()
    check("  back to Distance: the field shows again (10)", panel.distance.isVisibleTo(panel)
          and panel.distance.text() == "10" and panel.apply_button.isEnabled())
    panel.distance.setText("4")
    panel.apply_button.click()
    settle()
    feature = window.part.feature(feature.name)
    check("  Apply: a plain distance again, extent / to not written",
          feature["extent"] == "distance" and "extent" not in feature.to_dict()
          and abs(kernel.volume(window.result.shapes[feature["body"]]) - 16) < 1e-9)
    window.close()
    check("no error dialogs", not errors, f"{errors}")


def _raises(fn):
    try:
        fn()
    except Exception as error:      # noqa: BLE001 (what was raised is the answer)
        return error
    return None


def main():
    check_model()
    check_window()
    check("no errors in VisPy callbacks", not VISPY_ERRORS, f"{VISPY_ERRORS[:3]}")
    print("FAILED: " + ", ".join(FAILED) if FAILED else "all checks passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
