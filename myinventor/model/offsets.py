# offsets.py
#
# The Offset modifier of a sketch entity (model/sketch.py [[feature.offset]]):
# a band along a line, a spline or a generated curve, a ring around a circle
# or a closed curve. numpy only.
#
# - left / right: how far the band reaches to each side (mm, >= 0, not both
#   0), the sides taken along the entity from its start to its end (a
#   circle runs counter-clockwise: its left is inside).
# - start / end caps of an open entity: "flat" (straight across) or
#   "round" (a half circle on the cap, tangent to both sides).
# - A side of 0 is the entity itself, exactly (a parabola stays OCC's
#   parabola). A line's other side is a line, a circle's a circle (exact);
#   a curve's is one B-spline fitted within FIT_TOLERANCE (an offset of a
#   parabola or a B-spline is neither).
# - A side further than the radius of curvature on the inside of a bend
#   crosses itself: an OffsetError naming the radius and the place.
#
# offset_loops -> [(key, edges, polygon)]: each loop's kernel edges
# (model/curves.py) and polygon (N, 2) mm, counter-clockwise; an open
# entity gives one loop ("<name>.offset"), a closed one two: the outer
# ("<name>.offset") and the inner ("<name>.offset.inner"), whose regions
# are the band (with the inner as its hole) and what it encloses.

import numpy as np

from model.curves import CurveError, edge_function, fit_spline, reversed_edge

CAPS = ("flat", "round")
SAMPLES = 128               # polygon points along each side
CAP_SAMPLES = 24


class OffsetError(ValueError):
    pass


def offset_key(name, inner=False):
    """A profile key of an entity's offset."""
    return f"{name}.offset" + (".inner" if inner else "")


def offset_loops(name, base, left, right, start="flat", end="flat"):
    """
    The loops of entity `name`'s offset. base: ("line", a, b),
    ("circle", center, radius) or ("curve", shape) (a model.curves
    CurveShape: a generated curve or a spline, its kernel edges from its
    start to its end).
    """
    if left < 0 or right < 0:
        raise OffsetError("left and right must be >= 0")
    if left + right <= 1e-12:
        raise OffsetError("left and right are both 0: no band")
    for cap in (start, end):
        if cap not in CAPS:
            raise OffsetError(f"a cap is one of {', '.join(CAPS)}, not {cap!r}")
    if base[0] == "circle":
        return _circle(name, base[1], base[2], left, right)
    if base[0] == "line":
        a, b = np.asarray(base[1], float), np.asarray(base[2], float)
        if np.linalg.norm(b - a) < 1e-12:
            raise OffsetError("the line has no length")
        path = _Path(lambda s: a + np.asarray(s, float)[:, None] * (b - a), [("line", a, b)], False, True)
    else:
        shape = base[1]
        edge = shape.edges[0]
        if len(shape.edges) != 1 or edge[0] not in ("parabola", "bspline", "arc"):
            raise OffsetError(f"no offset of a curve made of {[e[0] for e in shape.edges]}")
        at, t0, t1 = edge_function(edge)
        path = _Path(lambda s: at(t0 + np.asarray(s, float) * (t1 - t0)), [edge], shape.closed, False)
        path.arc = edge[0] == "arc"
    path.check(left, right)
    if path.closed:
        return _closed(name, path, left, right)
    return [_open(name, path, left, right, start, end)]


