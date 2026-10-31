# project.py
#
# Projecting body faces into a sketch (Sketch > Project Geometry): the
# boundary edges of the faces, projected straight onto the sketch's plane,
# as sketch geometry in its own x, y (mm). OCP; the sketch side is
# model/sketch.py add_projection.
#   - a straight edge: ("line", a, b);
#   - a circle or an arc in a plane parallel to the sketch: exact,
#     ("circle", center, radius) or ("arc", center, radius, from, to)
#     (degrees, counter-clockwise from `from` to `to`, as a sketch arc);
#   - anything else (a circle seen at an angle is an ellipse, B-splines):
#     ("curve", at, t0, t1): at(ts) -> (N, 2) mm; the window fits a spline
#     through points of it (model/curves.fit_points);
#   - an edge that projects to a point (along the normal) is left out; one
#     that projects onto a straight segment (a circle seen edge-on) becomes
#     that segment.
# Edges shared by several faces are projected once.

import numpy as np
from OCP.BRep import BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Curve
from OCP.GeomAbs import GeomAbs_Circle, GeomAbs_Line
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS

GONE_MM = 1e-6              # a projection shorter than this is a point: left out
STRAIGHT_MM = 1e-6          # a projected curve this close to a segment is that segment
SAMPLES = 64


def face_of(shape, index):
    """Face `index` of a body (TopExp_Explorer order, as tessellate.py numbers them); IndexError if none."""
    explorer, k = TopExp_Explorer(shape, TopAbs_FACE), 0
    while explorer.More():
        if k == index:
            return TopoDS.Face(explorer.Current())
        k += 1
        explorer.Next()
    raise IndexError(f"no face {index}")


def _edges(faces):
    """The faces' edges, each once (a shared edge is the same TShape), degenerate ones left out."""
    out = []
    for face in faces:
        explorer = TopExp_Explorer(face, TopAbs_EDGE)
        while explorer.More():
            edge = TopoDS.Edge(explorer.Current())
            if not BRep_Tool.Degenerated_s(edge) and not any(edge.IsSame(e) for e in out):
                out.append(edge)
            explorer.Next()
    return out


def project_faces(faces, origin, u, v):
    """
    The faces' boundary edges projected onto the plane through `origin`
    (mm) with axes u, v (unit, the sketch's x and y): a list of items (see
    the top), in sketch mm.
    """
    origin, u, v = (np.asarray(a, dtype=float) for a in (origin, u, v))
    normal = np.cross(u, v)

    def to_xy(p):
        d = np.array([p.X(), p.Y(), p.Z()]) - origin
        return np.array([d @ u, d @ v])

    items = []
    for edge in _edges(faces):
        curve = BRepAdaptor_Curve(edge)
        t0, t1 = curve.FirstParameter(), curve.LastParameter()
        kind = curve.GetType()
        if kind == GeomAbs_Line:
            a, b = to_xy(curve.Value(t0)), to_xy(curve.Value(t1))
            if np.linalg.norm(b - a) > GONE_MM:
                items.append(("line", a, b))
            continue
        if kind == GeomAbs_Circle:
            circle = curve.Circle()
            axis = circle.Axis().Direction()
            if abs(abs(np.dot([axis.X(), axis.Y(), axis.Z()], normal)) - 1.0) < 1e-9:
                center, radius = to_xy(circle.Location()), circle.Radius()
                if abs(t1 - t0 - 2.0 * np.pi) < 1e-9:
                    items.append(("circle", center, radius))
                else:
                    items.append(("arc", center, radius, *_arc_angles(center, [to_xy(curve.Value(t))
                                                                               for t in (t0, 0.5 * (t0 + t1), t1)])))
                continue

        def at(ts, curve=curve):
            return np.array([to_xy(curve.Value(float(t))) for t in np.atleast_1d(ts)])

        points = at(np.linspace(t0, t1, SAMPLES + 1))
        straight = _segment(points)
        if straight is False:
            continue                            # projects to a point
        if straight is not None:
            items.append(("line", *straight))
            continue
        items.append(("curve", at, t0, t1))
    return items


def _arc_angles(center, points):
    """(from, to) degrees, counter-clockwise, of the arc through start, middle, end (sketch mm)."""
    a, m, b = (float(np.degrees(np.arctan2(*(p - center)[::-1]))) for p in points)
    span, middle = (b - a) % 360.0, (m - a) % 360.0
    return (a, b) if middle < span else (b, a)


def _segment(points):
    """False: the points are one point; (a, b): they lie on segment a-b; None: they do not."""
    lo = points.min(axis=0)
    if np.linalg.norm(points.max(axis=0) - lo) <= GONE_MM:
        return False
    mean = points.mean(axis=0)
    _, _, vt = np.linalg.svd(points - mean)
    direction = vt[0]
    along = (points - mean) @ direction
    off = np.abs((points - mean) @ np.array([-direction[1], direction[0]]))
    if off.max() > STRAIGHT_MM:
        return None
    return points[int(np.argmin(along))], points[int(np.argmax(along))]     # its ends exactly where they project
