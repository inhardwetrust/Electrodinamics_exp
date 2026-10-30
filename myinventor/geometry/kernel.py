# kernel.py
#
# The geometry the features build, through OpenCASCADE (OCP). Shapes are in
# millimeters, as everywhere in geometry/; bodies leave in meters (Body).
#
# - region_face: a sketch region (model/profiles.py) as a planar face in 3D:
#   its outer loop's wire (lines as straight edges, a circle as an exact
#   circle, an arc through three points exactly, a parabola as OCC's, a
#   B-spline as one B-spline edge), minus its holes;
# - extrude: the faces swept along the sketch's normal by a distance
#   (negative: the other way), several regions fused into one solid;
# - revolve: the faces turned around an axis by an angle, fused into one
#   solid;
# - boolean: join (fuse), cut or intersect (common) of two shapes, for the
#   features that change a body;
# - volume, bounds, solids: exact properties for the checks, the window and
#   the report (cli.py).
#
# OCP 8 naming: static methods end in _s; booleans are pairwise (no
# TopTools_ListOfShape binding). It binds no point arrays either, so a
# B-spline cannot be made through the API: _bspline writes it as OCC's own
# BREP text and reads that back (from memory).

import io

import numpy as np

from OCP.BRep import BRep_Builder
from OCP.BRepAlgoAPI import BRepAlgoAPI_Common, BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
from OCP.BRepBndLib import BRepBndLib
from OCP.Bnd import Bnd_Box
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakeWire
from OCP.BRepGProp import BRepGProp
from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism, BRepPrimAPI_MakeRevol
from OCP.BRepTools import BRepTools
from OCP.GC import GC_MakeArcOfCircle, GC_MakeArcOfParabola
from OCP.GProp import GProp_GProps
from OCP.GeomAbs import GeomAbs_C3
from OCP.ShapeUpgrade import ShapeUpgrade_ShapeDivideContinuity, ShapeUpgrade_UnifySameDomain
from OCP.TopAbs import TopAbs_SOLID
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS, TopoDS_Shape
from OCP.gp import gp_Ax1, gp_Ax2, gp_Circ, gp_Dir, gp_Parab, gp_Pnt, gp_Vec

from geometry.tessellate import body_from_shape

MM = 1e-3


def _world(frame, xy):
    """Sketch (2,) mm -> gp_Pnt in mm."""
    p = frame.to_world(np.asarray(xy, float)[None] * MM)[0] / MM
    return gp_Pnt(*map(float, p))


def _wire(frame, loop):
    builder = BRepBuilderAPI_MakeWire()
    for edge in loop.edges:
        if edge[0] == "line":
            builder.Add(BRepBuilderAPI_MakeEdge(_world(frame, edge[1]), _world(frame, edge[2])).Edge())
        elif edge[0] == "arc":                  # through three points: exact
            arc = GC_MakeArcOfCircle(_world(frame, edge[1]), _world(frame, edge[2]), _world(frame, edge[3])).Value()
            builder.Add(BRepBuilderAPI_MakeEdge(arc).Edge())
        elif edge[0] == "parabola":
            builder.Add(_parabola(frame, *edge[1:]))
        elif edge[0] == "bspline":
            builder.Add(_bspline(frame, *edge[1:]))
        else:
            _, center, radius = edge
            axis = gp_Ax2(_world(frame, center), gp_Dir(*map(float, frame.normal)), gp_Dir(*map(float, frame.u)))
            builder.Add(BRepBuilderAPI_MakeEdge(gp_Circ(axis, float(radius))).Edge())
    if not builder.IsDone():
        raise ValueError(f"the loop {', '.join(loop.names)} does not close")
    return builder.Wire()


def _parabola(frame, vertex, focal, u0, u1):
    """
    The exact parabola vertex + (u, u^2 / (4 focal)) of the sketch (model/curves.py), u from u0
    to u1. OCC's: P(u) = O + u^2 / (4F) XDir + u YDir, so XDir = the sketch's v (its axis),
    YDir = its u, and the main direction XDir x YDir = -normal.
    """
    axis = gp_Ax2(_world(frame, vertex), gp_Dir(*map(float, -np.asarray(frame.normal))),
                  gp_Dir(*map(float, frame.v)))
    parab = gp_Parab(axis, float(focal))
    lo, hi = sorted((float(u0), float(u1)))
    edge = BRepBuilderAPI_MakeEdge(GC_MakeArcOfParabola(parab, lo, hi, True).Value()).Edge()
    return edge if u0 < u1 else TopoDS.Edge(edge.Reversed())


