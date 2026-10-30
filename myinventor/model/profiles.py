# profiles.py
#
# The closed regions of a sketch (profiles, what an extrusion takes), from
# its evaluated geometry (model/sketch.py SketchResult). numpy only.
#
# Everything that bounds regions goes into one planar graph: lines, circles,
# generated curves and splines, the outlines of wide polylines
# (model/traces.py) and of offsets (model/offsets.py). Construction geometry
# and the spines of offsets are left out.
# - Its kernel edges (model/curves.py) are cut where they cross or touch
#   each other (a line over a circle, a line through a band, a line ending
#   on another's middle): crossings found on their polylines, then refined
#   on the exact curves (Newton); the pieces are exact (curves.sub_edge).
# - Ends within CONNECT_MM (1 um: points are stored rounded to it) are one
#   node, and so is an end that close to another curve's middle (it cuts
#   it there): the points of separate chains, a line ending on a curve. The
#   node lies where the least movable end is (a parabola's, a circle's);
#   lines, arcs and B-splines get their ends moved onto it (by < 1 um) so
#   the kernel's loops close.
# - Regions are the bounded faces of that graph (dangling pieces pruned),
#   traced around each node in the order the pieces leave it (their
#   tangent): a rectangle is one, with a diagonal two triangles, a circle
#   crossed by a line two halves. A closed curve nothing crosses is a loop
#   on its own.
# - A loop inside another is its hole (a circle in a rectangle: the plate
#   with a round hole, and the disc, as Inventor does).
#
# whole_regions: the same without cutting (each loop as it is: what files and
# the AI name); named_regions: both, what a feature's profile key may name.
#
# A region is named by its outer loop (Region.key): the sorted names of what
# bounds it. An entity cut into pieces is named by the piece: "L1:0",
# "L1:1"... from its start (a closed one: the piece through its start is 0),
# so the two halves of a band cut by a line differ ("L1:1", "W.offset:0" /
# "W.offset:1"). Nothing cut: the plain names, as before. Features refer to
# profiles by that key, so moving points or changing sizes keeps the
# reference; changing what crosses what does not.

from dataclasses import dataclass, field

import numpy as np

from model.curves import edge_function, end_points, reversed_edge, sub_edge

SAME_PLACE_MM = 1e-6
CONNECT_MM = 1e-3           # ends (and an end on a curve) this close are joined: points are rounded to 1 um
RIGIDITY = {"parabola": 0, "circle": 0, "arc": 1, "bspline": 2, "line": 3}     # who moves to a joint: the higher
PATH_SAMPLES = {"line": 1, "arc": 48, "circle": 128, "parabola": 96, "bspline": 128}
NEWTON_STEPS = 30


@dataclass
class Loop:
    names: tuple            # sorted names of what bounds it (the key)
    polygon: np.ndarray     # (N, 2) mm, counter-clockwise, not closed
    edges: list             # its kernel edges in order (model/curves.py): ("line", a, b[, line name]),
    #                         ("arc", a, middle, b), ("parabola", ...), ("bspline", ...), [("circle", center, r)]

    @property
    def area(self):
        return _area(self.polygon)

    def contains(self, xy):
        return _inside(np.asarray(xy, float), self.polygon)


@dataclass
class Region:
    outer: Loop
    holes: list = field(default_factory=list)       # Loop

    @property
    def key(self):
        return self.outer.names

    @property
    def area(self):
        return self.outer.area - sum(h.area for h in self.holes)

    def contains(self, xy):
        return self.outer.contains(xy) and not any(h.contains(xy) for h in self.holes)


def find_regions(result):
    """The regions of a SketchResult (cut where things cross: what is picked), largest first."""
    return _regions(_loops(_items(result)))


def whole_regions(result):
    """
    The regions of the loops as they are, nothing cut where they cross: a
    rectangle and a feed polyline overlapping it are two whole profiles,
    their keys the plain names (what files and the AI name; an extrusion of
    both fuses them).
    """
    return _regions(_loops(_items(result), cut=False))


def named_regions(result):
    """{key: Region} of every profile a feature may name: the pieces (find_regions) and the whole loops."""
    named = {r.key: r for r in whole_regions(result)}
    named.update({r.key: r for r in find_regions(result)})
    return named


def _regions(loops):
    regions = []
    for loop in loops:
        inside = [other for other in loops if other is not loop and other.area < loop.area
                  and loop.contains(other.polygon[0]) and loop.contains(_sample(other))]
        # Direct children: not inside another loop that is itself inside this one.
        holes = [h for h in inside if not any(h is not m and m.area > h.area and m.contains(_sample(h))
                                              for m in inside)]
        regions.append(Region(loop, holes))
    regions.sort(key=lambda r: -r.outer.area)
    return regions


