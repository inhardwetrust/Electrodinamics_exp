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
#   extrude_to_plane: up to a plane (parallel: exact by its distance;
#   inclined: cut by it); extrude_to_body: until it meets a body (a long
#   sweep minus the body, the pieces that start on the sketch);
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

from OCP.BOPAlgo import BOPAlgo_Splitter
from OCP.BRep import BRep_Builder, BRep_Tool
from OCP.BRepAlgoAPI import BRepAlgoAPI_Common, BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
from OCP.BRepBndLib import BRepBndLib
from OCP.Bnd import Bnd_Box
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepBuilderAPI import (
    BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakeWire, BRepBuilderAPI_Transform,
)
from OCP.BRepLib import BRepLib
from OCP.BRepGProp import BRepGProp
from OCP.BRepPrimAPI import BRepPrimAPI_MakeHalfSpace, BRepPrimAPI_MakePrism, BRepPrimAPI_MakeRevol
from OCP.BRepTools import BRepTools
from OCP.GC import GC_MakeArcOfCircle, GC_MakeArcOfParabola
from OCP.GProp import GProp_GProps
from OCP.GeomAbs import GeomAbs_C3
from OCP.HLRAlgo import HLRAlgo_Projector
from OCP.HLRBRep import HLRBRep_Algo, HLRBRep_HLRToShape
from OCP.IntCurvesFace import IntCurvesFace_ShapeIntersector
from OCP.ShapeUpgrade import ShapeUpgrade_ShapeDivideContinuity, ShapeUpgrade_UnifySameDomain
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_SOLID, TopAbs_VERTEX
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS, TopoDS_Shape
from OCP.gp import gp_Ax1, gp_Ax2, gp_Ax3, gp_Circ, gp_Dir, gp_Lin, gp_Parab, gp_Pln, gp_Pnt, gp_Trsf, gp_Vec

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


REACH_TOL = 1e-6        # mm: a piece "starts on the sketch" / "reaches the far end" within this


def _profile_points(frame, regions):
    """(N, 3) mm: the outer polygons of the regions in 3D (enough to bound them)."""
    xy = np.concatenate([r.outer.polygon for r in regions]) * 1e-3
    return frame.to_world(xy) * 1e3


def extrude_to_plane(frame, regions, origin, normal):
    """
    The regions swept along the frame's normal up to the plane through
    `origin` (3,) mm with normal `normal`, on whichever side it is.
    Returns (shape mm, distance): a plane parallel to the sketch gives an
    exact extrusion by that distance (signed); an inclined one, a longer one
    cut by the plane (distance None: no flat end of its own). ValueError if
    the plane never meets the sweep or crosses a profile.
    """
    n, m = np.asarray(frame.normal, float), np.asarray(normal, float)
    m = m / np.linalg.norm(m)
    along = float(n @ m)
    if abs(along) < 1e-9:
        raise ValueError("the plane runs along the extrusion: it is never reached")
    points = _profile_points(frame, regions)
    reach = (np.asarray(origin, float) - points) @ m / along      # mm along n from each profile point
    if np.all(np.abs(reach) < REACH_TOL):
        raise ValueError("the plane is the sketch's own: nothing to extrude")
    if reach.min() < -REACH_TOL and reach.max() > REACH_TOL:
        raise ValueError("the plane crosses the profile")
    if abs(abs(along) - 1.0) < 1e-12 or np.ptp(reach) < REACH_TOL:          # parallel: exact
        distance = float(np.mean(reach))
        return extrude(frame, regions, distance), distance
    side = 1.0 if reach.mean() > 0 else -1.0
    long = extrude(frame, regions, side * (float(np.abs(reach).max()) * 1.5 + 1.0))
    plane = BRepBuilderAPI_MakeFace(gp_Pln(gp_Pnt(*map(float, origin)), gp_Dir(*map(float, m)))).Face()
    keep = BRepPrimAPI_MakeHalfSpace(plane, gp_Pnt(*map(float, points[0]))).Solid()     # the sketch's side
    solid = BRepAlgoAPI_Common(long, keep).Shape()
    unify = ShapeUpgrade_UnifySameDomain(solid, True, True, False)
    unify.Build()
    solid = unify.Shape()
    _check_valid(solid)
    return solid, None


