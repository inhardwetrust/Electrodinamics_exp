# picking.py
#
# What is under the cursor in the 3D view: a point of the model, snapped
# like in CAD measuring tools, for measurements.
#   - priority: a CAD vertex (Body.corners) within VERTEX_PX of the click,
#     else the nearest point of a CAD edge within EDGE_PX, else the point
#     where the ray through the click enters the first face;
#   - a vertex or edge point must be visible: no face between the camera
#     and it (X-ray: everything is visible). Hidden bodies and the removed
#     half of a section are skipped;
#   - everything is numpy in scene units (mm). The camera comes in as a
#     VisPy transform (scene -> canvas pixels, `map` / `imap`); the click is
#     in canvas pixels, like the mouse events.

from dataclasses import dataclass

import numpy as np

VERTEX_PX = 8.0
EDGE_PX = 6.0
MAX_TRIES = 20              # candidates tested for visibility, nearest first
SCENE_UNIT = 1e-3


@dataclass
class Pick:
    point: np.ndarray       # (3,) mm
    kind: str               # "vertex" | "edge" | "face"
    body: str


class Picker:
    """Built once per geometry; pick() per click."""

    def __init__(self, bodies):
        self.names = [b.name for b in bodies]
        self.corners = [b.corners / SCENE_UNIT for b in bodies]
        self.edges = [b.edges / SCENE_UNIT for b in bodies]
        self.triangles = [b.vertices[b.triangles] / SCENE_UNIT for b in bodies]   # (M, 3, 3)
        size = [np.ptp(b.vertices, axis=0).max() for b in bodies if len(b.vertices)]
        # A point counts as visible up to this far behind the first face:
        # an edge lies on its own faces, and a curve's samples are off its
        # tessellated faces by up to the deflection.
        self.tolerance = 2e-4 * max(size, default=1.0) / SCENE_UNIT

    def pick(self, transform, pos, visible=None, section=None, occlude=True):
        """
        transform  scene (mm) -> canvas pixels (VisPy: map / imap, homogeneous)
        pos        (x, y) click in canvas pixels
        visible    names of the bodies shown (None = all)
        section    (axis, coordinate mm, keep_below) or None
        occlude    False in X-ray: vertices and edges behind faces count
        Returns a Pick or None.
        """
        shown = [i for i, n in enumerate(self.names) if visible is None or n in visible]
        if not shown:
            return None
        pos = np.asarray(pos[:2], dtype=float)
        faces = np.concatenate([self.triangles[i] for i in shown])
        owner = np.repeat(shown, [len(self.triangles[i]) for i in shown])

        def kept(points):
            if section is None:
                return np.ones(len(points), bool)
            axis, coordinate, keep_below = section
            c = points[:, axis]
            return c <= coordinate + self.tolerance if keep_below else c >= coordinate - self.tolerance

        def seen(point):
            if not occlude:
                return True
            origin, direction = _ray(transform, _project(transform, point[None])[0][0])
            t, _ = _first_hit(origin, direction, faces, kept)
            return t >= np.dot(point - origin, direction) - self.tolerance

        for kind, candidates in (("vertex", self._vertex_candidates), ("edge", self._edge_candidates)):
            points, bodies, distance = candidates(transform, pos, shown)
            ok = kept(points) if len(points) else np.zeros(0, bool)
            for k in np.argsort(distance)[:MAX_TRIES]:
                if ok[k] and seen(points[k]):
                    return Pick(points[k], kind, self.names[bodies[k]])

        origin, direction = _ray(transform, pos)
        t, face = _first_hit(origin, direction, faces, kept)
        if np.isfinite(t):
            return Pick(origin + t * direction, "face", self.names[owner[face]])
        return None

    def _vertex_candidates(self, transform, pos, shown):
        points = np.concatenate([self.corners[i] for i in shown])
        bodies = np.repeat(shown, [len(self.corners[i]) for i in shown])
        if not len(points):
            return points, bodies, np.zeros(0)
        xy, front = _project(transform, points)
        distance = np.linalg.norm(xy - pos, axis=1)
        near = front & (distance <= VERTEX_PX)
        return points[near], bodies[near], distance[near]

    def _edge_candidates(self, transform, pos, shown):
        segments = np.concatenate([self.edges[i] for i in shown])
        bodies = np.repeat(shown, [len(self.edges[i]) for i in shown])
        if not len(segments):
            return np.zeros((0, 3)), bodies, np.zeros(0)
        a, front_a = _project(transform, segments[:, 0])
        b, front_b = _project(transform, segments[:, 1])
        ab = b - a
        length2 = np.einsum("ij,ij->i", ab, ab)
        s = np.clip(np.einsum("ij,ij->i", pos - a, ab) / np.maximum(length2, 1e-12), 0.0, 1.0)
        distance = np.linalg.norm(a + s[:, None] * ab - pos, axis=1)
        near = front_a & front_b & (distance <= EDGE_PX)
        # The point is the one of the 3D segment closest to the ray through
        # the click, not the screen fraction s: in perspective a segment
        # going into the depth is foreshortened unevenly, and its screen
        # middle is not its middle.
        p, q = segments[near, 0], segments[near, 1]
        origin, direction = _ray(transform, pos)
        u, w = q - p, p - origin
        a = np.einsum("ij,ij->i", u, u)
        b = u @ direction
        d, e = np.einsum("ij,ij->i", u, w), w @ direction
        denominator = a - b * b                     # |direction| = 1
        parallel = denominator <= 1e-12 * np.maximum(a, 1e-30)
        t = np.where(parallel, s[near], (b * e - d) / np.where(parallel, 1.0, denominator))
        t = np.clip(t, 0.0, 1.0)
        return p + t[:, None] * u, bodies[near], distance[near]


def _project(transform, points):
    """(N, 3) mm -> (N, 2) canvas pixels, and which points are in front of the camera."""
    h = transform.map(np.asarray(points, dtype=float))
    w = h[:, 3]
    front = w > 0
    xy = h[:, :2] / np.where(front, w, 1.0)[:, None]
    return xy, front


def _ray(transform, pixel):
    """The ray through a canvas pixel: its point on the near plane and the unit direction."""
    near = transform.imap([pixel[0], pixel[1], -1.0, 1.0])
    mid = transform.imap([pixel[0], pixel[1], 0.0, 1.0])
    near, mid = near[:3] / near[3], mid[:3] / mid[3]
    direction = mid - near
    return near, direction / np.linalg.norm(direction)


def _first_hit(origin, direction, faces, kept):
    """
    Distance along the ray to the first triangle it crosses (Möller–Trumbore)
    whose hit point is kept by the section; (inf, -1) if none.
    """
    a, b, c = faces[:, 0], faces[:, 1], faces[:, 2]
    e1, e2 = b - a, c - a
    p = np.cross(direction, e2)
    det = np.einsum("ij,ij->i", e1, p)
    ok = np.abs(det) > 1e-18
    inv = np.where(ok, 1.0 / np.where(ok, det, 1.0), 0.0)
    s = origin - a
    u = np.einsum("ij,ij->i", s, p) * inv
    q = np.cross(s, e1)
    v = (q @ direction) * inv
    t = np.einsum("ij,ij->i", e2, q) * inv
    hit = ok & (u >= 0) & (v >= 0) & (u + v <= 1) & (t > 0)
    index = np.nonzero(hit)[0]
    if not len(index):
        return np.inf, -1
    index = index[kept(origin + t[index, None] * direction)]
    if not len(index):
        return np.inf, -1
    first = index[np.argmin(t[index])]
    return float(t[first]), int(first)
