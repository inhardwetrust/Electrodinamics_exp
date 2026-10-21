# step_io.py
#
# STEP <-> Geometry, through OpenCASCADE (OCP, `pip install cadquery-ocp`).
# The only module that imports OCP. Everything leaves here as numpy arrays in
# meters (see body.py).
#
# Reading goes through XCAF, so body names, colors and assembly placements
# from the file are kept. Every leaf shape is split into:
#   - its solids                          -> kind "solid"
#   - shells / faces outside any solid    -> kind "sheet" (zero thickness)
# Free edges (wire antennas drawn as curves) are not imported yet.
# Solids named like their own part form one body (a copper layer of
# disjoint pieces written by geometry/kicad_step.py). Inventor names a part
# after its file and may repeat body names inside it (pcb_topmetall x 3):
# those stay one body per solid.
#
# OCP 8 naming: static methods end in _s (BRep_Tool.Triangulation_s), but the
# TopoDS downcasts are plain functions (TopoDS.Face).

import os

import numpy as np

from OCP.BRep import BRep_Builder, BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.GCPnts import GCPnts_TangentialDeflection
from OCP.GeomAbs import GeomAbs_Circle, GeomAbs_Cylinder, GeomAbs_Plane
from OCP.IFSelect import IFSelect_RetDone
from OCP.Quantity import Quantity_Color, Quantity_TOC_RGB
from OCP.STEPCAFControl import STEPCAFControl_Reader, STEPCAFControl_Writer
from OCP.STEPControl import STEPControl_AsIs
from OCP.TCollection import TCollection_ExtendedString
from OCP.TDataStd import TDataStd_Name
from OCP.TDocStd import TDocStd_Document
from OCP.TopAbs import (
    TopAbs_EDGE, TopAbs_FACE, TopAbs_REVERSED, TopAbs_SHELL, TopAbs_SOLID,
)
from OCP.TopExp import TopExp_Explorer
from OCP.TopLoc import TopLoc_Location
from OCP.TopoDS import TopoDS, TopoDS_Compound
from OCP.gp import gp_Pnt, gp_Vec
from OCP.XCAFDoc import XCAFDoc_ColorType, XCAFDoc_DocumentTool, XCAFDoc_ShapeTool
from OCP.XCAFPrs import (
    XCAFPrs_DocumentExplorer, XCAFPrs_DocumentExplorerFlags_OnlyLeafNodes,
)

from geometry.body import Body, Geometry

# Shapes inside the document are in millimeters (the usual CAD unit, and the
# one we ask the reader to convert to); bodies leave in meters.
MM = 1e-3


def _silence_occt():
    """OCCT prints transfer statistics to stdout; keep only real failures."""
    try:
        from OCP.Message import Message, Message_Gravity

        for printer in Message.DefaultMessenger_s().Printers():
            printer.SetTraceLevel(Message_Gravity.Message_Fail)
    except Exception:   # cosmetic only
        pass


def _new_document():
    doc = TDocStd_Document(TCollection_ExtendedString("XmlXCAF"))
    XCAFDoc_DocumentTool.SetLengthUnit_s(doc, MM)   # document unit: mm
    return doc


# =====================================================
# Reading
# =====================================================

def read_step(path, linear_deflection=None, angular_deflection=0.3):
    """
    Reads a STEP file into a Geometry (meters).

    linear_deflection: max distance between the triangles and the true
    surface, in meters. Default: 1e-3 of the model's bounding-box diagonal.
    Later the mesher will ask for a deflection well below the cell size.
    """
    path = os.fspath(path)

    if not os.path.isfile(path):
        raise FileNotFoundError(path)

    _silence_occt()
    doc = _new_document()

    reader = STEPCAFControl_Reader()
    reader.SetNameMode(True)
    reader.SetColorMode(True)

    if reader.ReadFile(path) != IFSelect_RetDone or not reader.Transfer(doc):
        raise ValueError(f"cannot read STEP file {path!r}")

    leaves = list(_leaf_shapes(doc))
    deflection_mm = _deflection_mm(
        [(name, shape.Moved(location), color) for name, shape, location, color in leaves],
        linear_deflection,
    )
    transfer = reader.ChangeReader().WS().TransferReader()

    bodies = []
    taken = set()

    for part_name, shape, location, color in leaves:
        parts = _split(shape)
        names = [_body_name(transfer, part, part_name, n, len(parts)) for n, (_, part) in enumerate(parts)]

        for name, kind, part, solids in _merge_part_named(part_name, names, parts):
            body_name = _unique(name, taken)
            body = _make_body(body_name, kind, part.Moved(location), color, deflection_mm, angular_deflection)
            if len(solids) > 1:
                body.pieces = [
                    _make_body(f"{body_name}#{n + 1}", kind, solid.Moved(location), color,
                               deflection_mm, angular_deflection)
                    for n, solid in enumerate(solids)
                ]
            bodies.append(body)

    return Geometry(bodies, source=path)


