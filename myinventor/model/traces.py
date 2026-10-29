# traces.py
#
# The outline of a wide polyline (a sketch's "polyline": a center line with a
# width at every point, model/sketch.py), numpy only.
#   - along each segment the width changes linearly from one point's width
#     to the next one's: the two side lines are the center line moved out by
#     half the width at either end;
#   - at an inner point the side lines of the two segments meet where they
#     cross (a sharp, mitered corner); if that corner would stick out more
#     than MITER_LIMIT half widths (a very sharp turn), it is cut off (bevel);
#   - an inner point may bend otherwise (its "radius" is one number, its
#     meaning per bend):
#       "arc":     the center line turns along an arc of radius R tangent to
#                  both segments; the sides are concentric arcs R +- w/2, the
#                  width stays the point's (a curved microstrip). R < w/2
#                  leaves the inner corner sharp. Default R = the width;
#       "chamfer": the center line is cut by a straight line, `radius` (the
#                  setback) from the point along both segments; both sides
#                  get parallel cuts, the width stays. Default: the width;
#       "miter":   only the outer corner is cut: the circle of `radius`
#                  around the point crosses the outer edges, a straight cut
#                  between (the classic microstrip miter; the inner corner
#                  stays sharp). Default: half the width (a full bevel);
#     an inner side too narrow for the bend (its edges already cross before
#     it) keeps its sharp corner;
#   - an end is "flat" (a straight cap across) or "round" (a half circle of
#     the end's width, its center on the end point).
# outline() gives the closed boundary counter-clockwise as edges for the
# kernel (("line", a, b), ("arc", a, middle, b), sketch mm) and as a polygon
# (arcs sampled) for drawing and for point-in-region tests.

import numpy as np

MITER_LIMIT = 4.0           # a corner sticking out more than this many half widths is beveled
ARC_SAMPLES = 16            # points per arc in the polygon
BENDS = ("normal", "arc", "chamfer", "miter")


class TraceError(ValueError):
    pass


