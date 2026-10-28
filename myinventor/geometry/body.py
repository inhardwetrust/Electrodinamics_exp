# body.py
#
# Body: one modeled body after tessellation, in SI units (meters).
# The contract between the CAD kernel (OCP) and everything above it, as in
# customcst/geometry/body.py: the view and picking see only numpy arrays and
# never touch OCP. The OCP shape is kept as a handle for exact queries and
# for writing STEP.

from dataclasses import dataclass, field
from typing import Optional, Tuple

import numpy as np

BODY_KINDS = ("solid", "sheet")

Color = Tuple[float, float, float]


@dataclass
class Body:
    """
    name       unique within a Geometry
    kind       "solid": closed volume; "sheet": zero-thickness surface
    vertices   (N, 3) float64, meters
    triangles  (M, 3) int32 into vertices, outward winding for solids
    normals    (N, 3) float32 per vertex (vertices are not shared between
               CAD faces, so creases stay sharp)
    faces      (M,) int32 per triangle: the index of its CAD face (in
               TopExp_Explorer order), for picking and highlighting a face
    edges      (K, 2, 3) float64 segments of the CAD edges (display only)
    corners    (P, 3) float64 CAD vertices (the ends of the edges), meters;
               the points a measurement snaps to (view/picking.py)
    color      RGB in 0..1, or None (the view's default)
    shape      OCP TopoDS_Shape in millimeters, or None
    """

    name: str
    kind: str
    vertices: np.ndarray
    triangles: np.ndarray
    normals: np.ndarray
    edges: np.ndarray
    faces: np.ndarray = None
    corners: np.ndarray = None
    color: Optional[Color] = None
    shape: object = field(default=None, repr=False)

    def __post_init__(self):
        if self.kind not in BODY_KINDS:
            raise ValueError(f"body {self.name!r}: unknown kind {self.kind!r}")

        self.vertices = np.asarray(self.vertices, dtype=np.float64).reshape(-1, 3)
        self.triangles = np.asarray(self.triangles, dtype=np.int32).reshape(-1, 3)
        self.normals = np.asarray(self.normals, dtype=np.float32).reshape(-1, 3)
        self.edges = np.asarray(self.edges, dtype=np.float64).reshape(-1, 2, 3)
        corners = self.edges.reshape(-1, 3) if self.corners is None else self.corners
        self.corners = np.asarray(corners, dtype=np.float64).reshape(-1, 3)
        faces = np.zeros(len(self.triangles)) if self.faces is None else self.faces
        self.faces = np.asarray(faces, dtype=np.int32).reshape(-1)
        if len(self.faces) != len(self.triangles):
            raise ValueError(f"body {self.name!r}: {len(self.faces)} face indices "
                             f"for {len(self.triangles)} triangles")

    @property
    def face_count(self):
        return int(self.faces.max()) + 1 if len(self.faces) else 0

    @property
    def bounds(self):
        """((x0, y0, z0), (x1, y1, z1)) in meters."""
        return self.vertices.min(axis=0), self.vertices.max(axis=0)

    @property
    def area(self):
        a, b, c = (self.vertices[self.triangles[:, n]] for n in range(3))
        return 0.5 * float(np.linalg.norm(np.cross(b - a, c - a), axis=1).sum())

    @property
    def volume(self):
        """Enclosed volume from the triangles (divergence theorem); 0 for sheets."""
        if self.kind != "solid":
            return 0.0

        a, b, c = (self.vertices[self.triangles[:, n]] for n in range(3))
        return float(np.einsum("ij,ij->i", a, np.cross(b, c)).sum() / 6.0)


@dataclass
class Geometry:
    """All bodies of the model."""

    bodies: list
    source: str = ""

    def __post_init__(self):
        names = [b.name for b in self.bodies]
        duplicates = sorted({n for n in names if names.count(n) > 1})

        if duplicates:
            raise ValueError(f"duplicate body names: {duplicates}")

    def __getitem__(self, name):
        for body in self.bodies:
            if body.name == name:
                return body
        raise KeyError(name)

    @property
    def bounds(self):
        if not self.bodies:
            return np.zeros(3), np.zeros(3)

        lo = np.min([b.bounds[0] for b in self.bodies], axis=0)
        hi = np.max([b.bounds[1] for b in self.bodies], axis=0)
        return lo, hi