def _body_name(transfer, part, part_name, index, count):
    """
    A single-body part is named after the part (what the CAD assembly tree
    shows). In a multi-body part each body keeps its own name from the file
    (e.g. MANIFOLD_SOLID_BREP('port', ...)); unnamed ones get part#n.
    The XCAF sub-shape labels would also carry these names, but walking
    them crashes OCP 8, so the name is read from the STEP entity instead.
    """
    if count == 1:
        return part_name

    entity = transfer.EntityFromShapeResult(part, 1)
    name = ""

    if entity is not None and hasattr(entity, "Name"):
        name = entity.Name().ToCString().strip()

    return name or f"{part_name}#{index + 1}"


def _merge_part_named(part_name, names, parts):
    """[(name, kind, shape, [its solids])]: the solids named like the part become one compound."""
    groups = {}
    order = []
    for name, (kind, part) in zip(names, parts):
        key = (name, kind) if kind == "solid" and name == part_name else (name, kind, len(order))
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(part)

    merged = []
    for key in order:
        shapes = groups[key]
        if len(shapes) == 1:
            merged.append((key[0], key[1], shapes[0], shapes))
            continue
        compound = TopoDS_Compound()
        builder = BRep_Builder()
        builder.MakeCompound(compound)
        for shape in shapes:
            builder.Add(compound, shape)
        merged.append((key[0], key[1], compound, shapes))
    return merged


def _leaf_shapes(doc):
    """
    (part name, shape, location, color) for every leaf of the assembly tree.
    The shape is returned unlocated, as stored in the file, so the STEP
    reader can still map its sub-shapes back to entities (body names).
    """
    explorer = XCAFPrs_DocumentExplorer(doc, XCAFPrs_DocumentExplorerFlags_OnlyLeafNodes)
    index = 0

    while explorer.More():
        node = explorer.Current()       # keep the node alive while using its fields
        index += 1

        name = _label_name(node.RefLabel) or f"body{index}"
        shape = XCAFDoc_ShapeTool.GetShape_s(node.RefLabel)

        color = None
        style = node.Style
        if style.IsSetColorSurf():
            c = style.GetColorSurf()
            color = (c.Red(), c.Green(), c.Blue())

        yield name, shape, node.Location, color
        explorer.Next()


def _label_name(label):
    attribute = TDataStd_Name()

    if label.FindAttribute(TDataStd_Name.GetID_s(), attribute):
        return attribute.Get().ToExtString().strip()

    return ""


def _split(shape):
    """[(kind, shape)]: each solid, then each shell or face outside a solid."""
    parts = []

    explorer = TopExp_Explorer(shape, TopAbs_SOLID)
    while explorer.More():
        parts.append(("solid", explorer.Current()))
        explorer.Next()

    explorer = TopExp_Explorer(shape, TopAbs_SHELL, TopAbs_SOLID)
    while explorer.More():
        parts.append(("sheet", explorer.Current()))
        explorer.Next()

    explorer = TopExp_Explorer(shape, TopAbs_FACE, TopAbs_SHELL)
    while explorer.More():
        parts.append(("sheet", explorer.Current()))
        explorer.Next()

    return parts


def _unique(name, taken):
    candidate = name
    n = 2

    while candidate in taken:
        candidate = f"{name}_{n}"
        n += 1

    taken.add(candidate)
    return candidate


