# revolve.py
#
# The Revolve feature: closed regions of one sketch (profiles,
# model/profiles.py) turned around an axis, making a new body or changing
# one (as Extrude: New solid / Join / Cut / Intersect).
#
#   [[feature]]
#   name = "Revolution1"
#   type = "revolve"
#   sketch = "Sketch1"
#   profiles = [["Parabola1.offset"]]
#   axis = "L3"               # a line of that sketch (a construction one, usually), "Sketch2:L3" (a line of
#                             # another sketch above), or "X Axis" / "Y Axis" / "Z Axis" (an origin axis)
#   angle = "360"             # a formula (degrees), 0 < |angle| <= 360; negative: the other way
#   operation = "new"         # "new" | "join" | "cut" | "intersect"
#   body = "Reflector"
#
# The axis lies in the sketch's plane and no profile reaches across it (one
# may touch it: a reflector's profile starts on its axis). The sweep comes
# from ctx.kernel.revolve (geometry/kernel.py); this module stays
# numpy-only. Its faces are not named yet (no sketch on them).

from dataclasses import dataclass

import numpy as np

from model.extrude import OPERATIONS, apply_to_body, chosen_regions
from model.features import Feature, FeatureError, Field, feature_type

MM = 1e-3
ORIGIN_AXES = ("X Axis", "Y Axis", "Z Axis")


@dataclass
class RevolveResult:
    shape: object           # the turned profiles alone (OCP, mm): the tool of a join / cut
    body: str
    regions: list           # the profiles used (model.profiles.Region)
    frame: object = None    # its sketch's SketchFrame
    angle: float = 360.0    # degrees, signed
    operation: str = "new"
    axis: tuple = None      # (point (3,) mm, direction (3,)) in the world


def axis_of(sketch_result, axis, part, outputs):
    """
    (point (3,) mm, unit direction (3,)) of `axis` in the world: a line of
    the sketch, "Sketch2:L3" (a line of another sketch: outputs, the
    built features), or an origin axis; ValueError if it is none of them or
    does not lie in the sketch's plane.
    """
    frame = sketch_result.frame
    owner, _, name = axis.rpartition(":")
    lines_of = outputs.get(owner) if owner else sketch_result
    line = next((e for e in getattr(lines_of, "lines", []) if e.name == name), None)
    if line is not None:
        a, b = lines_of.points[line.start], lines_of.points[line.end]
        if np.linalg.norm(b - a) < 1e-9:
            raise ValueError(f"the axis {axis} has no length")
        ends = lines_of.frame.to_world(np.array([a, b]) * MM) / MM
        point, direction = ends[0], (ends[1] - ends[0]) / np.linalg.norm(ends[1] - ends[0])
    elif owner:
        raise ValueError(f"no line {name!r} in {owner!r} for the axis")
    else:
        try:
            datum = part.datum(axis)
        except KeyError:
            raise ValueError(f"no axis {axis!r}: a line of a sketch or an origin axis") from None
        if datum.kind != "axis":
            raise ValueError(f"{axis!r} is a work {datum.kind}, not an axis")
        point = np.asarray(datum.origin, float) / MM
        direction = np.asarray(datum.direction, float)
    normal = np.asarray(frame.normal, float)
    origin = np.asarray(frame.origin, float) / MM
    if abs(float(direction @ normal)) > 1e-9 or abs(float((point - origin) @ normal)) > 1e-6:
        raise ValueError(f"{axis} does not lie in the sketch's plane")
    return point, direction / np.linalg.norm(direction)


def check_side(sketch_result, regions, axis):
    """ValueError if a profile reaches across the axis (it may touch it)."""
    frame = sketch_result.frame
    point, direction = axis
    p2 = frame.to_sketch(np.asarray(point)[None] * MM)[0] / MM
    d2 = np.array([float(direction @ frame.u), float(direction @ frame.v)])
    for region in regions:
        rel = region.outer.polygon - p2
        side = rel[:, 0] * d2[1] - rel[:, 1] * d2[0]
        if side.max() > 1e-6 and side.min() < -1e-6:
            raise ValueError(f"the axis crosses profile {' '.join(region.key)}: the profile must lie on one "
                             "side of it")


