# params.py
#
# Declarative description of user-tunable values. The Qt panel builds its
# widgets from these, so a new parameter needs no UI code.

from dataclasses import dataclass


@dataclass(frozen=True)
class Param:
    """
    kind:    "float" | "int" | "choice"
    restart: True -> the new value takes effect only on Reset (e.g. the
             array size: the state has to be recreated).
    """

    name: str
    label: str
    kind: str
    default: object
    minimum: float = None
    maximum: float = None
    choices: tuple = ()
    step: float = None
    decimals: int = 3
    restart: bool = False
    tooltip: str = ""
    group: str = "model"      # panel section: "model" or "feed"
