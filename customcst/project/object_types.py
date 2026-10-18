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
    default: object                 # float, or a str for a choice
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    unit: str = ""
    choices: tuple = ()             # non-empty: the value is one of these strings
    boolean: bool = False           # an on / off switch (a check box in the tree)
    text: bool = False              # free text (e.g. a body name), not empty
    tooltip: str = ""

    def validate(self, value):
        """
        A number within the limits (or one of `choices`, or a bool), or
        ValueError naming the parameter.
        """
        if self.text:
            text = str(value).strip()
            if not text:
                raise ValueError(f"{self.label}: must not be empty")
            return text

        if self.boolean:
            if isinstance(value, bool):
                return value
            text = str(value).strip().lower()
            if text in ("true", "on", "yes", "1"):
                return True
            if text in ("false", "off", "no", "0"):
                return False
            raise ValueError(f"{self.label}: expected true or false, got {value!r}")

        if self.choices:
            text = str(value).strip().lower()
            if text not in self.choices:
                raise ValueError(f"{self.label}: must be one of {', '.join(self.choices)}")
            return text

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
            "lumped", "Lumped element",
            params=(
                Param("topology", "R, L, C in", "series", choices=("series", "parallel")),
                Param("r", "R", 0.0, minimum=0.0, unit="Ω"),
                Param("l", "L", 0.0, minimum=0.0, unit="nH"),
                Param("c", "C", 1.0, minimum=0.0, unit="pF"),
            ),
            note="cylinder or cone between two PEC bodies; 0 = that part is absent",
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
        ObjectType(
            "waveport", "Waveguide port",
            note="cylinder (coax) or box (PCB line) on the end face of a line, outside the model; Z0 = the line's own",
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
    material: Optional[str] = None      # dielectrics: preset name or materials.CUSTOM

    @classmethod
    def new(cls, type_name):
        return cls(type_name, OBJECT_TYPES[type_name].defaults())

    @property
    def object_type(self):
        return OBJECT_TYPES[self.type]
