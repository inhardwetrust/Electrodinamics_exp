# object_types.py
#
# Object types that can be assigned to a STEP body, and their parameters.
# The UI builds its menus and editors from these schemas: adding a type or a
# parameter needs no UI code.
#
# Values are stored in the units shown to the user (GHz etc., see `unit`);
# conversion to SI happens where the physics needs it.

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class Param:
    name: str                 # key in the TOML file
    label: str                # shown in the UI
    default: float
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    unit: str = ""

    def validate(self, value):
        """float(value) within the limits, or ValueError naming the parameter."""
        if isinstance(value, bool):
            raise ValueError(f"{self.label}: not a number: {value!r}")

        try:
            value = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"{self.label}: not a number: {value!r}") from None

        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError(f"{self.label}: must be finite")
        if self.minimum is not None and value < self.minimum:
            raise ValueError(f"{self.label} must be >= {self.minimum:g}")
        if self.maximum is not None and value > self.maximum:
            raise ValueError(f"{self.label} must be <= {self.maximum:g}")

        return value


@dataclass(frozen=True)
class ObjectType:
    name: str                 # key in the TOML file
    label: str
    params: tuple = ()
    note: str = ""            # read-only explanation shown under the type
    allowed_kinds: tuple = ("solid", "sheet")

    def param(self, name):
        for p in self.params:
            if p.name == name:
                return p
        raise KeyError(name)

    def defaults(self):
        return {p.name: p.default for p in self.params}


OBJECT_TYPES = {
    t.name: t
    for t in (
        ObjectType(
            "pec", "PEC",
            note="σ = ∞ (perfect conductor), no parameters",
        ),
        ObjectType(
            "dielectric", "Dielectric",
            params=(
                Param("eps_r", "εr", 1.0, minimum=1.0),
                Param("tan_d", "tan δ", 0.0, minimum=0.0, maximum=1.0),
                Param("f_ref", "f ref (tan δ)", 10.0, minimum=1e-6, unit="GHz"),
                Param("mu_r", "μr", 1.0, minimum=1.0),
            ),
            # A dielectric sheet has no volume to fill.
            allowed_kinds=("solid",),
        ),
        ObjectType(
            "port", "Port",
            params=(
                Param("impedance", "R", 50.0, minimum=1e-3, unit="Ω"),
            ),
            note="cone between two PEC bodies, small end = +",
            allowed_kinds=("solid",),
        ),
    )
}

# Types whose bodies conduct (port ends must touch one of them).
CONDUCTOR_TYPES = ("pec",)


@dataclass
class Assignment:
    """One body's type and parameter values (all parameters always present)."""

    type: str
    values: dict = field(default_factory=dict)

    @classmethod
    def new(cls, type_name):
        return cls(type_name, OBJECT_TYPES[type_name].defaults())

    @property
    def object_type(self):
        return OBJECT_TYPES[self.type]
