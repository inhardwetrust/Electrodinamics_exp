# kicad_step.py
#
# KiCad board STEP -> a STEP in our usual form (like the Inventor models):
# one named body per copper layer, the vias as one body, the board, and the
# footprints' 3D models as they are. The result is opened the normal way and
# the types are assigned by hand.
#
# KiCad's export (File > Export > STEP) puts every category of the board in
# its own part, named <board>_PCB, <board>_copper, <board>_pad, <board>_via
# (and _soldermask / _silkscreen when enabled); the footprints' models sit
# under an instance named by the reference (J2) and lose their body names.
# The categories are told apart by these part names, never by shape:
#   - copper: the solids grouped by their z range (a layer each), top down:
#     F.Cu, In1.Cu ... B.Cu;
#   - pads: KiCad exports them 5 um thicker than the copper (40 vs 35 um),
#     which would put a 5 um cell into the mesh; each pad is cut to the z
#     range of the one layer it overlaps and joins that layer. A pad through
#     several layers (a through-hole pad) is kept as its own body;
#   - each layer is fused into one shape (faces merged where pieces touch).
#     Its disjoint pieces (the ground pour, a trace in its clearance, an
#     island) are written as solids named like the layer: read_step makes
#     them one body;
#   - vias: one body "vias" (type it PEC; no hole rule needed). A
#     simplification for now: EVERY via is a through via, a solid copper
#     cylinder of its drill from the bottom of B.Cu to the top of F.Cu.
#     KiCad's blind and buried vias (from an outer layer to an inner one,
#     or between inner ones) and micro vias are made through too; the
#     report counts them;
#   - plated holes: newer exports draw a via as its plating, a tube (outer
#     radius = the drill, inner = the drill minus 2 x 25 um), and a
#     footprint's plated hole (a via in a footprint, a through-hole pad) as
#     such a tube in the pads plus a ring on each layer. Outside, a tube and
#     a solid cylinder of its outer radius are the same conductor, and the
#     solid one has no 25 um wall for the mesh: every round via, and every
#     round pad through several layers (one or two coaxial vertical
#     cylinders, horizontal ends), becomes such a through cylinder in
#     "vias". The rings join their layers like any pad. A plated hole in
#     the pads with no hole of its outer radius in the board on its axis is
#     reported (KiCad drills the board for pads, not for vias);
#   - board: "board" (one dielectric; the inner layers lie inside it);
#   - soldermask and silkscreen are dropped (not modeled);
#   - a footprint model: its solids named "<reference> 1", "<reference> 2"...
#     in KiCad's order: a body added to the model renumbers the others.
# Re-importing over an earlier _prep file keeps its project file, so bodies
# keep their types by name; compare_bodies() lists the names whose shape
# changed (a renumbered model part would get another part's type).
# OCP code, like geometry/step_io.py (the two modules that import OCP).

import os
import re
from dataclasses import dataclass, field

import numpy as np

from OCP.BRep import BRep_Builder
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepAlgoAPI import BRepAlgoAPI_Common, BRepAlgoAPI_Fuse
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
from OCP.Bnd import Bnd_Box
from OCP.GeomAbs import GeomAbs_Cylinder, GeomAbs_Plane
from OCP.IFSelect import IFSelect_RetDone
from OCP.ShapeUpgrade import ShapeUpgrade_UnifySameDomain
from OCP.STEPCAFControl import STEPCAFControl_Reader
from OCP.TopAbs import TopAbs_FACE, TopAbs_SOLID
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS, TopoDS_Compound
from OCP.XCAFDoc import XCAFDoc_ShapeTool
from OCP.XCAFPrs import XCAFPrs_DocumentExplorer, XCAFPrs_DocumentExplorerFlags_None
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

from geometry.step_io import _label_name, _new_document, _silence_occt, read_step, write_step

LAYER_TOLERANCE = 1e-3      # mm: solids whose z ranges match within this are one layer
MAX_LAYER_THICKNESS = 0.2   # mm: thicker "copper" is reported, not treated as a layer
DROPPED = ("_soldermask", "_silkscreen")
CHANGE_TOLERANCE = 1e-6     # m: bounds moved more than this -> the body changed
VOLUME_TOLERANCE = 0.01     # relative
AXIS_TOLERANCE = 1e-4       # mm: cylinders on one axis, equal radii
COPPER_COLOR = (0.85, 0.55, 0.2)
VIA_COLOR = (0.95, 0.78, 0.25)


@dataclass
class KicadReport:
    layers: list = field(default_factory=list)     # (name, z0, z1 mm, pieces)
    lines: list = field(default_factory=list)      # what was done, for the user
    warnings: list = field(default_factory=list)


