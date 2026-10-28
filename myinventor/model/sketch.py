# sketch.py
#
# The 2D sketch feature: a frame on a plane, later its entities (lines,
# rectangles, circles, with formula coordinates in the frame's u, v).
#
#   [[feature]]
#   name = "Sketch1"
#   type = "sketch"
#   plane = "XY Plane"        # an origin plane now; later a work plane or a face
#
# Its build result is the SketchFrame: where the sketch lies in 3D. The
# frame's u and v are the plane's in-plane axes, so the sketch's x runs along
# u, its y along v, and u x v is the normal (the side the sketch is seen from).

from dataclasses import dataclass

import numpy as np

from model.features import Feature, Field, feature_type


@dataclass
class SketchFrame:
    origin: np.ndarray      # (3,) m
    u: np.ndarray           # (3,) unit: the sketch's x
    v: np.ndarray           # (3,) unit: the sketch's y
    normal: np.ndarray      # (3,) unit: u x v

    def to_world(self, xy):
        """(N, 2) sketch coordinates (m) -> (N, 3) world points (m)."""
        xy = np.asarray(xy, dtype=float).reshape(-1, 2)
        return self.origin + xy[:, :1] * self.u + xy[:, 1:] * self.v


@feature_type
class Sketch(Feature):
    TYPE = "sketch"
    FIELDS = (
        Field("plane", "ref", doc="the plane the sketch lies on"),
    )

    def build(self, ctx):
        name = self["plane"]
        try:
            datum = ctx.part.datum(name)
        except KeyError:
            raise ValueError(f"{name!r} is not a plane") from None
        if datum.kind != "plane":
            raise ValueError(f"{name!r} is a work {datum.kind}, not a plane")
        return SketchFrame(datum.origin.copy(), datum.u.copy(), datum.v.copy(), datum.direction.copy())
