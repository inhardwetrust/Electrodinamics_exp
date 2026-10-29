# curves.py
#
# Generators: sketch curves computed from a few arguments (DESIGN.md "Two
# ways of drawing"). numpy only. A sketch holds them as
#
#   [[feature.curve]]
#   name = "Reflector"
#   kind = "parabola"         # a kind of the registry below
#   focal = "f"               # its arguments: formulas (mm), see each kind's ARGS
#   from = "0"
#   to = "D/2"
#   construction = true       # optional, as for lines
#
# A kind declares its arguments (ARGS: name, default formula or None if
# required, meaning) the way a Feature declares its FIELDS: the file, the
# properties panel and the AI skill all go by that declaration. A new kind is
# one class with @curve_kind.
#
# The curve is built as kernel edges in the sketch's 2D coordinates (mm),
# from its start to its end:
#   ("parabola", vertex (2,), focal, u0, u1): exact; the points
#       vertex + (u, u^2 / (4 focal)) for u from u0 to u1 (axis along y);
#   ("bspline", poles (K, 2), knots, multiplicities, degree): a B-spline
#       (geometry/kernel.py builds it through BREP text: OCP 8 binds no
#       point arrays, CLAUDE.md);
#   ("arc", a, middle, b), ("line", a, b): exact arcs and lines.
# A kind OCC has exactly is built exactly. Any other (the "function" kind)
# becomes one cubic B-spline through its points at evenly spaced t, refined
# until it lies within FIT_TOLERANCE of the curve between them. A sketch's
# interpolating spline (spline_shape) is a B-spline exactly.
# Its ends are points of the sketch named "<curve>.start" and "<curve>.end":
# lines may use them, so curves and lines close profiles together.

from dataclasses import dataclass

import numpy as np

from model.expressions import ExpressionError, compile_formula

SAMPLES = 128               # points of a curve's polygon (drawing, regions)
FIT_TOLERANCE = 1e-4        # mm: how far the B-spline of a "function" curve may be off it
MAX_SPANS = 4096


class CurveError(ValueError):
    pass


@dataclass(frozen=True)
class Arg:
    name: str
    default: object         # a formula, or None: required
    doc: str


@dataclass
class CurveShape:
    points: np.ndarray      # (N, 2) mm, from start to end: drawing, regions, picking
    edges: list             # kernel edges (see the top), from start to end
    start: np.ndarray       # (2,)
    end: np.ndarray

    @property
    def closed(self):
        return bool(np.allclose(self.start, self.end, atol=1e-9))


KINDS = {}


def curve_kind(cls):
    KINDS[cls.KIND] = cls
    return cls


def end_points(name):
    """The names of a curve's end points in its sketch."""
    return f"{name}.start", f"{name}.end"


def reversed_edge(edge):
    """The same kernel edge run the other way."""
    if edge[0] == "line":
        return ("line", edge[2], edge[1], *edge[3:])
    if edge[0] == "arc":
        return ("arc", edge[3], edge[2], edge[1])
    if edge[0] == "parabola":
        return ("parabola", edge[1], edge[2], edge[4], edge[3])
    if edge[0] == "bspline":
        _, poles, knots, mults, degree = edge
        return ("bspline", poles[::-1].copy(), knots[0] + knots[-1] - knots[::-1], list(mults)[::-1], degree)
    raise CurveError(f"no reverse of a {edge[0]} edge")


@curve_kind
class Parabola:
    KIND = "parabola"
    DOC = "y = y0 + (x - x0)^2 / (4 focal), for x - x0 from `from` to `to` (exact)"
    ARGS = (
        Arg("focal", None, "the focal length (mm, > 0): the focus is at (x0, y0 + focal)"),
        Arg("from", None, "where it starts: its distance from the axis x = x0 (mm, signed)"),
        Arg("to", None, "where it ends, the same way"),
        Arg("x0", "0", "the vertex's x (mm); the axis runs along the sketch's y through it"),
        Arg("y0", "0", "the vertex's y (mm)"),
    )

    @staticmethod
    def build(value, formula, values):
        """value(arg) -> its value; formula(arg) -> its text; values: the parameters'."""
        focal, u0, u1, x0, y0 = (value(a.name) for a in Parabola.ARGS)
        if focal <= 0:
            raise CurveError(f"the focal length must be > 0, it is {focal:g} mm")
        if abs(u1 - u0) < 1e-9:
            raise CurveError("from and to are the same: no curve")
        u = np.linspace(u0, u1, SAMPLES + 1)
        points = np.stack([x0 + u, y0 + u * u / (4.0 * focal)], axis=1)
        vertex = np.array([x0, y0])
        return CurveShape(points, [("parabola", vertex, focal, u0, u1)], points[0].copy(), points[-1].copy())