def _deflection_mm(leaves, linear_deflection):
    if linear_deflection is not None:
        return float(linear_deflection) / MM

    # Cheap bounding box: vertices of all edges are enough for a default.
    points = []
    for _, shape, _ in leaves:
        explorer = TopExp_Explorer(shape, TopAbs_EDGE)
        while explorer.More():
            curve = BRepAdaptor_Curve(TopoDS.Edge(explorer.Current()))
            for u in (curve.FirstParameter(), curve.LastParameter()):
                p = curve.Value(u)
                points.append((p.X(), p.Y(), p.Z()))
            explorer.Next()

    if not points:
        return 0.01

    points = np.asarray(points)
    diagonal = float(np.linalg.norm(points.max(axis=0) - points.min(axis=0)))
    return max(diagonal * 1e-3, 1e-4)


# =====================================================
# Tessellation (OCP shape in mm -> numpy in m)
# =====================================================

def _make_body(name, kind, shape, color, deflection_mm, angular_deflection):
    BRepMesh_IncrementalMesh(shape, deflection_mm, False, angular_deflection, True)

    vertices, triangles, planar = _triangles(shape)
    normals = _vertex_normals(vertices, triangles)
    edges, corners = _edge_segments(shape, deflection_mm, angular_deflection)
    circles = _circles(shape)
    circles[:, [0, 1, 2, 6]] *= MM
    holes = _holes(shape) if kind == "solid" else np.zeros((0, 8))
    holes[:, [0, 1, 2, 6, 7]] *= MM

    return Body(
        name=name,
        kind=kind,
        vertices=vertices * MM,
        triangles=triangles,
        normals=normals,
        edges=edges * MM,
        corners=corners * MM,
        circles=circles,
        holes=holes,
        planar=planar,
        color=color,
        shape=shape,
    )


def _circles(shape):
    """Exact circular edges: (C, 7) rows center(3), axis(3), radius; in mm."""
    rows = []
    explorer = TopExp_Explorer(shape, TopAbs_EDGE)

    while explorer.More():
        curve = BRepAdaptor_Curve(TopoDS.Edge(explorer.Current()))
        explorer.Next()

        if curve.GetType() != GeomAbs_Circle:
            continue

        circle = curve.Circle()
        c, d = circle.Location(), circle.Axis().Direction()
        rows.append((c.X(), c.Y(), c.Z(), d.X(), d.Y(), d.Z(), circle.Radius()))

    if not rows:
        return np.zeros((0, 7))

    # Each edge is visited once per adjacent face; seams may split a circle
    # into arcs with the same center and radius.
    rows = np.asarray(rows)
    key = np.round(rows[:, [0, 1, 2, 6]] / 1e-6).astype(np.int64)
    _, keep = np.unique(key, axis=0, return_index=True)
    return rows[np.sort(keep)]


def _holes(shape):
    """
    Cylindrical holes of a solid, (H, 8) rows: start point on the axis (3),
    unit axis (3), radius, length; in mm.
    A hole face is a cylinder whose outward normal (out of the material)
    points TOWARD its axis; a pin or a boss points away. CAD often splits a
    cylinder into two half faces: faces on the same axis line with the same
    radius and axial range are joined, and only a full turn is a hole.
    """
    parts = []          # (point on axis at v0, axis, radius, v-length, angular span)
    explorer = TopExp_Explorer(shape, TopAbs_FACE)
    while explorer.More():
        # Use the face before explorer.Next() (OCP 8: temporaries from
        # Current() may dangle, see CLAUDE.md).
        face = TopoDS.Face(explorer.Current())
        surface = BRepAdaptor_Surface(face, True)
        orientation = face.Orientation()
        explorer.Next()
        if surface.GetType() != GeomAbs_Cylinder:
            continue

        cylinder = surface.Cylinder()
        axis = cylinder.Axis()
        loc, d = axis.Location(), axis.Direction()
        u0, u1 = surface.FirstUParameter(), surface.LastUParameter()
        v0, v1 = surface.FirstVParameter(), surface.LastVParameter()

        point, du, dv = gp_Pnt(), gp_Vec(), gp_Vec()
        surface.D1(0.5 * (u0 + u1), 0.5 * (v0 + v1), point, du, dv)
        normal = du.Crossed(dv)
        if orientation == TopAbs_REVERSED:
            normal.Reverse()
        p = np.array([point.X(), point.Y(), point.Z()])
        o = np.array([loc.X(), loc.Y(), loc.Z()])
        a = np.array([d.X(), d.Y(), d.Z()])
        toward_axis = (o + a * np.dot(p - o, a)) - p
        if np.dot([normal.X(), normal.Y(), normal.Z()], toward_axis) <= 0:
            continue                                    # convex: a pin, not a hole

        lo, hi = sorted((v0, v1))
        parts.append((o + a * lo, a, cylinder.Radius(), hi - lo, abs(u1 - u0)))

    holes = []
    used = [False] * len(parts)
    for i, (start, a, r, length, span) in enumerate(parts):
        if used[i]:
            continue
        used[i] = True
        total = span
        for j in range(i + 1, len(parts)):
            s2, a2, r2, l2, span2 = parts[j]
            if used[j] or abs(r2 - r) > 1e-6 * max(r, 1.0) or abs(abs(np.dot(a, a2)) - 1) > 1e-9:
                continue
            # The same axis line and the same axial range (start points may
            # differ only by the direction convention of the second face).
            ends = (s2, s2 + a2 * l2)
            if (np.linalg.norm(np.cross(s2 - start, a)) < 1e-6 and abs(l2 - length) < 1e-6
                    and min(np.linalg.norm(e - start) for e in ends) < 1e-6):
                used[j] = True
                total += span2
        if total >= 2 * np.pi * (1 - 1e-6):
            holes.append((*start, *a, r, length))

    return np.array(holes, dtype=np.float64).reshape(-1, 8)


