# extrude.py
#
# The Extrude feature: closed regions of one sketch (profiles,
# model/profiles.py) swept along the sketch's normal, making a new body or
# changing one (Inventor's New solid / Join / Cut / Intersect).
#
#   [[feature]]
#   name = "Extrusion1"
#   type = "extrude"
#   sketch = "Sketch1"
#   profiles = [["L1", "L2", "L3", "L4"], ["C1"]]   # each: a region's outer loop
#   distance = "h"            # a formula (mm); negative: the other way
#   operation = "new"         # "new" | "join" | "cut" | "intersect"
#   body = "Solid1"           # new: the new body's name; else the body it changes
#   body = ["Top", "Bottom"]  # cut / intersect: several bodies, each cut (intersected) by the same tool
#   extent = "plane"          # optional: "distance" (the default) | "plane" | "body"
#   to = "WorkPlane1"         # plane: up to a plane (an origin plane, a work plane, "Feature:face"), on
#                             #   whichever side; parallel: exact (its "end" face a sketch can sit on),
#                             #   inclined: cut by it; body: until the profiles meet that body (on its
#                             #   side; a part missing it fails). distance is then unused.
#
# A profile is named by the curves of its outer loop (Region.key), so
# moving points and changing sizes keep it; a broken loop fails the feature
# naming the profile. Bodies live in ctx.shapes by name while the history
# rebuilds (model/rebuild.py): "new" adds one (its name must be free), the
# others change the named ones (they must exist above; join: one). A failed
# feature leaves the bodies as they were (all of them). Its planar faces have names a sketch can sit on
# (ExtrudeResult.face_frame: "end", "start", "side.L3"). The geometry comes from ctx.kernel (geometry/kernel.py:
# the only OCP code); this module stays numpy-only.

from dataclasses import dataclass

import numpy as np

from model.features import Feature, FeatureError, Field, body_list, feature_type
from model.profiles import named_regions
from model.sketch import SketchFrame

MM = 1e-3

OPERATIONS = ("new", "join", "cut", "intersect")
EXTENTS = ("distance", "plane", "body")


@dataclass
class ExtrudeResult:
    shape: object           # the swept profiles alone (OCP, mm): the tool of a join / cut
    body: str               # the body it made or changed (the first, if several)
    regions: list           # the profiles used (model.profiles.Region)
    frame: object = None    # its sketch's SketchFrame
    distance: float = 0.0   # mm, signed
    operation: str = "new"
    bodies: list = None     # all the bodies it made or changed
    flat_end: bool = True   # its far end is a cap parallel to the sketch (up to a body or an inclined plane: not)

    # ---- its planar faces, named by meaning (a sketch can sit on one) ----
    #   "end"        the cap at the distance (only with a flat end)
    #   "start"      the cap on the sketch's plane (not for a cut: that is the opening)
    #   "side.L3"    the wall a profile line L3 swept
    # Each comes with its outward normal: out of the material that is left
    # (for a cut: into the pocket).

    def face_roles(self):
        roles = (["end"] if self.flat_end else []) + ([] if self.operation == "cut" else ["start"])
        for region in self.regions:
            for loop in [region.outer] + region.holes:
                roles += [f"side.{edge[3]}" for edge in loop.edges if edge[0] == "line" and len(edge) > 3]
        return roles

    def face_frame(self, role):
        """The SketchFrame of a sketch on face `role`: on its plane, its normal outward."""
        f = self.frame
        sweep = np.asarray(f.normal) * np.sign(self.distance)          # the way the profiles went
        added = self.operation != "cut"
        if role in ("end", "start"):
            if role == "end" and not self.flat_end:
                raise ValueError("it ends on a body or an inclined plane: no flat end of its own (sketch on "
                                 "that plane, or that body's face)")
            if role == "start" and not added:
                raise ValueError("a cut has no start face (that is its opening)")
            origin = f.origin + (np.asarray(f.normal) * self.distance * MM if role == "end" else 0.0)
            outward = sweep if (role == "end") == added else -sweep
            u = np.asarray(f.u, float)
            return SketchFrame(origin, u, np.cross(outward, u), outward)
        if role.startswith("side."):
            name = role[5:]
            for region in self.regions:
                for loop in [region.outer] + region.holes:
                    for edge in loop.edges:
                        if edge[0] == "line" and len(edge) > 3 and edge[3] == name:
                            return self._side_frame(region, edge[1], edge[2], added)
        raise ValueError(f"no face {role!r}")

    def _side_frame(self, region, a, b, added):
        f = self.frame
        along = (b - a) / np.linalg.norm(b - a)                         # in the sketch, mm
        out2 = np.array([along[1], -along[0]])
        probe = 0.5 * (a + b) + out2 * 1e-3 * max(1.0, float(np.linalg.norm(b - a)))
        if region.contains(probe) == added:     # out2 points into the material: turn it
            out2 = -out2
        outward = out2[0] * np.asarray(f.u) + out2[1] * np.asarray(f.v)
        u = along[0] * np.asarray(f.u) + along[1] * np.asarray(f.v)
        origin = f.to_world(np.asarray(a)[None] * MM)[0]
        return SketchFrame(origin, u, np.cross(outward, u), outward)

    def face_planes(self):
        """{role: (point (3,) m, outward normal (3,))} of its planar faces."""
        planes = {}
        for role in self.face_roles():
            frame = self.face_frame(role)
            planes[role] = (frame.origin, frame.normal)
        return planes


