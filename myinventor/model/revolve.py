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
# numpy-only. Its planar faces have names a sketch can sit on
# (RevolveResult.face_frame): "start" / "end" (the profile's plane, and it
# turned by the angle: under 360 degrees only) and "disc.L3" (the flat ring
# a profile line across the axis sweeps).

from dataclasses import dataclass

import numpy as np

from model.extrude import OPERATIONS, apply_to_body, chosen_regions
from model.features import Feature, FeatureError, Field, feature_type
from model.sketch import SketchFrame

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

    # ---- its planar faces, named by meaning (a sketch can sit on one) ----
    #   "start"     the profile's own plane (under 360 degrees)
    #   "end"       that plane turned by the angle (under 360 degrees)
    #   "disc.L3"   the flat ring (or disc) a profile line across the axis sweeps
    # Each comes with its outward normal: out of the material that is left
    # (for a cut: into the pocket), as an extrusion's.

    @property
    def full(self):
        return abs(abs(self.angle) - 360.0) < 1e-9

    def face_roles(self):
        roles = [] if self.full else ["start", "end"]
        for region in self.regions:
            for loop in [region.outer] + region.holes:
                for edge in loop.edges:
                    role = f"disc.{edge[3]}" if edge[0] == "line" and len(edge) > 3 else None
                    if role is not None and role not in roles and self._across(edge):
                        roles.append(role)
        return roles

    def face_frame(self, role):
        """The SketchFrame of a sketch on face `role`: on its plane, its normal outward."""
        f = self.frame
        added = self.operation != "cut"
        if role in ("start", "end"):
            if self.full:
                raise ValueError("a revolution all the way round has no start or end face")
            sweep = self._sweep()                       # the way the start plane moves off
            if role == "start":
                outward = -sweep if added else sweep
                return SketchFrame(f.origin.copy(), np.asarray(f.u, float), np.cross(outward, f.u), outward)
            turn = lambda v: _rotate(v, self.axis[1], self.angle)        # noqa: E731
            point = np.asarray(self.axis[0]) * MM
            origin = point + turn(np.asarray(f.origin) - point)
            u = turn(np.asarray(f.u, float))
            outward = turn(sweep) if added else -turn(sweep)
            return SketchFrame(origin, u, np.cross(outward, u), outward)
        if role.startswith("disc."):
            name = role[5:]
            for region in self.regions:
                for loop in [region.outer] + region.holes:
                    for edge in loop.edges:
                        if edge[0] == "line" and len(edge) > 3 and edge[3] == name and self._across(edge):
                            return self._disc_frame(region, edge[1], edge[2], added)
        raise ValueError(f"no face {role!r}")

    def face_planes(self):
        """{role: (point (3,) m, outward normal (3,))} of its planar faces."""
        planes = {}
        for role in self.face_roles():
            frame = self.face_frame(role)
            planes[role] = (frame.origin, frame.normal)
        return planes

    def _axis2(self):
        """The axis in the sketch: (point (2,) mm, unit direction (2,))."""
        f = self.frame
        point = f.to_sketch(np.asarray(self.axis[0])[None] * MM)[0] / MM
        d = np.array([float(self.axis[1] @ f.u), float(self.axis[1] @ f.v)])
        return point, d / np.linalg.norm(d)

    def _across(self, edge):
        """A profile line square to the axis (it sweeps a flat ring)."""
        along = np.asarray(edge[2], float) - np.asarray(edge[1], float)
        length = float(np.linalg.norm(along))
        return length > 1e-9 and abs(float(along @ self._axis2()[1])) <= 1e-9 * max(1.0, length)

    def _sweep(self):
        """The way the profiles leave the start plane: the axis x (toward the profiles), signed by the angle."""
        point, d = self._axis2()
        middle = np.mean([r.outer.polygon.mean(axis=0) for r in self.regions], axis=0)
        side2 = (middle - point) - float((middle - point) @ d) * d
        side = side2[0] * np.asarray(self.frame.u) + side2[1] * np.asarray(self.frame.v)
        sweep = np.cross(np.asarray(self.axis[1], float), side)
        return np.sign(self.angle) * sweep / np.linalg.norm(sweep)

    def _disc_frame(self, region, a, b, added):
        """The flat ring line a-b sweeps: centred on the axis, its normal along the axis out of the material."""
        f = self.frame
        point, d = self._axis2()
        middle = 0.5 * (np.asarray(a) + np.asarray(b))
        probe = middle + d * 1e-3 * max(1.0, float(np.linalg.norm(np.asarray(b) - np.asarray(a))))
        axis = np.asarray(self.axis[1], float)
        outward = -axis if region.contains(probe) == added else axis     # away from the material (into a pocket)
        center2 = point + float((middle - point) @ d) * d
        origin = f.to_world(center2[None] * MM)[0]
        u = np.asarray(f.u, float) * (b - a)[0] + np.asarray(f.v, float) * (b - a)[1]
        u = u / np.linalg.norm(u)
        return SketchFrame(origin, u, np.cross(outward, u), outward)


def _rotate(v, axis, degrees):
    """v (3,) turned by `degrees` around the unit `axis` (the right hand way: Rodrigues)."""
    k = np.asarray(axis, float) / np.linalg.norm(axis)
    v = np.asarray(v, float)
    c, s = np.cos(np.radians(degrees)), np.sin(np.radians(degrees))
    return v * c + np.cross(k, v) * s + k * float(k @ v) * (1 - c)


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
