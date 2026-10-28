# features.py
#
# The feature registry. A feature is one entry of the part's history (a
# sketch, an extrusion, a work plane...). Each type is a Feature subclass,
# registered with @feature_type, that declares:
#   TYPE     its name in the file ("extrude")
#   FIELDS   its fields (Field): the file is validated against them, and the
#            property panel will be generated from them
#   build()  (later) its geometry from the evaluated values and the results
#            of the features above it
# The part file stores a feature as a table of its fields:
#   [[feature]]
#   name = "Extrusion1"
#   type = "extrude"
#   sketch = "Sketch1"        # a "ref" field: the name of a datum or a feature above
#   distance = "h"            # an "expr" field: a formula of the parameters
#
# Field kinds:
#   expr     a formula (a number in the file is taken as its text)
#   exprs    a list of formulas (a point: ["x", "y"])
#   ref      a name of a datum or of a feature above this one
#   choice   one of `choices`
#   text     any string (a body name)
#   bool     true / false

from dataclasses import dataclass

FIELD_KINDS = ("expr", "exprs", "ref", "choice", "text", "bool")

FEATURE_TYPES = {}          # TYPE -> Feature subclass


@dataclass(frozen=True)
class Field:
    name: str
    kind: str
    default: object = None      # None: required
    choices: tuple = ()
    doc: str = ""

    def __post_init__(self):
        if self.kind not in FIELD_KINDS:
            raise ValueError(f"field {self.name!r}: unknown kind {self.kind!r}")


def feature_type(cls):
    """Class decorator: registers a Feature subclass under its TYPE."""
    if not cls.TYPE or cls.TYPE in FEATURE_TYPES:
        raise ValueError(f"feature type {cls.TYPE!r} is empty or registered twice")
    FEATURE_TYPES[cls.TYPE] = cls
    return cls


class FeatureError(ValueError):
    pass


class Feature:
    TYPE = ""
    FIELDS = ()

    def __init__(self, name, **values):
        self.name = name
        self.values = {}
        for field in self.FIELDS:
            value = values.pop(field.name, field.default)
            if value is None:
                raise FeatureError(f"{name}: {field.name!r} is required")
            self.values[field.name] = _checked(name, field, value)
        if values:
            raise FeatureError(f"{name}: unknown field(s) {sorted(values)} for a {self.TYPE}")

    def __getitem__(self, key):
        return self.values[key]

    def __eq__(self, other):
        return type(self) is type(other) and self.to_dict() == other.to_dict()

    @classmethod
    def from_dict(cls, data):
        data = dict(data)
        name = data.pop("name")
        data.pop("type", None)
        return cls(name, **data)

    def to_dict(self):
        return {"name": self.name, "type": self.TYPE, **self.values}

    def references(self):
        """Names of the datums and features this one uses."""
        return [self.values[f.name] for f in self.FIELDS if f.kind == "ref"]

    def formulas(self):
        """[(field name, formula)] of every expression field (exprs: one row per item)."""
        rows = []
        for f in self.FIELDS:
            if f.kind == "expr":
                rows.append((f.name, self.values[f.name]))
            elif f.kind == "exprs":
                rows += [(f"{f.name}[{i}]", text) for i, text in enumerate(self.values[f.name])]
        return rows

    def rename_formulas(self, rename):
        """Applies rename(formula) -> formula to every expression field."""
        for f in self.FIELDS:
            if f.kind == "expr":
                self.values[f.name] = rename(self.values[f.name])
            elif f.kind == "exprs":
                self.values[f.name] = [rename(t) for t in self.values[f.name]]

    def rename_reference(self, old, new):
        for f in self.FIELDS:
            if f.kind == "ref" and self.values[f.name] == old:
                self.values[f.name] = new

    def build(self, ctx):
        raise NotImplementedError(f"{self.TYPE} cannot be built yet")


def _formula(owner, field, value):
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise FeatureError(f"{owner}: {field} must be a formula (a string or a number)")
    return value if isinstance(value, str) else repr(value)


def _checked(owner, field, value):
    kind = field.kind
    if kind == "expr":
        return _formula(owner, field.name, value)
    if kind == "exprs":
        if not isinstance(value, list):
            raise FeatureError(f"{owner}: {field.name} must be a list of formulas")
        return [_formula(owner, field.name, v) for v in value]
    if kind == "bool":
        if not isinstance(value, bool):
            raise FeatureError(f"{owner}: {field.name} must be true or false")
        return value
    if not isinstance(value, str):
        raise FeatureError(f"{owner}: {field.name} must be a string")
    if kind == "choice" and value not in field.choices:
        raise FeatureError(f"{owner}: {field.name} must be one of {list(field.choices)}, not {value!r}")
    return value
