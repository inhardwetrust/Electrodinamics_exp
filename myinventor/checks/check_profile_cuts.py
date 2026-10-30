# check_profile_cuts.py
#
# Regions cut where sketch curves cross (model/profiles.py), extruded by 1
# so the volume is the area (exact edges: the kernel's volume):
#   - a circle r 5 crossed by a line through its center: two halves of
#     25 pi / 2 each, named by their pieces ("C1:0" / "C1:1", "L1:1"); the
#     line through the circle's start (angle 0) too;
#   - a rectangle 10 x 10 cut by a line from the middle of its bottom to
#     the middle of its top (ends on the sides' middles: touching cuts):
#     two of 50;
#   - two lines crossing in a rectangle: four triangles of 25;
#   - the user's case: a band (Offset) along a parabola crossed by a line
#     across it: two halves, equal (symmetric), summing to the whole band;
#   - a closed spline crossed by a line: two pieces summing to its area;
#   - the whole loops stay nameable: a rectangle with a circle over its
#     edge: the rectangle (L1..L4) and the circle (C1) whole, the pieces
#     besides; an extrusion of the whole rectangle still 100;
#   - a line ending 0.4 um off another (points are stored rounded to 1 um):
#     still cuts it, the pieces exact (33.33); 2 um off: not;
#   - the window: Extrude offers the two halves of the cut circle; the Line
#     tool snaps onto a band's two sides (a parabola and its fitted offset,
#     clicked a pixel off): the line cuts the band in two.
#
#   python checks/check_profile_cuts.py [--no-window]

import math
import os
import sys
import tempfile

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "checks"))
sys.stdout.reconfigure(encoding="utf-8")
TMP = tempfile.mkdtemp()
os.environ["MYCAD_SETTINGS"] = os.path.join(TMP, "settings.json")     # never the user's settings

from geometry import kernel                                           # noqa: E402
from model.extrude import Extrude                                     # noqa: E402
from model.part import Part                                           # noqa: E402
from model.part_file import dumps                                     # noqa: E402
from model.profiles import find_regions, whole_regions                # noqa: E402
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


def volume(part, key):
    """Profile `key` of S extruded by 1 alone: its volume (None if it fails)."""
    copy = Part.from_dict(part.to_dict())
    copy.add_feature(Extrude("E", profiles=[key], sketch="S", distance="1", body="B"))
    result = rebuild(copy)
    return kernel.volume(result.shapes["B"]) if "B" in result.shapes else None


def regions_of(part):
    return find_regions(rebuild(part).outputs["S"])


def line(s, a, b):
    return s.add_line(s.add_point(*a), s.add_point(*b))


def rectangle(s, w, h):
    p = [s.add_point(x, y) for x, y in (("0", "0"), (w, "0"), (w, h), ("0", h))]
    return [s.add_line(a, b) for a, b in zip(p, p[1:] + p[:1])]


def check_circle():
    for a, b, label in ((("-10", "0"), ("10", "0"), "through the circle's start (angle 0)"),
                        (("-7", "-7"), ("7", "7"), "at 45 degrees")):
        part, s = sketch_part()
        s.add_circle(s.add_point("0", "0"), "5")
        line(s, a, b)
        regions = regions_of(part)
        keys = sorted(r.key for r in regions)
        volumes = [volume(part, k) for k in keys]
        check(f"a circle r 5 crossed by a line {label}: two halves of 25 pi / 2, named by their pieces",
              keys == [("C1:0", "L1:1"), ("C1:1", "L1:1")]
              and all(v is not None and math.isclose(v, 12.5 * math.pi, rel_tol=1e-9) for v in volumes),
              f"{keys} {volumes}")


def check_rectangles():
    part, s = sketch_part()
    rectangle(s, "10", "10")
    line(s, ("5", "0"), ("5", "10"))
    regions = regions_of(part)
    volumes = sorted(volume(part, r.key) for r in regions)
    check("a rectangle cut by a line between the middles of two sides (touching): two of 50",
          len(regions) == 2 and all(math.isclose(v, 50, rel_tol=1e-9) for v in volumes),
          f"{[r.key for r in regions]} {volumes}")

    part, s = sketch_part()
    rectangle(s, "10", "10")
    line(s, ("1", "1"), ("9", "9"))
    line(s, ("1", "9"), ("9", "1"))
    regions = regions_of(part)
    check("two crossing lines inside a rectangle (ends free): they cut nothing off, the rectangle whole",
          [r.key for r in regions] == [("L1", "L2", "L3", "L4")], f"{[r.key for r in regions]}")
    part, s = sketch_part()
    rectangle(s, "10", "10")
    line(s, ("0", "0"), ("10", "10"))
    line(s, ("0", "10"), ("10", "0"))
    regions = regions_of(part)
    volumes = [volume(part, r.key) for r in regions]
    check("both diagonals: four triangles of 25", len(regions) == 4
          and all(v is not None and math.isclose(v, 25, rel_tol=1e-9) for v in volumes), f"{volumes}")


def check_band():
    part, s = sketch_part()
    c = s.add_curve("parabola", focal="10", **{"from": "-20", "to": "20"})
    s.add_offset(c, left="1")
    line(s, ("0", "-5"), ("0", "15"))
    regions = regions_of(part)
    whole = Part.from_dict(part.to_dict())
    whole.feature("S").lines.clear()
    band = volume(whole, (f"{c}.offset",))
    halves = [volume(part, r.key) for r in regions]
    check("the user's case: a band along a parabola cut across by a line: two equal halves, the whole band",
          len(regions) == 2 and None not in halves and math.isclose(halves[0], halves[1], rel_tol=1e-7)
          and math.isclose(sum(halves), band, rel_tol=1e-7), f"{[r.key for r in regions]} {halves} {band}")


