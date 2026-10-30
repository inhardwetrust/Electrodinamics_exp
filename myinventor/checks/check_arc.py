# check_arc.py
#
# Sketch arcs ([[feature.arc]], SketchArc): from the center (center,
# radius, from / to angles; ends "A1.start" / "A1.end") or through three
# points (start, through, end). Extruded by 1, areas are volumes:
#   - a center arc r 5 from 0 to 180 closed by a line on its ends: 25 pi / 2;
#     the same through three points; exact arcs in the kernel;
#   - from 270 to 90 (counter-clockwise, across 0): the right half;
#   - an Offset of an arc: its sides concentric arcs (exact), the band
#     (a + b) L - (a^2 - b^2) / 2 * turn;
#   - the file: a TOML round trip; refused: an unknown kind, a missing point,
#     three points not different; a radius 0 or from = to: the sketch fails
#     naming the arc;
#   - deleting a center arc takes the line on its ends and its center;
#     deleting a point of a three-point arc takes the arc;
#   - a point snaps onto an arc (curve_snap);
#   - the window: Arc, method Center (below left): clicks center (0, 0),
#     start (10, 0), end (0, 10): an arc r 10 from 0 to 90; Method: Three
#     points: clicks (-10, 0), (10, 0), (0, 10): through them; selected:
#     Radius / From / To fields, the radius 20 follows; a quarter disc
#     (the center arc and two lines to the center) extruded: 100 pi / 4 * 4
#     (r 20).
#
#   python checks/check_arc.py [--no-window]

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
from model.extrude import Extrude                                     # noqa: E402
from model.part import Part                                           # noqa: E402
from model.part_file import PartFileError, dumps, from_data          # noqa: E402
from model.profiles import find_regions                               # noqa: E402
from model.rebuild import rebuild                                     # noqa: E402
from model.sketch import Sketch                                       # noqa: E402

FAILED = []


