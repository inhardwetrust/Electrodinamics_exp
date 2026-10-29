# check_curves.py
#
# Generators (model/curves.py): sketch curves from a few arguments.
#   - the registry: parabola and function, their declared arguments;
#   - a parabola y = x^2 / (4 f) from -2 to 2 (f = 1), closed by a line
#     between its ends: one profile, key ["L1", "Parabola1"], extruded by 1:
#     the volume 8/3 exactly (the edge is OCC's exact parabola);
#   - half of it (0..2) closed by two lines to the axis: 4/3 (a reflector's
#     profile: a line and the curve share both ends' neighbourhoods);
#   - a vertex moved (x0, y0): the profile and the body follow;
#   - a function (x = t, y = 1 + 0.5 sin(36 t), t 0..10, degrees) over the
#     x axis: the volume 10 within the fit tolerance; its kernel edge is one
#     B-spline within FIT_TOLERANCE of the curve (and OCC's edge is that
#     B-spline);
#   - the function's variable t hides a parameter named t inside x and y:
#     a parameter t = 0.035 does not change the curve; renaming that
#     parameter leaves x and y alone (and renames it in `from`); the
#     parameter is not counted as used by x, y;
#   - a TOML round trip; refused rows: an unknown kind, a missing required
#     argument, an unknown argument;
#   - a curve that fails (focal 0): the sketch fails naming the curve, but
#     shows; what uses its ends is left out of the result;
#   - delete: a curve takes the lines on its ends; a curve's end is not an
#     entity of its own (kind_of raises);
#   - Deparametrize leaves curves (and their ends) alone.
#   - the spline (model/curves.spline_function): through every point, C2,
#     natural ends; in the kernel one B-spline edge, the spline itself
#     (numpy and OCC agree to 1e-9 mm); closed by a line and extruded: its
#     area; closed on itself: smooth across the seam, a profile of its own;
#     STEP: one B-spline curve, the volume kept; points in a row: a straight
#     B-spline; TOML round trip and
#     refusals; delete a point / the spline;
#   - the window: Spline tool (curved preview, closing click), hover, info,
#     dragging a point, copy / paste, extrude.
#   - a curve to a spline (Convert / Copy as Spline): plain points, within
#     1 um, lines and the extrusion's profile moved, 4/3 kept; the copy's
#     curve hidden (not in profiles, visible = false, it moves alone); a
#     closed function (an ellipse): a closed spline of area pi 60;
#   - the browser: the curve's row (tick: shown, select: its arguments and
#     the spline buttons, its menu), both actions from the window.
#
#   python checks/check_curves.py [--no-window]

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

import numpy as np                                                    # noqa: E402

from geometry import kernel                                           # noqa: E402
from model.curves import FIT_TOLERANCE, KINDS, bspline_at             # noqa: E402
from model.extrude import Extrude                                     # noqa: E402
from model.part import Part                                           # noqa: E402
from model.part_file import PartFileError, dumps, from_data           # noqa: E402
from model.profiles import find_regions                               # noqa: E402
from model.rebuild import rebuild                                     # noqa: E402
from model.sketch import Sketch                                       # noqa: E402

FAILED = []


def occ_points(frame, edge, u):
    """(N, 2) points of OCC's edge built from a "bspline" kernel edge, at its parameters u (an XY sketch)."""
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.GeomAbs import GeomAbs_BSplineCurve

    curve = BRepAdaptor_Curve(kernel._bspline(frame, *edge[1:]))
    assert curve.GetType() == GeomAbs_BSplineCurve
    return np.array([[curve.Value(float(v)).X(), curve.Value(float(v)).Y()] for v in u])


