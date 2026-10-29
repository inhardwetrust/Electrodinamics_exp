# extrude.py
#
# The Extrude feature: closed regions of one sketch (profiles,
# model/profiles.py) swept along the sketch's normal into a new body.
#
#   [[feature]]
#   name = "Extrusion1"
#   type = "extrude"
#   sketch = "Sketch1"
#   profiles = [["L1", "L2", "L3", "L4"], ["C1"]]   # each: a region's outer loop
#   distance = "h"            # a formula (mm); negative: the other way
#   body = "Solid1"           # the new body's name (STEP, customcst materials)
#
# A profile is named by the curves of its outer loop (Region.key), so
# moving points and changing sizes keep it; a broken loop fails the feature
# naming the profile. The geometry comes from ctx.kernel (geometry/kernel.py:
# the only OCP code); this module stays numpy-only.

from dataclasses import dataclass

from model.expressions import ExpressionError, evaluate
from model.features import Feature, FeatureError, Field, feature_type
from model.profiles import find_regions


@dataclass
class ExtrudeResult:
    shape: object           # OCP shape (mm)
    body: object            # geometry.body.Body
    regions: list           # the profiles used (model.profiles.Region)


@feature_type
class Extrude(Feature):
    TYPE = "extrude"
    FIELDS = (
        Field("sketch", "ref", doc="the sketch the profiles are in"),
        Field("distance", "expr", doc="how far (mm) along the sketch's normal; negative: the other way"),
        Field("body", "text", doc="the new body's name"),
    )

    def __init__(self, name, profiles=(), **values):
        super().__init__(name, **values)
        self.profiles = [tuple(sorted(p)) for p in profiles]
        if not self.profiles:
            raise FeatureError(f"{name}: no profile")
        if not self.values["body"].strip():
            raise FeatureError(f"{name}: the body needs a name")

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

    def build(self, ctx):
        sketch = ctx.outputs.get(self["sketch"])
        if sketch is None or not hasattr(sketch, "frame"):
            raise ValueError(f"{self['sketch']!r} is not a sketch")
        regions = {r.key: r for r in find_regions(sketch)}
        missing = [p for p in self.profiles if p not in regions]
        if missing:
            raise ValueError(f"profile {' '.join(missing[0])} is not a closed region of {self['sketch']} any more")
        try:
            distance = evaluate(self["distance"], ctx.values)
        except ExpressionError as error:
            raise ValueError(f"distance: {error}") from None
        used = [regions[p] for p in self.profiles]
        shape = ctx.kernel.extrude(sketch.frame, used, distance)
        return ExtrudeResult(shape, ctx.kernel.body(self["body"], shape), used)