@feature_type
class Extrude(Feature):
    TYPE = "extrude"
    FIELDS = (
        Field("sketch", "ref", doc="the sketch the profiles are in"),
        Field("distance", "expr", "", doc="how far (mm) along the sketch's normal; negative: the other way "
                                          "(required by extent distance, else unused)"),
        Field("operation", "choice", "new", OPERATIONS, doc="a new body, or join / cut / intersect a body"),
        Field("body", "bodies", doc="new: the new body's name; else the body it changes (cut / intersect: "
                                    "a list of several)"),
        Field("extent", "choice", "distance", EXTENTS, doc="how far: by the distance, up to a plane, up to a body"),
        Field("to", "text", "", doc="plane: a plane ('WorkPlane1', 'XY Plane', 'Feature:face'); body: a body"),
    )

    def __init__(self, name, profiles=(), **values):
        super().__init__(name, **values)
        self.profiles = [tuple(sorted(p)) for p in profiles]
        if not self.profiles:
            raise FeatureError(f"{name}: no profile")
        if not all(b.strip() for b in body_list(self.values["body"])):
            raise FeatureError(f"{name}: the body needs a name")
        if self["extent"] != "distance" and not self["to"].strip():
            raise FeatureError(f"{name}: up to a {self['extent']} needs `to` (its name)")
        if self["extent"] == "distance" and not self["distance"].strip():
            raise FeatureError(f"{name}: 'distance' is required")

    def references(self):
        """Its sketch, and the plane it goes up to (its feature, for a face "Feature:face")."""
        refs = super().references()
        return refs + [self["to"].partition(":")[0]] if self["extent"] == "plane" else refs

    def rename_reference(self, old, new):
        super().rename_reference(old, new)
        feature, sep, face = self["to"].partition(":")
        if self["extent"] == "plane" and feature == old:
            self.values["to"] = new + sep + face

    def rename_body(self, old, new):
        """The body it goes up to (Part.rename_body renames its own `body`)."""
        if self["extent"] == "body" and self["to"] == old:
            self.values["to"] = new

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
        if self["extent"] == "distance":        # the default: not written (nor its unused `to`)
            del data["extent"], data["to"]
        elif not self["distance"].strip():      # unused up to a plane / body: written only if kept
            del data["distance"]
        return data

    def build(self, ctx):
        sketch, used = chosen_regions(ctx, self["sketch"], self.profiles)
        tool, distance, flat = sweep(ctx, sketch.frame, used, self["extent"], self["distance"], self["to"],
                                     self["operation"], self["body"])
        bodies = apply_to_body(ctx, self["operation"], self["body"], tool)
        return ExtrudeResult(tool, bodies[0], used, sketch.frame, distance, self["operation"], bodies, flat)


def sweep(ctx, frame, used, extent, distance, to, operation="new", body=()):
    """
    The swept profiles of an extrusion: (shape, signed distance or reach mm,
    flat end?). distance: its formula (extent "distance"); to: the plane or
    the body it goes up to. ValueError saying what is wrong.
    """
    if extent == "distance":
        d = ctx.evaluate(distance, "distance")
        return ctx.kernel.extrude(frame, used, d), d, True
    if extent == "plane":
        from model.sketch import plane_frame

        target = plane_frame(ctx, to)
        shape, d = ctx.kernel.extrude_to_plane(frame, used, np.asarray(target.origin) / MM, target.normal)
        if d is not None:
            return shape, d, True
        reach = (np.asarray(target.origin) - np.asarray(frame.origin)) @ np.asarray(target.normal)
        return shape, float(np.sign(reach * (np.asarray(frame.normal) @ np.asarray(target.normal))) or 1.0), False
    if to not in ctx.shapes:
        raise ValueError(f"no body {to!r} above it to extrude up to")
    if operation != "new" and to in body_list(body):
        raise ValueError(f"it cannot go up to {to!r}, the body it {operation}s")
    shape, reach = ctx.kernel.extrude_to_body(frame, used, ctx.shapes[to])
    return shape, reach, False


def chosen_regions(ctx, sketch_name, profiles):
    """(the sketch's result, [Region] of these profile keys); ValueError naming a profile that is gone."""
    sketch = ctx.outputs.get(sketch_name)
    if sketch is None or not hasattr(sketch, "frame"):
        raise ValueError(f"{sketch_name!r} is not a sketch")
    regions = named_regions(sketch)
    missing = [p for p in profiles if p not in regions]
    if missing:
        raise ValueError(f"profile {' '.join(missing[0])} is not a closed region of {sketch_name} any more")
    return sketch, [regions[p] for p in profiles]


def apply_to_body(ctx, operation, body, tool):
    """
    A swept tool made into body `body` ("new") or joined / cut /
    intersected with it, in ctx.shapes; `body` may be a list (cut,
    intersect: each one by the same tool). All or nothing: one that fails
    leaves every body as it was. Returns the body names.
    """
    bodies = body_list(body)
    if len(bodies) > 1 and operation in ("new", "join"):
        raise ValueError(f"{'a new solid' if operation == 'new' else 'join'} takes one body, not "
                         f"{', '.join(bodies)} (cut and intersect take several)")
    if operation == "new":
        if bodies[0] in ctx.shapes:
            raise ValueError(f"a body {bodies[0]!r} exists already")
        ctx.shapes[bodies[0]] = tool
        return bodies
    missing = [b for b in bodies if b not in ctx.shapes]
    if missing:
        raise ValueError(f"no body {missing[0]!r} above it to {operation}")
    done = {}
    for name in bodies:
        try:
            done[name] = ctx.kernel.boolean(operation, ctx.shapes[name], tool)
        except ValueError as error:
            raise ValueError(f"{operation} {name}: {error}") from None
    ctx.shapes.update(done)
    return bodies