def _bspline(frame, poles, knots, mults, degree):
    """
    A non-rational B-spline edge (poles (K, 2) mm in the sketch; the ends
    clamped) through OCC's BREP text (curve record 7: rational, periodic,
    degree, poles, knots, then the poles and the knots with multiplicities).
    The frame is affine, so the world poles make the same curve in 3D.
    """
    world = frame.to_world(np.asarray(poles, float) * MM) / MM
    numbers = lambda values: " ".join(f"{float(v):.17g}" for v in values)     # noqa: E731
    closed = bool(np.allclose(world[0], world[-1], atol=1e-9))
    ends = [world[0]] if closed else [world[0], world[-1]]
    lines = ["CASCADE Topology V3, (c) Open Cascade", "Locations 0", "Curve2ds 0", "Curves 1",
             f"7 0 0 {degree} {len(world)} {len(knots)} {numbers(world.ravel())} "
             + " ".join(f"{float(u):.17g} {int(m)}" for u, m in zip(knots, mults)),
             "Polygon3D 0", "PolygonOnTriangulations 0", "Surfaces 0", "Triangulations 0", "",
             f"TShapes {len(ends) + 1}"]
    for p in ends:                              # numbered from the end: the edge is 1
        lines += ["Ve", "1e-07", numbers(p), "0 0", "", "0101101", "*"]
    first, last = len(ends) + 1, len(ends) + 1 if closed else len(ends)
    lines += ["Ed", " 1e-07 1 1 0", f"1  1 0 {float(knots[0]):.17g} {float(knots[-1]):.17g}", "0", "",
              "1101000", f"+{first} 0 -{last} 0 *", "", "+1 0", ""]
    shape = TopoDS_Shape()
    BRepTools.Read_s(shape, io.BytesIO("\n".join(lines).encode()), BRep_Builder())
    if shape.IsNull():
        raise ValueError("a B-spline could not be built")
    return TopoDS.Edge(shape)


def region_face(frame, region):
    """A planar face (mm) of a model.profiles.Region on a model.sketch.SketchFrame."""
    face = BRepBuilderAPI_MakeFace(_wire(frame, region.outer), True).Shape()
    for hole in region.holes:
        face = BRepAlgoAPI_Cut(face, BRepBuilderAPI_MakeFace(_wire(frame, hole), True).Shape()).Shape()
    return face


def extrude(frame, regions, distance):
    """The regions swept by `distance` (mm) along the frame's normal, fused: an OCP shape (mm)."""
    if not regions:
        raise ValueError("no profile")
    if abs(distance) < 1e-9:
        raise ValueError("the distance must not be 0")
    vector = gp_Vec(*map(float, np.asarray(frame.normal) * distance))
    solid = None
    for region in regions:
        prism = BRepPrimAPI_MakePrism(region_face(frame, region), vector).Shape()
        solid = prism if solid is None else BRepAlgoAPI_Fuse(solid, prism).Shape()
    unify = ShapeUpgrade_UnifySameDomain(solid, True, True, False)
    unify.Build()
    solid = unify.Shape()
    _check_valid(solid)
    return solid


def revolve(frame, regions, point, direction, angle):
    """
    The regions turned around the axis through `point` (3,) mm along
    `direction` by `angle` degrees (right hand around it; 360: all the way
    round), fused: an OCP shape (mm).
    """
    if not regions:
        raise ValueError("no profile")
    axis = gp_Ax1(gp_Pnt(*map(float, point)), gp_Dir(*map(float, direction)))
    full = abs(abs(angle) - 360.0) < 1e-9
    solid = None
    for region in regions:
        face = region_face(frame, region)
        maker = BRepPrimAPI_MakeRevol(face, axis) if full else \
            BRepPrimAPI_MakeRevol(face, axis, float(np.radians(angle)))
        turned = maker.Shape()
        solid = turned if solid is None else BRepAlgoAPI_Fuse(solid, turned).Shape()
    unify = ShapeUpgrade_UnifySameDomain(solid, True, True, False)
    unify.Build()
    solid = unify.Shape()
    _check_valid(solid)
    return solid


def boolean(operation, target, tool):
    """
    "join" (fuse), "cut" (target - tool) or "intersect" (common) of two
    shapes (mm), coplanar faces merged; ValueError if nothing is left.
    """
    maker = {"join": BRepAlgoAPI_Fuse, "cut": BRepAlgoAPI_Cut, "intersect": BRepAlgoAPI_Common}[operation]
    result = maker(target, tool).Shape()
    unify = ShapeUpgrade_UnifySameDomain(result, True, True, False)
    unify.Build()
    result = unify.Shape()
    if solid_count(result) == 0 or volume(result) <= 1e-12:
        what = {"cut": "the cut removes all of it", "intersect": "they do not overlap",
                "join": "nothing is left"}[operation]
        raise ValueError(what)
    if operation == "cut" and abs(volume(result) - volume(target)) <= 1e-9 * max(volume(target), 1.0):
        raise ValueError("the cut removes nothing (it does not reach the body)")
    _check_valid(result)
    return result


