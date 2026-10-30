# transforms.py
#
# Placement: transforms of the bodies as the features built them (as an
# imported STEP's), applied after all the features, in order (DESIGN.md
# "transforms"). The part file holds them after the features:
#
#   [[transform]]
#   name = "Move1"
#   type = "move"
#   targets = ["group:SMA", "Board"]  # bodies, and groups ("group:<name>": its bodies)
#   offset = ["board_w / 2", "0", "h"]  # mm along X, Y, Z (formulas)
#   axis = "Z Axis"                   # optional: turned first, around an origin axis or a sketch line
#   angle = "90"                      #   ("Sketch1:L3"), by degrees (the right hand way)
#
#   [[transform]]
#   name = "Mirror1"
#   type = "mirror"
#   targets = ["group:SMA"]
#   plane = "YZ"                      # each target mirrored in place: in the plane parallel to YZ / XZ / XY
#                                     # through its own center (a group's: of all its bodies)
#
# No targets: it fails (a transform being set up). A body of a group is
# placed before its group (local, then global): a
# transform of a body after one of its group fails, naming it; a new one is
# put before its group's (Part.add_transform). Nothing of the features sees
# the placement. They are Features (fields, formulas, renames) of their own
# registry, so a [[feature]] cannot be one. numpy only (ctx.kernel moves).

from dataclasses import dataclass

import numpy as np

from model.features import Feature, FeatureError, Field

TRANSFORM_TYPES = {}
GROUP = "group:"


def transform_type(cls):
    TRANSFORM_TYPES[cls.TYPE] = cls
    return cls


def _place(ctx, body, trsf):
    """A body under one more placement; ctx.placements keeps its whole placement (what a feature made follows)."""
    ctx.shapes[body] = ctx.kernel.placed(ctx.shapes[body], trsf)
    ctx.placements[body] = ctx.kernel.then(ctx.placements.get(body), trsf)


@dataclass
class MoveResult:
    bodies: list            # the bodies it moved


def expand(targets, part, shapes):
    """The bodies of targets (a group: its bodies there are); ValueError naming one that is not there."""
    bodies = []
    for t in targets:
        if t.startswith(GROUP):
            name = t[len(GROUP):]
            try:
                members = [b for b in part.group(name).bodies if b in shapes]
            except KeyError:
                raise ValueError(f"no group {name!r}") from None
            bodies += members
        elif t in shapes:
            bodies.append(t)
        else:
            raise ValueError(f"no body {t!r}")
    return list(dict.fromkeys(bodies))


def order_error(transforms, part, index):
    """Why transform `index` may not come where it is (a body of a group moved above it), or None."""
    moved_groups = {t[len(GROUP):]: earlier.name for earlier in transforms[:index]
                    for t in earlier.targets if t.startswith(GROUP)}
    for t in transforms[index].targets:
        group = None if t.startswith(GROUP) else part.group_of(t)
        if group is not None and group.name in moved_groups:
            return (f"{t} is in group {group.name}, placed by {moved_groups[group.name]} above: "
                    "place a body of a group before its group")
    return None


class _Placing(Feature):
    """What the transforms share: their targets (bodies, "group:<name>"), in the file and through renames."""

    def __init__(self, name, targets=(), **values):
        super().__init__(name, **values)
        self.targets = list(targets)
        if not all(isinstance(t, str) and t.strip() for t in self.targets):
            raise FeatureError(f"{name}: targets must be the names of bodies or groups ('group:SMA')")

    @classmethod
    def from_dict(cls, data):
        data = dict(data)
        name = data.pop("name")
        data.pop("type", None)
        targets = data.pop("targets", None)
        if not isinstance(targets, list) or not all(isinstance(t, str) for t in targets):
            raise FeatureError(f"{name}: targets must be a list of body or group names")
        return cls(name, targets=targets, **data)

    def to_dict(self):
        data = super().to_dict()
        data["targets"] = list(self.targets)
        return data

    def rename_target(self, old, new):
        self.targets = [new if t == old else t for t in self.targets]

    def _bodies(self, ctx):
        if not self.targets:
            raise ValueError(f"no targets: pick the bodies or groups it {self.VERB}")
        return expand(self.targets, ctx.part, ctx.shapes)


@transform_type
class Move(_Placing):
    TYPE = "move"
    VERB = "moves"
    FIELDS = (
        Field("offset", "exprs", ["0", "0", "0"], doc="mm along X, Y, Z"),
        Field("axis", "text", "", doc="turned first around it: an origin axis or a sketch line ('Sketch1:L3')"),
        Field("angle", "expr", "0", doc="by degrees around the axis"),
    )

    def __init__(self, name, targets=(), **values):
        super().__init__(name, targets, **values)
        if len(self["offset"]) != 3:
            raise FeatureError(f"{name}: offset must be three formulas (X, Y, Z)")

    def references(self):
        owner = self["axis"].rpartition(":")[0]
        return [owner] if owner else []

    def describe(self):
        turn = f", turned {self['angle']} deg around {self['axis']} first" if self["axis"] else ""
        return f"offset ({', '.join(self['offset'])}) mm{turn}"

    def apply(self, ctx):
        """Moves its bodies in ctx.shapes; ValueError if it cannot."""
        from model.revolve import axis_line

        bodies = self._bodies(ctx)
        offset = np.array([ctx.evaluate(text, f"offset {axis}") for text, axis in zip(self["offset"], "XYZ")])
        point, direction, angle = np.zeros(3), np.array([0.0, 0.0, 1.0]), 0.0
        if self["axis"].strip():
            point, direction = axis_line(self["axis"], ctx.part, ctx.outputs)
            angle = ctx.evaluate(self["angle"], "angle")
        trsf = ctx.kernel.move_trsf(point, direction, angle, offset)
        for body in bodies:
            _place(ctx, body, trsf)
        return MoveResult(bodies)


MIRROR_PLANES = {"YZ": (1.0, 0.0, 0.0), "XZ": (0.0, 1.0, 0.0), "XY": (0.0, 0.0, 1.0)}


@transform_type
class Mirror(_Placing):
    """
    Each target mirrored in place: in the plane parallel to `plane` (YZ: x
    flips...) through its own center (the middle of its bounds; a group's:
    of all its bodies, mirrored as one).
    """

    TYPE = "mirror"
    VERB = "mirrors"
    FIELDS = (
        Field("plane", "choice", "YZ", tuple(MIRROR_PLANES), doc="mirrored in the plane parallel to it through "
                                                                  "its center"),
    )

    def describe(self):
        return f"mirrored in {self['plane']} through its center"

    def apply(self, ctx):
        self._bodies(ctx)                       # none: it fails
        normal = np.array(MIRROR_PLANES[self["plane"]])
        done = []
        for target in self.targets:
            bodies = [b for b in expand([target], ctx.part, ctx.shapes) if b not in done]
            if not bodies:
                continue
            corners = np.array([ctx.kernel.bounds(ctx.shapes[b]) for b in bodies])
            center = 0.5 * (corners[:, 0].min(axis=0) + corners[:, 1].max(axis=0))
            trsf = ctx.kernel.mirror_trsf(center, normal)
            for body in bodies:
                _place(ctx, body, trsf)
            done += bodies
        return MoveResult(done)
