# check_revolve.py
#
# The Revolve feature (model/revolve.py, kernel.revolve):
#   - a rectangle x 1..4, y 0..4 (XY) around a construction line x = -1:
#     360 degrees pi (25 - 4) 4 = 84 pi; 90: 21 pi, the right hand way
#     (-90: the other way, the same volume); around Y Axis (x = 0): 60 pi;
#   - a circle r 2 at x 10 around the line: a torus 2 pi^2 10 4 = 80 pi^2;
#   - an axis line of another sketch ("Sketch2:L1"): the same 84 pi, and
#     that sketch is a reference;
#   - refused: the axis across the profile, Z Axis (not in the XY plane),
#     angles 0 and 400;
#   - the reflector: a band 1 mm thick along y = x^2 / 120 (f 30) from the
#     axis to r 50 (XZ plane), turned all the way round: its volume by
#     Pappus (2 pi area x centroid) within the fit tolerance; STEP keeps it;
#   - join and cut: a disc joined under it; a groove cut into a cylinder;
#   - the file: a TOML round trip; the report's line;
#   - the window: Revolve takes the only profile and asks for the axis
#     (Axis on), a click on the line takes it (profiles again), the
#     preview shows, Apply: 84 pi; Edit Revolution with 90: 21 pi; Y Axis
#     picked by a click: 60 pi.
#
#   python checks/check_revolve.py [--no-window]

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

import numpy as np                                                    # noqa: E402

from geometry import kernel                                           # noqa: E402
from geometry.step_io import export_part                              # noqa: E402
from model.curves import FIT_TOLERANCE                                # noqa: E402
from model.extrude import Extrude                                     # noqa: E402
from model.part import Part                                           # noqa: E402
from model.part_file import dumps, from_data                          # noqa: E402
from model.rebuild import rebuild                                     # noqa: E402
from model.revolve import Revolve                                     # noqa: E402
from model.sketch import Sketch                                       # noqa: E402

FAILED = []
RECT = ("L1", "L2", "L3", "L4")


