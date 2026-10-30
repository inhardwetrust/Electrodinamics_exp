# workplane.py
#
# The Work Plane feature (Inventor's work plane): a plane to sketch on where
# no origin plane or body face is.
#
#   [[feature]]
#   name = "WorkPlane1"
#   type = "workplane"
#   base = "XY Plane"         # an origin plane, a planar face ("Extrusion1:end"), or a work plane above
#   offset = "f"              # Offset: mm along the base's normal (a formula; default 0)
#   axis = "Y Axis"           # Angle: an origin axis or a sketch line ("Sketch1:L3") in the base's plane...
#   angle = "30"              # ... and how far (degrees) the plane turns around it from the base
#   visible = true            # shown in 3D (its browser row's tick)
#
#   points = ["Sketch1:P3", "Center Point", ["10", "0", "h"]]     # Three points instead (no base):
#                             # a sketch point, an origin point, or x, y, z (formulas, mm)
#
# Without an axis it is the base moved by `offset` (its axes kept); with one
# it is the base turned by `angle` around that axis (the right hand way
# around the axis's direction; its axes turned along), `offset` must then be
# 0. With points it goes through the three: its origin the first, its x
# toward the second, its normal the right hand way round 1 -> 2 -> 3 (a
# sketch point followed: the plane moves with it). A sketch on it names it
# as its plane ("WorkPlane1"). numpy only.

from dataclasses import dataclass

import numpy as np

from model.features import Feature, FeatureError, Field, feature_type
from model.revolve import _rotate, axis_line
from model.sketch import SketchFrame, plane_frame

MM = 1e-3


@dataclass
class WorkPlaneResult:
    frame: SketchFrame      # where it lies: origin (m), u, v, normal

    def plane_frame(self):
        """Its SketchFrame (a sketch on it uses it as is)."""
        return self.frame


def turned_frame(frame, point, direction, angle):
    """`frame` turned by `angle` degrees around the axis through `point` (3,) mm along `direction` (3,)."""
    pivot = np.asarray(point, float) * MM
    origin = pivot + _rotate(np.asarray(frame.origin) - pivot, direction, angle)
    u, v = _rotate(frame.u, direction, angle), _rotate(frame.v, direction, angle)
    return SketchFrame(origin, u, v, np.cross(u, v))


