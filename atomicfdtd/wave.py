# wave.py

import math

import numpy as np

from params import Param
from simulation import Simulation


class _WaveBase(Simulation):
    """
    Wave equation u_tt = c^2 lap(u) with the classic leapfrog scheme:

        new = 2 u - u_prev + C^2 * L(u)

    C = c dt / h is the Courant number (cells per step). The state is TWO
    arrays per cell: the current value u and the previous one u_prev - their
    difference is the velocity. That memory of motion (inertia) is what makes
    a front travel instead of spreading out like diffusion. It is the same
    scheme as E / H leapfrog on a 1D Yee grid.

    Boundaries:
        "absorbing"  first-order Mur: the wave leaves (like PML)
        "fixed"      u = 0 outside: reflection with the sign flipped
        "reflect"    no slope at the edge: reflection with the same sign
        "periodic"   ring / torus
    """

    BOUNDARIES = ("absorbing", "fixed", "reflect", "periodic")
    PAD_MODE = {"absorbing": "edge", "fixed": "constant", "reflect": "edge", "periodic": "wrap"}

    DEFAULT_COLOR_LIMITS = "initial"
    DEFAULT_PALETTE = "signed"
    DEFAULT_DIF = "gaussian bump"
    DEFAULT_STEPS_PER_SECOND = 30.0

    BUMP_WIDTH = 2.0   # cells; a smooth bump avoids grid-scale ripples

    # Extra feed for waves: a SOFT source. It never overwrites the cell; it
    # adds a small amount every step, so the cell stays free and passing
    # waves go through it (like the port in pseudo_cst).
    SOFT_PULSE = "gaussian pulse (soft)"
    FEED_WAVES = Simulation.FEED_WAVES + (SOFT_PULSE,)

    def soft_pulse_shape(self, n):
        """G(n): the bump (in time) that the soft pulse emits."""
        tau = self._feed["feed_period"] / 4.0
        n0 = self._feed["feed_at"] + 3.0 * tau
        return math.exp(-((n - n0) / tau) ** 2)

    def feed_span(self):
        if self._feed["feed_wave"] == self.SOFT_PULSE:
            return int(max(30, self._feed["feed_at"] + 1.5 * self._feed["feed_period"] + 10))
        return super().feed_span()

    def feed_curves(self, steps):
        if self._feed["feed_wave"] != self.SOFT_PULSE:
            return super().feed_curves(steps)
        a, c = self._feed["feed_amplitude"], self._values["c"]
        g = np.array([self.soft_pulse_shape(int(n)) for n in steps])
        g_prev = np.array([self.soft_pulse_shape(int(n) - 1) for n in steps])
        added = a * 2.0 * c * (g - g_prev)
        added[np.asarray(steps) == 0] = np.nan       # nothing is added at reset
        return [("emitted bump A*G(n)", a * g), ("added to the cell", added)]

    def apply_set(self):
        """
        Soft pulse: add D(n) = 2 C (G(n) - G(n-1)) * A * set. Adding D to a
        free cell kicks its velocity, and in 1D a kick leaves a plateau of
        height D / (2C) behind the fronts. These kicks sum to zero and their
        plateaus build exactly G: a clean bump of height A runs out both
        ways, with no plateau. Other feeds: forced values, as in diffusion.
        """
        if self._feed["feed_wave"] != self.SOFT_PULSE:
            super().apply_set()
            return

        values = getattr(self, "_set", None)
        if values is None or self.step_index == 0:
            return

        n = self.step_index
        kick = 2.0 * self._values["c"] * (self.soft_pulse_shape(n) - self.soft_pulse_shape(n - 1))
        mask = ~np.isnan(values)
        self.state[mask] += values[mask] * self._feed["feed_amplitude"] * kick

    # -------------------------------------------------

    def _check_common(self, c, boundary):
        if boundary not in self.BOUNDARIES:
            raise ValueError(f"boundary must be one of {self.BOUNDARIES}")
        if float(c) < 0.0:
            raise ValueError("the Courant number must be >= 0")

    def get_parameter(self, name):
        if name.startswith("feed_"):
            return self.get_feed(name)
        if name in self._sizes:
            return self._sizes[name]
        return self._values[name]

    def set_parameter(self, name, value):
        if name.startswith("feed_"):
            self.set_feed(name, value)
            return
        if name not in self._values:
            raise KeyError(name)
        if name == "boundary" and value not in self.BOUNDARIES:
            raise ValueError(f"boundary must be one of {self.BOUNDARIES}")
        self._values[name] = type(self._values[name])(value)

    # -------------------------------------------------

    def _laplacian(self, u):
        """lap(u) with the boundary rule as ghost cells (absorbing: edge copy;
        its edge values are overwritten by the Mur update anyway)."""
        p = np.pad(u, 1, mode=self.PAD_MODE[self._values["boundary"]])
        if u.ndim == 1:
            return p[:-2] + p[2:] - 2.0 * u
        return p[1:-1, :-2] + p[1:-1, 2:] + p[:-2, 1:-1] + p[2:, 1:-1] - 4.0 * u

    def apply_dif(self, name):
        """
        Adds an initial DISPLACEMENT with zero initial velocity: u_prev is set
        so that the first leapfrog step is the exact Taylor step
        u1 = u0 + C^2/2 L(u0).
        """
        dif = self.make_dif(name)
        c2 = self._values["c"] ** 2
        self.state = self.state + dif
        self._prev = self._prev + dif + 0.5 * c2 * self._laplacian(dif)

    def reset(self, dif=None):
        shape = self._shape()
        self.state = np.zeros(shape)
        self._prev = np.zeros(shape)
        self._step_index = 0

        if dif is not None:
            self.apply_dif(dif)

        self.set_source(self.set_name)

    def step(self):
        u, prev = self.state, self._prev
        c = self._values["c"]

        new = 2.0 * u - prev + c * c * self._laplacian(u)

        if self._values["boundary"] == "absorbing":
            self._mur(new, u)

        self._prev, self.state = u, new
        self._step_index += 1
        self.apply_set()

    @property
    def step_index(self):
        return self._step_index

    def diagnostics(self):
        u = self.state
        return {
            "step": self._step_index,
            "max": float(u.max()),
            "min": float(u.min()),
            "C max (stable)": self.C_MAX,
        }


