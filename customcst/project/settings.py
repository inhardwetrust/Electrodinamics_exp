# settings.py
#
# Project-wide settings (not tied to a body), as schemas like the object
# types: each section is a TOML table and a node in the UI tree.
#
#   [simulation]
#   f_min = 1.0                 # GHz
#   f_max = 5.0                 # GHz
#   padding = 0.0               # mm, air around the model; 0 = auto
#   trim_pulse = false          # start / stop the pulse at 1e-4 of its peak, not 1e-7
#   accuracy = "-40"            # dB: a port's run stops when the field energy fell this far
#   excite = "all"              # "all" ports, one run each, or one port's name (one run)
#
#   [mesh]
#   cells_per_wavelength = 20   # at f_max, in each material
#   cells_per_feature = 4       # min cells across a gap between fixpoints
#   max_ratio = 1.3             # max step ratio between neighbor cells
#   min_step = 0.0              # mm, fixpoints closer than this merge; 0 = auto
#   slanted = true              # refine the step around slanted metal faces
#
#   [farfield]
#   enabled = true              # off: no far field, the frequencies kept (a quick S11 run)
#   frequencies = "5.5, 5.757"  # GHz, the far-field monitor's ("" = off; within f min .. f max)
#   f0 = 5.757                  # GHz, what a monitors preset's offsets refer to (0 = the band's center)
#
# [farfield] is not in a run's fingerprint (result_file.py): it does not
# change S, a run keeps the frequencies it was made with.

from dataclasses import dataclass

from project.object_types import Param

ACCURACY_CHOICES = ("-30", "-40", "-50", "-60")     # dB, like CST's "Accuracy"
EXCITE_ALL = "all"

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
                Param("trim_pulse", "trim pulse tails", False, boolean=True,
                      tooltip="Start and end the Gaussian pulse where it is 1e-4 of its peak "
                              "(-80 dB in energy, center ± 3 τ) instead of 1e-7 (± 4 τ): "
                              "about a quarter of the pulse length less to compute, the same "
                              "result. Most useful for narrow bands (long pulses)."),
                Param("accuracy", "accuracy", "-40", unit="dB", choices=ACCURACY_CHOICES,
                      tooltip="A port's run stops when the field energy fell this far below "
                              "its peak (after the pulse). -30: fast, S may drift by ~0.01 at "
                              "the band edges; -40: the usual; -50 / -60: slower, for narrow "
                              "bands and resonant structures."),
                Param("excite", "excite", EXCITE_ALL, text=True,
                      tooltip="Which ports are excited, one run each. All ports: the full "
                              "S-matrix. One port: one run (faster), only its column of S "
                              "(e.g. S11, S21); the other ports still absorb as matched loads."),
            ),
            note="padding 0 = auto: λ/4 in air at the center frequency, λ/2 at the lowest "
                 "far-field frequency when the far field is on",
        ),
        Section(
            "mesh", "Mesh",
            params=(
                Param("cells_per_wavelength", "cells per λ", 20.0, minimum=5.0, maximum=200.0),
                Param("cells_per_feature", "cells per feature", 4.0, minimum=1.0, maximum=50.0),
                Param("max_ratio", "max step ratio", 1.3, minimum=1.05, maximum=3.0),
                Param("min_step", "min step (0 = auto)", 0.0, minimum=0.0, unit="mm"),
                Param("slanted", "refine slanted metal", True, boolean=True,
                      tooltip="Slanted metal faces (a 45° trace, a rotated pad, a chamfer) put "
                              "no grid lines: the step along x and y around them is set to "
                              "(the gap or width there) / cells per feature, at least the min "
                              "step. Off: such a trace may join the pour across its gap on "
                              "the grid (see the shorts under Mesh)."),
            ),
            note="lines at body faces; step ≤ λ/N in each material",
        ),
        Section(
            "farfield", "Far field",
            params=(
                Param("enabled", "on", True, boolean=True,
                      tooltip="Off: the run computes no far field (no box, faster with less padding); "
                              "the frequencies stay for later"),
                Param("frequencies", "frequencies", "", minimum=1e-3, unit="GHz", numbers=True,
                      tooltip="The antenna's radiation pattern at these frequencies (comma separated, "
                              "within f min .. f max; empty: off). A box in the air around the model "
                              "collects the fields during the run: keep the model inside the padding."),
                Param("f0", "f0 (0 = band center)", 0.0, minimum=0.0, unit="GHz",
                      tooltip="The design frequency a monitors preset's offsets refer to (right click "
                              "Far field > Apply preset); 0: the center of f min .. f max"),
            ),
        ),
    )
}

NOT_IN_FINGERPRINT = ("farfield",)      # sections that do not change a run's S


def check_settings(settings):
    """Cross-parameter rules; raises ValueError."""
    sim = settings["simulation"]
    if sim["f_max"] <= sim["f_min"]:
        raise ValueError("f max must be greater than f min")
    f0 = settings.get("farfield", {}).get("f0", 0.0)
    if f0 and not sim["f_min"] <= f0 <= sim["f_max"]:
        raise ValueError(f"far-field f0 {f0:g} GHz is outside f min .. f max ({sim['f_min']:g}-{sim['f_max']:g} GHz)")
    outside = [f for f in farfield_frequencies(settings) if not sim["f_min"] <= f <= sim["f_max"]]
    if outside:
        raise ValueError(f"far-field frequencies {', '.join(f'{f:g}' for f in outside)} GHz are outside "
                         f"f min .. f max ({sim['f_min']:g}-{sim['f_max']:g} GHz)")


def farfield_frequencies(settings):
    """The far-field monitor's frequencies in GHz ([] = off: switched off, or none)."""
    if not settings.get("farfield", {}).get("enabled", True):
        return []
    text = settings.get("farfield", {}).get("frequencies", "")
    return [float(part) for part in text.split(",") if part.strip()]


def accuracy_db(settings):
    """The stop level of a run in dB (negative)."""
    return float(settings["simulation"]["accuracy"])


def auto_padding(settings):
    """
    (meters, why) of the auto padding: λ0/2 at the lowest far-field
    frequency when the far field is on (its box in air, away from the near
    field), else λ0/4 at the band's center (the absorber out of the near
    field: enough for S).
    """
    far = farfield_frequencies(settings)
    if far:
        f = min(far)
        return C0 / (f * GHZ) / 2.0, f"λ/2 at {f:g} GHz, far field on"
    sim = settings["simulation"]
    f = 0.5 * (sim["f_min"] + sim["f_max"])
    return C0 / (f * GHZ) / 4.0, f"λ/4 at {f:g} GHz"


def padding_m(settings):
    """Air around the model in meters (0 in the project: auto_padding)."""
    if settings["simulation"]["padding"] > 0:
        return settings["simulation"]["padding"] * MM
    return auto_padding(settings)[0]


def far_field_padding_mm(settings):
    """
    The auto padding in mm when the far field sets it (padding 0, far field
    on), else None: the run fingerprint takes it in then, as the far field
    is otherwise not in it (result_file.fingerprint).
    """
    if settings["simulation"]["padding"] > 0 or not farfield_frequencies(settings):
        return None
    return round(auto_padding(settings)[0] / MM, 6)