def prepare_kicad_step(src, dst):
    """Reads KiCad's STEP `src`, writes our form to `dst`. Returns a KicadReport."""
    report = KicadReport()
    leaves = _leaves(src)
    board_names = {_category(name)[0] for name, _, _, _ in leaves if _category(name)[1]}
    if not board_names:
        raise ValueError("no KiCad board parts (<board>_PCB, _copper, _pad, _via) in this file")

    copper, pads, vias, board, models = [], [], [], [], []
    for name, reference, shape, color in leaves:
        _, category = _category(name)
        solids = _solids(shape)
        if category == "copper":
            copper += solids
        elif category == "pad":
            pads += solids
        elif category == "via":
            vias += solids
        elif category == "PCB":
            board.append((solids, color))
        elif category in DROPPED:
            report.lines.append(f"{category[1:]}: dropped ({len(solids)} solids)")
        else:
            models.append((reference or name, solids, color))

    named = []          # (name, [solids], color)
    layers = _layers(copper, report)
    pieces = {n: list(s) for n, _, s in layers}
    extra = []
    for pad in pads:
        z0, z1 = _z_range(pad)
        hit = [(n, a, b) for n, (a, b), _ in layers if min(z1, b) - max(z0, a) > LAYER_TOLERANCE]
        if len(hit) == 1:
            n, a, b = hit[0]
            pieces[n].append(_cut_z(pad, a, b))
        else:
            extra.append(pad)
    if pads:
        report.lines.append(f"pads: {len(pads) - len(extra)} cut to their layer's copper and merged into it")

    # Vias and plated holes -> solid cylinders of the drill through all the copper.
    copper_z = (min(a for _, (a, b), _ in layers), max(b for _, (a, b), _ in layers)) if layers else None
    drills = [d for solids, _ in board for s in solids for d in _cylinders(s, only=False)]
    rods = {"via": [], "pad": []}           # (solid, (x, y, r), drawn as a tube)
    for kind, solids in (("via", vias), ("pad", extra)):
        kept = []
        for solid in solids:
            axis = _round(solid)
            if axis is None:
                kept.append(solid)
                continue
            x, y, r, tube = axis
            rods[kind].append((solid, (x, y, r), tube))
            if kind == "pad" and not any(abs(x - a) < AXIS_TOLERANCE and abs(y - b) < AXIS_TOLERANCE
                                         and abs(r - c) < AXIS_TOLERANCE for a, b, c in drills):
                report.warnings.append(f"plated hole at ({x:.3f}, {y:.3f}) mm, ⌀ {2 * r:.3f}: "
                                       "no hole of that size in the board there")
        solids[:] = kept
    if vias:
        report.warnings.append(f"{len(vias)} vias are not round: kept as they are")
    for kind, what in (("via", "vias"), ("pad", "plated holes in pads")):
        if not rods[kind]:
            continue
        sizes = sorted({round(2 * r, 3) for _, (_, _, r), _ in rods[kind]})
        tubes = sum(tube for _, _, tube in rods[kind])
        partial = 0                         # blind or buried: not over all the copper
        if copper_z is not None:
            for solid, _, _ in rods[kind]:
                z0, z1 = _z_range(solid)
                partial += z0 > copper_z[0] + LAYER_TOLERANCE or z1 < copper_z[1] - LAYER_TOLERANCE
        report.lines.append(
            f"{what}: {len(rods[kind])} -> solid cylinders through all the copper "
            f"(⌀ {', '.join(f'{d:g}' for d in sizes)} mm), body 'vias'"
            + (f"; {tubes} drawn as plating tubes, filled" if tubes else "")
            + (f"; {partial} blind or buried, made through" if partial else ""))
        vias += [_rod(solid, xyr, copper_z) for solid, xyr, _ in rods[kind]]
    if extra:
        report.warnings.append(f"{len(extra)} pads span no or several layers: kept as body 'pads'")

    for n, (a, b), _ in layers:
        fused = _fuse(pieces[n])
        report.layers.append((n, a, b, len(fused)))
        report.lines.append(f"{n}: z {a:.4g}..{b:.4g} mm, {len(pieces[n])} solids -> {len(fused)} pieces")
        named.append((n, fused, COPPER_COLOR))
    if extra:
        named.append(("pads", extra, COPPER_COLOR))
    if vias:
        named.append(("vias", vias, VIA_COLOR))
        report.lines.append(f"vias: {len(vias)} cylinders -> body 'vias'")
    for solids, color in board:
        named.append(("board", solids, color))
    report.lines.append("board: " + ("body 'board'" if board else "none found"))

    counts = {}
    for reference, solids, _ in models:
        counts[reference] = counts.get(reference, 0) + len(solids)
    seen = {}
    for reference, solids, color in models:
        for solid in solids:
            seen[reference] = seen.get(reference, 0) + 1
            name = reference if counts[reference] == 1 else f"{reference} {seen[reference]}"
            named.append((name, [solid], color))
    for reference, n in counts.items():
        report.lines.append(f"{reference}: {n} bodies (3D model, as is)")

    _write(dst, named)
    return report


