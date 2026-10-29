# kernel.py
#
# The geometry the features build, through OpenCASCADE (OCP). Shapes are in
# millimeters, as everywhere in geometry/; bodies leave in meters (Body).
#
# - region_face: a sketch region (model/profiles.py) as a planar face in 3D:
#   its outer loop's wire (lines as straight edges, a circle as an exact
#   circle, an arc through three points exactly), minus its holes;
# - extrude: the faces swept along the sketch's normal by a distance
#   (negative: the other way), several regions fused into one solid;
# - boolean: join (fuse), cut or intersect (common) of two shapes, for the
#   features that change a body;
# - volume, solids: exact properties for the checks and the window.
#
# OCP 8 naming: static methods end in _s; booleans are pairwise (no
# TopTools_ListOfShape binding).

import numpy as np

from OCP.BRepAlgoAPI import BRepAlgoAPI_Common, BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakeWire
from OCP.BRepGProp import BRepGProp
from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
from OCP.GC import GC_MakeArcOfCircle
from OCP.GProp import GProp_GProps
from OCP.ShapeUpgrade import ShapeUpgrade_UnifySameDomain
from OCP.TopAbs import TopAbs_SOLID
from OCP.TopExp import TopExp_Explorer
from OCP.gp import gp_Ax2, gp_Circ, gp_Dir, gp_Pnt, gp_Vec

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
        else:
            _, center, radius = edge
            axis = gp_Ax2(_world(frame, center), gp_Dir(*map(float, frame.normal)), gp_Dir(*map(float, frame.u)))
            builder.Add(BRepBuilderAPI_MakeEdge(gp_Circ(axis, float(radius))).Edge())
    if not builder.IsDone():
        raise ValueError(f"the loop {', '.join(loop.names)} does not close")
    return builder.Wire()


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


def body(name, shape, color=None):
    """A Body (meters, tessellated) of an OCP shape in mm."""
    return body_from_shape(name, shape, color=color)


def volume(shape):
    """Exact volume (mm^3)."""
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, props)
    return props.Mass()


def solid_count(shape):
    explorer, n = TopExp_Explorer(shape, TopAbs_SOLID), 0
    while explorer.More():
        n += 1
        explorer.Next()
    return n