def _check_valid(shape):
    """ValueError if OCC made a shape that is not a valid solid (self-crossing, degenerate)."""
    if not BRepCheck_Analyzer(shape).IsValid():
        raise ValueError("the result is not a valid solid (a self-crossing or degenerate profile?)")


def read_step(path):
    """[(name, kind, shape mm, color)] of a STEP file's bodies (geometry/step_io.py read_bodies)."""
    from geometry.step_io import read_bodies

    return read_bodies(path)


def file_sha256(path):
    from geometry.step_io import file_sha256 as sha

    return sha(path)


def transform(shape, point, direction, angle, offset):
    """
    A shape (mm) turned by `angle` degrees around the axis through `point`
    (3,) mm along `direction` (the right hand way), then moved by `offset`
    (3,) mm: a copy.
    """
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    from OCP.gp import gp_Trsf

    turn = gp_Trsf()
    if abs(angle) > 0:
        turn.SetRotation(gp_Ax1(gp_Pnt(*map(float, point)), gp_Dir(*map(float, direction))), float(np.radians(angle)))
    move = gp_Trsf()
    move.SetTranslation(gp_Vec(*map(float, offset)))
    return BRepBuilderAPI_Transform(shape, move.Multiplied(turn), True).Shape()


def mirror(shape, point, normal):
    """A shape (mm) mirrored in the plane through `point` (3,) mm with `normal` (3,): a copy."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    from OCP.gp import gp_Trsf

    trsf = gp_Trsf()
    trsf.SetMirror(gp_Ax2(gp_Pnt(*map(float, point)), gp_Dir(*map(float, normal))))
    return BRepBuilderAPI_Transform(shape, trsf, True).Shape()


_PLANAR = {}                # id of a shape -> its planar faces (an import's shapes are kept, read once)


def planar_faces(shape):
    """
    [(index, center (3,) mm, u (3,), normal (3,) outward)] of a shape's
    planar faces, by their index in the explorer's order (the one the view's
    face picking counts); the center is the face's center of mass.
    """
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Plane
    from OCP.TopAbs import TopAbs_FACE, TopAbs_REVERSED

    key = id(shape)
    if key in _PLANAR and _PLANAR[key][0] is shape:
        return _PLANAR[key][1]
    out, explorer, index = [], TopExp_Explorer(shape, TopAbs_FACE), 0
    while explorer.More():
        face = TopoDS.Face(explorer.Current())
        explorer.Next()
        surface = BRepAdaptor_Surface(face)
        if surface.GetType() == GeomAbs_Plane:
            plane = surface.Plane()
            normal = plane.Axis().Direction()
            normal = np.array([normal.X(), normal.Y(), normal.Z()])
            if face.Orientation() == TopAbs_REVERSED:
                normal = -normal
            x = plane.XAxis().Direction()
            props = GProp_GProps()
            BRepGProp.SurfaceProperties_s(face, props)
            c = props.CentreOfMass()
            out.append((index, np.array([c.X(), c.Y(), c.Z()]), np.array([x.X(), x.Y(), x.Z()]), normal))
        index += 1
    _PLANAR[key] = (shape, out)
    return out


def body(name, shape, color=None):
    """A Body (meters, tessellated) of an OCP shape in mm."""
    return body_from_shape(name, shape, color=color)


def volume(shape):
    """
    Exact volume (mm^3). OCC integrates a B-spline face of many spans badly
    (5e-5 off, worse with a tolerance): a copy split at its knots is exact.
    """
    split = ShapeUpgrade_ShapeDivideContinuity(shape)
    for criterion in (split.SetBoundaryCriterion, split.SetPCurveCriterion, split.SetSurfaceCriterion):
        criterion(GeomAbs_C3)
    split.Perform()
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(split.Result(), props)
    return props.Mass()


def centroid(shape):
    """(3,) mm: the center of mass of a solid (as volume() splits B-spline faces first)."""
    split = ShapeUpgrade_ShapeDivideContinuity(shape)
    for criterion in (split.SetBoundaryCriterion, split.SetPCurveCriterion, split.SetSurfaceCriterion):
        criterion(GeomAbs_C3)
    split.Perform()
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(split.Result(), props)
    c = props.CentreOfMass()
    return np.array([c.X(), c.Y(), c.Z()])


def bounds(shape):
    """Exact (2, 3) min / max corners (mm). Bnd_Box.Get() is broken in OCP 8: CornerMin / CornerMax."""
    box = Bnd_Box()
    BRepBndLib.AddOptimal_s(shape, box, False, False)
    lo, hi = box.CornerMin(), box.CornerMax()
    return np.array([[lo.X(), lo.Y(), lo.Z()], [hi.X(), hi.Y(), hi.Z()]])


def solid_count(shape):
    explorer, n = TopExp_Explorer(shape, TopAbs_SOLID), 0
    while explorer.More():
        n += 1
        explorer.Next()
    return n
