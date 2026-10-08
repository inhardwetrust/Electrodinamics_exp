# params.py
#
# Declarative description of user-tunable values. Simulations and layers
# describe their settings with these; the Qt panel builds widgets from them.

import dataclasses
from dataclasses import dataclass


@dataclass(frozen=True)
class Parameter:
    """
    Schema of one user-tunable value.

    kind:    "float" | "int" | "bool" | "choice"
    name:    key; for nested dataclasses a dotted path ("clim.mode")
    enabled_when: optional (other_name, allowed_values) - the widget is only
             enabled while that other value is one of allowed_values
             (e.g. linthresh only matters for the symlog scale)
    """

    name: str
    label: str
    kind: str
    default: object = None
    minimum: float = None
    maximum: float = None
    choices: tuple = ()
    unit: str = ""
    step: float = None
    decimals: int = 3
    enabled_when: tuple = None


def get_value(obj, path):
    """Read a (possibly dotted) attribute path from nested dataclasses."""
    for part in path.split("."):
        obj = getattr(obj, part)
    return obj


def with_value(obj, path, value):
    """Copy of nested dataclasses with one (dotted) attribute replaced."""
    head, _, rest = path.partition(".")

    if not rest:
        return dataclasses.replace(obj, **{head: value})

    return dataclasses.replace(
        obj,
        **{head: with_value(getattr(obj, head), rest, value)},
    )


def is_enabled(parameter, values):
    """values: {name: current value}."""
    if parameter.enabled_when is None:
        return True

    other, allowed = parameter.enabled_when
    return values.get(other) in allowed