def _sample(loop):
    """A point well inside a loop's boundary region: its centroid if inside, else a vertex."""
    c = loop.polygon.mean(axis=0)
    return c if loop.contains(c) else loop.polygon[0]


def _items(result):
    """[(name, kernel edges in order, closed)]: what bounds regions."""
    spines = getattr(result, "spines", set())
    items = [(line.name, [("line", result.points[line.start].copy(), result.points[line.end].copy(), line.name)],
              False) for line in result.lines if not line.construction and line.name not in spines]
    for c in getattr(result, "curves", []) + getattr(result, "splines", []) + getattr(result, "arcs", []):
        if not c.construction and c.name not in spines and c.name in result.shapes:
            shape = result.shapes[c.name]
            items.append((c.name, list(shape.edges), shape.closed))
    for c in result.circles:
        if not c.construction and c.name not in spines:
            items.append((c.name, [("circle", result.points[c.center].copy(), result.radii[c.name])], True))
    for w in getattr(result, "polylines", []):
        if w.name in result.outlines:
            items.append((w.name, list(result.outlines[w.name][0]), True))
    for built in getattr(result, "offsets", {}).values():
        items += [(key, list(edges), True) for key, edges, _ in built]
    return items


@dataclass
class _Curve:
    item: int               # index into the items
    edge: tuple             # its kernel edge
    g: object               # s in [0, 1] -> points (N, 2)
    path: np.ndarray        # (K + 1, 2) at s = linspace(0, 1, K + 1)
    cuts: list = field(default_factory=list)        # s where it is cut

    def point(self, s):
        return self.g(np.array([float(s)]))[0]

    def tangent(self, s):
        h = 1e-7
        lo, hi = max(s - h, 0.0), min(s + h, 1.0)
        return (self.point(hi) - self.point(lo)) / (hi - lo)


