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
# Without an axis it is the base moved by `offset` (its axes kept); with one
# it is the base turned by `angle` around that axis (the right hand way
# around the axis's direction; its axes turned along), `offset` must then be
# 0. A sketch on it names it as its plane ("WorkPlane1"). numpy only.

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
        Field("base", "ref", doc="an origin plane, a planar face ('Extrusion1:end') or a work plane"),
        Field("offset", "expr", "0", doc="Offset: mm along the base's normal"),
        Field("axis", "text", "", doc="Angle: an origin axis or a sketch line ('Sketch1:L3') in the base's plane"),
        Field("angle", "expr", "0", doc="Angle: degrees around the axis from the base"),
        Field("visible", "bool", True, doc="shown in 3D"),
    )

    def __init__(self, name, **values):
        super().__init__(name, **values)
        if self["axis"].strip() and self["offset"].strip() not in ("", "0"):
            raise FeatureError(f"{name}: an offset or an axis and an angle, not both")

    def references(self):
        """Its base's datum or feature (a face's feature), and the sketch of its axis line."""
        refs = [self["base"].split(":", 1)[0]]
        owner = self["axis"].rpartition(":")[0]
        return refs + ([owner] if owner else [])

    def rename_reference(self, old, new):
        feature, sep, face = self["base"].partition(":")
        if feature == old:
            self.values["base"] = new + sep + face
        owner, sep, name = self["axis"].rpartition(":")
        if sep and owner == old:
            self.values["axis"] = f"{new}:{name}"

    def build(self, ctx):
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
