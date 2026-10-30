# part.py
#
# The part document: what is saved and edited, never what is computed.
#   parameters   [Parameter]: name = formula, in table order (model/expressions.py)
#   origin       YZ / XZ / XY Plane, X / Y / Z Axis, Center Point: fixed,
#                only their visibility is the user's
#   features     [Feature] in history order (model/features.py); a feature
#                refers to datums and to features above it by name
#   bodies       {body name: {property: value}}: what belongs to a body itself,
#                not to the features making it (its color now; later its
#                material for customcst). [[body]] in the file:
#                    [[body]]
#                    name = "Solid1"
#                    color = "#c87533"
#   groups       [Group]: named sets of bodies, flat (a body in one group at
#                most): moved, hidden... together. [[group]] in the file:
#                    [[group]]
#                    name = "SMA"
#                    bodies = ["SMA:Shell", "SMA:Pin"]
#   transforms   [Move...] (model/transforms.py): the placement of the bodies
#                as built, after all the features, in order ([[transform]])
# The bodies come from the rebuild (model/rebuild.py), not from here.
#
# Names: parameters have their own namespace (formulas see only them);
# datums and features share one (references see both); bodies have theirs
# (the features making and changing them name them). A name is the id:
# renaming rewrites every formula or reference that uses it (rename_body:
# the features' body fields and the [[body]] row).
#
# to_dict / from_dict: the plain data of the part file (model/part_file.py);
# from_dict validates everything and raises PartError naming the place.

import re
from dataclasses import dataclass, field

import numpy as np

from model.expressions import ExpressionError, name_error, names, rename
from model.features import FEATURE_TYPES, FeatureError
from model import extrude, importstep, revolve, sketch, workplane  # noqa: F401  (register the feature types)
from model.transforms import GROUP, TRANSFORM_TYPES, order_error

DATUM_KINDS = ("plane", "axis", "point")
COLOR = re.compile(r"#[0-9a-fA-F]{6}\Z")
BODY_PROPERTIES = ("color",)


def color_rgb(text):
    """'#rrggbb' -> (r, g, b) in 0..1."""
    return tuple(int(text[i:i + 2], 16) / 255.0 for i in (1, 3, 5))


class PartError(ValueError):
    pass


@dataclass
class Datum:
    """
    name       unique within the part ("XY Plane")
    kind       "plane" | "axis" | "point"
    origin     (3,) m: the plane's center, a point of the axis, the point
    direction  (3,) unit: the plane's normal, the axis' direction; 0 for a point
    u, v       (3,) unit in-plane axes of a plane (u x v = normal); 0 otherwise
    visible    shown in 3D (the origin starts hidden, as in Inventor)
    """

    name: str
    kind: str
    origin: np.ndarray
    direction: np.ndarray = None
    u: np.ndarray = None
    v: np.ndarray = None
    visible: bool = False

    def __post_init__(self):
        if self.kind not in DATUM_KINDS:
            raise ValueError(f"datum {self.name!r}: unknown kind {self.kind!r}")
        self.origin = np.asarray(self.origin, dtype=float).reshape(3)
        for key in ("direction", "u", "v"):
            value = getattr(self, key)
            setattr(self, key, np.zeros(3) if value is None else np.asarray(value, dtype=float).reshape(3))
        if self.kind != "point" and abs(np.linalg.norm(self.direction) - 1.0) > 1e-9:
            raise ValueError(f"datum {self.name!r}: the direction must be a unit vector")
        if self.kind == "plane" and not np.allclose(np.cross(self.u, self.v), self.direction):
            raise ValueError(f"datum {self.name!r}: u x v must be the normal")


def origin_datums():
    """The origin of a part, in Inventor's order."""
    x, y, z = np.eye(3)
    o = np.zeros(3)
    return [
        Datum("YZ Plane", "plane", o, x, y, z),
        Datum("XZ Plane", "plane", o, y, x, -z),     # as Inventor's sketch on XZ: X right, Z down
        Datum("XY Plane", "plane", o, z, x, y),
        Datum("X Axis", "axis", o, x),
        Datum("Y Axis", "axis", o, y),
        Datum("Z Axis", "axis", o, z),
        Datum("Center Point", "point", o),
    ]