def onto_axis(sketch_result, regions, axis, tolerance=1e-6):
    """
    The regions with what lies within `tolerance` (mm) of the axis put on
    it exactly (a line's or an arc's ends, a B-spline's poles): a profile
    touching its axis (a reflector's) whose fitted side ends a hair across
    it would make OCC's revolution fail.
    """
    from model.profiles import Loop, Region

    frame = sketch_result.frame
    point, direction = axis
    p2 = frame.to_sketch(np.asarray(point)[None] * MM)[0] / MM
    d2 = np.array([float(direction @ frame.u), float(direction @ frame.v)])
    d2 = d2 / np.linalg.norm(d2)

    def snap(xy):
        xy = np.array(xy, float)
        foot = p2 + float((xy - p2) @ d2) * d2
        return foot if np.linalg.norm(xy - foot) <= tolerance else xy

    def edge(e):
        if e[0] == "line":
            return ("line", snap(e[1]), snap(e[2]), *e[3:])
        if e[0] == "arc":
            return ("arc", snap(e[1]), e[2], snap(e[3]))
        if e[0] == "bspline":
            return ("bspline", np.array([snap(q) for q in e[1]]), *e[2:])
        return e

    def loop(lp):
        return Loop(lp.names, lp.polygon, [edge(e) for e in lp.edges])

    return [Region(loop(r.outer), [loop(h) for h in r.holes]) for r in regions]


def angle_error(angle):
    """Why an angle (degrees) does not do, or None."""
    if not 0 < abs(angle) <= 360 + 1e-9:
        return f"the angle must be within 0 and 360 degrees (or -360), it is {angle:g}"
    return None


@feature_type
class Revolve(Feature):
    TYPE = "revolve"
    FIELDS = (
        Field("sketch", "ref", doc="the sketch the profiles are in"),
        Field("axis", "text", doc="a line of the sketch, or an origin axis (X Axis...) in its plane"),
        Field("angle", "expr", "360", doc="how far (degrees) around the axis; negative: the other way"),
        Field("operation", "choice", "new", OPERATIONS, doc="a new body, or join / cut / intersect a body"),
        Field("body", "text", doc="new: the new body's name; else the body it changes"),
    )

    def __init__(self, name, profiles=(), **values):
        super().__init__(name, **values)
        self.profiles = [tuple(sorted(p)) for p in profiles]
        if not self.profiles:
            raise FeatureError(f"{name}: no profile")
        if not self.values["body"].strip():
            raise FeatureError(f"{name}: the body needs a name")
        if not self.values["axis"].strip():
            raise FeatureError(f"{name}: no axis")

    @classmethod
    def from_dict(cls, data):
        data = dict(data)
        name = data.pop("name")
        data.pop("type", None)
        profiles = data.pop("profiles", None)
        if not isinstance(profiles, list) or not all(
                isinstance(p, list) and p and all(isinstance(n, str) for n in p) for p in profiles):
            raise FeatureError(f"{name}: profiles must be a list of lists of curve names")
        return cls(name, profiles=profiles, **data)

    def to_dict(self):
        data = super().to_dict()
        data["profiles"] = [list(p) for p in self.profiles]
        return data

    def references(self):
        """Its sketch, and the sketch of its axis line if that is another one."""
        owner = self["axis"].rpartition(":")[0]
        return super().references() + ([owner] if owner and owner != self["sketch"] else [])

    def rename_reference(self, old, new):
        super().rename_reference(old, new)
        owner, sep, name = self["axis"].rpartition(":")
        if sep and owner == old:
            self.values["axis"] = f"{new}:{name}"

    def build(self, ctx):
        sketch, used = chosen_regions(ctx, self["sketch"], self.profiles)
        axis = axis_of(sketch, self["axis"], ctx.part, ctx.outputs)
        check_side(sketch, used, axis)
        angle = ctx.evaluate(self["angle"], "angle")
        error = angle_error(angle)
        if error:
            raise ValueError(error)
        tool = ctx.kernel.revolve(sketch.frame, onto_axis(sketch, used, axis), axis[0], axis[1], angle)
        apply_to_body(ctx, self["operation"], self["body"], tool)
        return RevolveResult(tool, self["body"], used, sketch.frame, angle, self["operation"], axis)