class Wave1D(_WaveBase):
    C_MAX = 1.0

    DIFS = ("gaussian bump", "center peak", "left bump", "two bumps", "none")
    SETS = ("none", "center = 1", "left end = 1", "two points = 1")

    def __init__(self, n=61, c=0.5, boundary="absorbing", seed=None, **feed):
        self._check_common(c, boundary)
        self._sizes = {"n": int(n)}
        if self._sizes["n"] < 3:
            raise ValueError("the wave model needs at least 3 cells")
        self._values = {"c": float(c), "boundary": str(boundary)}
        self._init_feed(feed)
        self.set_name = "none"
        self.reset()

    @property
    def n(self):
        return self._sizes["n"]

    def _shape(self):
        return (self.n,)

    def parameters(self):
        return [
            Param("n", "Cells n", "int", 61, 3, 100000, restart=True,
                  tooltip="Array size. Takes effect on Reset."),
            Param("c", "Courant C", "float", 0.5, 0.0, 1.5, step=0.05,
                  tooltip="Wave speed in cells per step. Stable for C <= 1 (exact at C = 1)."),
            Param("boundary", "Boundary", "choice", "absorbing", choices=self.BOUNDARIES,
                  tooltip="absorbing: Mur, the wave leaves; fixed: flips; reflect: same sign; periodic"),
        ] + self.feed_parameters()

    def _mur(self, new, u):
        k = (self._values["c"] - 1.0) / (self._values["c"] + 1.0)
        new[0] = u[1] + k * (new[1] - u[0])
        new[-1] = u[-2] + k * (new[-2] - u[-1])

    def dif_presets(self):
        return list(self.DIFS)

    def make_dif(self, name):
        n = self.n
        x = np.arange(n)
        bump = lambda x0: np.exp(-((x - x0) / self.BUMP_WIDTH) ** 2)

        if name == "gaussian bump":
            return bump(n // 2)
        if name == "center peak":
            d = np.zeros(n); d[n // 2] = 1.0; return d
        if name == "left bump":
            return bump(n // 6)
        if name == "two bumps":
            return bump(n // 4) + bump((3 * n) // 4)
        if name == "none":
            return np.zeros(n)
        raise KeyError(f"unknown dif {name!r}; available: {self.DIFS}")

    def set_presets(self):
        return list(self.SETS)

    def make_set(self, name):
        n = self.n
        values = np.full(n, np.nan)
        if name == "center = 1":
            values[n // 2] = 1.0
        elif name == "left end = 1":
            values[0] = 1.0
        elif name == "two points = 1":
            values[n // 4] = values[(3 * n) // 4] = 1.0
        elif name != "none":
            raise KeyError(f"unknown set {name!r}; available: {self.SETS}")
        return values


class Wave2D(_WaveBase):
    C_MAX = 1.0 / math.sqrt(2.0)

    # A 2D pulse spreads over a growing ring and fades: stretch the colors.
    DEFAULT_COLOR_LIMITS = "auto"

    DIFS = ("gaussian bump", "center peak", "two bumps", "none")
    SETS = ("none", "center = 1", "two points = 1", "left column = 1")

    def __init__(self, nx=61, ny=61, c=0.5, boundary="absorbing", seed=None, **feed):
        self._check_common(c, boundary)
        self._sizes = {"nx": int(nx), "ny": int(ny)}
        if min(self._sizes.values()) < 3:
            raise ValueError("the wave model needs at least 3 x 3 cells")
        self._values = {"c": float(c), "boundary": str(boundary)}
        self._init_feed(feed)
        self.set_name = "none"
        self.reset()

    @property
    def nx(self):
        return self._sizes["nx"]

    @property
    def ny(self):
        return self._sizes["ny"]

    def _shape(self):
        return (self.ny, self.nx)

    def parameters(self):
        return [
            Param("nx", "Cells nx", "int", 61, 3, 2000, restart=True, tooltip="Takes effect on Reset."),
            Param("ny", "Cells ny", "int", 61, 3, 2000, restart=True, tooltip="Takes effect on Reset."),
            Param("c", "Courant C", "float", 0.5, 0.0, 1.5, step=0.05,
                  tooltip="Wave speed in cells per step. Stable for C <= 1/sqrt(2) = 0.707."),
            Param("boundary", "Boundary", "choice", "absorbing", choices=self.BOUNDARIES,
                  tooltip="absorbing: Mur, the wave leaves; fixed: flips; reflect: same sign; periodic"),
        ] + self.feed_parameters()

    def _mur(self, new, u):
        """First-order Mur on all four edges (exact for normal incidence)."""
        k = (self._values["c"] - 1.0) / (self._values["c"] + 1.0)
        new[0, :] = u[1, :] + k * (new[1, :] - u[0, :])
        new[-1, :] = u[-2, :] + k * (new[-2, :] - u[-1, :])
        new[:, 0] = u[:, 1] + k * (new[:, 1] - u[:, 0])
        new[:, -1] = u[:, -2] + k * (new[:, -2] - u[:, -1])

    def dif_presets(self):
        return list(self.DIFS)

    def make_dif(self, name):
        ny, nx = self.ny, self.nx
        yy, xx = np.mgrid[0:ny, 0:nx]
        bump = lambda y0, x0: np.exp(-(((xx - x0) ** 2 + (yy - y0) ** 2) / self.BUMP_WIDTH ** 2))

        if name == "gaussian bump":
            return bump(ny // 2, nx // 2)
        if name == "center peak":
            d = np.zeros((ny, nx)); d[ny // 2, nx // 2] = 1.0; return d
        if name == "two bumps":
            return bump(ny // 2, nx // 4) + bump(ny // 2, (3 * nx) // 4)
        if name == "none":
            return np.zeros((ny, nx))
        raise KeyError(f"unknown dif {name!r}; available: {self.DIFS}")

    def set_presets(self):
        return list(self.SETS)

    def make_set(self, name):
        ny, nx = self.ny, self.nx
        values = np.full((ny, nx), np.nan)
        if name == "center = 1":
            values[ny // 2, nx // 2] = 1.0
        elif name == "two points = 1":
            values[ny // 2, nx // 4] = values[ny // 2, (3 * nx) // 4] = 1.0
        elif name == "left column = 1":
            values[:, 0] = 1.0
        elif name != "none":
            raise KeyError(f"unknown set {name!r}; available: {self.SETS}")
        return values