@dataclass
class Group:
    """A named set of bodies (by name; a body in one group at most)."""
    name: str
    bodies: list


@dataclass
class Parameter:
    name: str
    formula: str
    comment: str = ""


@dataclass
class Part:
    parameters: list = field(default_factory=list)      # [Parameter]
    origin: list = field(default_factory=origin_datums)
    features: list = field(default_factory=list)        # [Feature]
    bodies: dict = field(default_factory=dict)          # body name -> {"color": "#rrggbb"}
    groups: list = field(default_factory=list)          # [Group]
    transforms: list = field(default_factory=list)      # [Move...]: after the features, in order

    # =====================================================
    # Lookup
    # =====================================================

    def datum(self, name):
        for d in self.origin:
            if d.name == name:
                return d
        raise KeyError(name)

    def parameter(self, name):
        for p in self.parameters:
            if p.name == name:
                return p
        raise KeyError(name)

    def feature(self, name):
        for f in self.features:
            if f.name == name:
                return f
        raise KeyError(name)

    def object_names(self):
        """Datums, features and transforms: the namespace references point into (names unique in it)."""
        return [d.name for d in self.origin] + [f.name for f in self.features] + [t.name for t in self.transforms]

    # =====================================================
    # Features
    # =====================================================

    def free_body_name(self, stem="Solid"):
        """Solid1, Solid2...: a body name no feature makes yet (as Inventor names bodies)."""
        taken = set(self.body_names())
        n = 1
        while f"{stem}{n}" in taken:
            n += 1
        return f"{stem}{n}"

    def body_names(self):
        """The bodies the features make or change (an import: all it makes), in history order."""
        names = []
        for f in self.features:
            names += f.made_bodies() if hasattr(f, "made_bodies") else ([f["body"]] if "body" in f.values else [])
        return list(dict.fromkeys(names))

    def rename_body(self, old, new):
        """
        Renames a body: every feature making or changing it, and its
        properties ([[body]]). PartError if the name is empty, taken, or
        would break a STEP file (a quote, a control character).
        """
        new = new.strip()
        if new == old:
            return
        if old not in self.body_names():
            raise PartError(f"no body {old!r}")
        if not new:
            raise PartError("a body needs a name")
        if "'" in new or any(ord(c) < 32 for c in new):
            raise PartError(f"{new!r}: no quotes or control characters in a body's name (STEP)")
        if new in self.body_names():
            raise PartError(f"a body {new!r} already exists")
        for f in self.features:
            if f.values.get("body") == old:
                f.values["body"] = new
            if hasattr(f, "rename_body"):
                f.rename_body(old, new)
        for t in self.transforms:
            t.rename_target(old, new)
        if old in self.bodies:
            self.bodies[new] = self.bodies.pop(old)
        for g in self.groups:
            g.bodies = [new if b == old else b for b in g.bodies]

    # =====================================================
    # Groups
    # =====================================================

    def group(self, name):
        for g in self.groups:
            if g.name == name:
                return g
        raise KeyError(name)

    def group_of(self, body):
        """The group a body is in, or None."""
        return next((g for g in self.groups if body in g.bodies), None)

    def free_group_name(self, stem="Group"):
        taken = {g.name for g in self.groups}
        n = 1
        while f"{stem}{n}" in taken:
            n += 1
        return f"{stem}{n}"

    def add_to_group(self, name, bodies):
        """These bodies into group `name` (made if new), out of the group they were in; empty groups go."""
        _check_group_name(name)
        bodies = [b for b in bodies]
        for g in self.groups:
            g.bodies = [b for b in g.bodies if b not in bodies]
        try:
            group = self.group(name)
        except KeyError:
            group = Group(name, [])
            self.groups.append(group)
        group.bodies += [b for b in bodies if b not in group.bodies]
        self.groups = [g for g in self.groups if g.bodies]
        return group

    def remove_from_group(self, bodies):
        for g in self.groups:
            g.bodies = [b for b in g.bodies if b not in bodies]
        self.groups = [g for g in self.groups if g.bodies]

    def rename_group(self, old, new):
        new = new.strip()
        if new == old:
            return
        _check_group_name(new)
        if any(g.name == new for g in self.groups):
            raise PartError(f"a group {new!r} already exists")
        self.group(old).name = new
        for t in self.transforms:
            t.rename_target(GROUP + old, GROUP + new)

    def ungroup(self, name):
        """The group goes; its bodies stay."""
        self.groups = [g for g in self.groups if g.name != name]

    # =====================================================
    # Transforms (placement)
    # =====================================================

    def transform(self, name):
        for t in self.transforms:
            if t.name == name:
                return t
        raise KeyError(name)

    def add_transform(self, transform):
        """
        A transform into the placement: before the first one of a group one
        of its bodies is in (a body of a group is placed before its group),
        else last. Returns its index.
        """
        if transform.name in self.object_names():
            raise PartError(f"the name {transform.name!r} is taken")
        groups = {g.name for t in transform.targets if not t.startswith(GROUP)
                  for g in [self.group_of(t)] if g is not None}
        at = next((i for i, t in enumerate(self.transforms)
                   if any(x.startswith(GROUP) and x[len(GROUP):] in groups for x in t.targets)), len(self.transforms))
        self.transforms.insert(at, transform)
        return at

    def move_transform(self, name, step):
        """A transform one up (step -1) or down (+1); PartError if that breaks the order of groups."""
        i = [t.name for t in self.transforms].index(name)
        j = i + step
        if not 0 <= j < len(self.transforms):
            return
        order = list(self.transforms)
        order[i], order[j] = order[j], order[i]
        for k in (min(i, j), max(i, j)):
            error = order_error(order, self, k)
            if error:
                raise PartError(error)
        self.transforms = order

    def delete_transform(self, name):
        self.transforms = [t for t in self.transforms if t.name != name]

    def free_feature_name(self, stem):
        """Sketch1, Sketch2...: the first one not taken (as Inventor names them)."""
        taken = set(self.object_names())
        n = 1
        while f"{stem}{n}" in taken:
            n += 1
        return f"{stem}{n}"

    def add_feature(self, feature):
        if feature.name in self.object_names():
            raise PartError(f"the name {feature.name!r} is taken")
        missing = [ref for ref in feature.references() if ref not in self.object_names()]
        if missing:
            raise PartError(f"{feature.name}: {missing[0]!r} is not a datum or a feature above it")
        self.features.append(feature)
        return feature

    def feature_users(self, name):
        """Features that refer to `name`."""
        return [f.name for f in self.features if name in f.references()]

    def delete_feature(self, name):
        """Deletes a feature nothing refers to (PartError names the users otherwise)."""
        users = self.feature_users(name)
        if users:
            raise PartError(f"{name!r} is used by {', '.join(users)}")
        self.features.remove(self.feature(name))

    # =====================================================
    # Parameters
    # =====================================================

    def replace_in_profiles(self, sketch, old, new):
        """Profiles of `sketch` that named entity `old` name `new` instead (a curve became a spline)."""
        for f in self.features:
            if f.TYPE == "extrude" and f["sketch"] == sketch:
                f.profiles = [tuple(sorted(new if n == old else n for n in p)) for p in f.profiles]

    def free_parameter_name(self, stem="p"):
        taken = {p.name for p in self.parameters}
        n = 1
        while f"{stem}{n}" in taken:
            n += 1
        return f"{stem}{n}"

    def add_parameter(self, name=None, formula="0", comment=""):
        name = name or self.free_parameter_name()
        self._check_parameter_name(name)
        self.parameters.append(Parameter(name, formula, comment))
        return name

    def rename_parameter(self, old, new):
        """Renames a parameter and every formula that uses it."""
        if old == new:
            return
        parameter = self.parameter(old)
        self._check_parameter_name(new)
        parameter.name = new
        for p in self.parameters:
            p.formula = rename(p.formula, old, new)
        for f in self.features + self.transforms:
            f.rename_formulas(lambda text: rename(text, old, new))

    def parameter_users(self, name):
        """Parameters and features whose formulas use `name`."""
        users = []
        for p in self.parameters:
            if p.name != name and name in _names_or_empty(p.formula):
                users.append(p.name)
        for f in self.features + self.transforms:
            if any(name in _names_or_empty(text) for _, text in f.formulas()):
                users.append(f.name)
        return users

    def delete_parameter(self, name):
        """Deletes an unused parameter (PartError names the users otherwise)."""
        users = self.parameter_users(name)
        if users:
            raise PartError(f"{name!r} is used by {', '.join(users)}")
        self.parameters.remove(self.parameter(name))

    def _check_parameter_name(self, name):
        error = name_error(name)
        if error:
            raise PartError(error)
        if any(p.name == name for p in self.parameters):
            raise PartError(f"a parameter {name!r} already exists")

    # =====================================================
    # Plain data
    # =====================================================

    def to_dict(self):
        data = {}
        visible = [d.name for d in self.origin if d.visible]
        if visible:
            data["origin"] = {"visible": visible}
        if self.parameters:
            data["parameter"] = [{"name": p.name, "formula": p.formula, **({"comment": p.comment} if p.comment else {})}
                                 for p in self.parameters]
        if self.features:
            data["feature"] = [f.to_dict() for f in self.features]
        rows = [{"name": name, **props} for name, props in self.bodies.items() if props]
        if rows:
            data["body"] = rows
        if self.groups:
            data["group"] = [{"name": g.name, "bodies": list(g.bodies)} for g in self.groups]
        if self.transforms:
            data["transform"] = [t.to_dict() for t in self.transforms]
        return data

    # =====================================================
    # Body properties
    # =====================================================

    def body_color(self, name):
        """'#rrggbb', or None (the default)."""
        return self.bodies.get(name, {}).get("color")

    def set_body_color(self, name, color):
        """color: '#rrggbb', or None for the default."""
        props = dict(self.bodies.get(name, {}))
        if color is None:
            props.pop("color", None)
        else:
            if not isinstance(color, str) or not COLOR.match(color):
                raise PartError(f"{color!r} is not a color (#rrggbb)")
            props["color"] = color.lower()
        if props:
            self.bodies[name] = props
        else:
            self.bodies.pop(name, None)

    @classmethod
    def from_dict(cls, data):
        """A validated part from plain data; PartError names what is wrong and where."""
        _keys(data, "the file", allowed={"origin", "parameter", "feature", "body", "group", "transform"})
        part = cls()

        origin = data.get("origin", {})
        _keys(origin, "[origin]", allowed={"visible"})
        visible = origin.get("visible", [])
        if not isinstance(visible, list) or not all(isinstance(n, str) for n in visible):
            raise PartError("[origin] visible must be a list of names")
        for name in visible:
            try:
                part.datum(name).visible = True
            except KeyError:
                raise PartError(f"[origin] visible: no origin item {name!r}") from None

        rows = data.get("parameter", [])
        if not isinstance(rows, list):
            raise PartError("parameter must be an array of tables ([[parameter]])")
        for i, row in enumerate(rows, 1):
            where = f"parameter {i}"
            if not isinstance(row, dict):
                raise PartError(f"{where} must be a table")
            _keys(row, where, allowed={"name", "formula", "comment"}, required={"name", "formula"})
            name, formula, comment = row["name"], row["formula"], row.get("comment", "")
            if not isinstance(name, str):
                raise PartError(f"{where}: name must be a string")
            if isinstance(formula, bool) or not isinstance(formula, (str, int, float)):
                raise PartError(f"{where} ({name}): formula must be a string or a number")
            if not isinstance(comment, str):
                raise PartError(f"{where} ({name}): comment must be a string")
            try:
                part.add_parameter(name, formula if isinstance(formula, str) else repr(formula), comment)
            except PartError as error:
                raise PartError(f"{where}: {error}") from None

        rows = data.get("feature", [])
        if not isinstance(rows, list):
            raise PartError("feature must be an array of tables ([[feature]])")
        for i, row in enumerate(rows, 1):
            if not isinstance(row, dict):
                raise PartError(f"feature {i} must be a table")
            name = row.get("name")
            where = f"feature {i}" + (f" ({name})" if isinstance(name, str) else "")
            if not isinstance(name, str) or not name.strip():
                raise PartError(f"{where}: name must be a non-empty string")
            kind = row.get("type")
            if kind not in FEATURE_TYPES:
                raise PartError(f"{where}: unknown type {kind!r}")
            if name in part.object_names():
                raise PartError(f"{where}: the name {name!r} is taken")
            try:
                feature = FEATURE_TYPES[kind].from_dict(row)
            except FeatureError as error:
                raise PartError(f"{where}: {error}") from None
            # References point up the history: datums and features above.
            for ref in feature.references():
                if ref not in part.object_names():
                    raise PartError(f"{where}: {ref!r} is not a datum or a feature above it")
            part.features.append(feature)

        rows = data.get("body", [])
        if not isinstance(rows, list):
            raise PartError("body must be an array of tables ([[body]])")
        for i, row in enumerate(rows, 1):
            where = f"body {i}"
            _keys(row, where, allowed={"name", *BODY_PROPERTIES}, required={"name"})
            name = row["name"]
            if not isinstance(name, str) or not name.strip():
                raise PartError(f"{where}: name must be a non-empty string")
            if name in part.bodies:
                raise PartError(f"{where}: {name!r} is listed twice")
            try:
                part.set_body_color(name, row.get("color"))
            except PartError as error:
                raise PartError(f"{where} ({name}): {error}") from None

        rows = data.get("group", [])
        if not isinstance(rows, list):
            raise PartError("group must be an array of tables ([[group]])")
        for i, row in enumerate(rows, 1):
            where = f"group {i}"
            if not isinstance(row, dict):
                raise PartError(f"{where} must be a table")
            _keys(row, where, allowed={"name", "bodies"}, required={"name", "bodies"})
            name, bodies = row["name"], row["bodies"]
            if not isinstance(name, str):
                raise PartError(f"{where}: name must be a string")
            where = f"{where} ({name})"
            try:
                _check_group_name(name)
            except PartError as error:
                raise PartError(f"{where}: {error}") from None
            if any(g.name == name for g in part.groups):
                raise PartError(f"{where}: the name is taken")
            if not isinstance(bodies, list) or not bodies or not all(isinstance(b, str) for b in bodies):
                raise PartError(f"{where}: bodies must be a list of body names")
            for b in bodies:
                other = part.group_of(b)
                if other is not None or bodies.count(b) > 1:
                    raise PartError(f"{where}: {b!r} is in {other.name if other else 'it'} already "
                                    "(a body is in one group at most)")
            part.groups.append(Group(name, list(bodies)))

        rows = data.get("transform", [])
        if not isinstance(rows, list):
            raise PartError("transform must be an array of tables ([[transform]])")
        for i, row in enumerate(rows, 1):
            if not isinstance(row, dict):
                raise PartError(f"transform {i} must be a table")
            name = row.get("name")
            where = f"transform {i}" + (f" ({name})" if isinstance(name, str) else "")
            if not isinstance(name, str) or not name.strip():
                raise PartError(f"{where}: name must be a non-empty string")
            kind = row.get("type")
            if kind not in TRANSFORM_TYPES:
                raise PartError(f"{where}: unknown type {kind!r} (one of {', '.join(TRANSFORM_TYPES)})")
            if name in part.object_names():
                raise PartError(f"{where}: the name {name!r} is taken")
            try:
                transform = TRANSFORM_TYPES[kind].from_dict(row)
            except FeatureError as error:
                raise PartError(f"{where}: {error}") from None
            for ref in transform.references():
                if ref not in part.object_names():
                    raise PartError(f"{where}: {ref!r} is not a feature")
            part.transforms.append(transform)        # in the file's order (a wrong order fails it, named)
        return part


def _check_group_name(name):
    if not isinstance(name, str) or not name.strip():
        raise PartError("a group needs a name")
    if "'" in name or any(ord(c) < 32 for c in name):
        raise PartError(f"{name!r}: no quotes or control characters in a group's name")


def _names_or_empty(formula):
    try:
        return names(formula)
    except ExpressionError:
        return set()


def _keys(table, where, allowed, required=()):
    if not isinstance(table, dict):
        raise PartError(f"{where} must be a table")
    unknown = sorted(set(table) - set(allowed))
    if unknown:
        raise PartError(f"{where}: unknown key(s) {unknown}")
    missing = sorted(set(required) - set(table))
    if missing:
        raise PartError(f"{where}: missing key(s) {missing}")