@curve_kind
class Function:
    KIND = "function"
    DOC = "(x(t), y(t)) for t from `from` to `to`, as a B-spline within 0.1 um"
    ARGS = (
        Arg("x", None, "x of t (mm): a formula of the parameters and t"),
        Arg("y", None, "y of t (mm), the same way"),
        Arg("from", "0", "t at the start"),
        Arg("to", "1", "t at the end"),
    )
    VARIABLE = "t"          # inside x and y, t is the curve's own (a parameter named t is hidden there)
    VARIABLE_ARGS = ("x", "y")

    @staticmethod
    def build(value, formula, values):
        t0, t1 = value("from"), value("to")
        if abs(t1 - t0) < 1e-12:
            raise CurveError("from and to are the same: no curve")
        try:
            fx, fy = compile_formula(formula("x")), compile_formula(formula("y"))
        except ExpressionError as error:
            raise CurveError(str(error)) from None
        values = dict(values)

        def at(ts):
            out = np.empty((len(ts), 2))
            for i, t in enumerate(ts):
                values[Function.VARIABLE] = float(t)
                try:
                    out[i] = fx(values), fy(values)
                except ExpressionError as error:
                    raise CurveError(f"at t = {t:g}: {error}") from None
            return out

        points = at(np.linspace(t0, t1, SAMPLES + 1))
        if np.ptp(points, axis=0).max() < 1e-9:
            raise CurveError("the curve is a single point")
        edge = fit_spline(at, t0, t1)
        return CurveShape(points, [edge], edge[1][0].copy(), edge[1][-1].copy())


def spline_shape(points):
    """
    The interpolating spline through points (N, 2) mm (a sketch's Spline):
    a cubic C2 curve through every point, parametrized by chord length (as
    CAD interpolation splines are); natural ends, or periodic (smooth all
    around) when the last point is the first. One exact B-spline edge.
    """
    at, length = spline_function(points)
    samples = np.linspace(0.0, length, max(SAMPLES, 16 * (len(points) - 1)) + 1)
    p = np.asarray(points, float)
    return CurveShape(at(samples), [bspline_edge(at)], p[0].copy(), p[-1].copy())


def spline_function(points, params=None):
    """
    (at, length): at(s) -> (N, 2) points of the interpolating cubic spline at
    parameters s in [0, length]; at.knots: the parameters of its points.
    params: the points' parameters (increasing), chord length by default.
    """
    p = np.asarray(points, float).reshape(-1, 2)
    keep = [0] + [i for i in range(1, len(p)) if np.linalg.norm(p[i] - p[i - 1]) > 1e-9]
    p = p[keep]                                 # a point twice in a row adds nothing
    if len(p) < 2:
        raise CurveError("a spline needs two points apart")
    if params is None:
        knots = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(p, axis=0), axis=1))])
    else:
        knots = np.asarray(params, float)[keep]
        knots = knots - knots[0]
    h = np.diff(knots)
    n = len(p)
    closed = n > 3 and np.allclose(p[0], p[-1], atol=1e-9)
    m = np.zeros((n, 2))                        # second derivatives at the points
    if closed:                                  # periodic: n - 1 unknowns, around the loop
        k = n - 1
        a = np.zeros((k, k))
        rhs = np.zeros((k, 2))
        for i in range(k):
            before, after = h[i - 1], h[i]      # h[-1]: the segment closing the loop
            a[i, (i - 1) % k] += before
            a[i, i] += 2.0 * (before + after)
            a[i, (i + 1) % k] += after
            rhs[i] = 6.0 * ((p[i + 1] - p[i]) / after - (p[i] - p[i - 1 if i else k - 1]) / before)
        m[:k] = np.linalg.solve(a, rhs)
        m[k] = m[0]
    elif n > 2:                                 # natural: no curvature at the ends
        a = np.zeros((n - 2, n - 2))
        rhs = np.zeros((n - 2, 2))
        for i in range(1, n - 1):
            a[i - 1, i - 1] = 2.0 * (h[i - 1] + h[i])
            if i > 1:
                a[i - 1, i - 2] = h[i - 1]
            if i < n - 2:
                a[i - 1, i] = h[i]
            rhs[i - 1] = 6.0 * ((p[i + 1] - p[i]) / h[i] - (p[i] - p[i - 1]) / h[i - 1])
        m[1:-1] = np.linalg.solve(a, rhs)

    def at(s):
        s = np.clip(np.asarray(s, float), 0.0, knots[-1])
        i = np.clip(np.searchsorted(knots, s, side="right") - 1, 0, n - 2)
        hi, a_, b_ = h[i][:, None], (knots[i + 1] - s)[:, None], (s - knots[i])[:, None]
        return (m[i] * a_ ** 3 / (6 * hi) + m[i + 1] * b_ ** 3 / (6 * hi)
                + (p[i] / hi - m[i] * hi / 6) * a_ + (p[i + 1] / hi - m[i + 1] * hi / 6) * b_)

    at.knots = knots
    return at, float(knots[-1])


