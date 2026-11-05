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
#   sweep_points = 21           # more frequencies evenly over f min .. f max, both ends in (0 = none)
#   f0 = 5.757                  # GHz, what a monitors preset's offsets refer to (0 = the band's center)
#
# [farfield] is not in a run's fingerprint (result_file.py): it does not
# change S, a run keeps the frequencies it was made with.
#
# The far field is computed at the listed frequencies (named: the ones
# looked at in the pattern) and at the sweep's (D, G, realized G,
# efficiency against frequency; solver/farfield.py sweep()). A sweep point
# within SAME_GHZ of a listed one is left out. Each costs little in the run
# (a DFT on the box's faces every few hundred steps) but a few seconds
# after it (the radiation integral) and ~12 MB of memory on a 7 M cell
# model; the run file grows by ~0.5 MB per frequency and excited port.

from dataclasses import dataclass

import numpy as np

from project.object_types import Param

ACCURACY_CHOICES = ("-30", "-40", "-50", "-60")     # dB, like CST's "Accuracy"
EXCITE_ALL = "all"
MAX_SWEEP_POINTS = 201
SAME_GHZ = 1e-3             # a sweep frequency this close to a listed one is that one

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
                Param("sweep_points", "sweep points", 0.0, minimum=0.0, maximum=MAX_SWEEP_POINTS,
                      tooltip="The far field also at this many frequencies evenly over f min .. f max "
                              "(both ends in), for the Far field vs f tab: D, G, realized gain and "
                              "efficiency against frequency. 0: only the listed frequencies. Each "
                              "costs a few seconds after the run, nearly nothing during it. At the "
                              "band's edges the pulse is 20 dB down: there the far field needs "
                              "accuracy -60 (at -40 / -50 it is off by up to ~10 %)."),
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
    points = settings.get("farfield", {}).get("sweep_points", 0.0)
    if points != int(points) or points == 1:
        raise ValueError(f"sweep points must be a whole number, 0 or 2..{MAX_SWEEP_POINTS} (got {points:g})")
    outside = [f for f in farfield_named(settings) if not sim["f_min"] <= f <= sim["f_max"]]
    if outside:
        raise ValueError(f"far-field frequencies {', '.join(f'{f:g}' for f in outside)} GHz are outside "
                         f"f min .. f max ({sim['f_min']:g}-{sim['f_max']:g} GHz)")


def farfield_named(settings):
    """The listed far-field frequencies in GHz ([] = switched off, or none listed)."""
    if not settings.get("farfield", {}).get("enabled", True):
        return []
    text = settings.get("farfield", {}).get("frequencies", "")
    return [float(part) for part in text.split(",") if part.strip()]


def farfield_sweep(settings):
    """
    GHz: the sweep's frequencies, `sweep_points` evenly over f min .. f max
    (rounded to 0.1 MHz), without those within SAME_GHZ of a listed one;
    [] when the far field is off or the sweep is 0.
    """
    far = settings.get("farfield", {})
    points = int(far.get("sweep_points", 0) or 0)
    if not far.get("enabled", True) or points < 2:
        return []
    sim = settings["simulation"]
    named = farfield_named(settings)
    return [f for f in (round(float(x), 4) for x in np.linspace(sim["f_min"], sim["f_max"], points))
            if all(abs(f - n) >= SAME_GHZ for n in named)]


def merge_frequencies(named, sweep):
    """(GHz sorted, named mask) of the listed and the sweep frequencies together."""
    pairs = sorted([(float(f), True) for f in named] + [(float(f), False) for f in sweep])
    return [f for f, _ in pairs], [n for _, n in pairs]


def farfield_frequencies(settings):
    """Every far-field frequency in GHz, listed and sweep ([] = off: switched off, or none)."""
    return merge_frequencies(farfield_named(settings), farfield_sweep(settings))[0]


def accuracy_db(settings):
    """The stop level of a run in dB (negative)."""
    return float(settings["simulation"]["accuracy"])


def auto_padding(settings):
    """
    (meters, why) of the auto padding: λ0/2 at the lowest listed far-field
    frequency when the far field is on (its box in air, away from the near
    field; with a sweep alone the band's lowest), else λ0/4 at the band's
    center (the absorber out of the near field: enough for S). A sweep
    added to listed frequencies keeps their padding: the box is a closed
    surface, exact at any distance; λ/2 at the listed ones is ~0.4 λ at the
    band's lower edge, still clear of the near field.
    """
    far = farfield_named(settings) or farfield_sweep(settings)
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