def outline(points, widths, start="flat", end="flat", bends=None, radii=None):
    """
    points (N, 2) mm, the center line (N >= 2); widths (N,) mm > 0;
    bends (N,) one of BENDS at the inner points (the ends' are ignored);
    radii (N,) mm or None (the bend's default there).
    Returns (edges, polygon (M, 2)), counter-clockwise.
    """
    points = np.asarray(points, dtype=float).reshape(-1, 2)
    widths = np.asarray(widths, dtype=float).reshape(-1)
    bends = list(bends) if bends is not None else ["normal"] * len(points)
    radii = list(radii) if radii is not None else [None] * len(points)
    if (widths <= 0).any():
        raise TraceError("every width must be > 0")
    if any(b not in BENDS for b in bends):
        raise TraceError(f"a bend is one of {', '.join(BENDS)}")
    # Points at one place add nothing: keep the first (its width, bend, radius).
    keep = [0] + [i for i in range(1, len(points)) if np.linalg.norm(points[i] - points[i - 1]) > 1e-9]
    points, widths = points[keep], widths[keep]
    bends, radii = [bends[i] for i in keep], [radii[i] for i in keep]
    if len(points) < 2:
        raise TraceError("a polyline needs two points apart")
    half = widths / 2.0
    along = np.diff(points, axis=0)
    along /= np.linalg.norm(along, axis=1, keepdims=True)
    left = np.stack([-along[:, 1], along[:, 0]], axis=1)
    lengths = np.linalg.norm(np.diff(points, axis=0), axis=1)

    # Arc and chamfer trim the center line: by `setback` before and after the point.
    n = len(points)
    setback = np.zeros(n)
    turns = np.zeros(n)                     # the turn's sign: > 0 left, < 0 right, 0 straight on
    arc_radius = np.zeros(n)
    for j in range(1, n - 1):
        u, v = along[j - 1], along[j]
        cross, dot = u[0] * v[1] - u[1] * v[0], float(np.dot(u, v))
        if abs(cross) < 1e-9 and dot > 0:
            continue                        # straight on: no bend
        turns[j] = 1.0 if cross > 0 else -1.0
        theta = float(np.arctan2(abs(cross), dot))     # how far it turns, 0..pi
        if bends[j] == "arc":
            arc_radius[j] = 2.0 * half[j] if radii[j] is None else float(radii[j])
            if arc_radius[j] <= 0:
                raise TraceError("an arc bend's radius must be > 0")
            if theta > np.pi - 1e-6:
                raise TraceError("an arc bend cannot turn straight back")
            setback[j] = arc_radius[j] * np.tan(theta / 2.0)
        elif bends[j] == "chamfer":
            setback[j] = 2.0 * half[j] if radii[j] is None else float(radii[j])
            if setback[j] <= 0:
                raise TraceError("a chamfer's setback must be > 0")
    for i in range(n - 1):
        if setback[i] + setback[i + 1] > lengths[i] * (1 + 1e-9):
            raise TraceError("a bend's radius or setback reaches past a neighbouring point")
    starts = points[:-1] + along * setback[:-1, None]   # each segment as trimmed by its bends
    ends = points[1:] - along * setback[1:, None]

    def side(sign):
        """
        The chain of one side (sign +1: left, -1: right), from the start to
        the end: [("p", point) | ("m", the middle of an arc to the next point)].
        """
        chain = [("p", points[0] + sign * left[0] * half[0])]
        for j in range(1, n - 1):
            a0 = starts[j - 1] + sign * left[j - 1] * half[j - 1]      # the segment before, on this side
            a1 = ends[j - 1] + sign * left[j - 1] * half[j]
            b0 = starts[j] + sign * left[j] * half[j]                  # the segment after
            b1 = ends[j] + sign * left[j] * half[j + 1]
            if turns[j] == 0:                   # straight on: one point
                chain.append(("p", a1))
                continue
            corner = _crossing(a0, a1, b0, b1)
            outer = (turns[j] < 0) == (sign > 0)    # this side is the corner's outside
            if bends[j] == "chamfer":
                # The cut is a short center segment of its own (width the point's),
                # its side line meeting the two segments' side lines.
                cut = starts[j] - ends[j - 1]
                cut /= np.linalg.norm(cut)
                c0 = ends[j - 1] + sign * np.array([-cut[1], cut[0]]) * half[j]
                c1 = c0 + cut
                p, q = _crossing(a0, a1, c0, c1), _crossing(c0, c1, b0, b1)
                if p is None or q is None or np.dot(q - p, cut) < 0:
                    chain.append(("p", corner))     # an inner side too narrow for the cut: sharp
                else:
                    chain += [("p", p), ("p", q)]
            elif not outer and corner is not None and bends[j] == "arc" and np.dot(corner - a1, a1 - a0) < 0:
                chain.append(("p", corner))     # the inner edges cross before the arc (R < w/2): sharp
            elif bends[j] == "arc":
                center = ends[j - 1] + turns[j] * left[j - 1] * arc_radius[j]
                r = arc_radius[j] + half[j] if outer else arc_radius[j] - half[j]     # concentric
                if r <= 1e-9 * half[j]:
                    chain.append(("p", a1))     # R = w/2: the inner arc shrinks to its center
                else:
                    to_point = points[j] - center
                    chain += [("p", a1), ("m", center + r * to_point / np.linalg.norm(to_point)), ("p", b0)]
            elif corner is None:
                chain.append(("p", a1))
            elif outer and bends[j] == "miter":
                chain += _miter(points[j], corner, a0, a1, b0, b1, radii[j], half[j])
            elif np.linalg.norm(corner - points[j]) > MITER_LIMIT * half[j]:
                chain += [("p", a1), ("p", b0)]           # too sharp: beveled
            else:
                chain.append(("p", corner))
        chain.append(("p", points[-1] + sign * left[-1] * half[-1]))
        return chain

    right_chain, left_chain = side(-1.0), side(1.0)
    edges = _chain_edges(right_chain)
    edges.append(_cap(right_chain[-1][1], left_chain[-1][1], points[-1], along[-1], half[-1], end))
    edges += _chain_edges(left_chain[::-1])
    edges.append(_cap(left_chain[0][1], right_chain[0][1], points[0], -along[0], half[0], start))
    edges = [e for e in edges if e[0] == "arc" or np.linalg.norm(e[2] - e[1]) > 1e-12]
    return edges, _polygon(edges)


