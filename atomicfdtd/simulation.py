# simulation.py
#
# Contract between a time-stepping model and the frontend. Diffusion1D is
# the first model; later ones (2D diffusion, 1D wave / FDTD...) plug in the
# same way.

import math

import numpy as np

from params import Param


class Simulation:
    """
    Life cycle:

        reset()            state = zeros, then + dif, then the set is applied
        apply_dif(name)    state += dif(name)   (any time, e.g. a kick)
        step()             one time step: new state from the old one,
                           then the set is applied
        set_source(name)   choose the set ("feed"): an array of the state's
                           shape, NaN = no value. Wherever it has a value,
                           that value is forced into the state after every
                           step (state[mask] = set[mask]); elsewhere the
                           state is left as computed.

    `state` is a NumPy array of any dimension (1D now); the renderer draws
    it as a heatmap.
    """

    def parameters(self):
        """List of params.Param."""
        return []

    def get_parameter(self, name):
        raise KeyError(name)

    def set_parameter(self, name, value):
        """Live parameters only; restart parameters are passed to __init__."""
        raise KeyError(name)

    def dif_presets(self):
        """Names of the initial perturbations ("dif") this model offers."""
        return []

    def make_dif(self, name):
        """The dif array (same shape as state) for a preset name."""
        raise KeyError(name)

    def apply_dif(self, name):
        """state += make_dif(name)"""
        self.state = self.state + self.make_dif(name)

    # -------------------------------------------------
    # Set ("feed"): values forced into the state on every step
    # -------------------------------------------------

    # Time modulation of the set: forced = A * set * w(n) + offset, where n is
    # the step index and w = 1 ("constant") or sin(2 pi n / period) ("sine").
    FEED_WAVES = ("constant", "sine")
    FEED_DEFAULTS = {"feed_wave": "constant", "feed_period": 20.0,
                     "feed_amplitude": 1.0, "feed_offset": 0.0}

    def _init_feed(self, values):
        self._feed = dict(self.FEED_DEFAULTS)
        for name, value in values.items():
            self.set_feed(name, value)

    def feed_parameters(self):
        return [
            Param("feed_wave", "Waveform", "choice", "constant", choices=self.FEED_WAVES,
                  group="feed", tooltip="constant: the set values as they are\n"
                                        "sine: set * A * sin(2 pi n / period) + offset"),
            Param("feed_period", "Period", "float", 20.0, 1.0, 100000.0, step=1.0,
                  decimals=1, group="feed", tooltip="In steps (sine only)."),
            Param("feed_amplitude", "Amplitude A", "float", 1.0, -1000.0, 1000.0,
                  step=0.1, group="feed"),
            Param("feed_offset", "Offset", "float", 0.0, -1000.0, 1000.0,
                  step=0.1, group="feed", tooltip="Added to the forced cells."),
        ]

    def get_feed(self, name):
        return self._feed[name]

    def set_feed(self, name, value):
        if name not in self.FEED_DEFAULTS:
            raise KeyError(name)
        if name == "feed_wave" and value not in self.FEED_WAVES:
            raise ValueError(f"feed_wave must be one of {self.FEED_WAVES}")
        if name == "feed_period" and float(value) <= 0.0:
            raise ValueError("feed_period must be positive")
        self._feed[name] = type(self.FEED_DEFAULTS[name])(value)

    def feed_factor(self, n):
        """w(n): 1, or sin(2 pi n / period)."""
        if self._feed["feed_wave"] == "sine":
            return math.sin(2.0 * math.pi * n / self._feed["feed_period"])
        return 1.0

    def set_presets(self):
        """Names of the set patterns this model offers ("none" first)."""
        return ["none"]

    def make_set(self, name):
        """Array of the state's shape: a value where it is forced, NaN elsewhere."""
        return np.full(self.state.shape, np.nan)

    def set_source(self, name):
        """Select the set; it is applied right away and after every step."""
        self.set_name = name
        self._set = self.make_set(name)
        self.apply_set()

    def apply_set(self):
        """
        state[mask] = A * set[mask] * w(n) + offset, mask = where the set
        has a value, n = the current step index (0 right after reset, so a
        sine starts at 0).
        """
        values = getattr(self, "_set", None)
        if values is None:
            return
        mask = ~np.isnan(values)
        feed = self._feed
        factor = feed["feed_amplitude"] * self.feed_factor(self.step_index)
        self.state[mask] = values[mask] * factor + feed["feed_offset"]

    def set_mask(self):
        """Boolean array: cells that are forced by the set."""
        values = getattr(self, "_set", None)
        if values is None:
            return np.zeros(self.state.shape, dtype=bool)
        return ~np.isnan(values)

    def reset(self, dif=None):
        raise NotImplementedError

    def step(self):
        raise NotImplementedError

    @property
    def step_index(self):
        raise NotImplementedError

    def diagnostics(self):
        """{label: value} shown in the panel."""
        return {}