@feature_type
class WorkPlane(Feature):
    TYPE = "workplane"
    FIELDS = (
        Field("base", "ref", "", doc="an origin plane, a planar face ('Extrusion1:end') or a work plane"),
        Field("offset", "expr", "0", doc="Offset: mm along the base's normal"),
        Field("axis", "text", "", doc="Angle: an origin axis or a sketch line ('Sketch1:L3') in the base's plane"),
        Field("angle", "expr", "0", doc="Angle: degrees around the axis from the base"),
        Field("visible", "bool", True, doc="shown in 3D"),
    )

    def __init__(self, name, points=None, **values):
        super().__init__(name, **values)
        self.points = None if points is None else [p if isinstance(p, str) else [str(c) for c in p] for p in points]
        if self["axis"].strip() and self["offset"].strip() not in ("", "0"):
            raise FeatureError(f"{name}: an offset or an axis and an angle, not both")
        if self.points is not None:
            if len(self.points) != 3 or not all(
                    (isinstance(p, str) and p.strip()) or (isinstance(p, list) and len(p) == 3) for p in self.points):
                raise FeatureError(f"{name}: points must be three: a point's name, or [x, y, z] (formulas)")
            if self["axis"].strip() or self["base"].strip():
                raise FeatureError(f"{name}: three points, or a base (with an offset or an angle), not both")
        elif not self["base"].strip():
            raise FeatureError(f"{name}: a base plane (or three points)")

    @classmethod
    def from_dict(cls, data):
        data = dict(data)
        name = data.pop("name")
        data.pop("type", None)
        points = data.pop("points", None)
        if points is not None and (not isinstance(points, list) or not all(
                isinstance(p, str) or (isinstance(p, list) and all(isinstance(c, (str, int, float))
                                                                   and not isinstance(c, bool) for c in p))
                for p in points)):
            raise FeatureError(f"{name}: points must be point names or [x, y, z] lists")
        if points is not None:
            points = [p if isinstance(p, str) else [c if isinstance(c, str) else repr(c) for c in p] for p in points]
        return cls(name, points=points, **data)

    def to_dict(self):
        data = super().to_dict()
        if self.points is not None:
            data["points"] = [p if isinstance(p, str) else list(p) for p in self.points]
            for key in ("base", "offset", "axis", "angle"):
                data.pop(key, None)
        return data

    def formulas(self):
        rows = super().formulas()
        for i, p in enumerate(self.points or []):
            if isinstance(p, list):
                rows += [(f"points[{i}].{a}", text) for a, text in zip("xyz", p)]
        return rows

    def rename_formulas(self, rename):
        super().rename_formulas(rename)
        if self.points is not None:
            self.points = [p if isinstance(p, str) else [rename(t) for t in p] for p in self.points]

    def references(self):
        """Its base's datum or feature (a face's feature), the sketch of its axis line, its points' sketches."""
        refs = [self["base"].split(":", 1)[0]] if self["base"].strip() else []
        owner = self["axis"].rpartition(":")[0]
        refs += [owner] if owner else []
        refs += [p.rpartition(":")[0] for p in self.points or [] if isinstance(p, str) and ":" in p]
        return list(dict.fromkeys(refs))

    def rename_reference(self, old, new):
        feature, sep, face = self["base"].partition(":")
        if feature == old:
            self.values["base"] = new + sep + face
        owner, sep, name = self["axis"].rpartition(":")
        if sep and owner == old:
            self.values["axis"] = f"{new}:{name}"
        if self.points is not None:
            self.points = [new + ":" + p.rpartition(":")[2] if isinstance(p, str) and p.rpartition(":")[0] == old
                           else p for p in self.points]

    def _point(self, ctx, p, i):
        """(3,) mm of point i: a sketch point, an origin point, or x, y, z."""
        if isinstance(p, list):
            return np.array([ctx.evaluate(t, f"point {i + 1} {a}") for t, a in zip(p, "xyz")])
        owner, sep, name = p.rpartition(":")
        if sep:
            built = ctx.outputs.get(owner)
            if built is None or not hasattr(built, "points") or name not in built.points:
                raise ValueError(f"point {i + 1}: no point {name!r} in {owner!r}")
            return built.world_mm(name)
        try:
            datum = ctx.part.datum(p)
        except KeyError:
            raise ValueError(f"point {i + 1}: no point {p!r} (a sketch point, Sketch1:P3, or an origin point)") \
                from None
        if datum.kind != "point":
            raise ValueError(f"point {i + 1}: {p!r} is a work {datum.kind}, not a point")
        return np.asarray(datum.origin, float) / MM

    def build(self, ctx):
        if self.points is not None:
            a, b, c = (self._point(ctx, p, i) for i, p in enumerate(self.points))
            u, normal = b - a, np.cross(b - a, c - a)
            if np.linalg.norm(u) < 1e-9 or np.linalg.norm(normal) < 1e-9 * max(1.0, np.linalg.norm(u)) ** 2:
                raise ValueError("the three points lie on one line (or two are one): no plane")
            u, normal = u / np.linalg.norm(u), normal / np.linalg.norm(normal)
            return WorkPlaneResult(SketchFrame(a * MM, u, np.cross(normal, u), normal))
        base = plane_frame(ctx, self["base"])
        if not self["axis"].strip():
            offset = ctx.evaluate(self["offset"], "offset")
            normal = np.asarray(base.normal, float)
            return WorkPlaneResult(SketchFrame(np.asarray(base.origin) + normal * offset * MM,
                                               np.asarray(base.u, float), np.asarray(base.v, float), normal))
        point, direction = axis_line(self["axis"], ctx.part, ctx.outputs)
        normal = np.asarray(base.normal, float)
        if abs(float(direction @ normal)) > 1e-9 \
                or abs(float((np.asarray(point) * MM - np.asarray(base.origin)) @ normal)) > 1e-9:
            raise ValueError(f"the axis {self['axis']} does not lie in {self['base']}")
        angle = ctx.evaluate(self["angle"], "angle")
        return WorkPlaneResult(turned_frame(base, point, direction, angle))