def _triangles(shape):
    """
    Per-face triangulations merged; vertices are NOT shared between faces.
    Returns (vertices, triangles, planar): planar marks the triangles of
    planar CAD faces.
    """
    vertex_blocks = []
    triangle_blocks = []
    planar_blocks = []
    offset = 0

    explorer = TopExp_Explorer(shape, TopAbs_FACE)
    while explorer.More():
        face = TopoDS.Face(explorer.Current())
        location = TopLoc_Location()
        tri = BRep_Tool.Triangulation_s(face, location)
        is_plane = BRepAdaptor_Surface(face, True).GetType() == GeomAbs_Plane
        explorer.Next()

        if tri is None or tri.NbTriangles() == 0:
            continue

        trsf = location.Transformation()
        nodes = np.empty((tri.NbNodes(), 3))
        for i in range(tri.NbNodes()):
            p = tri.Node(i + 1).Transformed(trsf)
            nodes[i] = (p.X(), p.Y(), p.Z())

        faces = np.empty((tri.NbTriangles(), 3), dtype=np.int64)
        for i in range(tri.NbTriangles()):
            t = tri.Triangle(i + 1)
            faces[i] = (t.Value(1), t.Value(2), t.Value(3))
        faces -= 1

        if face.Orientation() == TopAbs_REVERSED:
            faces = faces[:, ::-1]

        vertex_blocks.append(nodes)
        triangle_blocks.append(faces + offset)
        planar_blocks.append(np.full(len(faces), is_plane))
        offset += len(nodes)

    if not vertex_blocks:
        return np.zeros((0, 3)), np.zeros((0, 3), dtype=np.int32), np.zeros(0, dtype=bool)

    return (np.concatenate(vertex_blocks), np.concatenate(triangle_blocks).astype(np.int32),
            np.concatenate(planar_blocks))


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


# =====================================================
# Writing (used by tools/make_test_models.py)
# =====================================================

def write_step(path, named_shapes):
    """
    named_shapes: [(name, OCP shape in millimeters, rgb or None)].
    Names and colors are stored, so read_step() gets them back.
    """
    _silence_occt()
    doc = _new_document()
    shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    color_tool = XCAFDoc_DocumentTool.ColorTool_s(doc.Main())

    for name, shape, color in named_shapes:
        label = shape_tool.AddShape(shape, False)
        TDataStd_Name.Set_s(label, TCollection_ExtendedString(name))

        if color is not None:
            color_tool.SetColor(
                label, Quantity_Color(*color, Quantity_TOC_RGB),
                XCAFDoc_ColorType.XCAFDoc_ColorGen,
            )

    writer = STEPCAFControl_Writer()
    writer.SetNameMode(True)
    writer.SetColorMode(True)

    if not writer.Transfer(doc, STEPControl_AsIs) or writer.Write(os.fspath(path)) != IFSelect_RetDone:
        raise IOError(f"cannot write STEP file {path!r}")