def compare_bodies(old, new):
    """(new names, gone names, changed names) between two Geometries of the same file."""
    before = {b.name: b for b in old.bodies}
    after = {b.name: b for b in new.bodies}
    changed = []
    for name in after.keys() & before.keys():
        a, b = before[name], after[name]
        moved = np.max(np.abs(np.asarray(a.bounds) - np.asarray(b.bounds))) > CHANGE_TOLERANCE
        resized = abs(a.volume - b.volume) > VOLUME_TOLERANCE * max(abs(a.volume), 1e-30)
        if moved or resized:
            changed.append(name)
    order = [b.name for b in new.bodies]
    return ([n for n in order if n not in before], sorted(before.keys() - after.keys()),
            sorted(changed, key=order.index))


# =====================================================
# Reading
# =====================================================

def _leaves(path):
    """[(part name, reference of the enclosing instance, located shape mm, color)]."""
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

    leaves = []
    stack = []          # instance names by depth
    explorer = XCAFPrs_DocumentExplorer(doc, XCAFPrs_DocumentExplorerFlags_None)
    while explorer.More():
        node = explorer.Current()
        depth = explorer.CurrentDepth()
        del stack[depth:]
        stack.append(_label_name(node.Label))
        if not node.IsAssembly:
            color = None
            if node.Style.IsSetColorSurf():
                c = node.Style.GetColorSurf()
                color = (c.Red(), c.Green(), c.Blue())
            # The nearest named instance above the part: a footprint's reference.
            reference = next((n for n in reversed(stack[1:-1]) if n and not n.startswith("=>")), "")
            shape = XCAFDoc_ShapeTool.GetShape_s(node.RefLabel).Moved(node.Location)
            leaves.append((_label_name(node.RefLabel), reference, shape, color))
        explorer.Next()
    return leaves


def _category(part_name):
    """(board name, 'copper' / 'pad' / 'via' / 'PCB' / '_soldermask' / '_silkscreen' or None)."""
    for suffix, category in (("_copper", "copper"), ("_pad", "pad"), ("_via", "via"), ("_PCB", "PCB"),
                             ("_soldermask", "_soldermask"), ("_silkscreen", "_silkscreen")):
        if part_name.endswith(suffix) and len(part_name) > len(suffix):
            return part_name[:-len(suffix)], category
    return None, None


def _solids(shape):
    solids = []
    explorer = TopExp_Explorer(shape, TopAbs_SOLID)
    while explorer.More():
        solids.append(explorer.Current())
        explorer.Next()
    return solids


def _bounds(shape):
    box = Bnd_Box()
    BRepBndLib.AddOptimal_s(shape, box, False, False)
    lo, hi = box.CornerMin(), box.CornerMax()
    return np.array([lo.X(), lo.Y(), lo.Z()]), np.array([hi.X(), hi.Y(), hi.Z()])


def _z_range(shape):
    lo, hi = _bounds(shape)
    return float(lo[2]), float(hi[2])


# =====================================================
# Layers, pads, fusing
# =====================================================

def _layers(copper, report):
    """[(name, (z0, z1), solids)] top down; thick 'copper' is reported and skipped."""
    groups = []
    for solid in copper:
        z0, z1 = _z_range(solid)
        if z1 - z0 > MAX_LAYER_THICKNESS:
            report.warnings.append(f"a copper solid {z1 - z0:.3g} mm thick: not a layer, skipped")
            continue
        for g in groups:
            if abs(g[0] - z0) < LAYER_TOLERANCE and abs(g[1] - z1) < LAYER_TOLERANCE:
                g[2].append(solid)
                break
        else:
            groups.append([z0, z1, [solid]])
    groups.sort(key=lambda g: -(g[0] + g[1]))
    n = len(groups)
    names = (["F.Cu"] + [f"In{k}.Cu" for k in range(1, n - 1)] + ["B.Cu"]) if n > 1 else ["F.Cu"] * n
    return [(name, (g[0], g[1]), g[2]) for name, g in zip(names, groups)]


