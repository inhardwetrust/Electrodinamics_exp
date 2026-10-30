# probe.py
#
# Measuring bodies: what tools/probe.py prints for Claude (and, later, the
# window's section tool for the user). OCP, mm throughout.
#   load(path)             the bodies of a part file (rebuilt, placed or
#                          not) or of a STEP file: [(name, shape)]
#   body_info(shape)       volume, solids, bounds (each solid's too)
#   section(shape, axis, at)   the cut by the plane axis = at: its pieces
#                          (faces), each with its exact area and bounds
#   gap(a, b)              the least distance between two bodies (0: they
#                          touch or overlap)
#   overlap(a, b)          the volume they share (0 if they only touch)

import fnmatch
import os

import numpy as np
from OCP.BRepAlgoAPI import BRepAlgoAPI_Common
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
from OCP.BRepExtrema import BRepExtrema_DistShapeShape
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps
from OCP.TopAbs import TopAbs_FACE, TopAbs_SOLID
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS
from OCP.gp import gp_Dir, gp_Pln, gp_Pnt

from geometry import kernel

AXES = {"x": 0, "y": 1, "z": 2}


def load(path, placed=True):
    """[(name, shape)]: a part file's bodies (rebuilt; placed=False: as the features built them) or a STEP file's."""
    path = os.fspath(path)
    if path.lower().endswith((".step", ".stp")):
        from geometry.step_io import read_bodies

        return [(row[0], row[2]) for row in read_bodies(path)]
    from model.part_file import load as load_part
    from model.rebuild import rebuild

    result = rebuild(load_part(path), folder=os.path.dirname(os.path.abspath(path)), tessellate=False,
                     placement=placed)
    failed = dict(result.errors, **result.feature_errors)
    if failed:
        name, error = next(iter(failed.items()))
        raise ValueError(f"the part does not build: {name}: {error}")
    return list(result.shapes.items())


def pick(bodies, patterns):
    """The bodies whose names match any of the patterns (fnmatch: "SMA:*"); all for none. KeyError: no match."""
    if not patterns:
        return bodies
    out = [(n, s) for n, s in bodies if any(fnmatch.fnmatchcase(n, p) for p in patterns)]
    missing = [p for p in patterns if not any(fnmatch.fnmatchcase(n, p) for n, _ in bodies)]
    if missing:
        raise KeyError(f"no body matches {missing}; bodies: {[n for n, _ in bodies]}")
    return out


def _solids(shape):
    out, explorer = [], TopExp_Explorer(shape, TopAbs_SOLID)
    while explorer.More():
        out.append(TopoDS.Solid(explorer.Current()))
        explorer.Next()
    return out


def body_info(shape):
    """{volume, solids, bounds, size, parts: [{volume, bounds}] (each solid, when more than one)}."""
    lo, hi = kernel.bounds(shape)
    info = {"volume": kernel.volume(shape), "solids": kernel.solid_count(shape),
            "bounds": [lo.tolist(), hi.tolist()], "size": (hi - lo).tolist()}
    solids = _solids(shape)
    if len(solids) > 1:
        info["parts"] = [{"volume": kernel.volume(s), "bounds": [b.tolist() for b in kernel.bounds(s)]}
                         for s in solids]
    return info


def section(shape, axis, at):
    """
    The cut of a body by the plane axis = at ("x" | "y" | "z", mm): its
    pieces [{area mm², bounds [[lo], [hi]]}], largest first; [] if the
    plane misses it. Exact (a boolean with the plane's face, areas
    integrated on the faces).
    """
    k = AXES[axis]
    lo, hi = kernel.bounds(shape)
    if not lo[k] - 1e-9 <= at <= hi[k] + 1e-9:
        return []
    half = float(np.max(hi - lo)) + 1.0
    center = 0.5 * (lo + hi)
    center[k] = at
    normal = [0.0, 0.0, 0.0]
    normal[k] = 1.0
    plane = gp_Pln(gp_Pnt(*map(float, center)), gp_Dir(*normal))
    face = BRepBuilderAPI_MakeFace(plane, -half, half, -half, half).Face()
    cut = BRepAlgoAPI_Common(shape, face).Shape()
    pieces, explorer = [], TopExp_Explorer(cut, TopAbs_FACE)
    while explorer.More():
        f = TopoDS.Face(explorer.Current())
        props = GProp_GProps()
        BRepGProp.SurfaceProperties_s(f, props)
        if props.Mass() > 1e-12:
            pieces.append({"area": props.Mass(), "bounds": [b.tolist() for b in kernel.bounds(f)]})
        explorer.Next()
    pieces.sort(key=lambda p: -p["area"])
    return pieces


def gap(a, b):
    """The least distance between two shapes (mm); 0 when they touch or overlap."""
    dist = BRepExtrema_DistShapeShape(a, b)
    dist.Perform()
    if not dist.IsDone():
        raise ValueError("the distance could not be computed")
    return dist.Value()


def overlap(a, b):
    """The volume two bodies share (mm³)."""
    return kernel.volume(BRepAlgoAPI_Common(a, b).Shape())