def extrude_to_body(frame, regions, body, fallback=None):
    """
    The regions swept along the frame's normal until they meet `body` (an
    OCP shape, mm), on the side the body is. Exactly, ray by ray: the
    body's outline seen along the sweep splits the profiles (_shadow_split);
    a piece whose rays meet the body goes up to it (a long sweep minus the
    body, the part that starts on the sketch); a piece whose rays miss it
    goes `fallback` mm (> 0, toward the body: the plane at that distance),
    or, with no fallback, fails the extrusion, saying how much misses and
    where. Returns (shape mm, reach: the longest it went, signed).
    ValueError if no profile meets the body or one starts inside it.
    """
    n = np.asarray(frame.normal, float)
    start = np.asarray(frame.origin, float) * 1e3
    lo, hi = bounds(body)
    corners = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])
    depth = (corners - start) @ n
    side = 1.0 if (lo + hi) @ n / 2 - start @ n >= 0 else -1.0
    far = float(max(side * depth.max(), side * depth.min(), 0.0))
    if side * depth.max() <= REACH_TOL and side * depth.min() <= REACH_TOL:
        raise ValueError("the body is not on either side of the sketch")
    if fallback is not None and fallback <= 0:
        raise ValueError("the distance where it misses the body must be more than 0 (toward the body)")
    length = far + 1.0 + 0.1 * far
    sweep = side * n
    pieces = _shadow_split(frame, [region_face(frame, r) for r in regions], body, sweep, length)
    if not any(hit for _, hit in pieces):
        raise ValueError("the profile never meets the body")
    missed = [face for face, hit in pieces if not hit]
    if missed and fallback is None:
        raise ValueError(_missed_text(frame, missed, [face for face, _ in pieces]))
    parts, reach = [], 0.0
    for face, hit in pieces:
        if not hit:
            parts.append(BRepPrimAPI_MakePrism(face, gp_Vec(*map(float, sweep * fallback))).Shape())
            reach = max(reach, float(fallback))
            continue
        rest = BRepAlgoAPI_Cut(BRepPrimAPI_MakePrism(face, gp_Vec(*map(float, sweep * length))).Shape(), body).Shape()
        kept = False
        for solid in _each_solid(rest):
            heights = (_vertices(solid) - start) @ sweep
            if heights.min() > REACH_TOL:
                continue                        # beyond the body (the sweep went through it)
            if heights.max() > length - REACH_TOL:      # a sliver along a tangent edge: nothing stops it
                if fallback is None:
                    raise ValueError("a sliver of the profile only grazes the body's edge (it would go on forever "
                                     "there): give a distance where it misses")
                solid = BRepAlgoAPI_Common(solid, BRepPrimAPI_MakePrism(
                    face, gp_Vec(*map(float, sweep * fallback))).Shape()).Shape()
                heights = np.minimum(heights, fallback)
            parts.append(solid)
            reach = max(reach, float(heights.max()))
            kept = True
        if not kept:
            raise ValueError("the profile starts inside the body")
    solid = parts[0]
    for part in parts[1:]:
        solid = BRepAlgoAPI_Fuse(solid, part).Shape()
    unify = ShapeUpgrade_UnifySameDomain(solid, True, True, False)
    unify.Build()
    solid = unify.Shape()
    _check_valid(solid)
    return solid, side * reach


def _shadow_split(frame, faces, body, sweep, length):
    """
    [(face, its rays meet the body?)]: the profile faces split by the body's
    outline seen along `sweep` (OCC's hidden-line removal: every edge and
    silhouette, visible and hidden, laid on the sketch's plane), each piece
    tested by one ray from a point inside it.
    """
    axes = gp_Ax2(gp_Pnt(*map(float, np.asarray(frame.origin) * 1e3)), gp_Dir(*map(float, sweep)),
                  gp_Dir(*map(float, frame.u)))
    algo = HLRBRep_Algo()
    algo.Add(body)
    algo.Projector(HLRAlgo_Projector(axes))
    algo.Update()
    algo.Hide()
    outline = HLRBRep_HLRToShape(algo)
    onto_plane = gp_Trsf()
    onto_plane.SetTransformation(gp_Ax3(axes), gp_Ax3())         # the projector's plane -> the sketch's
    edges = []
    for compound in (outline.VCompound(), outline.HCompound(), outline.OutLineVCompound(),
                     outline.OutLineHCompound()):
        if compound is None or compound.IsNull():
            continue
        BRepLib.BuildCurves3d_s(compound)       # HLR edges come as 2D curves only
        explorer = TopExp_Explorer(compound, TopAbs_EDGE)
        while explorer.More():
            edges.append(BRepBuilderAPI_Transform(explorer.Current(), onto_plane, True).Shape())
            explorer.Next()
    rays = IntCurvesFace_ShapeIntersector()
    rays.Load(body, 1e-7)
    direction = gp_Dir(*map(float, sweep))
    out = []
    for face in faces:
        splitter = BOPAlgo_Splitter()
        splitter.AddArgument(face)
        for edge in edges:
            splitter.AddTool(edge)
        splitter.Perform()
        explorer = TopExp_Explorer(splitter.Shape(), TopAbs_FACE)
        split = []
        while explorer.More():
            split.append(TopoDS.Face(explorer.Current()))
            explorer.Next()
        for piece in split or [face]:
            mesh = body_from_shape("piece", piece)
            inside = mesh.vertices[mesh.triangles[np.argmax(_triangle_areas(mesh))]].mean(axis=0) * 1e3   # m -> mm
            rays.Perform(gp_Lin(gp_Pnt(*map(float, inside)), direction), 0.0, length)
            out.append((piece, rays.NbPnt() > 0))
    return out


