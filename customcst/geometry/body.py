# body.py
#
# Body: one CAD body after import, in SI units (meters).
# This is the contract between the CAD kernel (OCP) and everything above it:
# upper levels (materials, meshing, voxelization, display) see only numpy
# arrays and never touch OCP. The OCP shape is kept as an optional handle for
# exact queries later (point classification, edge snapping).

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
    edges      (K, 2, 3) float64 segments of the CAD edges (display only)
    corners    (P, 3) float64 CAD vertices (the ends of the edges), meters;
               the points a measurement snaps to (view/picking.py)
    circles    (C, 7) float64 exact circular CAD edges, one row per circle:
               center (3, m), unit axis (3), radius (m). Used to read ports
               (the ends of a cone / cylinder).
    planar     (M,) bool per triangle: it lies on a planar CAD face (not on
               a facet of a tessellated curved face). The mesh takes its
               fixpoints only from these. None = all planar.
    holes      (H, 8) float64 cylindrical holes of a solid (concave
               cylinder faces closing a full turn): start point on the axis
               (3, m), unit axis (3), radius (m), length along the axis (m).
               See geometry/holes.py.
    color      RGB in 0..1 from the STEP file, or None
    shape      OCP TopoDS_Shape in millimeters, or None
    pieces     a body of several disjoint solids (a KiCad copper layer: the
               pour and a trace in its clearance): one Body per solid, else
               None. A conductor's PEC closure runs per piece, never across
               the gap between two of them (discretize/materials.py).
    """

    name: str
    kind: str
    vertices: np.ndarray
    triangles: np.ndarray
    normals: np.ndarray
    edges: np.ndarray
    corners: np.ndarray = None
    circles: np.ndarray = None
    holes: np.ndarray = None
    planar: np.ndarray = None
    color: Optional[Color] = None
    shape: object = field(default=None, repr=False)
    pieces: Optional[list] = field(default=None, repr=False)

    def __post_init__(self):
        if self.kind not in BODY_KINDS:
            raise ValueError(f"body {self.name!r}: unknown kind {self.kind!r}")

        self.vertices = np.asarray(self.vertices, dtype=np.float64).reshape(-1, 3)
        self.triangles = np.asarray(self.triangles, dtype=np.int32).reshape(-1, 3)
        self.normals = np.asarray(self.normals, dtype=np.float32).reshape(-1, 3)
        self.edges = np.asarray(self.edges, dtype=np.float64).reshape(-1, 2, 3)
        corners = self.edges.reshape(-1, 3) if self.corners is None else self.corners
        self.corners = np.asarray(corners, dtype=np.float64).reshape(-1, 3)
        circles = np.zeros((0, 7)) if self.circles is None else self.circles
        self.circles = np.asarray(circles, dtype=np.float64).reshape(-1, 7)
        holes = np.zeros((0, 8)) if self.holes is None else self.holes
        self.holes = np.asarray(holes, dtype=np.float64).reshape(-1, 8)
        planar = np.ones(len(self.triangles), dtype=bool) if self.planar is None else self.planar
        self.planar = np.asarray(planar, dtype=bool).reshape(-1)
        if len(self.planar) != len(self.triangles):
            raise ValueError(f"body {self.name!r}: {len(self.planar)} planar flags "
                             f"for {len(self.triangles)} triangles")

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
    """All bodies of one imported file."""

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
