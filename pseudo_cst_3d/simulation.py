# simulation.py

import math

from field import GridArray, GridFieldSource
from params import Parameter  # noqa: F401  (part of the Simulation contract)
from scene_model import PointCharge


class Simulation:
    """
    Contract for anything that evolves a field in time.

    Own solvers (FDTD, ...) and wrappers around external ones (Meep,
    openEMS, recorded results) all plug in through this. The frontend only
    calls step()/reset() and reads source() - it never sees solver internals.
    """

    def parameters(self):
        """List of Parameter."""
        return []

    def get_parameter(self, name):
        raise KeyError(name)

    def set_parameter(self, name, value):
        raise KeyError(name)

    @property
    def t(self):
        """Current simulation time."""
        raise NotImplementedError

    @property
    def dt(self):
        """Time step."""
        raise NotImplementedError

    @property
    def step_index(self):
        raise NotImplementedError

    def step(self, n=1):
        """Advance n time steps."""
        raise NotImplementedError

    def reset(self):
        """Back to t = 0 with the current parameters."""
        raise NotImplementedError

    def source(self):
        """FieldSource describing the CURRENT state."""
        raise NotImplementedError

    def diagnostics(self):
        """
        Optional scalar readouts of the current state, {label: value}
        (total energy, port current, ...). Shown by the frontend as is.
        """
        return {}

    def wire_currents(self):
        """
        Optional: current along conductors (wires and port gaps), for a plot.

        Returns {"axis_label": str, "s": array, "current": array}: s is the
        position of each edge center along the antenna axis, current the
        current along it (signed, + = along the axis). None if not applicable.
        """
        return None

    def overlays(self):
        """
        Optional regions the frontend should mark, e.g. absorbing layers
        that are not physical space:
            {"kind": "frame", "label": str,
             "outer": (x0, y0, x1, y1), "inner": (x0, y0, x1, y1)}
        """
        return []


class OscillatingDipoleSimulation(Simulation):
    """
    Quasi-static demo for the time pipeline (not real electrodynamics):

        q(t) = +/- q0 * cos(2 pi t / period)

    The field is the static solution scaled by cos(...): no propagation,
    no retardation. Real waves arrive with FDTD. This exists to exercise
    the frontend: stepping, per-frame grid uploads, a field that passes
    through zero (clim, arrow threshold) and charge icons that flip sign.

    The scene charges are updated in place, so the scene model always
    reflects the current state.
    """

    def __init__(self, scene_model, base_source, period=4.0, steps_per_period=240):
        self.scene_model = scene_model
        self.base_source = base_source

        # Charges of the scene at t = 0 (amplitude and sign).
        self._charges = scene_model.get_objects(PointCharge)
        self._base_charges = [c.charge for c in self._charges]

        self._values = {
            "period": float(period),
            "steps_per_period": int(steps_per_period),
            "amplitude": 1.0,
        }

        self.reset()

    # -------------------------------------------------
    # Parameters
    # -------------------------------------------------

    def parameters(self):
        return [
            Parameter("period", "Period", "float", 4.0, 0.1, 100.0, unit="s"),
            Parameter("steps_per_period", "Steps per period", "int", 240, 8, 10000),
            Parameter("amplitude", "Amplitude", "float", 1.0, 0.0, 10.0),
        ]

    def get_parameter(self, name):
        return self._values[name]

    def set_parameter(self, name, value):
        if name not in self._values:
            raise KeyError(name)

        self._values[name] = type(self._values[name])(value)
        self._update_state()

    # -------------------------------------------------
    # Time
    # -------------------------------------------------

    @property
    def dt(self):
        return self._values["period"] / self._values["steps_per_period"]

    @property
    def t(self):
        return self._step_index * self.dt

    @property
    def step_index(self):
        return self._step_index

    def step(self, n=1):
        self._step_index += int(n)
        self._update_state()

    def reset(self):
        self._step_index = 0
        self._update_state()

    def source(self):
        return self._source

    # -------------------------------------------------

    def _update_state(self):
        factor = self._values["amplitude"] * math.cos(
            2.0 * math.pi * self.t / self._values["period"]
        )

        for charge, base_charge in zip(self._charges, self._base_charges):
            charge.charge = base_charge * factor

        base = self.base_source

        def scaled(name, f):
            a = base.arrays[name]
            return GridArray(a.values * f, a.x0, a.y0, a.dx, a.dy)

        # E and V are linear in q; |E| scales with |q|.
        self._source = GridFieldSource(
            quantities=base.quantities(),
            arrays={
                "E": scaled("E", factor),
                "|E|": scaled("|E|", abs(factor)),
                "V": scaled("V", factor),
            },
            derived=base.derived,
        )