def check(label, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {label} {detail}")
    if not ok:
        FAILED.append(label)


def parabola_part(lo="-2", hi="2", half=False, x0="0", y0="0"):
    part = Part()
    part.add_parameter("f", "1")
    s = part.add_feature(Sketch("S", plane="XY Plane"))
    c = s.add_curve("parabola", focal="f", **{"from": lo, "to": hi}, x0=x0, y0=y0)
    start, end = f"{c}.start", f"{c}.end"
    if half:                                    # end (hi, ...) -> the axis point -> start (on the axis)
        p = s.add_point(x0, f"{y0} + ({hi})^2 / (4 * f)")
        s.add_line(end, p)
        s.add_line(p, start)
    else:
        s.add_line(end, start)
    return part, s, c


def volume_of(part, key, distance="1"):
    part.add_feature(Extrude("E", profiles=[key], sketch="S", distance=distance, body="B"))
    result = rebuild(part)
    shape = result.shapes.get("B")
    return (kernel.volume(shape) if shape is not None else None), result


def check_parabola():
    check("the registry: parabola and function with their arguments",
          {"parabola", "function"} <= set(KINDS)
          and [a.name for a in KINDS["parabola"].ARGS] == ["focal", "from", "to", "x0", "y0"]
          and [a.name for a in KINDS["function"].ARGS] == ["x", "y", "from", "to"])
    part, s, c = parabola_part()
    out = rebuild(part).outputs["S"]
    regions = find_regions(out)
    check("a parabola closed by a line: one profile, keyed by the line and the curve",
          [r.key for r in regions] == [("L1", "Parabola1")], f"{[r.key for r in regions]}")
    check("  its ends are points of the sketch", np.allclose(out.points["Parabola1.start"], (-2, 1))
          and np.allclose(out.points["Parabola1.end"], (2, 1)))
    v, result = volume_of(part, ("L1", "Parabola1"))
    check("  extruded by 1: 8/3 exactly (OCC's parabola)", v is not None and math.isclose(v, 8 / 3, rel_tol=1e-9),
          f"{v} {result.feature_errors}")

    part, s, c = parabola_part("0", "2", half=True)
    regions = find_regions(rebuild(part).outputs["S"])
    v, result = volume_of(part, regions[0].key if regions else ())
    check("half of it closed by two lines to the axis: 4/3", v is not None and math.isclose(v, 4 / 3, rel_tol=1e-9),
          f"{[r.key for r in regions]} {v} {result.feature_errors}")
    part, s, c = parabola_part("2", "0", half=False)
    regions = find_regions(rebuild(part).outputs["S"])
    v, result = volume_of(part, ("L1", "Parabola1"))
    check("from 2 to 0 (the curve run the other way), closed by its chord: 1/3 exactly",
          [r.key for r in regions] == [("L1", "Parabola1")] and v is not None
          and math.isclose(v, 1 / 3, rel_tol=1e-9), f"{[(r.key, r.area) for r in regions]} {v}")

    part, s, c = parabola_part(x0="3", y0="-1")
    v, result = volume_of(part, ("L1", "Parabola1"))
    lo, hi = kernel.bounds(result.shapes["B"])
    check("the vertex moved to (3, -1): the body follows", math.isclose(v, 8 / 3, rel_tol=1e-9)
          and np.allclose(lo, (1, -1, 0), atol=1e-6) and np.allclose(hi, (5, 0, 1), atol=1e-6), f"{lo} {hi}")


def function_part(t_param=None):
    part = Part()
    if t_param is not None:
        part.add_parameter("t", t_param)
    part.add_parameter("L", "10")
    s = part.add_feature(Sketch("S", plane="XY Plane"))
    c = s.add_curve("function", x="t", y="1 + 0.5 * sin(36 * t)", **{"from": "0", "to": "L"})
    a, b = s.add_point("L", "0"), s.add_point("0", "0")
    s.add_line(f"{c}.end", a)
    s.add_line(a, b)
    s.add_line(b, f"{c}.start")
    return part, s, c


def check_function():
    part, s, c = function_part()
    out = rebuild(part).outputs["S"]
    shape = out.shapes[c]
    edge = shape.edges[0]
    t = np.linspace(0, 10, 20001)               # its parameter runs with t: 0..spans for t 0..10
    u = t / 10 * (len(edge[2]) - 1)
    exact = np.stack([t, 1 + 0.5 * np.sin(np.radians(36 * t))], axis=1)
    worst = float(np.linalg.norm(bspline_at(edge, u) - exact, axis=1).max())
    occ = float(np.abs(occ_points(out.frame, edge, u[::50]) - bspline_at(edge, u[::50])).max())
    check("a function: one B-spline edge within the tolerance of the curve; OCC's edge is it",
          len(shape.edges) == 1 and edge[0] == "bspline" and worst <= FIT_TOLERANCE and occ < 1e-9,
          f"{len(edge[2]) - 1} spans, worst {worst:.2e} mm, OCC {occ:.1e}")
    regions = find_regions(out)
    v, result = volume_of(part, regions[0].key if regions else ())
    check("  closed over the x axis, extruded by 1: 10 (the sine's area cancels)",
          v is not None and math.isclose(v, 10, abs_tol=10 * FIT_TOLERANCE), f"{v} {result.feature_errors}")

    part, s, c = function_part("0.035")
    out = rebuild(part).outputs["S"]
    check("a parameter t: inside x and y the curve's own t wins", np.allclose(out.points[f"{c}.end"],
                                                                              (10, 1), atol=1e-9),
          f"{out.points[f'{c}.end']}")
    check("  t is not counted as used by x, y", part.parameter_users("t") == [], f"{part.parameter_users('t')}")
    s.gen_curve(c).args["to"] = "L + t * 0"
    part.rename_parameter("t", "thick")
    args = s.gen_curve(c).args
    check("  renaming the parameter t: x, y untouched, `to` renamed",
          args["x"] == "t" and args["y"].startswith("1 + 0.5 * sin(36 * t)") and args["to"] == "L + thick * 0",
          f"{args}")


def check_file():
    part, s, c = parabola_part()
    text = dumps(part)
    check("TOML round trip", from_data(tomllib.loads(text)).to_dict() == part.to_dict()
          and "[[feature.curve]]" in text and 'kind = "parabola"' in text, text[text.find("[[feature.curve]]"):][:160])
    head = 'format = "mycad-part"\nversion = 1\n[[feature]]\nname = "S"\ntype = "sketch"\nplane = "XY Plane"\n'
    for label, row in (("an unknown kind", 'kind = "spiral"\n'),
                       ("a missing required argument", 'kind = "parabola"\nfocal = "1"\nfrom = "0"\n'),
                       ("an unknown argument", 'kind = "parabola"\nfocal = "1"\nfrom = "0"\nto = "1"\nsize = "2"\n')):
        try:
            from_data(tomllib.loads(head + '[[feature.curve]]\nname = "K"\n' + row))
            check(f"refused: {label}", False)
        except PartFileError as error:
            check(f"refused: {label}", "curve" in str(error), str(error))
    text = head + '[[feature.curve]]\nname = "K"\nkind = "parabola"\nfocal = 2\nfrom = -1\nto = 1\n'
    k = from_data(tomllib.loads(text)).feature("S").gen_curve("K")
    check("numbers in the file read as formulas; defaults filled", k.args == {"focal": "2", "from": "-1", "to": "1",
                                                                          "x0": "0", "y0": "0"}, f"{k.args}")


def check_failures():
    part, s, c = parabola_part()
    s.gen_curve(c).args["focal"] = "0"
    result = rebuild(part)
    out = result.outputs.get("S")
    check("focal 0: the sketch fails naming the curve, but shows; the line on its ends left out",
          "curve Parabola1" in result.feature_errors.get("S", "") and out is not None and not out.lines,
          f"{result.feature_errors}")
    part, s, c = parabola_part("0", "2", half=True)
    s.delete(c)
    check("deleting a curve takes the lines on its ends (and their free points)",
          not s.curves and not s.lines and not s.points, f"{s.lines} {s.points}")
    part, s, c = parabola_part()
    try:
        s.kind_of(f"{c}.start")
        check("a curve's end is not an entity", False)
    except KeyError:
        check("a curve's end is not an entity", True)
    check("Deparametrize leaves curves and their ends alone",
          s.formula_slots([c, "L1"]) == [] and s.deparametrize({"f": 1.0}) == ([], {}))


def check_splines():
    from model.curves import spline_function

    pts = np.array([[0, 0], [3, 4], [7, 5], [10, 0]], float)
    at, length = spline_function(pts)
    knots = np.concatenate([[0], np.cumsum(np.linalg.norm(np.diff(pts, axis=0), axis=1))])
    check("a spline passes through every point", np.allclose(at(knots), pts, atol=1e-12))
    s = np.linspace(0, length, 20001)
    d1 = np.gradient(at(s), s, axis=0)
    d2 = np.gradient(d1, s, axis=0)
    check("  C2: no jump of the tangent or the curvature along it",
          np.abs(np.diff(d1, axis=0)).max() < 1e-2 and np.abs(np.diff(d2, axis=0)).max() < 5e-2)
    check("  natural ends (no curvature there)", np.abs(d2[[2, -3]]).max() < 1e-2, f"{d2[[2, -3]]}")

    part = Part()
    sk = part.add_feature(Sketch("S", plane="XY Plane"))
    names = [sk.add_point(str(x), str(y)) for x, y in pts]
    w = sk.add_spline(names[0], names[1])
    for n in names[2:]:
        sk.extend_spline(w, n)
    sk.add_line(names[-1], names[0])
    out = rebuild(part).outputs["S"]
    edges = out.shapes[w].edges
    worst = float(np.abs(bspline_at(edges[0], s) - at(s)).max()) if edges[0][0] == "bspline" else np.inf
    occ = float(np.abs(occ_points(out.frame, edges[0], s[::100]) - at(s[::100])).max())
    check("the spline in the kernel: one B-spline edge, the spline itself (numpy, OCC)",
          len(edges) == 1 and worst < 1e-9 and occ < 1e-9, f"{worst:.1e} {occ:.1e}")
    regions = find_regions(out)
    curve = at(s)
    area = 0.5 * abs(float(np.dot(curve[:-1, 0], curve[1:, 1]) - np.dot(curve[1:, 0], curve[:-1, 1])))
    v, result = volume_of(part, regions[0].key if regions else ())
    check("  closed by a line, extruded by 1: the spline's area (integrated over 20000 pieces)",
          [r.key for r in regions] == [("L1", w)] and v is not None and math.isclose(v, area, rel_tol=1e-6),
          f"{[r.key for r in regions]} {v} vs {area}")

    part = Part()
    sk = part.add_feature(Sketch("S", plane="XY Plane"))
    ring = [sk.add_point(x, y) for x, y in (("10", "0"), ("0", "6"), ("-10", "0"), ("0", "-6"))]
    w = sk.add_spline(ring[0], ring[1])
    for n in ring[2:] + ring[:1]:
        sk.extend_spline(w, n)
    out = rebuild(part).outputs["S"]
    at, length = spline_function(np.array([out.points[n] for n in ring + ring[:1]]))
    eps = 1e-6
    seam = (at([length])[0] - at([length - eps])[0]) / eps, (at([eps])[0] - at([0.0])[0]) / eps
    check("a closed spline (the last point = the first): smooth across the seam, one loop of its own",
          np.allclose(seam[0], seam[1], atol=1e-4) and [r.key for r in find_regions(out)] == [(w,)],
          f"{seam} {[r.key for r in find_regions(out)]}")
    check_spline_step(part, w)

    part = Part()
    sk = part.add_feature(Sketch("S", plane="XY Plane"))
    line = [sk.add_point(str(x), "0") for x in (0, 2, 7, 10)]
    w = sk.add_spline(line[0], line[1])
    for n in line[2:]:
        sk.extend_spline(w, n)
    edges = rebuild(part).outputs["S"].shapes[w].edges
    check("points in a row: a straight B-spline", len(edges) == 1 and np.abs(edges[0][1][:, 1]).max() < 1e-12)

    text = dumps(part)
    check("TOML round trip of a spline", from_data(tomllib.loads(text)).to_dict() == part.to_dict()
          and "[[feature.spline]]" in text)
    head = ('format = "mycad-part"\nversion = 1\n[[feature]]\nname = "S"\ntype = "sketch"\nplane = "XY Plane"\n'
            '[[feature.point]]\nname = "P1"\nx = "0"\ny = "0"\n[[feature.point]]\nname = "P2"\nx = "1"\ny = "0"\n')
    for label, points in (("a missing point", '["P1", "P9"]'), ("a point twice", '["P1", "P2", "P1", "P2"]'),
                          ("one point", '["P1"]')):
        try:
            from_data(tomllib.loads(head + f'[[feature.spline]]\nname = "Spline1"\npoints = {points}\n'))
            check(f"refused spline: {label}", False)
        except PartFileError as error:
            check(f"refused spline: {label}", "spline" in str(error), str(error))

    sk.delete(line[1])
    check("a point deleted: the spline runs through the others", sk.spline(w).points == [line[0]] + line[2:])
    check("  kind_of says spline; Deparametrize takes its points' formulas",
          sk.kind_of(w) == "spline" and [lbl for lbl, _, _ in sk.formula_slots([w])] == [])
    sk.point(line[2]).x = "2 * 3.5"
    check("  (a formula point of it is listed)", [lbl for lbl, _, _ in sk.formula_slots([w])] == [f"{line[2]}.x"])
    sk.delete(w)
    check("deleting the spline takes its points", not sk.splines and not sk.points)


def check_spline_step(part, spline):
    """A closed spline extruded by 2, written to STEP: one B-spline curve per cap, one side face; read back."""
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPControl import STEPControl_Reader

    from geometry.step_io import export_part

    part.add_feature(Extrude("E", profiles=[(spline,)], sketch="S", distance="2", body="B"))
    result = rebuild(part)
    v = kernel.volume(result.shapes["B"])
    path = os.path.join(TMP, "spline.step")
    export_part(path, part, result)
    text = open(path, encoding="utf-8", errors="replace").read()
    reader = STEPControl_Reader()
    ok = reader.ReadFile(path) == IFSelect_RetDone
    reader.TransferRoots()
    back = kernel.volume(reader.OneShape()) if ok else None
    from model.curves import spline_function

    out = result.outputs["S"]
    at, length = spline_function(np.array([out.points[p] for p in part.feature("S").spline(spline).points]))
    curve = at(np.linspace(0, length, 200001))
    area = 0.5 * abs(float(np.dot(curve[:-1, 0], curve[1:, 1]) - np.dot(curve[1:, 0], curve[:-1, 1])))
    check("a closed spline extruded by 2: its area x 2; STEP has B-spline curves, small, the same volume back",
          math.isclose(v, 2 * area, rel_tol=1e-8) and "B_SPLINE_CURVE_WITH_KNOTS" in text
          and os.path.getsize(path) < 40000 and back is not None and math.isclose(back, v, rel_tol=1e-9),
          f"{v} vs {2 * area}, {os.path.getsize(path)} bytes, back {back}")


def reflector_part():
    """Half a parabola (f = 1, 0..2) closed by two lines to the axis, extruded by 1 (4/3)."""
    part, s, c = parabola_part("0", "2", half=True)
    part.add_feature(Extrude("E", profiles=[("L1", "L2", c)], sketch="S", distance="1", body="B"))
    return part, s, c


def check_curve_to_spline():
    from model.curves import spline_shape
    from model.sketch import _is_number

    for keep in (False, True):
        part, s, c = reflector_part()
        shape = rebuild(part).outputs["S"].shapes[c]
        w = s.curve_to_spline(c, shape, 1e-3, keep)
        part.replace_in_profiles("S", c, w)
        result = rebuild(part)
        spline = s.spline(w)
        xy = np.array([[float(s.point(p).x), float(s.point(p).y)] for p in spline.points])
        curve = spline_shape(xy).points
        off = float(np.abs(curve[:, 1] - curve[:, 0] ** 2 / 4).max())
        v = kernel.volume(result.shapes["B"]) if "B" in result.shapes else None
        how = "Copy as Spline" if keep else "Convert to Spline"
        check(f"{how}: points with plain numbers, the spline within 1 um of the parabola",
              all(_is_number(s.point(p).x) and _is_number(s.point(p).y) for p in spline.points) and off <= 1e-3,
              f"{len(spline.points)} points, off {off:.1e} mm")
        check("  the lines on its ends now on the spline's; the extrusion's profile names the spline; 4/3",
              {s.line("L1").start, s.line("L2").end} == {spline.points[0], spline.points[-1]}
              and part.feature("E").profiles == [tuple(sorted(("L1", "L2", w)))] and not result.feature_errors
              and v is not None and math.isclose(v, 4 / 3, abs_tol=2.5e-3),
              f"{part.feature('E').profiles} {v} {result.feature_errors}")
        if keep:
            text = dumps(part)
            regions = find_regions(result.outputs["S"])
            check("  the curve stays, hidden: not drawn, not in profiles; visible = false in the file",
                  not s.gen_curve(c).visible and c in result.outputs["S"].shapes
                  and c not in [x.name for x in result.outputs["S"].curves]
                  and all(c not in r.key for r in regions) and "visible = false" in text
                  and from_data(tomllib.loads(text)).to_dict() == part.to_dict(), f"{[r.key for r in regions]}")
            s.gen_curve(c).args["focal"] = "2"
            result = rebuild(part)
            check("  its focal changed: the hidden curve moves, the spline (and the body) stay",
                  np.allclose(result.outputs["S"].points[f"{c}.end"], (2, 0.5))
                  and math.isclose(kernel.volume(result.shapes["B"]), v, rel_tol=1e-12))
        else:
            check("  the curve is gone", not s.curves)

    part = Part()
    sk = part.add_feature(Sketch("S", plane="XY Plane"))
    ring = sk.add_curve("function", x="10 * cos(360 * t)", y="6 * sin(360 * t)")
    shape = rebuild(part).outputs["S"].shapes[ring]
    w = sk.curve_to_spline(ring, shape, 1e-3, False)
    points = sk.spline(w).points
    result = rebuild(part)
    area = find_regions(result.outputs["S"])[0].area if find_regions(result.outputs["S"]) else 0
    check("a closed function (an ellipse 10 x 6): a closed spline, its area pi 60",
          points[0] == points[-1] and len(points) > 4 and math.isclose(area, math.pi * 60, rel_tol=1e-4),
          f"{len(points)} points, area {area}")


def check_window_tree(window, settle):
    """A curve's browser row: tick = shown, select = its arguments and the spline buttons, the menu."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QPushButton

    part, _, c = reflector_part()
    path = os.path.join(TMP, "reflector.part.toml")
    with open(path, "w", encoding="utf-8") as f:
        f.write(dumps(part))
    window.open_file(path)
    settle()
    key = ("entity", "S", c)
    row = window._rows.get(key)
    check("the browser: a row for the curve under its sketch, ticked, in sight",
          row is not None and row.parent() is window._rows[("feature", "S")]
          and row.checkState(0) == Qt.Checked and row.parent().isExpanded())
    window.tree.setCurrentItem(row)
    settle()
    buttons = [window.props_form.itemAt(i, window.props_form.ItemRole.SpanningRole) for i in
               range(window.props_form.rowCount())]
    labels = [b.widget().text() for b in buttons if b is not None and isinstance(b.widget(), QPushButton)]
    check("  selected: its arguments, Convert / Copy as Spline; the used sketch shows",
          view_selected(window) == key and "parabola curve" in window.info.text()
          and labels == ["Convert to Spline", "Copy as Spline"] and "S" in window.view._sketch_geometry,
          f"{labels}")
    steps = len(window.doc._undo)
    row.setCheckState(0, Qt.Unchecked)
    settle()
    out = window.result.outputs["S"]
    check("  unticked: hidden (one undo step), no profile with it: the extrusion fails, red",
          not window.part.feature("S").gen_curve(c).visible and len(window.doc._undo) == steps + 1
          and "E" in window.result.feature_errors and c not in window.view._sketch_geometry["S"].seg_owner,
          f"{window.result.feature_errors}")
    window.undo()
    settle()
    texts = [t for t, _ in window.context_actions(window._rows[key])]
    check("  undone: shown again; its menu: Hide, Convert to Spline, Copy as Spline",
          window.part.feature("S").gen_curve(c).visible and texts == ["Hide", "Convert to Spline", "Copy as Spline"]
          and not window.result.feature_errors, f"{texts}")
    v = kernel.volume(window.result.shapes["B"])
    steps = len(window.doc._undo)
    w = window.curve_to_spline("S", c, keep=True)
    settle()
    sketch = window.part.feature("S")
    row = window._rows.get(key)
    check("Copy as Spline: one undo step, the curve hidden (its row unticked), the spline selected, "
          "the body the same within the tolerance",
          w is not None and len(window.doc._undo) == steps + 1 and not sketch.gen_curve(c).visible
          and row is not None and row.checkState(0) == Qt.Unchecked and view_selected(window) == ("entity", "S", w)
          and not window.result.feature_errors and math.isclose(kernel.volume(window.result.shapes["B"]), v,
                                                                abs_tol=2.5e-3), f"{window.result.feature_errors}")
    window.undo()
    settle()
    w = window.curve_to_spline("S", c, keep=False)
    settle()
    check("Convert to Spline: the curve and its row gone, the extrusion on the spline",
          not window.part.feature("S").curves and key not in window._rows
          and window.part.feature("E").profiles == [tuple(sorted(("L1", "L2", w)))]
          and not window.result.feature_errors)


def view_selected(window):
    return window.view.selected


def check_window():
    """The window: the Curve button, the argument fields, hover, drag refused, lines on its ends, delete."""
    from vispy import app

    app.use_app("pyside6")
    app.use_app().create()
    sys.path.insert(0, os.path.join(ROOT, "checks"))
    from vispy_errors import install as _watch_vispy

    vispy_errors = _watch_vispy()
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QFormLayout, QLabel

    from check_sketch_drag import drag
    from check_sketch_line import click, hover, pixel, settle, shield
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

    steps = len(window.doc._undo)
    window.curve_action.trigger()
    settle()
    fields = rows(window)
    check("Curve: a parabola, one undo step, selected, its arguments in the properties",
          [c.kind for c in sketch().curves] == ["parabola"] and len(window.doc._undo) == steps + 1
          and view.selected == ("entity", "Sketch1", "Parabola1")
          and list(fields) == ["focal:", "from:", "to:", "x0:", "y0:", "Tolerance, µm:"] and fields["focal:"].text() == "10"
          and "parabola curve" in window.info.text(), f"{list(fields)} {view.selected}")
    fields["focal:"].setText("5")
    fields["focal:"].editingFinished.emit()
    settle()
    out = window.result.outputs["Sketch1"]
    check("  focal 5: the curve follows (its end at (20, 20))", sketch().gen_curve("Parabola1").args["focal"] == "5"
          and np.allclose(out.points["Parabola1.end"], (20, 20)), f"{out.points.get('Parabola1.end')}")
    titles = [form.widget().text() for form in (window.props_form.itemAt(0, QFormLayout.ItemRole.SpanningRole),)
              if form is not None and isinstance(form.widget(), QLabel)]
    check("  the title names its kind", titles == ["<b>Parabola1</b>: parabola"], f"{titles}")

    view.select(None)
    hover(native, pixel(view, frame, (10, 5)))               # on the curve (x = 10: y = 100 / 20)
    settle()
    check("hovering the curve takes it", view.hovered == ("entity", "Sketch1", "Parabola1"), f"{view.hovered}")
    drag(native, pixel(view, frame, (10, 5)), pixel(view, frame, (14, 2)))
    check("  dragged: it stays, the status bar says why", sketch().gen_curve("Parabola1").args["x0"] == "0"
          and "generated curve" in window.statusBar().currentMessage(), window.statusBar().currentMessage())

    window.line_action.trigger()
    settle(100)                                             # the properties emptied: the canvas re-laid out
    for xy in ((-20, 20), (20, 20), (20, 20)):              # from the start to the end (snapped), then stop
        click(native, pixel(view, frame, xy))
    QTest.keyClick(native, Qt.Key_Escape)
    settle()
    lines = sketch().lines
    check("Line from end to end: it uses the curve's end points", len(lines) == 1
          and {lines[0].start, lines[0].end} == {"Parabola1.start", "Parabola1.end"} and not sketch().points,
          f"{lines} {sketch().points}")
    out = window.result.outputs["Sketch1"]
    regions = find_regions(out)
    check("  a profile: the line and the curve", [r.key for r in regions] == [("L1", "Parabola1")],
          f"{[r.key for r in regions]}")
    window.finish_sketch()
    window.part.add_feature(Extrude("E", profiles=[("L1", "Parabola1")], sketch="Sketch1", distance="2", body="B"))
    window._commit()
    v = kernel.volume(window.result.shapes["B"])
    check("  extruded by 2: (800 - 800/3) x 2 exactly", math.isclose(v, (800 - 800 / 3) * 2, rel_tol=1e-9), f"{v}")
    window.undo()

    window.edit_sketch("Sketch1")
    settle()
    click(native, pixel(view, frame, (10, 5)))
    window.construction_action.trigger()
    settle()
    check("Construction on the curve: dashed, no profile", sketch().gen_curve("Parabola1").construction
          and "Parabola1" in view._sketch_geometry["Sketch1"].construction
          and not find_regions(window.result.outputs["Sketch1"]))
    window.construction_action.trigger()
    settle()
    click(native, pixel(view, frame, (10, 5)))
    window.delete_selected()
    settle()
    check("Delete the curve: the line on its ends goes too", not sketch().curves and not sketch().lines)

    window.spline_action.trigger()
    settle(100)
    click(native, pixel(view, frame, (-20, 0)))
    click(native, pixel(view, frame, (0, 10)))
    hover(native, pixel(view, frame, (20, 0)))
    settle()
    preview = np.asarray(view.tool.line.pos)
    check("Spline: after two points the preview runs through the cursor, curved",
          len(preview) > 3 and np.allclose(frame.to_sketch(preview[[0, -1]] * 1e-3) * 1e3, [(-20, 0), (20, 0)],
                                           atol=0.05), f"{len(preview)} {frame.to_sketch(preview[[0, -1]] * 1e-3) * 1e3}")
    steps = len(window.doc._undo)
    click(native, pixel(view, frame, (20, 0)))
    click(native, pixel(view, frame, (-20, 0)))             # its first point again: closed
    settle()
    splines = sketch().splines
    check("  a click on its first point closes it: one spline of 3 points, one undo step per point",
          len(splines) == 1 and len(splines[0].points) == 4 and splines[0].points[0] == splines[0].points[-1]
          and len(window.doc._undo) == steps + 2 and window.view.tool.last is None,
          f"{[w.points for w in splines]} {len(window.doc._undo) - steps}")
    QTest.keyClick(native, Qt.Key_Escape)
    settle()
    out = window.result.outputs["Sketch1"]
    regions = find_regions(out)
    check("  a profile of its own", [r.key for r in regions] == [("Spline1",)], f"{[r.key for r in regions]}")
    view.select(None)
    hover(native, pixel(view, frame, tuple(out.shapes["Spline1"].points[40])))
    settle()
    check("hovering the spline takes it", view.hovered == ("entity", "Sketch1", "Spline1"), f"{view.hovered}")
    click(native, pixel(view, frame, tuple(out.shapes["Spline1"].points[40])))
    settle()
    check("  selected: its info says spline, closed, through 3 points",
          "closed spline" in window.info.text() and "through 3 points" in window.info.text(), window.info.text())
    top = splines[0].points[1]
    drag(native, pixel(view, frame, (0, 10)), pixel(view, frame, (0, 16)))
    out = window.result.outputs["Sketch1"]
    check("  its point dragged: the spline follows through it",
          np.allclose(out.points[top], (0, 16), atol=0.1)
          and abs(np.abs(out.shapes["Spline1"].points[:, 1]).max() - out.points[top][1]) < 0.5,
          f"{out.points[top]}")
    window.view.select_keys([("entity", "Sketch1", "Spline1")])
    window._on_view_select(view.selected)
    window.copy_selection()
    window._paste(np.array([0.0, 40.0]))
    settle()
    check("Copy / Paste: a second closed spline 40 mm up", [len(w.points) for w in sketch().splines] == [4, 4]
          and len(find_regions(window.result.outputs["Sketch1"])) == 2)
    window.undo()
    window.finish_sketch()
    window.part.add_feature(Extrude("E", profiles=[("Spline1",)], sketch="Sketch1", distance="1", body="B"))
    window._commit()
    curve = window.result.outputs["Sketch1"].shapes["Spline1"].points
    area = 0.5 * abs(float(np.dot(curve[:-1, 0], curve[1:, 1]) - np.dot(curve[1:, 0], curve[:-1, 1])))
    v = kernel.volume(window.result.shapes["B"])
    check("  extruded by 1: its area (the polygon of its samples, within 0.1 %)", math.isclose(v, area, rel_tol=1e-3),
          f"{v} vs {area}")
    check_window_tree(window, settle)
    check("no error dialogs", not errors, f"{errors}")
    window.close()
    check("no errors in VisPy callbacks", not vispy_errors, f"{vispy_errors[:3]}")


def main():
    check_parabola()
    check_function()
    check_file()
    check_failures()
    check_splines()
    check_curve_to_spline()
    if "--no-window" not in sys.argv:
        check_window()
    print("FAILED: " + ", ".join(FAILED) if FAILED else "all checks passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
