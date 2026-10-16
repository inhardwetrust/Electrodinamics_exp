# settings.py
#
# Project-wide settings (not tied to a body), as schemas like the object
# types: each section is a TOML table and a node in the UI tree.
#
#   [simulation]
#   f_min = 1.0                 # GHz
#   f_max = 5.0                 # GHz
#   padding = 0.0               # mm, air around the model; 0 = auto
#
#   [mesh]
#   cells_per_wavelength = 20   # at f_max, in each material
#   cells_per_feature = 4       # min cells across a gap between fixpoints
#   max_ratio = 1.3             # max step ratio between neighbor cells
#   min_step = 0.0              # mm, fixpoints closer than this merge; 0 = auto

from dataclasses import dataclass

from project.object_types import Param

C0 = 299_792_458.0          # m/s
GHZ = 1e9
MM = 1e-3


@dataclass(frozen=True)
class Section:
    name: str               # TOML table name
    label: str
    params: tuple
    note: str = ""

    def param(self, name):
        for p in self.params:
            if p.name == name:
                return p
        raise KeyError(name)

    def defaults(self):
        return {p.name: p.default for p in self.params}


SECTIONS = {
    s.name: s
    for s in (
        Section(
            "simulation", "Simulation",
            params=(
                Param("f_min", "f min", 1.0, minimum=1e-3, unit="GHz"),
                Param("f_max", "f max", 5.0, minimum=1e-3, unit="GHz"),
                Param("padding", "padding (0 = auto)", 0.0, minimum=0.0, unit="mm"),
            ),
            note="auto padding = λ/4 in air at the center frequency",
        ),
        Section(
            "mesh", "Mesh",
            params=(
                Param("cells_per_wavelength", "cells per λ", 20.0, minimum=5.0, maximum=200.0),
                Param("cells_per_feature", "cells per feature", 4.0, minimum=1.0, maximum=50.0),
                Param("max_ratio", "max step ratio", 1.3, minimum=1.05, maximum=3.0),
                Param("min_step", "min step (0 = auto)", 0.0, minimum=0.0, unit="mm"),
            ),
            note="lines at body faces; step ≤ λ/N in each material",
        ),
    )
}


def check_settings(settings):
    """Cross-parameter rules; raises ValueError."""
    sim = settings["simulation"]
    if sim["f_max"] <= sim["f_min"]:
        raise ValueError("f max must be greater than f min")


def padding_m(settings):
    """Air around the model in meters (auto: λ0/4 at the center frequency)."""
    sim = settings["simulation"]
    if sim["padding"] > 0:
        return sim["padding"] * MM

    f_center = 0.5 * (sim["f_min"] + sim["f_max"]) * GHZ
    return C0 / f_center / 4.0