def check(label, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {label} {detail}")
    if not ok:
        FAILED.append(label)


def rect_part():
    """Sketch S (XY): the rectangle x 1..4, y 0..4 (L1..L4) and a construction line x = -1 (L5)."""
    part = Part()
    s = part.add_feature(Sketch("S", plane="XY Plane"))
    p = [s.add_point(x, y) for x, y in (("1", "0"), ("4", "0"), ("4", "4"), ("1", "4"))]
    for a, b in zip(p, p[1:] + p[:1]):
        s.add_line(a, b)
    s.add_line(s.add_point("-1", "-1"), s.add_point("-1", "6"), construction=True)
    return part, s


def revolved(part, axis, angle="360", profiles=(RECT,), sketch="S", body="B", operation="new"):
    part.add_feature(Revolve("R", profiles=list(profiles), sketch=sketch, axis=axis, angle=angle,
                             operation=operation, body=body))
    result = rebuild(part)
    shape = result.shapes.get(body)
    return (kernel.volume(shape) if shape is not None and "R" not in result.feature_errors else None), result


def check_volumes():
    for angle, want in (("360", 84 * math.pi), ("90", 21 * math.pi), ("-90", 21 * math.pi)):
        part, _ = rect_part()
        v, result = revolved(part, "L5", angle)
        check(f"a rectangle around a line, {angle} degrees: {want / math.pi:g} pi",
              v is not None and math.isclose(v, want, rel_tol=1e-9), f"{v} {result.feature_errors}")
        if angle != "360":
            lo, hi = kernel.bounds(result.shapes["B"])
            # The axis runs +y (L5 from y -1 to 6): turning the right hand way takes +x to -z.
            check(f"  {angle}: the right hand way around the line's direction (+y)",
                  (hi[2] < 1e-6) if angle == "90" else (lo[2] > -1e-6), f"z {lo[2]:.3g}..{hi[2]:.3g}")
    part, _ = rect_part()
    v, result = revolved(part, "Y Axis")
    check("around Y Axis (x = 0): 60 pi", v is not None and math.isclose(v, 60 * math.pi, rel_tol=1e-9),
          f"{v} {result.feature_errors}")

    part, s = rect_part()
    s.add_circle(s.add_point("10", "2"), "2")
    v, result = revolved(part, "L5", profiles=[("C1",)])
    check("a circle r 2 at 11 from the line: a torus 2 pi^2 11 4", v is not None
          and math.isclose(v, 2 * math.pi ** 2 * 11 * 4, rel_tol=1e-9), f"{v} {result.feature_errors}")

    part, _ = rect_part()
    t = part.add_feature(Sketch("Sketch2", plane="XY Plane"))
    t.add_line(t.add_point("-1", "0"), t.add_point("-1", "1"))
    v, result = revolved(part, "Sketch2:L1")
    check("the axis a line of another sketch (Sketch2:L1): 84 pi, Sketch2 a reference",
          v is not None and math.isclose(v, 84 * math.pi, rel_tol=1e-9)
          and "Sketch2" in part.feature("R").references(), f"{v} {result.feature_errors}")


def check_refused():
    for label, axis, angle, words in (("the axis across the profile", "L2", "360", "crosses"),
                                      ("Z Axis, not in the sketch's plane", "Z Axis", "360", "plane"),
                                      ("angle 0", "L5", "0", "angle"), ("angle 400", "L5", "400", "angle"),
                                      ("no such axis", "L9", "360", "no axis")):
        part, s = rect_part()
        if axis == "L2":                        # a line through the rectangle
            s.add_line(s.add_point("2", "-1"), s.add_point("2", "6"), construction=True)
            axis = "L6"
        _, result = revolved(part, axis, angle)
        error = result.feature_errors.get("R", "")
        check(f"refused: {label}", words in error, error)


def reflector_part():
    part = Part()
    part.add_parameter("f", "30")
    part.add_parameter("D", "100")
    s = part.add_feature(Sketch("S", plane="XZ Plane"))
    c = s.add_curve("parabola", focal="f", **{"from": "0", "to": "D / 2"})
    s.add_offset(c, left="1")
    s.add_line(s.add_point("0", "-5"), s.add_point("0", "40"), construction=True)
    return part, c


def pappus_band(f, r, d):
    """2 pi A x_c of the band from y = x^2 / 4f (x 0..r) to its offset by d inward, dense."""
    t = np.linspace(0, r, 400001)
    g = np.stack([t, t * t / (4 * f)], 1)
    tangent = np.stack([np.ones_like(t), t / (2 * f)], 1)
    tangent /= np.linalg.norm(tangent, axis=1)[:, None]
    normal = np.stack([-tangent[:, 1], tangent[:, 0]], 1)
    poly = np.vstack([g, (g + d * normal)[::-1]])
    x, y = poly[:, 0], poly[:, 1]
    x1, y1 = np.roll(x, -1), np.roll(y, -1)
    cross = x * y1 - x1 * y
    area = cross.sum() / 2
    return 2 * math.pi * abs(area) * abs(((x + x1) * cross).sum() / (6 * area)), abs(area)


def check_reflector():
    part, c = reflector_part()
    v, result = revolved(part, "L1", profiles=[(f"{c}.offset",)], body="Reflector")
    want, area = pappus_band(30, 50, 1)
    check("the reflector (a band along a parabola, around its axis): Pappus within the fit tolerance",
          v is not None and abs(v - want) <= 2 * math.pi * 50 * FIT_TOLERANCE * 60, f"{v} vs {want}")
    lo, hi = kernel.bounds(result.shapes["Reflector"])
    check("  a dish 100 across (+ the band's lean), on the XY plane's side the parabola opens to",
          np.allclose([lo[0], hi[0]], [-50, 50], atol=1) and np.allclose([lo[1], hi[1]], [-50, 50], atol=1),
          f"{lo} {hi}")
    path = os.path.join(TMP, "reflector.step")
    export_part(path, part, result)
    from OCP.STEPControl import STEPControl_Reader

    reader = STEPControl_Reader()
    reader.ReadFile(path)
    reader.TransferRoots()
    back = kernel.volume(reader.OneShape())
    check("  STEP keeps it", math.isclose(back, v, rel_tol=1e-6), f"{back} vs {v}")


def check_operations():
    part, s = rect_part()
    t = part.add_feature(Sketch("Base", plane="XZ Plane"))
    q = [t.add_point(x, y) for x, y in (("-6", "0"), ("6", "0"), ("6", "1"), ("-6", "1"))]
    for a, b in zip(q, q[1:] + q[:1]):
        t.add_line(a, b)
    part.add_feature(Extrude("Slab", profiles=[RECT], sketch="Base", distance="12", body="B"))
    slab = kernel.volume(rebuild(part).shapes["B"])
    v, result = revolved(part, "L5", operation="join")
    check("join: a ring revolved onto a slab: the sum less the overlap (a body still)", v is not None
          and slab < v < slab + 84 * math.pi, f"{slab} {v} {result.feature_errors}")

    part, s = rect_part()
    t = part.add_feature(Sketch("Disc", plane="XZ Plane"))
    t.add_circle(t.add_point("-1", "0"), "6")
    part.add_feature(Extrude("Can", profiles=[("C1",)], sketch="Disc", distance="4", body="B"))
    can = kernel.volume(rebuild(part).shapes["B"])
    v, result = revolved(part, "L5", operation="cut")
    check("cut: a ring groove out of a cylinder around the same axis: 144 pi - 84 pi",
          v is not None and math.isclose(can, 144 * math.pi, rel_tol=1e-9)
          and math.isclose(v, 60 * math.pi, rel_tol=1e-9), f"{can} {v} {result.feature_errors}")


def check_file():
    part, _ = rect_part()
    part.add_feature(Revolve("R", profiles=[RECT], sketch="S", axis="L5", angle="270", body="B"))
    text = dumps(part)
    check("TOML round trip", from_data(tomllib.loads(text)).to_dict() == part.to_dict() and 'type = "revolve"' in text)
    import cli

    data = cli.report(part, rebuild(part))
    row = data["features"][-1]
    check("the report: axis and angle", row.get("axis") == "L5" and row.get("angle") == "270"
          and "around L5 by 270 deg" in cli.text(data))


def check_window():
    from vispy import app

    app.use_app("pyside6")
    app.use_app().create()
    from vispy_errors import install as _watch_vispy

    vispy_errors = _watch_vispy()
    from check_sketch_line import click, hover, pixel, settle, shield
    from view.main_window import MainWindow

    part, _ = rect_part()
    path = os.path.join(TMP, "rect.part.toml")
    with open(path, "w", encoding="utf-8") as f:
        f.write(dumps(part))
    window = MainWindow()
    window.ask_save_changes = lambda: "discard"
    errors = []
    window.show_error = errors.append
    window.resize(1200, 800)
    window.show()
    settle(300)
    shield(window)
    window.open_file(path)
    settle()
    view, native = window.view, window.view.canvas.native
    frame = window.result.outputs["S"].frame
    window.revolve_action.trigger()
    settle()
    panel = window.extrude_panel
    check("Revolve: the only profile taken, then the axis asked (Axis on), Apply off",
          panel is not None and panel.kind == "revolve" and len(view.profile_selection) == 1
          and panel.pick_axis.isChecked() and view.axis_mode and not panel.apply_button.isEnabled())
    hover(native, pixel(view, frame, (-1, 3)))
    settle()
    check("  hovering the construction line: it is the axis under the cursor", view.hovered == ("entity", "S", "L5"),
          f"{view.hovered}")
    click(native, pixel(view, frame, (-1, 3)))
    settle()
    check("  a click takes it: shown in the panel, clicks take profiles again, the preview, Apply on",
          view.axis_key == ("entity", "S", "L5") and "L5" in panel.axis.text() and panel.pick_profiles.isChecked()
          and not view.axis_mode and view.preview_body is not None and panel.apply_button.isEnabled(),
          f"{view.axis_key} {panel.axis.text()}")
    panel.apply_button.click()
    settle()
    feature = window.part.features[-1]
    v = kernel.volume(window.result.shapes[feature["body"]])
    check("Apply: Revolution1 around L5, 84 pi", feature.name == "Revolution1" and feature["axis"] == "L5"
          and math.isclose(v, 84 * math.pi, rel_tol=1e-9) and window.extrude_panel is None, f"{v}")
    window.start_extrude(edit="Revolution1")
    settle()
    panel = window.extrude_panel
    check("Edit Revolution: its angle and axis filled in, Apply on", panel.distance.text() == "360"
          and view.axis_key == ("entity", "S", "L5") and panel.apply_button.isEnabled())
    panel.distance.setText("90")
    panel.apply_button.click()
    settle()
    v = kernel.volume(window.result.shapes[feature["body"]])
    check("  90 degrees: 21 pi", math.isclose(v, 21 * math.pi, rel_tol=1e-9), f"{v}")
    window.start_extrude(edit="Revolution1")
    settle()
    panel = window.extrude_panel
    panel.distance.setText("360")
    panel.select("axis")
    settle()
    click(native, pixel(view, frame, (0, 2)))
    settle()
    check("  Axis, a click on Y Axis (shown meanwhile): taken", view.axis_key == ("datum", "Y Axis"),
          f"{view.axis_key}")
    panel.apply_button.click()
    settle()
    feature = window.part.feature("Revolution1")
    v = kernel.volume(window.result.shapes[feature["body"]])
    check("  Apply: around Y Axis, 60 pi", feature["axis"] == "Y Axis" and math.isclose(v, 60 * math.pi, rel_tol=1e-9),
          f"{feature['axis']} {v}")
    check("no error dialogs", not errors, f"{errors}")
    window.close()
    check("no errors in VisPy callbacks", not vispy_errors, f"{vispy_errors[:3]}")


def main():
    check_volumes()
    check_refused()
    check_reflector()
    check_operations()
    check_file()
    if "--no-window" not in sys.argv:
        check_window()
    print("FAILED: " + ", ".join(FAILED) if FAILED else "all checks passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
