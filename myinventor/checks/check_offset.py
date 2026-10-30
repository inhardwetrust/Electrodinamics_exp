# check_offset.py
#
# The Offset modifier (model/offsets.py, [[feature.offset]]), extruded by 1
# so the volume is the band's area:
#   - a line 10 long, left 1, right 0.5: 15 exactly; round caps: + pi 0.75^2
#     (half circles on the caps, exact arcs);
#   - a rectangle with an offset on one of its lines: that line is a spine,
#     the rectangle no longer closes; the band is the only profile;
#   - a circle r 5, left 1 (inside), right 2: the ring pi (49 - 16) with its
#     inner disc as a hole; the disc 16 pi a profile of its own;
#   - a parabola y = x^2 / 4 from 0 to 2 (length sqrt 2 + asinh 1, turning
#     pi / 4 to the left): band area (a + b) L - (a^2 - b^2) / 2 * turn for
#     left a / right b, within the fit tolerance; a side of 0 is the exact
#     parabola in the kernel; left 2.5 (past the radius 2 at the vertex)
#     fails naming the radius;
#   - a closed spline, 0.5 to each side: a ring of area L (its length);
#   - the file: round trip, refused rows; deleting the entity drops its
#     offset; a curve converted to a spline takes its offset along;
#   - the window: Offset on a selected spline, its fields (left, right,
#     caps; a value typed then a click on the other field: it crashed), the
#     band picks the spline, Remove Offset; Offset (2) and
#     Remove Offset (2) on a line and a circle selected together.
#
#   python checks/check_offset.py [--no-window]

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
from model.curves import FIT_TOLERANCE, spline_function               # noqa: E402
from model.extrude import Extrude                                     # noqa: E402
from model.part import Part                                           # noqa: E402
from model.part_file import PartFileError, dumps, from_data           # noqa: E402
from model.profiles import find_regions                               # noqa: E402
from model.rebuild import rebuild                                     # noqa: E402
from model.sketch import Sketch                                       # noqa: E402

FAILED = []
PARABOLA_LENGTH = math.sqrt(2) + math.asinh(1)