def check(label, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {label} {detail}")
    if not ok:
        FAILED.append(label)


def sketch_part():
    part = Part()
    return part, part.add_feature(Sketch("S", plane="XY Plane"))


def volume(part):
    """The sketch's largest region extruded by 1: (its key, the volume or None, the Result)."""
    regions = find_regions(rebuild(part).outputs["S"])
    key = regions[0].key if regions else ()
    part.add_feature(Extrude("E", profiles=[key], sketch="S", distance="1", body="B"))
    result = rebuild(part)
    return key, (kernel.volume(result.shapes["B"]) if "B" in result.shapes else None), result


def check_model():
    part, s = sketch_part()
    a = s.add_arc(kind="center", center=s.add_point("0", "0"), radius="5", start_angle="0", end_angle="180")
    s.add_line(f"{a}.end", f"{a}.start")
    key, v, result = volume(part)
    edges = result.outputs["S"].shapes[a].edges
    check("a center arc r 5, 0..180, closed by a line on its ends: 25 pi / 2, an exact arc",
          key == ("A1", "L1") and v is not None and math.isclose(v, 12.5 * math.pi, rel_tol=1e-12)
          and [e[0] for e in edges] == ["arc"], f"{key} {v}")
    part, s = sketch_part()
    p = [s.add_point(x, y) for x, y in (("5", "0"), ("0", "5"), ("-5", "0"))]
    s.add_arc(kind="three", start=p[0], through=p[1], end=p[2])
    s.add_line(p[2], p[0])
    key, v, _ = volume(part)
    check("the same through three points", v is not None and math.isclose(v, 12.5 * math.pi, rel_tol=1e-12), f"{v}")
    part, s = sketch_part()
    a = s.add_arc(kind="center", center=s.add_point("0", "0"), radius="5", start_angle="270", end_angle="90")
    s.add_line(f"{a}.end", f"{a}.start")
    key, v, result = volume(part)
    lo, hi = kernel.bounds(result.shapes["B"])
    check("from 270 to 90 (counter-clockwise across 0): the right half", v is not None
          and math.isclose(v, 12.5 * math.pi, rel_tol=1e-12) and np.isclose(lo[0], 0, atol=1e-9), f"{lo}")

    part, s = sketch_part()
    a = s.add_arc(kind="center", center=s.add_point("0", "0"), radius="5", start_angle="0", end_angle="180")
    s.add_offset(a, left="1", right="0.5")
    key, v, result = volume(part)
    kinds = [e[0] for e in result.outputs["S"].offsets[a][0][1]]
    want = 1.5 * 5 * math.pi - (1 - 0.25) / 2 * math.pi
    check("an arc's Offset: its sides concentric arcs, the band (a + b) L - (a^2 - b^2)/2 turn",
          v is not None and math.isclose(v, want, rel_tol=1e-6) and kinds.count("arc") == 2, f"{v} vs {want} {kinds}")

    part, s = sketch_part()
    c = s.add_point("0", "0")
    s.add_arc(kind="center", center=c, radius="r0", start_angle="10", end_angle="200")
    p = [s.add_point(x, y) for x, y in (("1", "0"), ("2", "1"), ("3", "0"))]
    s.add_arc(kind="three", start=p[0], through=p[1], end=p[2], construction=True)
    part.add_parameter("r0", "4")
    text = dumps(part)
    check("TOML round trip", "[[feature.arc]]" in text and from_data(tomllib.loads(text)).to_dict() == part.to_dict())
    head = ('format = "mycad-part"\nversion = 1\n[[feature]]\nname = "S"\ntype = "sketch"\nplane = "XY Plane"\n'
            '[[feature.point]]\nname = "P1"\nx = "0"\ny = "0"\n[[feature.point]]\nname = "P2"\nx = "1"\ny = "0"\n')
    for label, row in (("an unknown kind", 'kind = "spiral"\ncenter = "P1"\nradius = "1"\n'),
                       ("a missing point", 'kind = "center"\ncenter = "P9"\nradius = "1"\n'),
                       ("three points not different", 'kind = "three"\nstart = "P1"\nend = "P2"\nthrough = "P1"\n')):
        try:
            from_data(tomllib.loads(head + '[[feature.arc]]\nname = "A1"\n' + row))
            check(f"refused: {label}", False)
        except PartFileError as error:
            check(f"refused: {label}", "arc" in str(error), str(error))
    for label, values, words in (("radius 0", {"radius": "0"}, "radius"), ("from = to", {"end_angle": "10"}, "same")):
        part, s = sketch_part()
        a = s.add_arc(kind="center", center=s.add_point("0", "0"), radius="5", start_angle="10", end_angle="90")
        for k, val in values.items():
            setattr(s.arc(a), k, val)
        error = rebuild(part).feature_errors.get("S", "")
        check(f"{label}: the sketch fails naming the arc", f"arc {a}" in error and words in error, error)

    part, s = sketch_part()
    a = s.add_arc(kind="center", center=s.add_point("0", "0"), radius="5", start_angle="0", end_angle="180")
    s.add_line(f"{a}.end", f"{a}.start")
    s.delete(a)
    check("deleting a center arc takes the line on its ends and its center", not s.arcs and not s.lines and not s.points)
    part, s = sketch_part()
    p = [s.add_point(x, y) for x, y in (("5", "0"), ("0", "5"), ("-5", "0"))]
    s.add_arc(kind="three", start=p[0], through=p[1], end=p[2])
    s.delete(p[1])
    check("deleting a point of a three-point arc takes the arc", not s.arcs)

    from view.sketch_tools import curve_snap

    part, s = sketch_part()
    s.add_arc(kind="center", center=s.add_point("0", "0"), radius="5", start_angle="0", end_angle="180")
    hit = curve_snap((0.1, 5.2), rebuild(part).outputs["S"], 0.5)
    check("a point snaps onto the arc (exactly on it)", hit is not None and hit[1] == "A1"
          and math.isclose(float(np.linalg.norm(hit[0])), 5, rel_tol=1e-9), f"{hit}")


def check_window():
    from vispy import app

    app.use_app("pyside6")
    app.use_app().create()
    from vispy_errors import install as _watch_vispy

    vispy_errors = _watch_vispy()
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QComboBox, QFormLayout

    from check_sketch_line import click, pixel, settle, shield
    from view.main_window import MainWindow

    def rows(window):
        out, form = {}, window.props_form
        for i in range(form.rowCount()):
            label = form.itemAt(i, QFormLayout.ItemRole.LabelRole)
            field = form.itemAt(i, QFormLayout.ItemRole.FieldRole)
            if label is not None and field is not None:
                out[label.widget().text()] = field.widget()
        return out

    window = MainWindow()
    window.ask_save_changes = lambda: "discard"
    errors = []
    window.show_error = errors.append
    window.resize(1200, 800)
    window.show()
    settle(300)
    shield(window)
    view, native = window.view, window.view.canvas.native
    window.new_sketch("XY Plane")
    settle()
    frame = view.sketch_frame
    sketch = lambda: window.part.feature("Sketch1")         # noqa: E731
    window.arc_action.trigger()
    settle(100)
    method = rows(window).get("Method:")
    check("Arc: the method below left (From the center)", isinstance(method, QComboBox)
          and method.currentData() == "center")
    for xy in ((0, 0), (10, 0), (0, 10)):
        click(native, pixel(view, frame, xy))
    settle()
    arcs = sketch().arcs
    a = arcs[0] if arcs else None
    check("  clicks center (0, 0), start (10, 0), end (0, 10): r 10 from 0 to 90",
          a is not None and a.kind == "center" and math.isclose(float(a.radius), 10, abs_tol=0.1)
          and math.isclose(float(a.start_angle), 0, abs_tol=0.2) and math.isclose(float(a.end_angle), 90, abs_tol=0.2),
          f"{a}")
    rows(window)["Method:"].setCurrentIndex(1)
    settle()
    check("  Method: Three points", window.arc_method == "three" and window.view.tool.method == "three")
    for xy in ((-10, -20), (10, -20), (0, -10)):
        click(native, pixel(view, frame, xy))
    settle()
    a2 = sketch().arcs[1] if len(sketch().arcs) > 1 else None
    out = window.result.outputs["Sketch1"]
    check("  clicks (-10, -20), (10, -20), (0, -10): an arc through them",
          a2 is not None and a2.kind == "three" and np.allclose(out.points[a2.through], (0, -10), atol=0.05), f"{a2}")
    QTest.keyClick(native, Qt.Key_Escape)
    QTest.keyClick(native, Qt.Key_Escape)
    settle()

    s = sketch()
    s.add_line(f"{a.name}.start", s.arc(a.name).center)
    s.add_line(s.arc(a.name).center, f"{a.name}.end")
    window._commit()
    view.select_keys([("entity", "Sketch1", a.name)])
    window._on_view_select(view.selected)
    settle()
    fields = rows(window)
    check("the arc selected: Radius, From, To fields; its info", {"Radius:", "From:", "To:"} <= set(fields)
          and "from its center" in window.info.text(), f"{list(fields)}")
    fields["Radius:"].setText("20")
    fields["Radius:"].editingFinished.emit()
    settle()
    s = sketch()
    s.arc(a.name).start_angle, s.arc(a.name).end_angle = "0", "90"
    window._commit()
    window.finish_sketch()
    regions = find_regions(window.result.outputs["Sketch1"])
    key = next((r.key for r in regions if a.name in r.key), None)
    window.part.add_feature(Extrude("E", profiles=[key], sketch="Sketch1", distance="4", body="B"))
    window._commit()
    v = kernel.volume(window.result.shapes["B"]) if "B" in window.result.shapes else None
    check("  radius 20: a quarter disc with two lines to the center, extruded by 4: 400 pi / 4 * 4",
          sketch().arc(a.name).radius == "20" and v is not None and math.isclose(v, 400 * math.pi, rel_tol=1e-9),
          f"{key} {v} {window.result.feature_errors}")
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