def _triangle_areas(mesh):
    t = mesh.vertices[mesh.triangles]
    return np.linalg.norm(np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0]), axis=1)


def _mesh_area(face):
    """A face's area from its triangles (OCC's own integral is off on the pieces the HLR edges cut)."""
    return float(_triangle_areas(body_from_shape("piece", face)).sum() / 2)


def _missed_text(frame, missed, faces):
    """The error for profiles partly missing the body: how much (areas) and where (the sketch's x, y)."""
    share = 100 * sum(_mesh_area(f) for f in missed) / sum(_mesh_area(f) for f in faces)
    points = np.concatenate([body_from_shape("piece", f).vertices for f in missed])
    xy = frame.to_sketch(points) * 1e3
    return (f"a part of the profile misses the body ({share:.0f} % of its area: sketch x "
            f"{xy[:, 0].min():.4g}..{xy[:, 0].max():.4g}, y {xy[:, 1].min():.4g}..{xy[:, 1].max():.4g} mm) would go "
            "on forever: give a distance where it misses (the body's outline cuts it there), make the body reach "
            "over the whole profile, or the profile smaller")


def _each_solid(shape):
    explorer, out = TopExp_Explorer(shape, TopAbs_SOLID), []
    while explorer.More():
        out.append(TopoDS.Solid(explorer.Current()))
        explorer.Next()
    return out


def _vertices(shape):
    """(N, 3) mm: the points of a shape's vertices."""
    explorer, out = TopExp_Explorer(shape, TopAbs_VERTEX), []
    while explorer.More():
        vertex = TopoDS.Vertex(explorer.Current())
        p = BRep_Tool.Pnt_s(vertex)
        out.append((p.X(), p.Y(), p.Z()))
        explorer.Next()
    return np.array(out)


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


def move_trsf(point, direction, angle, offset):
    """The placement turning by `angle` degrees around the axis through `point` (3,) mm along `direction`, then
    moving by `offset` (3,) mm (opaque: for placed / then)."""
    from OCP.gp import gp_Trsf

    turn = gp_Trsf()
    if abs(angle) > 0:
        turn.SetRotation(gp_Ax1(gp_Pnt(*map(float, point)), gp_Dir(*map(float, direction))), float(np.radians(angle)))
    move = gp_Trsf()
    move.SetTranslation(gp_Vec(*map(float, offset)))
    return move.Multiplied(turn)


def mirror_trsf(point, normal):
    """The placement mirroring in the plane through `point` (3,) mm with `normal` (3,)."""
    from OCP.gp import gp_Trsf

    trsf = gp_Trsf()
    trsf.SetMirror(gp_Ax2(gp_Pnt(*map(float, point)), gp_Dir(*map(float, normal))))
    return trsf


def placed(shape, trsf):
    """A shape (mm) under a placement: a copy."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform

    return BRepBuilderAPI_Transform(shape, trsf, True).Shape()


def placed_frame(trsf, origin, u, v):
    """A sketch frame (origin mm, unit axes u, v) under a placement: (origin, u, v) as numpy arrays."""
    o = gp_Pnt(*map(float, origin)).Transformed(trsf)
    a, b = (gp_Vec(*map(float, w)).Transformed(trsf) for w in (u, v))
    return np.array([o.X(), o.Y(), o.Z()]), np.array([a.X(), a.Y(), a.Z()]), np.array([b.X(), b.Y(), b.Z()])


def then(first, second):
    """The placement `first`, then `second` (first may be None: none yet)."""
    return second if first is None else second.Multiplied(first)


def transform(shape, point, direction, angle, offset):
    """A shape (mm) turned by `angle` degrees around the axis, then moved by `offset` (3,) mm: a copy."""
    return placed(shape, move_trsf(point, direction, angle, offset))


def mirror(shape, point, normal):
    """A shape (mm) mirrored in the plane through `point` (3,) mm with `normal` (3,): a copy."""
    return placed(shape, mirror_trsf(point, normal))


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