def check_spline():
    part, s = sketch_part()
    ring = [s.add_point(x, y) for x, y in (("10", "0"), ("0", "6"), ("-10", "0"), ("0", "-6"))]
    w = s.add_spline(ring[0], ring[1])
    for n in ring[2:] + ring[:1]:
        s.extend_spline(w, n)
    whole = volume(part, (w,))
    line(s, ("-3", "-10"), ("2", "10"))
    regions = regions_of(part)
    pieces = [volume(part, r.key) for r in regions]
    check("a closed spline crossed by a line: two pieces summing to its area", len(regions) == 2
          and None not in pieces and math.isclose(sum(pieces), whole, rel_tol=1e-9), f"{pieces} {whole}")


def check_whole():
    part, s = sketch_part()
    rectangle(s, "10", "10")
    s.add_circle(s.add_point("10", "5"), "3")
    out = rebuild(part).outputs["S"]
    whole = sorted(r.key for r in whole_regions(out))
    pieces = sorted(r.key for r in find_regions(out))
    check("a rectangle with a circle over its edge: the whole loops keep their names, the pieces besides",
          whole == [("C1",), ("L1", "L2", "L3", "L4")] and len(pieces) == 3 and ("C1",) not in pieces,
          f"{whole} {pieces}")
    v = volume(part, ("L1", "L2", "L3", "L4"))
    check("  an extrusion of the whole rectangle (its old name) still 100", v is not None and math.isclose(v, 100),
          f"{v}")


def check_near():
    for y0, cut in (("0.0004", True), ("0.002", False)):
        part, s = sketch_part()
        rectangle(s, "10", "10")
        line(s, ("3.333", y0), ("3.333", "10"))
        regions = regions_of(part)
        volumes = sorted(volume(part, r.key) for r in regions)
        want = [33.33, 66.67] if cut else [100.0]
        check(f"a line ending {y0} mm off the bottom: {'cuts it' if cut else 'does not cut'} ({want})",
              len(volumes) == len(want) and all(math.isclose(v, w, rel_tol=1e-9) for v, w in zip(volumes, want)),
              f"{[r.key for r in regions]} {volumes}")


def check_window():
    from vispy import app

    app.use_app("pyside6")
    app.use_app().create()
    from vispy_errors import install as _watch_vispy

    vispy_errors = _watch_vispy()
    from check_sketch_line import settle
    from view.main_window import MainWindow

    part, s = sketch_part()
    s.add_circle(s.add_point("0", "0"), "5")
    line(s, ("-10", "0"), ("10", "0"))
    path = os.path.join(TMP, "cut.part.toml")
    with open(path, "w", encoding="utf-8") as f:
        f.write(dumps(part))
    window = MainWindow()
    window.ask_save_changes = lambda: "discard"
    errors = []
    window.show_error = errors.append
    window.resize(1200, 800)
    window.show()
    settle(300)
    window.open_file(path)
    settle()
    window.start_extrude()
    settle()
    keys = sorted(item[0][2] for item in window.view._profiles)
    check("the window: Extrude offers the two halves of the cut circle",
          keys == [("C1:0", "L1:1"), ("C1:1", "L1:1")], f"{keys}")
    window.extrude_panel.reject()
    settle()

    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    from check_sketch_line import click, pixel, shield

    part, s = sketch_part()
    c = s.add_curve("parabola", focal="10", **{"from": "-20", "to": "20"})
    s.add_offset(c, left="1")
    path = os.path.join(TMP, "band.part.toml")
    with open(path, "w", encoding="utf-8") as f:
        f.write(dumps(part))
    window.open_file(path)
    window.edit_sketch("S")
    settle()
    shield(window)
    view, native = window.view, window.view.canvas.native
    frame = view.sketch_frame
    band = window.result.outputs["S"].offsets[c][0][2]
    outer = band[np.argmin(np.where(band[:, 1] > 1.2, np.abs(band[:, 0] - 5), np.inf))]     # the offset side at x 5
    mm = view._mm_per_px()
    window.line_action.trigger()
    settle(100)
    click(native, pixel(view, frame, (5, 0.625 - 1.2 * mm)))      # a pixel under the parabola
    click(native, pixel(view, frame, tuple(outer + (0, 1.2 * mm))))  # a pixel over the offset side
    QTest.keyClick(native, Qt.Key_Escape)
    settle()
    out = window.result.outputs["S"]
    drawn = window.part.feature("S").line("L1")
    ends = sorted((out.points[drawn.start], out.points[drawn.end]), key=lambda xy: xy[1])
    on_parabola = abs(ends[0][1] - ends[0][0] ** 2 / 40) <= 1e-3
    regions = find_regions(out)
    check("  Line snapped onto both sides of a band (a pixel off each): its ends on them, the band cut in two",
          on_parabola and len(regions) == 2 and all("L1" in r.key for r in regions),
          f"{ends} {[r.key for r in regions]}")
    check("no error dialogs", not errors, f"{errors}")
    window.close()
    check("no errors in VisPy callbacks", not vispy_errors, f"{vispy_errors[:3]}")


def main():
    check_circle()
    check_rectangles()
    check_band()
    check_spline()
    check_whole()
    check_near()
    if "--no-window" not in sys.argv:
        check_window()
    print("FAILED: " + ", ".join(FAILED) if FAILED else "all checks passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