def _curves(items):
    curves = []
    for i, (_, edges, _) in enumerate(items):
        for edge in edges:
            at, t0, t1 = edge_function(edge)
            g = (lambda at, t0, t1: lambda s: at(t0 + np.asarray(s, float) * (t1 - t0)))(at, t0, t1)
            samples = PATH_SAMPLES[edge[0]] * (max(1, (len(edge[2]) - 1) // 8) if edge[0] == "bspline" else 1)
            path = g(np.linspace(0.0, 1.0, samples + 1))
            curves.append(_Curve(i, edge, g, path))
    return curves


def _cut(curves):
    """Marks where curves of different items cross or touch (s on each, refined)."""
    boxes = [(c.path.min(axis=0) - 1e-6, c.path.max(axis=0) + 1e-6) for c in curves]
    for i, a in enumerate(curves):
        for j in range(i + 1, len(curves)):
            b = curves[j]
            if a.item == b.item or np.any(boxes[i][1] < boxes[j][0]) or np.any(boxes[j][1] < boxes[i][0]):
                continue
            for sa, sb in _crossings(a, b):
                a.cuts.append(sa)
                b.cuts.append(sb)
    for c in curves:                            # touching: an end of one on another's middle
        for s_end in (0.0, 1.0):
            p = c.point(s_end)
            for d in curves:
                if d.item == c.item or np.any(p < d.path.min(axis=0) - CONNECT_MM) \
                        or np.any(p > d.path.max(axis=0) + CONNECT_MM):
                    continue
                s = _nearest(d, p)
                if s is not None and np.linalg.norm(d.point(s) - p) <= CONNECT_MM:
                    d.cuts.append(s)


def _crossings(a, b):
    """[(s on a, s on b)] where two curves cross: their polylines' crossings, refined (Newton)."""
    pa, pb = a.path, b.path
    p, r = pa[:-1][:, None, :], (pa[1:] - pa[:-1])[:, None, :]
    q, t = pb[:-1][None, :, :], (pb[1:] - pb[:-1])[None, :, :]
    denom = r[..., 0] * t[..., 1] - r[..., 1] * t[..., 0]
    qp = q - p
    with np.errstate(divide="ignore", invalid="ignore"):
        u = (qp[..., 0] * t[..., 1] - qp[..., 1] * t[..., 0]) / denom
        v = (qp[..., 0] * r[..., 1] - qp[..., 1] * r[..., 0]) / denom
    eps = 1e-9
    hit = (np.abs(denom) > 1e-30) & (u >= -eps) & (u <= 1 + eps) & (v >= -eps) & (v <= 1 + eps)
    na, nb = len(pa) - 1, len(pb) - 1
    found = []
    for i, j in zip(*np.nonzero(hit)):
        sa, sb = _refine(a, b, (i + u[i, j]) / na, (j + v[i, j]) / nb)
        if sa is None:
            continue
        if not any(abs(sa - x) < 1e-7 and abs(sb - y) < 1e-7 for x, y in found):
            found.append((sa, sb))
    return found


def _refine(a, b, sa, sb):
    """Newton on a(sa) = b(sb), from a guess; (sa, sb), or (None, None) if it does not settle in [0, 1]."""
    for _ in range(NEWTON_STEPS):
        f = a.point(sa) - b.point(sb)
        if np.linalg.norm(f) < 1e-12:
            break
        jac = np.column_stack([a.tangent(sa), -b.tangent(sb)])
        try:
            step = np.linalg.solve(jac, -f)
        except np.linalg.LinAlgError:
            break
        sa, sb = float(np.clip(sa + step[0], 0.0, 1.0)), float(np.clip(sb + step[1], 0.0, 1.0))
    if np.linalg.norm(a.point(sa) - b.point(sb)) > SAME_PLACE_MM:
        return None, None
    return sa, sb


def _nearest(c, p):
    """s of the point of curve c nearest to p (refined), or None."""
    d = np.linalg.norm(c.path - p, axis=1)
    s = np.argmin(d) / (len(c.path) - 1)
    for _ in range(NEWTON_STEPS):
        tangent = c.tangent(s)
        length2 = float(tangent @ tangent)
        if length2 < 1e-30:
            return None
        step = float((p - c.point(s)) @ tangent) / length2
        s = float(np.clip(s + step, 0.0, 1.0))
        if abs(step) < 1e-13:
            break
    return s


@dataclass
class _Edge:
    a: int                  # nodes
    b: int
    name: str               # what bounds: the item's name, or "name:k" for its piece k
    path: np.ndarray        # (K, 2) mm from a to b
    kernel: list            # its kernel edges from a to b


def _pieces(items, curves):
    """
    The graph's pieces [(name, s0, s1, curve)]: each curve cut at its inner
    cuts; an item cut anywhere is named by piece ("name:k", k from its start;
    a closed one's piece through its start is 0), else by itself.
    """
    by_item = {}
    for c in curves:
        by_item.setdefault(c.item, []).append(c)
    pieces = []
    for i, (name, _, closed) in enumerate(items):
        runs = []                               # (curve, s0, s1, starts at a cut), in the item's order
        for c in by_item.get(i, []):
            bounds = [0.0] + _cuts(c) + [1.0]
            runs += [(c, bounds[k], bounds[k + 1], k > 0) for k in range(len(bounds) - 1)]
        cut = any(starts for *_, starts in runs)
        index, numbered = 0, []
        for c, s0, s1, starts in runs:
            index += starts
            numbered.append((c, s0, s1, index))
        mine = by_item.get(i, [])
        start_cut = bool(mine) and (any(x <= 1e-9 for x in mine[0].cuts) or any(x >= 1 - 1e-9 for x in mine[-1].cuts))
        if closed and cut and not start_cut:    # the pieces after the last cut run on into piece 0
            numbered = [(c, s0, s1, 0 if n == index else n) for c, s0, s1, n in numbered]
        pieces += [(f"{name}:{n}" if cut else name, s0, s1, c) for c, s0, s1, n in numbered]
    return pieces


def _cuts(c):
    """A curve's cuts (s), sorted: those within CONNECT_MM of each other one, none within it of its ends."""
    length = float(np.linalg.norm(np.diff(c.path, axis=0), axis=1).sum())
    out = []
    for s in sorted(c.cuts):
        apart = not out or (s - out[-1]) * length > CONNECT_MM
        if s * length > CONNECT_MM and (1.0 - s) * length > CONNECT_MM and apart:
            out.append(s)
    return out


def _loops(items, cut=True):
    if not items:
        return []
    curves = _curves(items)
    if cut:
        _cut(curves)
    places, edges, loops = [], [], []

    built = []                                  # (name, curve, path, kernel)
    for name, s0, s1, c in _pieces(items, curves):
        count = max(2, int(np.ceil((s1 - s0) * (len(c.path) - 1))) + 1)
        path = c.g(np.linspace(s0, s1, count))
        if np.linalg.norm(np.diff(path, axis=0), axis=1).sum() <= SAME_PLACE_MM:
            continue                            # no length
        built.append((name, c, path, [c.edge if (s0, s1) == (0.0, 1.0) else sub_edge(c.edge, s0, s1)]))
    # Nodes: ends within CONNECT_MM are one, placed by the end least free to move (an exact curve's).
    ends = [(RIGIDITY[k[0][0]], xy) for _, _, path, k in built for xy in (path[0], path[-1])]
    for _, xy in sorted(ends, key=lambda end: end[0]):
        if all(np.linalg.norm(p - xy) > CONNECT_MM for p in places):
            places.append(np.asarray(xy, float))

    def node(xy):
        gaps = [np.linalg.norm(p - xy) for p in places]
        return int(np.argmin(gaps))

    for name, c, path, kernel in built:
        a, b = node(path[0]), node(path[-1])
        if a == b and len(items[c.item][1]) == 1 and len(kernel) == 1 and kernel[0][0] in ("circle", "bspline")                 and np.linalg.norm(path[0] - path[-1]) <= SAME_PLACE_MM:
            polygon = path[:-1]                 # a closed curve nothing cuts: a loop on its own
            loop = Loop((name,), polygon, kernel)
            loops.append(loop if loop.area > 0 else Loop((name,), polygon[::-1], [reversed_edge(kernel[0])]))
            continue
        if a == b:
            continue                            # shorter than CONNECT_MM end to end
        path = path.copy()
        path[0], path[-1] = places[a], places[b]
        kernel = [_ended(kernel[0], places[a], places[b])]
        middle = path[len(path) // 2]
        if any({e.a, e.b} == {a, b} and np.linalg.norm(e.path[len(e.path) // 2] - middle) <= 1e-4
               for e in edges):
            continue                            # the same way as one there already (a band's cap on a line)
        edges.append(_Edge(a, b, name, path, kernel))
    # Prune dangling pieces.
    while True:
        degree = {}
        for e in edges:
            degree[e.a] = degree.get(e.a, 0) + 1
            degree[e.b] = degree.get(e.b, 0) + 1
        kept = [e for e in edges if degree[e.a] > 1 and degree[e.b] > 1]
        if len(kept) == len(edges):
            break
        edges = kept
    # Half-edges (edge index, forward) leaving each node, sorted by the way they leave it.
    around = {}
    for i, e in enumerate(edges):
        around.setdefault(e.a, []).append((i, True))
        around.setdefault(e.b, []).append((i, False))

    def leaving(half):
        path = edges[half[0]].path if half[1] else edges[half[0]].path[::-1]
        d = path[1] - path[0]
        return np.arctan2(d[1], d[0])

    for ring in around.values():
        ring.sort(key=leaving)
    used = set()
    for start in [(i, f) for i in range(len(edges)) for f in (True, False)]:
        if start in used:
            continue
        face, half = [], start
        while half not in used:
            used.add(half)
            face.append(half)
            e = edges[half[0]]
            head = e.b if half[1] else e.a
            ring = around[head]
            # The next edge: the one just clockwise of the way back, so faces run counter-clockwise.
            k = ring.index((half[0], not half[1]))
            half = ring[(k - 1) % len(ring)]
        polygon = np.concatenate([(edges[i].path if f else edges[i].path[::-1])[:-1] for i, f in face])
        if _area(polygon) <= 1e-12:
            continue                            # the outer face of a component (clockwise)
        names = tuple(sorted({edges[i].name for i, _ in face}))
        loop_edges = []
        for i, f in face:
            run = edges[i].kernel if f else [reversed_edge(k) for k in edges[i].kernel[::-1]]
            loop_edges += [tuple(np.copy(v) if isinstance(v, np.ndarray) else v for v in k) for k in run]
        loops.append(Loop(names, polygon, loop_edges))
    return loops


def _ended(edge, a, b):
    """
    A kernel edge with its ends moved onto the joints a, b (by under
    CONNECT_MM): a line's ends, an arc's, a B-spline's end poles; a
    parabola or a circle stays (the joint was placed on it).
    """
    if edge[0] == "line":
        return ("line", a.copy(), b.copy(), *edge[3:])
    if edge[0] == "arc":
        return ("arc", a.copy(), edge[2], b.copy())
    if edge[0] == "bspline":
        poles = np.array(edge[1], float)
        poles[0], poles[-1] = a, b
        return ("bspline", poles, *edge[2:])
    return edge


def _area(polygon):
    """Signed area (counter-clockwise > 0)."""
    x, y = polygon[:, 0], polygon[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


def _inside(xy, polygon):
    """Point in polygon (even-odd rule)."""
    x, y = xy
    px, py = polygon[:, 0], polygon[:, 1]
    qx, qy = np.roll(px, -1), np.roll(py, -1)
    crosses = (py > y) != (qy > y)
    with np.errstate(divide="ignore", invalid="ignore"):
        at = px + (y - py) * (qx - px) / (qy - py)
    return bool(np.count_nonzero(crosses & (x < at)) % 2)
