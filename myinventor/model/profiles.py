# profiles.py
#
# The closed regions of a sketch (profiles, what an extrusion takes), from
# its evaluated geometry (model/sketch.py SketchResult). numpy only.
#
# - Lines join where they share a point; points at the same place (the ends
#   of separate chains) count as one. Construction geometry is left out.
# - Lines alone: the bounded faces of the planar graph they form (dangling
#   lines pruned first). A rectangle is one face; with a diagonal, two
#   triangles. Each face's boundary is a loop of lines.
# - A circle (not construction) is a loop on its own, and so is a wide
#   polyline's outline (model/traces.py: its edges are lines and arcs).
# - A generated curve (model/curves.py) is an edge between its two end
#   points, a spline one between its first and last points, like a line (a
#   closed one is a loop on its own); edges are ordered around a point by
#   the way they leave it (a curve: its tangent).
# - Regions: a loop minus the loops directly inside it (holes). A circle in
#   a rectangle gives two regions: the rectangle with a round hole, and the
#   disc (as Inventor does).
# - Not yet: curves crossing without a shared point (a line over a circle)
#   do not split regions; loops that overlap without one inside the other
#   are taken as they are.
#
# A region is named by its outer loop: the sorted names of its lines or its
# circle (Region.key). Features refer to profiles by that key, so moving
# points or changing sizes keeps the reference; breaking the loop does not.

from dataclasses import dataclass, field

import numpy as np

from model.curves import end_points, reversed_edge

SAME_PLACE_MM = 1e-6
CIRCLE_SAMPLES = 96


@dataclass
class Loop:
    names: tuple            # sorted entity names (the key)
    polygon: np.ndarray     # (N, 2) mm, counter-clockwise, not closed
    edges: list             # in order: ("line", (2,) mm, (2,) mm[, line name]), ("arc", a, middle, b), curve
    #                         edges (model/curves.py: "parabola", "bspline"),
    #                         or [("circle", center (2,), radius)]

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
    """The regions of a SketchResult, largest first."""
    loops = _line_loops(result) + _circle_loops(result) + _polyline_loops(result)
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


def _polyline_loops(result):
    """Each wide polyline's outline is a loop on its own (model/traces.py), named by the polyline."""
    loops = []
    for polyline in getattr(result, "polylines", []):
        if polyline.name not in result.outlines:
            continue                        # it failed: no outline, no profile
        edges, polygon = result.outlines[polyline.name]
        loops.append(Loop((polyline.name,), polygon, list(edges)))
    return loops


def _circle_loops(result):
    loops = []
    for circle in result.circles:
        if circle.construction:
            continue
        center, radius = result.points[circle.center], result.radii[circle.name]
        t = np.linspace(0.0, 2.0 * np.pi, CIRCLE_SAMPLES, endpoint=False)
        polygon = center + radius * np.stack([np.cos(t), np.sin(t)], axis=1)
        loops.append(Loop((circle.name,), polygon, [("circle", center.copy(), radius)]))
    return loops


@dataclass
class _Edge:
    a: int                  # nodes
    b: int
    name: str
    path: np.ndarray        # (K, 2) mm from a to b (a line: its two ends; a curve: its samples)
    kernel: list            # its kernel edges from a to b (model/curves.py)


def _line_loops(result):
    """
    Bounded faces of the planar graph of the (non-construction) lines and
    generated curves (each an edge between its two end points; a closed
    curve is a loop on its own).
    """
    lines = [line for line in result.lines if not line.construction]
    # Generated curves run between their end points, splines between their first and last points.
    curves = [(c.name, *end_points(c.name)) for c in getattr(result, "curves", [])
              if not c.construction and c.name in result.shapes]
    curves += [(w.name, w.points[0], w.points[-1]) for w in getattr(result, "splines", [])
               if not w.construction and w.name in result.shapes]
    if not lines and not curves:
        return []
    # Nodes: points merged by place.
    node_of, places = {}, []
    for name, xy in result.points.items():
        for i, p in enumerate(places):
            if np.allclose(p, xy, atol=SAME_PLACE_MM):
                node_of[name] = i
                break
        else:
            node_of[name] = len(places)
            places.append(np.asarray(xy, float))
    edges, loops = [], []
    pairs = set()
    for line in lines:
        a, b = node_of[line.start], node_of[line.end]
        if a != b and (a, b) not in pairs and (b, a) not in pairs:
            pairs |= {(a, b)}
            pa, pb = places[a].copy(), places[b].copy()
            edges.append(_Edge(a, b, line.name, np.array([pa, pb]), [("line", pa, pb, line.name)]))
    for name, start, end in curves:
        shape = result.shapes[name]
        a, b = node_of[start], node_of[end]
        if a == b:                              # closed: a loop on its own
            loop = Loop((name,), shape.points[:-1], list(shape.edges))
            loops.append(loop if loop.area > 0 else Loop((name,), shape.points[:0:-1],
                                                         [reversed_edge(e) for e in shape.edges[::-1]]))
            continue
        edges.append(_Edge(a, b, name, shape.points, list(shape.edges)))
    # Prune dangling edges.
    while True:
        degree = {}
        for e in edges:
            degree[e.a] = degree.get(e.a, 0) + 1
            degree[e.b] = degree.get(e.b, 0) + 1
        kept = [e for e in edges if degree[e.a] > 1 and degree[e.b] > 1]
        if len(kept) == len(edges):
            break
        edges = kept
    # Half-edges (edge index, forward) leaving each node, sorted by the way they leave it:
    # a curve by its tangent there, so a line and a curve between the same two points differ.
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
