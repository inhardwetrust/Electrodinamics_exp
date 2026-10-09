# diffusion.py

import numpy as np

from params import Param
from simulation import Simulation


class Diffusion1D(Simulation):
    """
    Local diffusion on a 1D array of n cells (explicit scheme):

        new[i] = old[i] + D * (old[i-1] - 2 old[i] + old[i+1])

    Every new value depends only on the old value and its two neighbours.
    The new array is written into a second buffer; then the buffers swap
    (old <- new). D = 0.5 gives new[i] = (old[i-1] + old[i+1]) / 2.

    Stability: the scheme is stable for D <= 0.5. Above that the high
    frequencies grow every step (the same kind of limit as the Courant
    number in FDTD) - the UI allows it on purpose, to see it happen.

    Boundaries (what the missing neighbour outside the array is):
        "fixed"    0 outside: heat leaks out through the ends
        "reflect"  copy of the edge cell: no flux, the sum is conserved
        "periodic" the other end: a ring
    """

    BOUNDARIES = ("fixed", "reflect", "periodic")

    # 1D: a peak decays slowly, fixed colors show the decay nicely.
    DEFAULT_COLOR_LIMITS = "initial"

    DIFS = ("center peak", "left peak", "two peaks", "step (left half)", "random", "none")

    def __init__(self, n=7, d=0.25, boundary="fixed", seed=None, **feed):
        self.n = int(n)
        if self.n < 1:
            raise ValueError("the array needs at least one cell")

        self._values = {"d": float(d), "boundary": str(boundary)}
        if self._values["boundary"] not in self.BOUNDARIES:
            raise ValueError(f"boundary must be one of {self.BOUNDARIES}")

        self._rng = np.random.default_rng(seed)
        self._init_feed(feed)
        self.set_name = "none"
        self.reset()

    # -------------------------------------------------
    # Parameters
    # -------------------------------------------------

    def parameters(self):
        return [
            Param("n", "Cells n", "int", 7, 1, 100000, restart=True,
                  tooltip="Array size. Takes effect on Reset."),
            Param("d", "Diffusion D", "float", 0.25, 0.0, 1.0, step=0.05,
                  tooltip="new = old + D (left - 2 old + right). Stable for D <= 0.5."),
            Param("boundary", "Boundary", "choice", "fixed", choices=self.BOUNDARIES,
                  tooltip="fixed: 0 outside; reflect: no flux; periodic: ring"),
        ] + self.feed_parameters()

    def get_parameter(self, name):
        if name.startswith("feed_"):
            return self.get_feed(name)
        if name == "n":
            return self.n
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
    # Initial state ("dif")
    # -------------------------------------------------

    SETS = ("none", "center = 1", "left end = 1", "left = 1, right = 0", "two points = 1")

    def set_presets(self):
        return list(self.SETS)

    def make_set(self, name):
        n = self.n
        values = np.full(n, np.nan)

        if name == "center = 1":
            values[n // 2] = 1.0
        elif name == "left end = 1":
            values[0] = 1.0
        elif name == "left = 1, right = 0":
            values[0], values[-1] = 1.0, 0.0
        elif name == "two points = 1":
            values[n // 4] = values[(3 * n) // 4] = 1.0
        elif name != "none":
            raise KeyError(f"unknown set {name!r}; available: {self.SETS}")

        return values

    def dif_presets(self):
        return list(self.DIFS)

    def make_dif(self, name):
        n = self.n
        dif = np.zeros(n)

        if name == "center peak":
            dif[n // 2] = 1.0
        elif name == "left peak":
            dif[0] = 1.0
        elif name == "two peaks":
            dif[n // 4] = 1.0
            dif[(3 * n) // 4] = 1.0
        elif name == "step (left half)":
            dif[: max(1, n // 2)] = 1.0
        elif name == "random":
            dif = self._rng.random(n)
        elif name != "none":
            raise KeyError(f"unknown dif {name!r}; available: {self.DIFS}")

        return dif

    def reset(self, dif=None):
        """All zeros, then (optionally) the dif is added."""
        self.state = np.zeros(self.n)
        self._next = np.empty(self.n)
        self._step_index = 0

        if dif is not None:
            self.apply_dif(dif)

        # The set follows the (possibly new) shape and wins over the dif.
        self.set_source(self.set_name)

    # -------------------------------------------------
    # Time step
    # -------------------------------------------------

    def _neighbours(self, u):
        """(left, right): the old values to the left / right of every cell."""
        left = np.empty_like(u)
        right = np.empty_like(u)
        left[1:] = u[:-1]
        right[:-1] = u[1:]

        boundary = self._values["boundary"]
        if boundary == "fixed":
            left[0] = right[-1] = 0.0
        elif boundary == "reflect":
            left[0], right[-1] = u[0], u[-1]
        else:  # periodic
            left[0], right[-1] = u[-1], u[0]

        return left, right

    def step(self):
        old = self.state
        left, right = self._neighbours(old)

        # Write the whole new array from the OLD one only...
        new = self._next
        np.multiply(old, -2.0, out=new)
        new += left
        new += right
        new *= self._values["d"]
        new += old

        # ...then replace the old array with it (swap the two buffers).
        self.state, self._next = new, old

        # Feed: forced values replace the computed ones under the mask.
        self._step_index += 1
        self.apply_set()

    @property
    def step_index(self):
        return self._step_index

    def diagnostics(self):
        u = self.state
        return {
            "step": self._step_index,
            "sum": float(u.sum()),
            "max": float(u.max()),
            "min": float(u.min()),
        }


class Diffusion2D(Simulation):
    """
    Local diffusion on an ny x nx grid:

        new = old + D * L(old)
        L = a * (sum of the 4 face neighbours   - 4 old)
          + b * (sum of the 4 diagonal neighbours - 4 old)

    Stencils (normalized to the same spreading rate, 2a + 4b = 2, so
    D means the same thing in all of them and in 1D):

        "5-point (faces)"        a = 1,   b = 0     the standard Laplacian;
                                                    only cells sharing a face
                                                    exchange (like Yee FDTD)
        "9-point isotropic"      a = 2/3, b = 1/6   diagonals at 1/4 of the face
                                                    weight: removes the leading
                                                    direction-dependent error
        "9-point equal weights"  a = b = 1/3        naive: diagonals like faces

    Stability limit D_max = 2 / max|symbol|: 0.25, 0.375 and 0.5.
    """

    BOUNDARIES = Diffusion1D.BOUNDARIES

    # 2D: a peak drops ~ 1/t; with fixed colors it would go black quickly.
    DEFAULT_COLOR_LIMITS = "auto"

    STENCILS = {
        "5-point (faces)": (1.0, 0.0),
        "9-point isotropic": (2.0 / 3.0, 1.0 / 6.0),
        "9-point equal weights": (1.0 / 3.0, 1.0 / 3.0),
    }
    DIFS = ("center peak", "corner peak", "two peaks", "horizontal line",
            "square block", "random", "none")
    PAD_MODE = {"fixed": "constant", "reflect": "edge", "periodic": "wrap"}

    def __init__(self, nx=7, ny=7, d=0.2, boundary="reflect",
                 stencil="5-point (faces)", seed=None, **feed):
        self.nx, self.ny = int(nx), int(ny)
        if self.nx < 1 or self.ny < 1:
            raise ValueError("the grid needs at least one cell")

        self._values = {"d": float(d), "boundary": str(boundary), "stencil": str(stencil)}
        if boundary not in self.BOUNDARIES:
            raise ValueError(f"boundary must be one of {self.BOUNDARIES}")
        if stencil not in self.STENCILS:
            raise ValueError(f"stencil must be one of {tuple(self.STENCILS)}")

        self._rng = np.random.default_rng(seed)
        self._init_feed(feed)
        self.set_name = "none"
        self.reset()

    # -------------------------------------------------

    def parameters(self):
        return [
            Param("nx", "Cells nx", "int", 7, 1, 2000, restart=True,
                  tooltip="Columns. Takes effect on Reset."),
            Param("ny", "Cells ny", "int", 7, 1, 2000, restart=True,
                  tooltip="Rows. Takes effect on Reset."),
            Param("d", "Diffusion D", "float", 0.2, 0.0, 1.0, step=0.05,
                  tooltip="new = old + D L(old). Stable up to 'D max' (see State)."),
            Param("stencil", "Neighbours", "choice", "5-point (faces)",
                  choices=tuple(self.STENCILS),
                  tooltip="Which neighbours exchange, and with which weights."),
            Param("boundary", "Boundary", "choice", "reflect", choices=self.BOUNDARIES,
                  tooltip="fixed: 0 outside; reflect: no flux; periodic: torus"),
        ] + self.feed_parameters()

    def get_parameter(self, name):
        if name.startswith("feed_"):
            return self.get_feed(name)
        if name == "nx":
            return self.nx
        if name == "ny":
            return self.ny
        return self._values[name]

    def set_parameter(self, name, value):
        if name.startswith("feed_"):
            self.set_feed(name, value)
            return
        if name not in self._values:
            raise KeyError(name)
        if name == "boundary" and value not in self.BOUNDARIES:
            raise ValueError(f"boundary must be one of {self.BOUNDARIES}")
        if name == "stencil" and value not in self.STENCILS:
            raise ValueError(f"stencil must be one of {tuple(self.STENCILS)}")
        self._values[name] = type(self._values[name])(value)

    @property
    def d_max(self):
        """Stability limit: 2 / max |symbol| over the grid modes."""
        a, b = self.STENCILS[self._values["stencil"]]
        return 2.0 / max(8.0 * a, 4.0 * a + 8.0 * b)

    # -------------------------------------------------

    SETS = ("none", "center = 1", "corner = 1", "left column = 1",
            "left = 1, right = 0", "center = 1, edges = 0")

    def set_presets(self):
        return list(self.SETS)

    def make_set(self, name):
        ny, nx = self.ny, self.nx
        values = np.full((ny, nx), np.nan)

        if name == "center = 1":
            values[ny // 2, nx // 2] = 1.0
        elif name == "corner = 1":
            values[0, 0] = 1.0
        elif name == "left column = 1":
            values[:, 0] = 1.0
        elif name == "left = 1, right = 0":
            values[:, 0], values[:, -1] = 1.0, 0.0
        elif name == "center = 1, edges = 0":
            values[0, :] = values[-1, :] = 0.0
            values[:, 0] = values[:, -1] = 0.0
            values[ny // 2, nx // 2] = 1.0
        elif name != "none":
            raise KeyError(f"unknown set {name!r}; available: {self.SETS}")

        return values

    def dif_presets(self):
        return list(self.DIFS)

    def make_dif(self, name):
        ny, nx = self.ny, self.nx
        dif = np.zeros((ny, nx))
        cy, cx = ny // 2, nx // 2

        if name == "center peak":
            dif[cy, cx] = 1.0
        elif name == "corner peak":
            dif[0, 0] = 1.0
        elif name == "two peaks":
            dif[cy, nx // 4] = 1.0
            dif[cy, (3 * nx) // 4] = 1.0
        elif name == "horizontal line":
            dif[cy, :] = 1.0
        elif name == "square block":
            r = max(1, min(nx, ny) // 6)
            dif[max(0, cy - r):cy + r + 1, max(0, cx - r):cx + r + 1] = 1.0
        elif name == "random":
            dif = self._rng.random((ny, nx))
        elif name != "none":
            raise KeyError(f"unknown dif {name!r}; available: {self.DIFS}")

        return dif

    def reset(self, dif=None):
        self.state = np.zeros((self.ny, self.nx))
        self._next = np.empty((self.ny, self.nx))
        self._step_index = 0

        if dif is not None:
            self.apply_dif(dif)

        # The set follows the (possibly new) shape and wins over the dif.
        self.set_source(self.set_name)

    # -------------------------------------------------

    def step(self):
        old = self.state
        a, b = self.STENCILS[self._values["stencil"]]

        # One ring of ghost cells around the grid, filled by the boundary rule.
        p = np.pad(old, 1, mode=self.PAD_MODE[self._values["boundary"]])

        faces = p[1:-1, :-2] + p[1:-1, 2:] + p[:-2, 1:-1] + p[2:, 1:-1]

        new = self._next
        np.multiply(faces - 4.0 * old, a, out=new)

        if b:
            diagonals = p[:-2, :-2] + p[:-2, 2:] + p[2:, :-2] + p[2:, 2:]
            new += b * (diagonals - 4.0 * old)

        new *= self._values["d"]
        new += old

        self.state, self._next = new, old

        # Feed: forced values replace the computed ones under the mask.
        self._step_index += 1
        self.apply_set()

    @property
    def step_index(self):
        return self._step_index

    def diagnostics(self):
        u = self.state
        return {
            "step": self._step_index,
            "sum": float(u.sum()),
            "max": float(u.max()),
            "min": float(u.min()),
            "D max (stable)": self.d_max,
        }
