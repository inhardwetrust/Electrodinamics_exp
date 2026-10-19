# holes.py
#
# Through holes of the model (drilled vias, mounting holes), found from the
# cylindrical holes of each solid (Body.holes, read by step_io from the
# exact CAD faces), numpy only.
#
# A body hole counts when it is
#   - through: just beyond both ends, on the axis, there is no material of
#     the same body (a blind pocket has a bottom);
#   - open: the middle of its axis is not inside another solid (a pin in a
#     connector's bore, a dielectric in a coax shield are not holes).
# Holes of different bodies on the same axis line, with the same radius and
# touching axial ranges (the drill through FR4 and both copper layers) are
# one hole. A different radius on the same axis stays a separate hole: a
# ground plane's clearance (antipad) around a via must not touch it.
#
# Hole ids (H1, H2 ...) follow the diameter, then the position: stable while
# the model does not change. Stored assignments (later) use coordinates.

from dataclasses import dataclass, field

import numpy as np

from geometry.queries import points_inside

LINE_TOL = 1e-6         # m: same axis line, touching ranges
RADIUS_TOL = 1e-7       # m


@dataclass
class Hole:
    id: str
    start: np.ndarray           # point on the axis where the hole begins (m)
    axis: np.ndarray            # unit vector, start -> end
    radius: float               # m
    length: float               # m
    bodies: list = field(default_factory=list)

    @property
    def end(self):
        return self.start + self.axis * self.length

    @property
    def center(self):
        return self.start + 0.5 * self.axis * self.length

    @property
    def diameter(self):
        return 2.0 * self.radius

    @property
    def axis_index(self):
        """0 / 1 / 2 when the axis is along x / y / z, else None."""
        a = int(np.argmax(np.abs(self.axis)))
        return a if abs(abs(self.axis[a]) - 1.0) < 1e-9 else None


def find_holes(geometry):
    """[Hole] of the whole model, sorted by diameter, then position."""
    solids = [b for b in geometry.bodies if b.kind == "solid"]
    parts = []                              # (body name, start, axis, radius, length)
    for body in solids:
        for row in body.holes:
            start, axis, radius, length = row[:3], _canonical(row[3:6]), row[6], row[7]
            if np.dot(row[3:6], axis) < 0:          # same segment, canonical direction
                start = start + row[3:6] * length
            if _through(body, start, axis, length) and _open(solids, body, start + 0.5 * axis * length):
                parts.append((body.name, start, axis, radius, length))

    stacks = []                             # [[start, axis, radius, lo, hi, {bodies}]]
    for name, start, axis, radius, length in parts:
        for stack in stacks:
            s0, a0, r0, lo, hi, names = stack
            offset = start - s0
            lateral = np.linalg.norm(offset - np.dot(offset, a0) * a0)
            t0 = float(np.dot(offset, a0))
            if (abs(np.dot(axis, a0)) > 1 - 1e-9 and lateral < LINE_TOL and abs(radius - r0) < RADIUS_TOL
                    and t0 <= hi + LINE_TOL and t0 + length >= lo - LINE_TOL):
                stack[3], stack[4] = min(lo, t0), max(hi, t0 + length)
                names.add(name)
                break
        else:
            stacks.append([start, axis, radius, 0.0, length, {name}])

    # Merging can make two stacks touch (A-C seen before B): repeat until stable.
    merged = True
    while merged:
        merged = False
        for i in range(len(stacks)):
            for j in range(i + 1, len(stacks)):
                s0, a0, r0, lo, hi, names = stacks[i]
                s1, a1, r1, lo1, hi1, names1 = stacks[j]
                offset = s1 - s0
                t = float(np.dot(offset, a0))
                lateral = np.linalg.norm(offset - t * a0)
                if (lateral < LINE_TOL and abs(r1 - r0) < RADIUS_TOL
                        and t + lo1 <= hi + LINE_TOL and t + hi1 >= lo - LINE_TOL):
                    stacks[i][3], stacks[i][4] = min(lo, t + lo1), max(hi, t + hi1)
                    names |= names1
                    del stacks[j]
                    merged = True
                    break
            if merged:
                break

    holes = [Hole("", s0 + a0 * lo, a0, r0, hi - lo, sorted(names)) for s0, a0, r0, lo, hi, names in stacks]
    holes.sort(key=lambda h: (round(h.diameter * 1e7), *np.round(h.center * 1e7)))
    for n, hole in enumerate(holes, start=1):
        hole.id = f"H{n}"
    return holes


def group_by_diameter(holes):
    """{diameter (m, rounded to 0.1 um): [Hole]} in increasing diameter."""
    groups = {}
    for hole in holes:
        groups.setdefault(round(hole.diameter, 7), []).append(hole)
    return dict(sorted(groups.items()))


def _canonical(axis):
    """The axis with its first non-zero component positive (one direction per line)."""
    axis = np.asarray(axis, dtype=np.float64)
    axis = axis / np.linalg.norm(axis)
    k = int(np.argmax(np.abs(axis) > 1e-12))
    return axis if axis[k] > 0 else -axis


def _through(body, start, axis, length):
    """No material of the body just beyond either end of the hole, on its axis."""
    step = max(1e-6, 1e-3 * length)
    beyond = np.array([start - axis * step, start + axis * (length + step)])
    return not points_inside(body, beyond).any()


def _open(solids, owner, point):
    """The middle of the hole is not inside another solid."""
    return not any(points_inside(b, point[None])[0] for b in solids if b is not owner)


def via_body(hole, segments=48):
    """
    A closed PEC cylinder filling `hole` (geometry.body.Body, solid, named
    "via H<n>"): what an assigned via adds to the solver model. A regular
    48-gon is within 0.2 % of the radius.
    """
    from geometry.body import Body

    axis = hole.axis / np.linalg.norm(hole.axis)
    helper = np.array([1.0, 0.0, 0.0]) if abs(axis[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    e1 = np.cross(axis, helper)
    e1 /= np.linalg.norm(e1)
    e2 = np.cross(axis, e1)
    t = np.linspace(0, 2 * np.pi, segments, endpoint=False)
    ring = hole.radius * (np.cos(t)[:, None] * e1 + np.sin(t)[:, None] * e2)
    bottom, top = hole.start + ring, hole.end + ring

    n = segments
    vertices = np.concatenate([bottom, top, [hole.start, hole.end]])
    c0, c1 = 2 * n, 2 * n + 1
    triangles = []
    for i in range(n):
        j = (i + 1) % n
        triangles += [(i, j, n + j), (i, n + j, n + i)]        # side, outward (e1 x e2 = axis)
        triangles += [(c0, j, i), (c1, n + i, n + j)]          # caps: bottom -axis, top +axis
    triangles = np.array(triangles, dtype=np.int32)
    planar = np.tile([False, False, True, True], n)              # sides curved, caps planar

    a, b, c = (vertices[triangles[:, k]] for k in range(3))
    face_normal = np.cross(b - a, c - a)
    normals = np.zeros_like(vertices)
    for k in range(3):
        np.add.at(normals, triangles[:, k], face_normal)
    normals /= np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-300)

    edges = np.concatenate([np.stack([bottom, np.roll(bottom, -1, axis=0)], axis=1),
                            np.stack([top, np.roll(top, -1, axis=0)], axis=1)])
    circles = np.array([[*hole.start, *axis, hole.radius], [*hole.end, *axis, hole.radius]])
    return Body(f"via {hole.id}", "solid", vertices, triangles, normals, edges,
                circles=circles, planar=planar)
