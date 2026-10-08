# fdtd3d.py

import math

import numpy as np

from field3d import Field3D, GridArray3D
from params import Parameter
from scene3d import BoxRegion, Port3D, SphereRegion
from scene_model import Wire
from simulation import Simulation
from waveforms import WAVEFORMS, waveform


class FdtdYee3D(Simulation):
    """
    3D FDTD on a Yee grid. Normalized units: c = eps0 = mu0 = 1.

        dH/dt     = -curl E
        eps dE/dt =  curl H - J - sigma E

    Cubic cells h, nx x ny x nz cells; arrays indexed [k, j, i] (z, y, x):
        Ex (nz+1, ny+1, nx  ) at (i+1/2, j,     k    )
        Ey (nz+1, ny,   nx+1) at (i,     j+1/2, k    )
        Ez (nz,   ny+1, nx+1) at (i,     j,     k+1/2)
        Hx (nz,   ny,   nx+1) at (i,     j+1/2, k+1/2)
        Hy (nz,   ny+1, nx  ) at (i+1/2, j,     k+1/2)
        Hz (nz+1, ny,   nx  ) at (i+1/2, j+1/2, k    )

    Same building blocks as the 2D solver: per-edge material coefficients
    (Ca, Cb; PEC -> 0), CPML strips on all six faces (PEC behind them),
    soft-source ports along a 6-connected staircase of edges.
    Stability: Courant number c dt / h <= 1 / sqrt(3).
    """

    WAVEFORMS = WAVEFORMS
    RESTART_PARAMETERS = ("courant", "pml_cells")
    MAX_COURANT = 1.0 / math.sqrt(3.0)

    PML_GRADING = 3
    PML_SIGMA_FACTOR = 0.8
    PML_ALPHA_MAX = 0.05

    def __init__(
        self,
        scene_model,
        bounds,
        cell_size,
        courant=0.5,
        waveform="sine",
        frequency=1.0,
        amplitude=1.0,
        pml_cells=10,
    ):
        self.scene_model = scene_model

        h = float(cell_size)
        lo = np.asarray(bounds[0], dtype=np.float64)
        size = np.asarray(bounds[1], dtype=np.float64) - lo
        self.nx, self.ny, self.nz = (max(4, int(round(s / h))) for s in size)

        self.h = h
        self.lo = lo
        self.hi = lo + h * np.array([self.nx, self.ny, self.nz], dtype=np.float64)

        self._values = {
            "courant": float(courant),
            "waveform": str(waveform),
            "frequency": float(frequency),
            "amplitude": float(amplitude),
            "pml_cells": int(pml_cells),
        }
        self._validate()

        self._rasterize_materials()
        self._rasterize_ports()
        self.reset()

    @property
    def bounds(self):
        return tuple(self.lo), tuple(self.hi)

    @property
    def cell_count(self):
        return self.nx * self.ny * self.nz

    # =====================================================
    # Parameters
    # =====================================================

    def parameters(self):
        return [
            Parameter("waveform", "Waveform", "choice", "sine", choices=self.WAVEFORMS),
            Parameter("frequency", "Frequency", "float", 1.0, 0.05, 10.0, unit="c/length"),
            Parameter("amplitude", "Amplitude", "float", 1.0, 0.0, 100.0),
            Parameter("courant", "Courant number", "float", 0.5, 0.05, self.MAX_COURANT),
            Parameter("pml_cells", "PML thickness", "int", 10, 2, 40, unit="cells"),
        ]

    def get_parameter(self, name):
        return self._values[name]

    def set_parameter(self, name, value):
        if name not in self._values:
            raise KeyError(name)

        old = dict(self._values)
        self._values[name] = type(self._values[name])(value)

        try:
            self._validate()
        except ValueError:
            self._values = old
            raise

        if name in self.RESTART_PARAMETERS:
            self.reset()

    def _validate(self):
        v = self._values

        if not 0.0 < v["courant"] <= self.MAX_COURANT:
            raise ValueError(f"Courant number must be in (0, {self.MAX_COURANT:.4f}] in 3D")
        if v["waveform"] not in self.WAVEFORMS:
            raise ValueError(f"waveform must be one of {self.WAVEFORMS}")
        if v["frequency"] <= 0.0:
            raise ValueError("frequency must be positive")
        if not (1 <= v["pml_cells"] and 2 * v["pml_cells"] + 4 <= min(self.nx, self.ny, self.nz)):
            raise ValueError("PML is too thick for this domain")

    @property
    def cells_per_wavelength(self):
        return (1.0 / self._values["frequency"]) / self.h

    @property
    def min_cells_per_wavelength(self):
        return self.cells_per_wavelength / math.sqrt(self._eps_max)

    # =====================================================
    # Time
    # =====================================================

    @property
    def dt(self):
        return self._values["courant"] * self.h

    @property
    def t(self):
        return self._step_index * self.dt

    @property
    def step_index(self):
        return self._step_index

    def reset(self):
        f32 = np.float32
        nx, ny, nz = self.nx, self.ny, self.nz

        self.ex = np.zeros((nz + 1, ny + 1, nx), dtype=f32)
        self.ey = np.zeros((nz + 1, ny, nx + 1), dtype=f32)
        self.ez = np.zeros((nz, ny + 1, nx + 1), dtype=f32)
        self.hx = np.zeros((nz, ny, nx + 1), dtype=f32)
        self.hy = np.zeros((nz, ny + 1, nx), dtype=f32)
        self.hz = np.zeros((nz + 1, ny, nx), dtype=f32)

        self._build_update_coefficients()

        self._step_index = 0
        self._last_current = 0.0
        self._field_cache = None

    def step(self, n=1):
        for _ in range(int(n)):
            self._step_once()

        self._field_cache = None

    def _step_once(self):
        ex, ey, ez = self.ex, self.ey, self.ez
        hx, hy, hz = self.hx, self.hy, self.hz
        ih = np.float32(1.0 / self.h)
        dt = np.float32(self.dt)

        # ---------------- H: n -> n + 1/2      dH/dt = -curl E
        # Hx: -(dEz/dy - dEy/dz)
        d1 = (ez[:, 1:, :] - ez[:, :-1, :]) * ih
        d2 = (ey[1:, :, :] - ey[:-1, :, :]) * ih
        self._apply_cpml(d1, self._cp["hx_y"])
        self._apply_cpml(d2, self._cp["hx_z"])
        hx -= dt * (d1 - d2)

        # Hy: -(dEx/dz - dEz/dx)
        d1 = (ex[1:, :, :] - ex[:-1, :, :]) * ih
        d2 = (ez[:, :, 1:] - ez[:, :, :-1]) * ih
        self._apply_cpml(d1, self._cp["hy_z"])
        self._apply_cpml(d2, self._cp["hy_x"])
        hy -= dt * (d1 - d2)

        # Hz: -(dEy/dx - dEx/dy)
        d1 = (ey[:, :, 1:] - ey[:, :, :-1]) * ih
        d2 = (ex[:, 1:, :] - ex[:, :-1, :]) * ih
        self._apply_cpml(d1, self._cp["hz_x"])
        self._apply_cpml(d2, self._cp["hz_y"])
        hz -= dt * (d1 - d2)

        # ---------------- E: n -> n + 1 (interior edges; outer faces PEC)
        # Ex: dHz/dy - dHy/dz
        d1 = (hz[1:-1, 1:, :] - hz[1:-1, :-1, :]) * ih
        d2 = (hy[1:, 1:-1, :] - hy[:-1, 1:-1, :]) * ih
        self._apply_cpml(d1, self._cp["ex_y"])
        self._apply_cpml(d2, self._cp["ex_z"])
        e = ex[1:-1, 1:-1, :]
        e *= self._ca["ex"]
        e += self._cb["ex"] * (d1 - d2)

        # Ey: dHx/dz - dHz/dx
        d1 = (hx[1:, :, 1:-1] - hx[:-1, :, 1:-1]) * ih
        d2 = (hz[1:-1, :, 1:] - hz[1:-1, :, :-1]) * ih
        self._apply_cpml(d1, self._cp["ey_z"])
        self._apply_cpml(d2, self._cp["ey_x"])
        e = ey[1:-1, :, 1:-1]
        e *= self._ca["ey"]
        e += self._cb["ey"] * (d1 - d2)

        # Ez: dHy/dx - dHx/dy
        d1 = (hy[:, 1:-1, 1:] - hy[:, 1:-1, :-1]) * ih
        d2 = (hx[:, 1:, 1:-1] - hx[:, :-1, 1:-1]) * ih
        self._apply_cpml(d1, self._cp["ez_x"])
        self._apply_cpml(d2, self._cp["ez_y"])
        e = ez[:, 1:-1, 1:-1]
        e *= self._ca["ez"]
        e += self._cb["ez"] * (d1 - d2)

        # ---------------- Port currents at n + 1/2 (E -= Cb J, J = I / h^2)
        v = self._values
        current = waveform(v["waveform"], (self._step_index + 0.5) * self.dt, v["frequency"], v["amplitude"])
        self._last_current = current

        if current != 0.0:
            j = current / self.h ** 2
            for field, (kk, jj, ii, coef) in ((ex, self._port["x"]), (ey, self._port["y"]), (ez, self._port["z"])):
                if len(kk):
                    field[kk, jj, ii] -= j * coef

        self._step_index += 1

    # =====================================================
    # Materials and coefficients
    # =====================================================

    def _positions(self, offsets, shape):
        """Meshgrid (x, y, z) of a component with half-cell offsets (ox, oy, oz)."""
        h = self.h
        nz, ny, nx = shape
        x = self.lo[0] + (np.arange(nx) + offsets[0]) * h
        y = self.lo[1] + (np.arange(ny) + offsets[1]) * h
        z = self.lo[2] + (np.arange(nz) + offsets[2]) * h
        zz, yy, xx = np.meshgrid(z, y, x, indexing="ij")
        return xx, yy, zz

    def _material_maps(self, xx, yy, zz):
        eps = np.ones(xx.shape)
        sigma = np.zeros(xx.shape)
        pec = np.zeros(xx.shape, dtype=bool)

        for region in self.scene_model.objects:
            if isinstance(region, (BoxRegion, SphereRegion)):
                inside = region.contains(xx, yy, zz)
                m = region.material
                eps[inside] = m.eps_r
                sigma[inside] = m.sigma
                pec[inside] = m.pec

        return eps, sigma, pec

    # Half-cell offsets of each component (x, y, z) and its array shape.
    def _layout(self):
        nx, ny, nz = self.nx, self.ny, self.nz
        return {
            "ex": ((0.5, 0.0, 0.0), (nz + 1, ny + 1, nx)),
            "ey": ((0.0, 0.5, 0.0), (nz + 1, ny, nx + 1)),
            "ez": ((0.0, 0.0, 0.5), (nz, ny + 1, nx + 1)),
            "hx": ((0.0, 0.5, 0.5), (nz, ny, nx + 1)),
            "hy": ((0.5, 0.0, 0.5), (nz, ny + 1, nx)),
            "hz": ((0.5, 0.5, 0.0), (nz + 1, ny, nx)),
        }

    def _rasterize_materials(self):
        layout = self._layout()
        self._mat = {}

        for name in ("ex", "ey", "ez"):
            offsets, shape = layout[name]
            self._mat[name] = self._material_maps(*self._positions(offsets, shape))

        offsets, shape = (0.5, 0.5, 0.5), (self.nz, self.ny, self.nx)
        eps_c, _, pec_c = self._material_maps(*self._positions(offsets, shape))
        self._eps_c = np.where(pec_c, 0.0, eps_c)
        self._eps_max = float(max(self._mat[n][0].max() for n in ("ex", "ey", "ez")))

        # Thin wires: the edges along their staircase become PEC.
        for wire in self.scene_model.get_objects(Wire):
            for axis, k, j, i, _ in self._port_edges(wire):
                self._mat["e" + axis][2][k, j, i] = True

    def _build_update_coefficients(self):
        dt = self.dt
        interior = {
            "ex": (slice(1, -1), slice(1, -1), slice(None)),
            "ey": (slice(1, -1), slice(None), slice(1, -1)),
            "ez": (slice(None), slice(1, -1), slice(1, -1)),
        }

        self._ca, self._cb, cb_full = {}, {}, {}

        for name in ("ex", "ey", "ez"):
            eps, sigma, pec = self._mat[name]
            loss = sigma * dt / (2.0 * eps)
            ca = (1.0 - loss) / (1.0 + loss)
            cb = (dt / eps) / (1.0 + loss)
            ca[pec] = 0.0
            cb[pec] = 0.0
            self._ca[name] = ca[interior[name]].astype(np.float32)
            self._cb[name] = cb[interior[name]].astype(np.float32)
            cb_full[name] = cb

        # Port edges: E -= Cb J (Cb includes dt / eps of the local material).
        self._port = {}
        for axis, name in (("x", "ex"), ("y", "ey"), ("z", "ez")):
            kk, jj, ii, signs = self._port_idx[axis]
            coef = (cb_full[name][kk, jj, ii] * signs).astype(np.float32) if len(kk) else np.zeros(0, np.float32)
            self._port[axis] = (kk, jj, ii, coef)

            if len(kk) and np.any(self._mat[name][2][kk, jj, ii]):
                raise ValueError("a port lies on metal (PEC) edges; leave a gap for the port")

        self._build_cpml()

    # =====================================================
    # CPML
    # =====================================================

    def _axis_profile(self, coords, axis):
        dt, h = self.dt, self.h
        t = self._values["pml_cells"] * h
        a_ = "xyz".index(axis)
        lo, hi = self.lo[a_] + t, self.hi[a_] - t

        depth = np.maximum(np.maximum(lo - coords, coords - hi), 0.0) / t
        m = self.PML_GRADING
        sigma = self.PML_SIGMA_FACTOR * (m + 1) / h * depth ** m
        alpha = np.where(depth > 0.0, self.PML_ALPHA_MAX * (1.0 - depth), 0.0)

        b = np.exp(-(sigma + alpha) * dt)
        with np.errstate(divide="ignore", invalid="ignore"):
            a = np.where(sigma > 0.0, sigma / (sigma + alpha) * (b - 1.0), 0.0)
        return a.astype(np.float32), b.astype(np.float32)

    def _slabs(self, axis, offset, interior, shape):
        """
        CPML strips for a derivative along `axis` (array axis 2/1/0 for
        x/y/z). offset: 0.5 for cell-centered positions, 0 for nodes;
        interior: drop the outermost nodes (E-update derivatives).
        """
        a_ = "xyz".index(axis)
        n = (self.nx, self.ny, self.nz)[a_]
        idx = np.arange(1, n) if interior else np.arange(n)
        coords = self.lo[a_] + (idx + offset) * self.h

        a, b = self._axis_profile(coords, axis)
        inside = np.nonzero(a != 0.0)[0]
        array_axis = 2 - a_
        slabs = []

        for part in (inside[inside < 0.5 * len(coords)], inside[inside >= 0.5 * len(coords)]):
            if part.size == 0:
                continue
            sl = slice(int(part[0]), int(part[-1]) + 1)
            bshape = [1, 1, 1]
            bshape[array_axis] = sl.stop - sl.start
            pshape = list(shape)
            pshape[array_axis] = sl.stop - sl.start
            index = [slice(None)] * 3
            index[array_axis] = sl
            slabs.append((tuple(index), a[sl].reshape(bshape), b[sl].reshape(bshape),
                          np.zeros(pshape, dtype=np.float32)))
        return slabs

    def _build_cpml(self):
        nx, ny, nz = self.nx, self.ny, self.nz
        self._cp = {
            # H updates: derivatives at cell-centered positions along their axis.
            "hx_y": self._slabs("y", 0.5, False, (nz, ny, nx + 1)),
            "hx_z": self._slabs("z", 0.5, False, (nz, ny, nx + 1)),
            "hy_z": self._slabs("z", 0.5, False, (nz, ny + 1, nx)),
            "hy_x": self._slabs("x", 0.5, False, (nz, ny + 1, nx)),
            "hz_x": self._slabs("x", 0.5, False, (nz + 1, ny, nx)),
            "hz_y": self._slabs("y", 0.5, False, (nz + 1, ny, nx)),
            # E updates: derivatives at interior nodes along their axis.
            "ex_y": self._slabs("y", 0.0, True, (nz - 1, ny - 1, nx)),
            "ex_z": self._slabs("z", 0.0, True, (nz - 1, ny - 1, nx)),
            "ey_z": self._slabs("z", 0.0, True, (nz - 1, ny, nx - 1)),
            "ey_x": self._slabs("x", 0.0, True, (nz - 1, ny, nx - 1)),
            "ez_x": self._slabs("x", 0.0, True, (nz, ny - 1, nx - 1)),
            "ez_y": self._slabs("y", 0.0, True, (nz, ny - 1, nx - 1)),
        }

    @staticmethod
    def _apply_cpml(derivative, slabs):
        for index, a, b, psi in slabs:
            d = derivative[index]
            psi *= b
            psi += a * d
            d += psi

    # =====================================================
    # Ports
    # =====================================================

    def _rasterize_ports(self):
        idx = {axis: ([], [], [], []) for axis in "xyz"}

        for port in self.scene_model.objects:
            if isinstance(port, Port3D):
                for axis, k, j, i, sign in self._port_edges(port):
                    for lst, val in zip(idx[axis], (k, j, i, sign)):
                        lst.append(val)

        self._port_idx = {
            axis: (np.array(k, np.int64), np.array(j, np.int64), np.array(i, np.int64), np.array(s, np.float64))
            for axis, (k, j, i, s) in idx.items()
        }

    def _port_edges(self, port):
        """6-connected staircase of edges between the nearest nodes of a and b."""
        start = [int(round((port.a[a] - self.lo[a]) / self.h)) for a in range(3)]
        end = [int(round((port.b[a] - self.lo[a]) / self.h)) for a in range(3)]
        sizes = (self.nx, self.ny, self.nz)

        for p in (start, end):
            if not all(1 <= p[a] <= sizes[a] - 1 for a in range(3)):
                raise ValueError(f"{type(port).__name__} {port.name!r} must lie inside the domain")
        if start == end:
            raise ValueError(f"{type(port).__name__} {port.name!r} is shorter than one cell")

        counts = [abs(end[a] - start[a]) for a in range(3)]
        signs = [1 if end[a] > start[a] else -1 for a in range(3)]
        taken = [0, 0, 0]
        pos = list(start)

        for _ in range(sum(counts)):
            # Advance along the axis that is furthest behind the straight line.
            axis = min(
                (a for a in range(3) if taken[a] < counts[a]),
                key=lambda a: (taken[a] + 0.5) / counts[a],
            )
            lo_node = pos[axis] if signs[axis] > 0 else pos[axis] - 1
            i, j, k = pos
            if axis == 0:
                yield "x", k, j, lo_node, float(signs[0])
            elif axis == 1:
                yield "y", k, lo_node, i, float(signs[1])
            else:
                yield "z", lo_node, j, i, float(signs[2])
            pos[axis] += signs[axis]
            taken[axis] += 1

    # =====================================================
    # Diagnostics, overlays, field
    # =====================================================

    def energy(self):
        h3 = self.h ** 3
        we = sum(
            np.sum(self._mat[n][0] * getattr(self, n).astype(np.float64) ** 2)
            for n in ("ex", "ey", "ez")
        )
        wh = sum(np.sum(getattr(self, n).astype(np.float64) ** 2) for n in ("hx", "hy", "hz"))
        return 0.5 * h3 * (we + wh)

    def port_power(self):
        e_along = 0.0
        for axis, name in (("x", "ex"), ("y", "ey"), ("z", "ez")):
            kk, jj, ii, signs = self._port_idx[axis]
            if len(kk):
                e_along += float(np.sum(signs * getattr(self, name)[kk, jj, ii]))
        return -self._last_current * self.h * e_along

    def diagnostics(self):
        return {"W": self.energy(), "I port": self._last_current, "P port": self.port_power()}

    def overlays_3d(self):
        t = self._values["pml_cells"] * self.h
        return [{
            "kind": "frame3d",
            "label": "PML",
            "outer": (tuple(self.lo), tuple(self.hi)),
            "inner": (tuple(self.lo + t), tuple(self.hi - t)),
        }]

    def field(self):
        """Current state as a Field3D snapshot (cached until the next step)."""
        if self._field_cache is None:
            self._field_cache = self._build_field()
        return self._field_cache

    def _build_field(self):
        h, half_dt = self.h, 0.5 * self.dt
        ex, ey, ez = (getattr(self, n).astype(np.float64) for n in ("ex", "ey", "ez"))
        hx, hy, hz = (getattr(self, n).astype(np.float64) for n in ("hx", "hy", "hz"))

        # H is half a step behind E (leapfrog): H(n) = H(n-1/2) - dt/2 curl E.
        # (Inside the PML this ignores psi - display only.)
        hx = hx - half_dt * ((ez[:, 1:, :] - ez[:, :-1, :]) - (ey[1:, :, :] - ey[:-1, :, :])) / h
        hy = hy - half_dt * ((ex[1:, :, :] - ex[:-1, :, :]) - (ez[:, :, 1:] - ez[:, :, :-1])) / h
        hz = hz - half_dt * ((ey[:, :, 1:] - ey[:, :, :-1]) - (ex[:, 1:, :] - ex[:, :-1, :])) / h

        # div E at interior nodes (exact Yee differences): charge density.
        div_e = (
            (ex[1:-1, 1:-1, 1:] - ex[1:-1, 1:-1, :-1])
            + (ey[1:-1, 1:, 1:-1] - ey[1:-1, :-1, 1:-1])
            + (ez[1:, 1:-1, 1:-1] - ez[:-1, 1:-1, 1:-1])
        ) / h

        lo = self.lo
        layout = self._layout()

        def grid(values, offsets):
            return GridArray3D(values, tuple(lo + np.asarray(offsets) * h), h)

        arrays = {
            "Ex": grid(ex, layout["ex"][0]),
            "Ey": grid(ey, layout["ey"][0]),
            "Ez": grid(ez, layout["ez"][0]),
            "Hx": grid(hx, layout["hx"][0]),
            "Hy": grid(hy, layout["hy"][0]),
            "Hz": grid(hz, layout["hz"][0]),
            "eps_r": grid(self._eps_c, (0.5, 0.5, 0.5)),
            "div E": grid(div_e, (1.0, 1.0, 1.0)),
        }

        return Field3D(
            arrays,
            vectors={"E": ("Ex", "Ey", "Ez"), "H": ("Hx", "Hy", "Hz"), "S": ("Sx", "Sy", "Sz")},
            bounds=self.bounds,
        )

    def source(self):
        raise NotImplementedError(
            "A 3D simulation is viewed through slicing.SliceSource(sim.field(), plane)"
        )
