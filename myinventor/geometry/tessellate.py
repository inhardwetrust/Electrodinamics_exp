# tessellate.py
#
# OCP shape (millimeters) -> Body (numpy, meters). Taken from
# customcst/geometry/step_io.py, plus the CAD face index of every triangle
# (Body.faces), so a click can select a face.
#
# OCP 8 naming: static methods end in _s (BRep_Tool.Triangulation_s), but the
# TopoDS downcasts are plain functions (TopoDS.Face).

import numpy as np

from OCP.BRep import BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Curve
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.Bnd import Bnd_Box
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCP.GCPnts import GCPnts_TangentialDeflection
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_REVERSED, TopAbs_SOLID
from OCP.TopExp import TopExp_Explorer
from OCP.TopLoc import TopLoc_Location
from OCP.TopoDS import TopoDS
from OCP.gp import gp_Pnt

from geometry.body import Body

# Shapes are in millimeters (the usual CAD unit); bodies leave in meters.
MM = 1e-3


def make_box(lo, hi):
    """An OCP box between two corners (mm)."""
    return BRepPrimAPI_MakeBox(gp_Pnt(*map(float, lo)), gp_Pnt(*map(float, hi))).Shape()


def body_from_shape(name, shape, color=None, linear_deflection=None, angular_deflection=0.3):
    """
    A Body of an OCP shape in mm. linear_deflection (m): the largest gap
    between a curved face and its triangles; None = 1e-3 of the shape's size.
    """
    deflection_mm = _deflection_mm(shape) if linear_deflection is None else linear_deflection / MM
    BRepMesh_IncrementalMesh(shape, deflection_mm, False, angular_deflection, True)

    vertices, triangles, faces = _triangles(shape)
    normals = _vertex_normals(vertices, triangles)
    edges, corners = _edge_segments(shape, deflection_mm, angular_deflection)
    kind = "solid" if TopExp_Explorer(shape, TopAbs_SOLID).More() else "sheet"

    return Body(
        name=name,
        kind=kind,
        vertices=vertices * MM,
        triangles=triangles,
        normals=normals,
        edges=edges * MM,
        faces=faces,
        corners=corners * MM,
        color=color,
        shape=shape,
    )


def _deflection_mm(shape):
    """
    1e-3 of the shape's diagonal (its exact bounds: the ends of its edges
    are no measure, a torus's or a revolution's closed edges all end where
    they start, which asked for millions of triangles).
    """
    box = Bnd_Box()
    BRepBndLib.AddOptimal_s(shape, box, False, False)
    if box.IsVoid():
        return 0.01
    lo, hi = box.CornerMin(), box.CornerMax()
    diagonal = float(np.linalg.norm([hi.X() - lo.X(), hi.Y() - lo.Y(), hi.Z() - lo.Z()]))
    return max(diagonal * 1e-3, 1e-4)


def _triangles(shape):
    """
    Per-face triangulations merged; vertices are NOT shared between faces.
    Returns (vertices, triangles, faces): faces is the CAD face index of each
    triangle (explorer order, counting faces without triangles too).
    """
    vertex_blocks = []
    triangle_blocks = []
    face_blocks = []
    offset = 0
    index = -1

    explorer = TopExp_Explorer(shape, TopAbs_FACE)
    while explorer.More():
        face = TopoDS.Face(explorer.Current())
        location = TopLoc_Location()
        tri = BRep_Tool.Triangulation_s(face, location)
        explorer.Next()
        index += 1

        if tri is None or tri.NbTriangles() == 0:
            continue

        trsf = location.Transformation()
        nodes = np.empty((tri.NbNodes(), 3))
        for i in range(tri.NbNodes()):
            p = tri.Node(i + 1).Transformed(trsf)
            nodes[i] = (p.X(), p.Y(), p.Z())

        triangles = np.empty((tri.NbTriangles(), 3), dtype=np.int64)
        for i in range(tri.NbTriangles()):
            t = tri.Triangle(i + 1)
            triangles[i] = (t.Value(1), t.Value(2), t.Value(3))
        triangles -= 1

        if face.Orientation() == TopAbs_REVERSED:
            triangles = triangles[:, ::-1]

        vertex_blocks.append(nodes)
        triangle_blocks.append(triangles + offset)
        face_blocks.append(np.full(len(triangles), index))
        offset += len(nodes)

    if not vertex_blocks:
        return np.zeros((0, 3)), np.zeros((0, 3), dtype=np.int32), np.zeros(0, dtype=np.int32)

    return (np.concatenate(vertex_blocks), np.concatenate(triangle_blocks).astype(np.int32),
            np.concatenate(face_blocks).astype(np.int32))


def _vertex_normals(vertices, triangles):
    """Area-weighted vertex normals (shared only within one CAD face)."""
    normals = np.zeros_like(vertices)

    if len(triangles):
        a, b, c = (vertices[triangles[:, n]] for n in range(3))
        face_normals = np.cross(b - a, c - a)
        for n in range(3):
            np.add.at(normals, triangles[:, n], face_normals)

    length = np.linalg.norm(normals, axis=1, keepdims=True)
    return normals / np.where(length > 0.0, length, 1.0)


def _edge_segments(shape, deflection_mm, angular_deflection):
    """
    CAD edges as line segments, and the CAD vertices (the edges' ends). The
    explorer visits an edge once per face that uses it (OCP 8 has no bound
    shape maps to deduplicate shapes), so identical segments and points are
    removed afterwards.
    """
    segments = []
    ends = []
    explorer = TopExp_Explorer(shape, TopAbs_EDGE)

    while explorer.More():
        edge = TopoDS.Edge(explorer.Current())
        explorer.Next()

        if BRep_Tool.Degenerated_s(edge):
            continue

        curve = BRepAdaptor_Curve(edge)
        sampler = GCPnts_TangentialDeflection(curve, angular_deflection, deflection_mm)
        points = [sampler.Value(i + 1) for i in range(sampler.NbPoints())]
        points = np.array([(p.X(), p.Y(), p.Z()) for p in points])

        if len(points) >= 2:
            segments.append(np.stack((points[:-1], points[1:]), axis=1))
            ends += [points[0], points[-1]]

    if not segments:
        return np.zeros((0, 2, 3)), np.zeros((0, 3))

    segments = np.concatenate(segments)

    # Same segment in either direction -> one key (1 nm grid in mm units).
    key = np.round(segments / 1e-6).astype(np.int64)
    forward = np.array([tuple(a) <= tuple(b) for a, b in key])
    ordered = np.where(forward[:, None, None], key, key[:, ::-1])
    _, keep = np.unique(ordered.reshape(len(key), 6), axis=0, return_index=True)
    ends = np.array(ends)
    _, first = np.unique(np.round(ends / 1e-6).astype(np.int64), axis=0, return_index=True)
    return segments[np.sort(keep)], ends[np.sort(first)]
