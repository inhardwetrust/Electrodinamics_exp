# part.py
#
# The part document: what is saved and edited, never what is computed.
#   parameters   [Parameter]: name = formula, in table order (model/expressions.py)
#   origin       YZ / XZ / XY Plane, X / Y / Z Axis, Center Point: fixed,
#                only their visibility is the user's
#   features     [Feature] in history order (model/features.py); a feature
#                refers to datums and to features above it by name
# The bodies come from the rebuild (model/rebuild.py), not from here.
#
# Names: parameters have their own namespace (formulas see only them);
# datums and features share one (references see both). A name is the id:
# renaming rewrites every formula or reference that uses it.
#
# to_dict / from_dict: the plain data of the part file (model/part_file.py);
# from_dict validates everything and raises PartError naming the place.

from dataclasses import dataclass, field

import numpy as np

from model.expressions import ExpressionError, name_error, names, rename
from model.features import FEATURE_TYPES, FeatureError
from model import sketch  # noqa: F401  (registers the feature types)

DATUM_KINDS = ("plane", "axis", "point")


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
class Parameter:
    name: str
    formula: str
    comment: str = ""


@dataclass
class Part:
    parameters: list = field(default_factory=list)      # [Parameter]
    origin: list = field(default_factory=origin_datums)
    features: list = field(default_factory=list)        # [Feature]

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
        """Datums and features: the namespace references point into."""
        return [d.name for d in self.origin] + [f.name for f in self.features]

    # =====================================================
    # Features
    # =====================================================

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
        for f in self.features:
            f.rename_formulas(lambda text: rename(text, old, new))

    def parameter_users(self, name):
        """Parameters and features whose formulas use `name`."""
        users = []
        for p in self.parameters:
            if p.name != name and name in _names_or_empty(p.formula):
                users.append(p.name)
        for f in self.features:
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
        return data

    @classmethod
    def from_dict(cls, data):
        """A validated part from plain data; PartError names what is wrong and where."""
        _keys(data, "the file", allowed={"origin", "parameter", "feature"})
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
        return part


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