def _cylinders(shape, only=True):
    """
    [(x, y, r) mm] of the vertical cylindrical faces of a shape. only: its
    other faces must all be horizontal planes, else None.
    """
    found = []
    explorer = TopExp_Explorer(shape, TopAbs_FACE)
    while explorer.More():
        surface = BRepAdaptor_Surface(TopoDS.Face(explorer.Current()), True)
        kind = surface.GetType()
        if kind == GeomAbs_Cylinder:
            cylinder = surface.Cylinder()
            if abs(abs(cylinder.Axis().Direction().Z()) - 1.0) < 1e-9:
                p = cylinder.Location()
                found.append((p.X(), p.Y(), cylinder.Radius()))
            elif only:
                return None
        elif only and (kind != GeomAbs_Plane or abs(abs(surface.Plane().Axis().Direction().Z()) - 1.0) > 1e-9):
            return None
        explorer.Next()
    return found


def _round(solid):
    """
    (x, y, outer radius mm, tube) if the solid is round about a vertical
    axis: one cylinder (a solid via) or two coaxial ones (a plating tube),
    horizontal ends; None otherwise.
    """
    found = _cylinders(solid)
    if not found:
        return None
    xyr = np.array(found)
    if np.ptp(xyr[:, :2], axis=0).max() > AXIS_TOLERANCE:
        return None
    radii = np.sort(xyr[:, 2])
    steps = np.count_nonzero(np.diff(radii) > AXIS_TOLERANCE)       # 0: one radius, 1: two
    if steps > 1:
        return None
    return float(xyr[0, 0]), float(xyr[0, 1]), float(radii[-1]), steps == 1


def _rod(solid, xyr, copper_z):
    """
    A solid cylinder of radius r on the axis (x, y) through all the copper,
    from the bottom of B.Cu to the top of F.Cu: every via is a through via
    here. Without copper layers: over the solid's own z range.
    """
    x, y, r = xyr
    z0, z1 = copper_z if copper_z is not None else _z_range(solid)
    return BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(x, y, z0), gp_Dir(0, 0, 1)), r, z1 - z0).Shape()


def _cut_z(shape, z0, z1):
    """The part of `shape` between z0 and z1."""
    lo, hi = _bounds(shape)
    box = BRepPrimAPI_MakeBox(gp_Pnt(lo[0] - 1, lo[1] - 1, z0), gp_Pnt(hi[0] + 1, hi[1] + 1, z1)).Shape()
    return BRepAlgoAPI_Common(shape, box).Shape()


def _fuse(shapes):
    """One fused shape of all pieces, its touching faces merged; returned as its solids."""
    if not shapes:
        return []
    result = shapes[0]
    for shape in shapes[1:]:
        result = BRepAlgoAPI_Fuse(result, shape).Shape()
    unify = ShapeUpgrade_UnifySameDomain(result, True, True, False)
    unify.Build()
    return _solids(unify.Shape())


# =====================================================
# Writing: solids of a multi-piece body named like the body
# =====================================================

def _write(dst, named):
    """
    named: [(name, [solids], color)]. A body of several solids is one part
    whose solids carry its name (read_step merges them back into one body).
    OCC's writer leaves solid names empty, so they are filled into the
    file's MANIFOLD_SOLID_BREP entities in writing order, then checked by
    reading the file back.
    """
    shapes = []
    for name, solids, color in named:
        # Always a compound: a located solid of KiCad's assembly written on
        # its own becomes an instance of an unnamed part ('SOLID').
        compound = TopoDS_Compound()
        builder = BRep_Builder()
        builder.MakeCompound(compound)
        for solid in solids:
            builder.Add(compound, solid)
        shapes.append((name, compound, color))
    write_step(dst, shapes)

    names = [name for name, solids, _ in named for _ in solids]
    with open(dst, encoding="utf-8", errors="replace") as f:
        text = f.read()
    pattern = re.compile(r"MANIFOLD_SOLID_BREP\('[^']*'")
    if len(pattern.findall(text)) != len(names):
        raise IOError("unexpected STEP output: solid count differs")
    it = iter(names)
    text = pattern.sub(lambda m: "MANIFOLD_SOLID_BREP('" + next(it).replace("'", "") + "'", text)
    with open(dst, "w", encoding="utf-8") as f:
        f.write(text)

    got = sorted(b.name for b in read_step(dst).bodies)
    want = sorted(name for name, _, _ in named)
    if got != want:
        raise IOError(f"written STEP reads back differently: {got} vs {want}")