def bspline_edge(at):
    """
    A cubic spline from spline_function as its kernel edge ("bspline", poles
    (K, 2), knots, multiplicities, degree): the same curve exactly (clamped
    ends, simple interior knots: C2), its poles fitted to it span by span.
    """
    knots = at.knots
    full = np.concatenate([[knots[0]] * 3, knots, [knots[-1]] * 3])
    s = np.concatenate([np.linspace(a, b, 6)[:-1] for a, b in zip(knots[:-1], knots[1:])] + [knots[-1:]])
    poles = np.linalg.lstsq(_basis(full, 3, s), at(s), rcond=None)[0]
    mults = [4] + [1] * (len(knots) - 2) + [4]
    return ("bspline", poles, knots.copy(), mults, 3)


def bspline_at(edge, u):
    """(N, 2) points of a "bspline" edge at its parameters u."""
    _, poles, knots, mults, degree = edge
    return _basis(np.repeat(knots, mults), degree, u) @ poles


def _basis(full, degree, u):
    """(len(u), poles) B-spline basis values (Cox - de Boor) for the full knot vector."""
    u = np.asarray(u, float)
    t = np.asarray(full, float)
    n = np.zeros((len(u), len(t) - 1))
    for j in range(len(t) - 1):
        n[:, j] = (t[j] <= u) & (u < t[j + 1])
    n[u >= t[-1], np.nonzero(t < t[-1])[0][-1]] = 1.0        # the end belongs to the last span
    for d in range(1, degree + 1):
        out = np.zeros((len(u), len(t) - 1 - d))
        for j in range(len(t) - 1 - d):
            a, b = t[j + d] - t[j], t[j + d + 1] - t[j + 1]
            if a > 0:
                out[:, j] += (u - t[j]) / a * n[:, j]
            if b > 0:
                out[:, j] += (t[j + d + 1] - u) / b * n[:, j + 1]
        n = out
    return n[:, :len(t) - degree - 1]


def fit_spline(at, t0, t1, tolerance=FIT_TOLERANCE):
    """
    A curve given by `at` (t values -> (N, 2) points) as one B-spline edge:
    the cubic spline through its points at evenly spaced t, doubled until the
    spline is within `tolerance` of the curve halfway between them.
    """
    spans = 8
    while spans <= MAX_SPANS:
        ts = np.linspace(t0, t1, spans + 1)
        spline, _ = spline_function(at(ts), params=np.arange(spans + 1.0))
        between = np.arange(spans) + 0.5
        worst = float(np.abs(spline(between) - at(0.5 * (ts[:-1] + ts[1:]))).max())
        if worst <= tolerance and len(spline.knots) == spans + 1:
            return bspline_edge(spline)
        spans *= 2
    raise CurveError(f"not within {tolerance:g} mm with {MAX_SPANS} spans (does it jump, stop or have a cusp?)")


def edge_function(edge):
    """(at, t0, t1) of a curve's kernel edge: at(t values) -> (N, 2) points, t from t0 to t1."""
    if edge[0] == "parabola":
        _, vertex, focal, u0, u1 = edge
        return (lambda u: np.asarray(vertex, float)
                + np.stack([np.asarray(u, float), np.asarray(u, float) ** 2 / (4.0 * focal)], axis=1)), u0, u1
    if edge[0] == "bspline":
        return (lambda u: bspline_at(edge, u)), float(edge[2][0]), float(edge[2][-1])
    raise CurveError(f"no points of a {edge[0]} edge")


def spline_points(edge, tolerance=1e-3, checks=64):
    """
    (K, 2) points on a curve's kernel edge such that the interpolating
    spline through them (spline_shape) stays within `tolerance` (mm) of the
    curve both ways (closed: the last point is the first). Starts with 5
    points; a span off by more gets a point halfway (in the curve's
    parameter) until none is.
    """
    at, t0, t1 = edge_function(edge)
    ts = list(np.linspace(t0, t1, 5))
    while len(ts) <= MAX_SPANS:
        p = at(np.array(ts))
        spline, _ = spline_function(p)
        if len(spline.knots) != len(ts):
            raise CurveError("the curve stops (two of its points at one place)")
        bad = []
        for j in range(len(ts) - 1):
            curve = at(np.linspace(ts[j], ts[j + 1], checks + 1))
            near = spline(np.linspace(spline.knots[j], spline.knots[j + 1], 4 * checks + 1))
            if max(_off(curve, near), _off(near, curve)) > tolerance:
                bad.append(j)
        if not bad:
            return p
        for j in reversed(bad):
            ts.insert(j + 1, 0.5 * (ts[j] + ts[j + 1]))
    raise CurveError(f"not within {tolerance:g} mm with {MAX_SPANS} points")


def _off(points, path):
    """The largest distance from points (N, 2) to the polyline path (M, 2)."""
    a, b = path[:-1], path[1:]
    d = b - a
    length2 = np.maximum((d * d).sum(axis=1), 1e-30)
    t = np.clip(((points[:, None, :] - a[None]) * d[None]).sum(axis=2) / length2, 0.0, 1.0)
    nearest = a[None] + t[..., None] * d[None]
    return float(np.sqrt(((points[:, None, :] - nearest) ** 2).sum(axis=2).min(axis=1)).max())