def check(label, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {label} {detail}")
    if not ok:
        FAILED.append(label)


def extruded(part, key):
    """The volume of profile `key` of sketch S extruded by 1 (None if it fails), and the Result."""
    part.add_feature(Extrude("E", profiles=[key], sketch="S", distance="1", body="B"))
    result = rebuild(part)
    shape = result.shapes.get("B")
    return (kernel.volume(shape) if shape is not None else None), result


def sketch_part():
    part = Part()
    return part, part.add_feature(Sketch("S", plane="XY Plane"))


def check_line():
    for caps, want in ((("flat", "flat"), 15.0), (("round", "round"), 15 + math.pi * 0.75 ** 2),
                       (("round", "flat"), 15 + math.pi * 0.75 ** 2 / 2)):
        part, s = sketch_part()
        line = s.add_line(s.add_point("0", "0"), s.add_point("10", "0"))
        s.add_offset(line, left="1", right="0.5", start=caps[0], end=caps[1])
        v, result = extruded(part, ("L1.offset",))
        check(f"a line, left 1, right 0.5, caps {caps[0]} / {caps[1]}: {want:.6g}",
              v is not None and math.isclose(v, want, rel_tol=1e-9), f"{v} {result.feature_errors}")
    lo, hi = kernel.bounds(result.shapes["B"])
    check("  left is up (the line runs +x): from y -0.5 to 1", np.allclose([lo[1], hi[1]], [-0.5, 1], atol=1e-6))

    part, s = sketch_part()
    p = [s.add_point(x, y) for x, y in (("0", "0"), ("10", "0"), ("10", "5"), ("0", "5"))]
    for a, b in zip(p, p[1:] + p[:1]):
        s.add_line(a, b)
    s.add_offset("L1", left="1")
    keys = [r.key for r in find_regions(rebuild(part).outputs["S"])]
    check("a rectangle with an offset line: the line is a spine, only the band is a profile",
          keys == [("L1.offset",)], f"{keys}")


def check_circle():
    part, s = sketch_part()
    c = s.add_circle(s.add_point("3", "4"), "5")
    s.add_offset(c, left="1", right="2")
    regions = find_regions(rebuild(part).outputs["S"])
    check("a circle r 5, left 1, right 2: the ring with the disc as its hole, and the disc",
          [(r.key, len(r.holes)) for r in regions] == [(("C1.offset",), 1), (("C1.offset.inner",), 0)],
          f"{[(r.key, len(r.holes)) for r in regions]}")
    v, result = extruded(part, ("C1.offset",))
    check("  the ring extruded: pi (49 - 16) exactly", v is not None and math.isclose(v, 33 * math.pi, rel_tol=1e-9),
          f"{v} {result.feature_errors}")
    s.offset(c).left = "5"
    result = rebuild(part)
    check("  left 5 (to the center): the sketch fails, naming the offset",
          "offset of C1" in result.feature_errors.get("S", ""), f"{result.feature_errors}")


def check_parabola():
    for a, b in ((0.2, 0.0), (0.0, 0.3), (0.1, 0.25)):
        part, s = sketch_part()
        c = s.add_curve("parabola", focal="1", **{"from": "0", "to": "2"})
        s.add_offset(c, left=str(a), right=str(b))
        v, result = extruded(part, (f"{c}.offset",))
        want = (a + b) * PARABOLA_LENGTH - (a * a - b * b) / 2 * math.pi / 4
        kinds = [e[0] for e in result.outputs["S"].offsets[c][0][1]]
        exact = ("parabola" in kinds) == (a == 0 or b == 0)
        check(f"a parabola, left {a}, right {b}: (a + b) L - (a^2 - b^2) / 2 * pi/4 within the fit tolerance",
              v is not None and abs(v - want) <= 2 * PARABOLA_LENGTH * FIT_TOLERANCE and exact,
              f"{v} vs {want}, edges {kinds}")
    part, s = sketch_part()
    c = s.add_curve("parabola", focal="1", **{"from": "0", "to": "2"})
    s.add_offset(c, left="2.5")
    result = rebuild(part)
    check("  left 2.5, past the radius 2 at the vertex: fails naming the radius",
          "radius of the bend (2 mm)" in result.feature_errors.get("S", ""), f"{result.feature_errors}")


def check_closed_spline():
    part, s = sketch_part()
    ring = [s.add_point(x, y) for x, y in (("10", "0"), ("0", "6"), ("-10", "0"), ("0", "-6"))]
    w = s.add_spline(ring[0], ring[1])
    for n in ring[2:] + ring[:1]:
        s.extend_spline(w, n)
    s.add_offset(w, left="0.5", right="0.5")
    out = rebuild(part).outputs["S"]
    at, length = spline_function(np.array([out.points[n] for n in ring + ring[:1]]))
    p = at(np.linspace(0, length, 200001))
    perimeter = float(np.linalg.norm(np.diff(p, axis=0), axis=1).sum())
    keys = [r.key for r in find_regions(out)]
    v, result = extruded(part, (f"{w}.offset",))
    check("a closed spline, 0.5 to each side: a ring (and its inner disc) of area = its length",
          keys == [(f"{w}.offset",), (f"{w}.offset.inner",)] and v is not None
          and abs(v - perimeter) <= 2 * perimeter * FIT_TOLERANCE, f"{keys} {v} vs {perimeter}")


def check_file():
    part, s = sketch_part()
    line = s.add_line(s.add_point("0", "0"), s.add_point("10", "0"))
    s.add_offset(line, left="w / 2", right="w / 2", start="round")
    text = dumps(part)
    check("TOML round trip", "[[feature.offset]]" in text and from_data(tomllib.loads(text)).to_dict() == part.to_dict())
    head = ('format = "mycad-part"\nversion = 1\n[[feature]]\nname = "S"\ntype = "sketch"\nplane = "XY Plane"\n'
            '[[feature.point]]\nname = "P1"\nx = "0"\ny = "0"\n[[feature.point]]\nname = "P2"\nx = "1"\ny = "0"\n'
            '[[feature.line]]\nname = "L1"\nstart = "P1"\nend = "P2"\n')
    for label, rows in (("of nothing", '[[feature.offset]]\nof = "L9"\nleft = "1"\n'),
                        ("two on one line", '[[feature.offset]]\nof = "L1"\nleft = "1"\n' * 2),
                        ("a bad cap", '[[feature.offset]]\nof = "L1"\nleft = "1"\nend = "square"\n'),
                        ("no side", '[[feature.offset]]\nof = "L1"\n'),
                        ("an unknown key", '[[feature.offset]]\nof = "L1"\nleft = "1"\nwidth = "2"\n')):
        try:
            from_data(tomllib.loads(head + rows))
            check(f"refused: {label}", False)
        except PartFileError as error:
            check(f"refused: {label}", "offset" in str(error) or "L9" in str(error), str(error))
    s.delete(line)
    check("deleting the line drops its offset", not s.offsets)

    part, s = sketch_part()
    c = s.add_curve("parabola", focal="1", **{"from": "0", "to": "2"})
    s.add_offset(c, left="0.2")
    w = s.curve_to_spline(c, rebuild(part).outputs["S"].shapes[c], 1e-3, keep=False)
    check("a curve converted to a spline: the offset goes with it", s.offset(w) is not None and s.offset(c) is None
          and [r.key for r in find_regions(rebuild(part).outputs["S"])] == [(f"{w}.offset",)])


def check_window():
    from vispy import app

    app.use_app("pyside6")
    app.use_app().create()
    from vispy_errors import install as _watch_vispy

    vispy_errors = _watch_vispy()
    from PySide6.QtWidgets import QComboBox, QFormLayout, QLineEdit, QPushButton

    from check_sketch_line import hover, pixel, settle, shield
    from view.main_window import MainWindow

    def rows(window):
        out = {}
        form = window.props_form
        for i in range(form.rowCount()):
            label = form.itemAt(i, QFormLayout.ItemRole.LabelRole)
            field = form.itemAt(i, QFormLayout.ItemRole.FieldRole)
            span = form.itemAt(i, QFormLayout.ItemRole.SpanningRole)
            if label is not None and field is not None:
                out[label.widget().text()] = field.widget()
            elif span is not None and isinstance(span.widget(), QPushButton):
                out[span.widget().text()] = span.widget()
        return out

    part, s = sketch_part()
    p = [s.add_point(x, y) for x, y in (("-20", "0"), ("0", "10"), ("20", "0"))]
    w = s.add_spline(p[0], p[1])
    s.extend_spline(w, p[2])
    s.add_line(s.add_point("-20", "-20"), s.add_point("20", "-20"))
    s.add_circle(s.add_point("30", "20"), "5")
    path = os.path.join(TMP, "offset.part.toml")
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
    window.edit_sketch("S")
    settle()
    view, native = window.view, window.view.canvas.native
    sketch = lambda: window.part.feature("S")               # noqa: E731

    def pick(*names):
        view.select_keys([("entity", "S", n) for n in names])
        window._on_view_select(view.selected)
        settle()

    pick(w)
    fields = rows(window)
    check("a spline selected: an Offset button", "Offset" in fields, f"{list(fields)}")
    fields["Offset"].click()
    settle()
    fields = rows(window)
    check("  pressed: 1 mm to the left; its fields, caps (open), Remove Offset",
          sketch().offset(w) is not None and sketch().offset(w).left == "1"
          and {"Offset left:", "Offset right:", "Start cap:", "End cap:", "Remove Offset"} <= set(fields)
          and isinstance(fields["Offset left:"], QLineEdit) and isinstance(fields["End cap:"], QComboBox),
          f"{list(fields)}")
    fields["Offset right:"].setText("0.5")
    fields["Offset right:"].editingFinished.emit()
    settle()
    rows(window)["End cap:"].setCurrentText("round")
    settle()
    regions = [r.key for r in find_regions(window.result.outputs["S"])]
    check("  right 0.5, end round: the band is a profile", sketch().offset(w).right == "0.5"
          and sketch().offset(w).end == "round" and (f"{w}.offset",) in regions, f"{regions}")
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    for side, value, then in (("left", "2", "right"), ("right", "0", "left")):
        field = rows(window)[f"Offset {side}:"]       # typed, then a click on the other field (it crashed:
        field.setFocus()                              # the props rebuilt inside the focus-out deleted it)
        settle(30)
        field.selectAll()
        QTest.keyClicks(field, value)
        QTest.mouseClick(rows(window)[f"Offset {then}:"], Qt.LeftButton)
        settle(150)
    check("  typed, then a click on the other field (twice): applied, no crash",
          (sketch().offset(w).left, sketch().offset(w).right) == ("2", "0"), f"{sketch().offset(w)}")
    view.select(None)
    band = window.result.outputs["S"].offsets[w][0][2]
    hover(native, pixel(view, view.sketch_frame, tuple(band[10])))
    settle()
    check("  hovering the band takes the spline", view.hovered == ("entity", "S", w), f"{view.hovered}")
    pick(w)
    rows(window)["Remove Offset"].click()
    settle()
    check("  Remove Offset: gone, its band no profile any more", sketch().offset(w) is None
          and all(r.key != (f"{w}.offset",) for r in find_regions(window.result.outputs["S"])))

    pick("L1", "C1")
    fields = rows(window)
    check("a line and a circle selected: Offset (2)", "Offset (2)" in fields, f"{list(fields)}")
    fields["Offset (2)"].click()
    settle()
    pick("L1", "C1")
    fields = rows(window)
    keys = sorted(r.key for r in find_regions(window.result.outputs["S"]))
    check("  both have one: three profiles (the band, the ring, the disc); Remove Offset (2)",
          keys == [("C1.offset",), ("C1.offset.inner",), ("L1.offset",)] and "Remove Offset (2)" in fields,
          f"{keys} {list(fields)}")
    fields["Remove Offset (2)"].click()
    settle()
    check("  removed both", not sketch().offsets)
    check("no error dialogs", not errors, f"{errors}")
    window.close()
    check("no errors in VisPy callbacks", not vispy_errors, f"{vispy_errors[:3]}")


def main():
    check_line()
    check_circle()
    check_parabola()
    check_closed_spline()
    check_file()
    if "--no-window" not in sys.argv:
        check_window()
    print("FAILED: " + ", ".join(FAILED) if FAILED else "all checks passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