def _miter(center, corner, a0, a1, b0, b1, radius, half):
    """
    A mitered outer corner: the circle of `radius` around the center-line
    point cuts the two outer edges near the corner, a straight line between
    the cuts. A radius below the half width counts as the half width (a full
    bevel); one beyond the corner leaves it sharp.
    """
    radius = half if radius is None else max(float(radius), half)
    if radius >= np.linalg.norm(corner - center) - 1e-12:
        return [("p", corner)]
    if radius <= half * (1 + 1e-9):
        # The circle only touches the outer edges, at their feet from the point
        # (a fully round join); taken exactly, a crossing would be lost to rounding.
        p, q = a1, b0
    else:
        p = _cut(center, radius, a0, a1, corner)
        q = _cut(center, radius, b1, b0, corner)
    if p is None or q is None:
        raise TraceError("the bend's radius reaches past a neighbouring point")
    return [("p", p), ("p", q)]


def _cut(center, radius, far, near, corner):
    """
    Where the circle crosses the edge from `far` toward the corner (the
    crossing nearest the corner), or None if that is not on the edge.
    """
    d = corner - far
    length = float(np.linalg.norm(d))
    d /= length
    f = far - center
    b = float(np.dot(f, d))
    disc = b * b - (float(np.dot(f, f)) - radius * radius)
    if disc < -1e-12 * radius * radius:
        return None
    disc = max(disc, 0.0)                   # touching, up to rounding
    t = -b + np.sqrt(disc)                  # the root toward the corner
    if t < -1e-9 or t > length + 1e-9:
        return None
    return far + t * d


def _chain_edges(chain):
    """[("line", a, b) | ("arc", a, middle, b)] along a side's chain."""
    edges, k = [], 0
    while k < len(chain) - 1:
        a = chain[k][1]
        if chain[k + 1][0] == "m":
            edges.append(("arc", a, chain[k + 1][1], chain[k + 2][1]))
            k += 2
        else:
            edges.append(("line", a, chain[k + 1][1]))
            k += 1
    return edges


def _crossing(a0, a1, b0, b1):
    """Where the lines a0-a1 and b0-b1 cross, or None if they are parallel."""
    da, db = a1 - a0, b1 - b0
    det = da[0] * db[1] - da[1] * db[0]
    if abs(det) < 1e-12 * max(np.dot(da, da), np.dot(db, db), 1e-30):
        return None
    t = ((b0[0] - a0[0]) * db[1] - (b0[1] - a0[1]) * db[0]) / det
    return a0 + t * da


def _cap(a, b, center, outward, half, kind):
    """The end cap from a to b around `center`: a line, or a half circle bulging along `outward`."""
    if kind == "round":
        return ("arc", a, center + outward * half, b)
    if kind != "flat":
        raise TraceError(f"an end is 'flat' or 'round', not {kind!r}")
    return ("line", a, b)


def _polygon(edges):
    out = []
    for edge in edges:
        if edge[0] == "line":
            out.append(edge[1])
        else:
            _, a, m, b = edge
            center, radius, t0, t1 = _arc(a, m, b)
            for t in np.linspace(t0, t1, ARC_SAMPLES, endpoint=False):
                out.append(center + radius * np.array([np.cos(t), np.sin(t)]))
    return np.array(out)


def _arc(a, m, b):
    """Center, radius and angles (t0 to t1 through m) of the arc a -> m -> b."""
    ax, ay = a
    bx, by = b
    mx, my = m
    d = 2.0 * (ax * (my - by) + mx * (by - ay) + bx * (ay - my))
    ux = ((ax * ax + ay * ay) * (my - by) + (mx * mx + my * my) * (by - ay) + (bx * bx + by * by) * (ay - my)) / d
    uy = ((ax * ax + ay * ay) * (bx - mx) + (mx * mx + my * my) * (ax - bx) + (bx * bx + by * by) * (mx - ax)) / d
    center = np.array([ux, uy])
    radius = float(np.linalg.norm(a - center))
    angle = lambda p: float(np.arctan2(p[1] - center[1], p[0] - center[0]))   # noqa: E731
    t0, tm, t1 = angle(a), angle(m), angle(b)
    # Go from t0 to t1 the way that passes tm.
    span = (t1 - t0) % (2 * np.pi)
    if (tm - t0) % (2 * np.pi) > span:
        span -= 2 * np.pi
    return center, radius, t0, t0 + span
