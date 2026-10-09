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
    #   "pulse (hard)": forced to A * set only at step `feed_at`, free otherwise
    FEED_WAVES = ("constant", "sine", "pulse (hard)")
    FEED_DEFAULTS = {"feed_wave": "constant", "feed_period": 20.0,
                     "feed_amplitude": 1.0, "feed_offset": 0.0, "feed_at": 1}

    def _init_feed(self, values):
        self._feed = dict(self.FEED_DEFAULTS)
        for name, value in values.items():
            self.set_feed(name, value)

    def feed_parameters(self):
        return [
            Param("feed_wave", "Waveform", "choice", "constant", choices=self.FEED_WAVES,
                  group="feed", tooltip="constant: the set values as they are\n"
                                        "sine: set * A * sin(2 pi n / period) + offset\n"
                                        "pulse (hard): set * A once, at step 'At step'; "
                                        "free before and after\n"
                                        "gaussian pulse (soft, waves only): adds a bump of "
                                        "height A that runs away; the cell stays free"),
            Param("feed_period", "Period", "float", 20.0, 1.0, 100000.0, step=1.0,
                  decimals=1, group="feed",
                  tooltip="In steps. sine: the period; soft pulse: its duration (width = period / 4)."),
            Param("feed_amplitude", "Amplitude A", "float", 1.0, -1000.0, 1000.0,
                  step=0.1, group="feed"),
            Param("feed_offset", "Offset", "float", 0.0, -1000.0, 1000.0,
                  step=0.1, group="feed", tooltip="Added to the forced cells."),
            Param("feed_at", "At step", "int", 1, 1, 1000000, group="feed",
                  tooltip="pulses: the step at which the pulse starts"),
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
        """w(n): 1, sin(2 pi n / period), or for a pulse 1 / None (= not forced)."""
        wave = self._feed["feed_wave"]
        if wave == "sine":
            return math.sin(2.0 * math.pi * n / self._feed["feed_period"])
        if wave == "pulse (hard)":
            return 1.0 if n == self._feed["feed_at"] else None
        return 1.0

    def feed_span(self):
        """How many steps the feed plot shows: enough to see one whole event."""
        feed, wave = self._feed, self._feed["feed_wave"]
        if wave == "sine":
            return int(max(30, 3 * feed["feed_period"]))
        if wave == "constant":
            return 30
        return int(max(30, feed["feed_at"] + feed["feed_period"] + 10))

    def feed_window(self):
        """Step range for the feed plot; it scrolls to keep the current step in view."""
        span = self.feed_span()
        start = max(0, self.step_index - int(0.75 * span))
        return np.arange(start, start + span + 1)

    def feed_curves(self, steps):
        """
        [(label, values)] for the feed plot, per set value 1. NaN = the cell
        is free at that step (not forced).
        """
        feed = self._feed
        values = np.full(len(steps), np.nan)
        for i, n in enumerate(steps):
            w = self.feed_factor(int(n))
            if w is not None:
                values[i] = feed["feed_amplitude"] * w + feed["feed_offset"]
        return [("forced value", values)]

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
        w = self.feed_factor(self.step_index)
        if w is None:
            return  # a pulse outside its step: the cells are free

        mask = ~np.isnan(values)
        feed = self._feed
        self.state[mask] = values[mask] * feed["feed_amplitude"] * w + feed["feed_offset"]

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