class _Path:
    """An entity as g(s), s from 0 (its start) to 1 (its end); its exact edges; closed or not."""

    def __init__(self, g, edges, closed, straight):
        self.g, self.edges, self.closed, self.straight = g, edges, closed, straight

    def frame(self, s):
        """(tangent, left normal) unit vectors (N, 2) at s."""
        s = np.asarray(s, float)
        h = 1e-6
        if self.closed:
            lo, hi = s - h, s + h
            d = (self.g(np.mod(hi, 1.0)) - self.g(np.mod(lo, 1.0))) / (2 * h)
        else:
            lo, hi = np.maximum(s - h, 0.0), np.minimum(s + h, 1.0)
            d = (self.g(hi) - self.g(lo)) / (hi - lo)[:, None]
        t = d / np.linalg.norm(d, axis=1)[:, None]
        return t, np.stack([-t[:, 1], t[:, 0]], axis=1)

    def side(self, d):
        """The offset by d along the left normal (d < 0: to the right), as s -> points."""
        return lambda s: self.g(s) + d * self.frame(s)[1]

    def check(self, left, right):
        """OffsetError if a side reaches past the radius of curvature on its inner side."""
        if self.straight:
            return
        s = np.linspace(0.0, 1.0, 4001)
        p = self.g(s)
        d1 = np.gradient(p, s, axis=0)
        d2 = np.gradient(d1, s, axis=0)
        speed = np.linalg.norm(d1, axis=1)
        kappa = (d1[:, 0] * d2[:, 1] - d1[:, 1] * d2[:, 0]) / np.maximum(speed, 1e-30) ** 3   # > 0: turns left
        for d, sign, label in ((left, 1.0, "left"), (right, -1.0, "right")):
            room = 1.0 - sign * d * kappa
            i = int(np.argmin(room))
            if d > 0 and room[i] <= 1e-6:
                raise OffsetError(f"the {label} side ({d:g} mm) reaches past the radius of the bend "
                                  f"({1.0 / abs(kappa[i]):.4g} mm) near ({p[i][0]:.4g}, {p[i][1]:.4g})")

    def side_edges(self, d):
        """Kernel edges of the side at d (left > 0), from start to end: the path itself at 0."""
        if abs(d) < 1e-12:
            return [tuple(e) for e in self.edges]
        if self.straight:
            ends = self.side(d)(np.array([0.0, 1.0]))
            return [("line", ends[0], ends[1])]
        if getattr(self, "arc", False):         # an arc's side: the concentric arc, exact
            p = self.side(d)(np.array([0.0, 0.5, 1.0]))
            return [("arc", p[0], p[1], p[2])]
        try:
            return [fit_spline(self.side(d), 0.0, 1.0)]
        except CurveError as error:
            raise OffsetError(f"the side at {d:g} mm: {error}") from None


def _reverse(edges):
    return [reversed_edge(e) for e in edges[::-1]]


def _area(polygon):
    x, y = polygon[:, 0], polygon[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


def _cap(a, b, outward, round_):
    """The cap from a to b: a line, or a half circle on it bulging `outward`; (edges, points between)."""
    if not round_:
        return [("line", a, b)], np.zeros((0, 2))
    middle = 0.5 * (a + b)
    radius = 0.5 * np.linalg.norm(b - a)
    across = (a - middle) / radius
    tip = middle + radius * outward
    angles = np.linspace(0.0, np.pi, CAP_SAMPLES + 1)[1:-1]
    # From a round to b through the tip: a = middle + r across, the tip = middle + r outward.
    points = middle + radius * (np.cos(angles)[:, None] * across + np.sin(angles)[:, None] * outward)
    return [("arc", a, tip, b)], points


def _open(name, path, left, right, start, end):
    """One loop: the right side forward, the end cap, the left side back, the start cap."""
    s = np.linspace(0.0, 1.0, SAMPLES + 1)
    tangent, _ = path.frame(np.array([0.0, 1.0]))
    lefts, rights = path.side(left)(s), path.side(-right)(s)
    end_cap, end_points = _cap(rights[-1], lefts[-1], tangent[1], end == "round")
    start_cap, start_points = _cap(lefts[0], rights[0], -tangent[0], start == "round")
    edges = path.side_edges(-right) + end_cap + _reverse(path.side_edges(left)) + start_cap
    polygon = np.vstack([rights, end_points, lefts[::-1], start_points])
    if _area(polygon) < 0:
        edges, polygon = _reverse(edges), polygon[::-1]
    return offset_key(name), edges, polygon


def _closed(name, path, left, right):
    """Two loops: the sides at left and at right, the larger the outer one; each counter-clockwise."""
    s = np.linspace(0.0, 1.0, 2 * SAMPLES + 1)[:-1]
    loops = []
    for d in (left, -right):
        polygon = path.side(d)(s)
        edges = path.side_edges(d)
        if _area(polygon) < 0:
            edges, polygon = _reverse(edges), polygon[::-1]
        loops.append((edges, polygon))
    loops.sort(key=lambda loop: -_area(loop[1]))
    (outer, outer_polygon), (inner, inner_polygon) = loops
    return [(offset_key(name), outer, outer_polygon), (offset_key(name, True), inner, inner_polygon)]


def _circle(name, center, radius, left, right):
    """A ring: a circle runs counter-clockwise, so its left is inside (radius - left) and its right outside."""
    inner, outer = radius - left, radius + right
    if inner <= 1e-9:
        raise OffsetError(f"the left side ({left:g} mm) reaches the center (the radius is {radius:g} mm)")
    center = np.asarray(center, float)
    t = np.linspace(0.0, 2.0 * np.pi, 2 * SAMPLES, endpoint=False)
    ring = np.stack([np.cos(t), np.sin(t)], axis=1)
    return [(offset_key(name), [("circle", center.copy(), outer)], center + outer * ring),
            (offset_key(name, True), [("circle", center.copy(), inner)], center + inner * ring)]
