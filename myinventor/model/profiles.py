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
# - A circle (not construction) is a loop on its own.
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

SAME_PLACE_MM = 1e-6
CIRCLE_SAMPLES = 96


@dataclass
class Loop:
    names: tuple            # sorted entity names (the key)
    polygon: np.ndarray     # (N, 2) mm, counter-clockwise, not closed
    edges: list             # [("line", (2,) mm, (2,) mm, line name)] in order, or [("circle", center (2,), radius)]

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
    loops = _line_loops(result) + _circle_loops(result)
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


def _line_loops(result):
    """Bounded faces of the planar graph of the (non-construction) lines."""
    lines = [line for line in result.lines if not line.construction]
    if not lines:
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
    edges = {}
    for line in lines:
        a, b = node_of[line.start], node_of[line.end]
        if a != b and (a, b) not in edges and (b, a) not in edges:
            edges[(a, b)] = line.name
    # Prune dangling edges.
    while True:
        degree = {}
        for a, b in edges:
            degree[a] = degree.get(a, 0) + 1
            degree[b] = degree.get(b, 0) + 1
        dangling = [e for e in edges if degree[e[0]] == 1 or degree[e[1]] == 1]
        if not dangling:
            break
        for e in dangling:
            del edges[e]
    if not edges:
        return []
    # Half-edges sorted by angle around each node.
    around = {}
    name_of = {}
    for (a, b), name in edges.items():
        around.setdefault(a, []).append(b)
        around.setdefault(b, []).append(a)
        name_of[(a, b)] = name_of[(b, a)] = name
    for node, others in around.items():
        others.sort(key=lambda o: np.arctan2(*(places[o] - places[node])[::-1]))
    loops, used = [], set()
    for start in name_of:
        if start in used:
            continue
        face, half = [], start
        while half not in used:
            used.add(half)
            face.append(half)
            a, b = half
            ring = around[b]
            # The next edge: the one just clockwise of the way back (b -> a), so faces run counter-clockwise.
            k = ring.index(a)
            half = (b, ring[(k - 1) % len(ring)])
        polygon = np.array([places[a] for a, _ in face])
        if _area(polygon) <= 1e-12:
            continue                            # the outer face of a component (clockwise)
        names = tuple(sorted({name_of[h] for h in face}))
        loop_edges = [("line", places[a].copy(), places[b].copy(), name_of[(a, b)]) for a, b in face]
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
