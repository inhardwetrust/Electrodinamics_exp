# fdtd.py

import math

import numpy as np

from domain import SimulationDomain2D
from field import GridArray, GridFieldSource, Quantity
from mesh import RectangularMesh2D
from params import Parameter
from scene_model import DiskRegion, Port, RectRegion
from simulation import Simulation
from waveforms import WAVEFORMS, waveform


class FdtdTE2D(Simulation):
    """
    2D FDTD, TE polarization (Ex, Ey in the plane, Hz out of plane).

    Normalized units: c = eps0 = mu0 = 1, so wavelength = 1 / frequency
    in vacuum and the wave impedance is 1.

        dHz/dt     = -(dEy/dx - dEx/dy)
        eps dEx/dt =  dHz/dy - Jx - sigma Ex
        eps dEy/dt = -dHz/dx - Jy - sigma Ey

    Yee grid, square cells of size h, nx x ny cells:
        Hz  at cell centers      (i + 1/2, j + 1/2) h    shape (ny,     nx)
        Ex  on horizontal edges  (i + 1/2, j      ) h    shape (ny + 1, nx)
        Ey  on vertical edges    (i,       j + 1/2) h    shape (ny,     nx + 1)
    Leapfrog in time: E at integer steps, H (and J) at half steps.

    Materials (RectRegion / DiskRegion in the scene) are sampled at the
    center of every E edge (staircase boundaries):
        E <- Ca E + Cb (curl H - J)
        Ca = (1 - s) / (1 + s),  Cb = (dt / eps) / (1 + s),  s = sigma dt / (2 eps)
        PEC edges: Ca = Cb = 0 (tangential E stays 0).

    Boundary:
        "pml" -> convolutional PML (CPML) in the outer pml_cells of the
                 domain, polynomial grading, small CFS alpha; the outermost
                 edges are PEC behind it
        "mur" -> first-order Mur (cheap; reflects a few % at oblique incidence)

    Ports: current driven along the segment a -> b, rasterized into a
    4-connected staircase of grid edges. Soft source: the current is added
    to the update, so the port itself is transparent to passing waves.

    Arrays are float32 and updated in place: half the memory traffic of
    float64 and no temporaries, which is what limits a NumPy FDTD.
    """

    QUANTITIES = (
        Quantity("E", "vector", label="Electric field"),
        Quantity("|E|", "scalar", signed=False, label="|E|"),
        Quantity("Ex", "scalar", signed=True, label="Ex"),
        Quantity("Ey", "scalar", signed=True, label="Ey"),
        Quantity("Hz", "scalar", signed=True, label="Hz"),

        # Derived on the Yee grid (see _build_source for where each lives).
        Quantity("S", "vector", label="Poynting vector E x H"),
        Quantity("|S|", "scalar", signed=False, label="|S|"),
        Quantity("u", "scalar", signed=False, label="Energy density"),
        Quantity("div E", "scalar", signed=True, label="Total charge (free + bound)"),
        Quantity("div D", "scalar", signed=True, label="Free charge density"),
        Quantity("curl E", "scalar", signed=True, label="(curl E)z = -dHz/dt"),
        Quantity("eps_r", "scalar", signed=False, label="Relative permittivity"),
    )

    WAVEFORMS = WAVEFORMS
    BOUNDARIES = ("pml", "mur")

    # Changing these rebuilds the update coefficients: restart from t = 0.
    RESTART_PARAMETERS = ("courant", "boundary", "pml_cells")

    # Courant number S = c dt / h; 2D stability needs S <= 1/sqrt(2).
    MAX_COURANT = 1.0 / math.sqrt(2.0)

    # CPML profile: sigma(d) = sigma_max (d / L)^m, alpha(d) = alpha_max (1 - d / L)
    PML_GRADING = 3
    PML_SIGMA_FACTOR = 0.8      # sigma_max = factor (m + 1) / (eta h), eta = 1
    PML_ALPHA_MAX = 0.05        # CFS term: absorbs slowly varying fields too

    def __init__(
        self,
        scene_model,
        domain,
        cell_size,
        courant=0.5,
        waveform="sine",
        frequency=1.0,
        amplitude=1.0,
        boundary="pml",
        pml_cells=12,
    ):
        self.scene_model = scene_model

        h = float(cell_size)
        nx = max(2, int(round(domain.width / h)))
        ny = max(2, int(round(domain.height / h)))

        # Square cells: the domain is adjusted to a whole number of cells.
        self.domain = SimulationDomain2D(
            x_min=domain.x_min,
            x_max=domain.x_min + nx * h,
            y_min=domain.y_min,
            y_max=domain.y_min + ny * h,
        )
        self.mesh = RectangularMesh2D(self.domain, nx, ny)
        self.h = h
        self.nx, self.ny = nx, ny

        self._values = {
            "courant": float(courant),
            "waveform": str(waveform),
            "frequency": float(frequency),
            "amplitude": float(amplitude),
            "boundary": str(boundary),
            "pml_cells": int(pml_cells),
        }
        self._validate()

        self._rasterize_materials()
        self._rasterize_ports()
        self.reset()

    # =====================================================
    # Parameters
    # =====================================================

    def parameters(self):
        return [
            Parameter(
                "waveform", "Waveform", "choice", "sine",
                choices=self.WAVEFORMS,
            ),
            Parameter(
                "frequency", "Frequency", "float", 1.0, 0.05, 10.0,
                unit="c/length",
            ),
            Parameter("amplitude", "Amplitude", "float", 1.0, 0.0, 100.0),
            Parameter(
                "courant", "Courant number", "float", 0.5, 0.05,
                self.MAX_COURANT,
            ),
            Parameter(
                "boundary", "Boundary", "choice", "pml",
                choices=self.BOUNDARIES,
            ),
            Parameter(
                "pml_cells", "PML thickness", "int", 12, 2, 64, unit="cells",
                enabled_when=("boundary", ("pml",)),
            ),
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
            raise ValueError(
                f"Courant number must be in (0, {self.MAX_COURANT:.4f}]"
            )
        if v["waveform"] not in self.WAVEFORMS:
            raise ValueError(f"waveform must be one of {self.WAVEFORMS}")
        if v["frequency"] <= 0.0:
            raise ValueError("frequency must be positive")
        if v["boundary"] not in self.BOUNDARIES:
            raise ValueError(f"boundary must be one of {self.BOUNDARIES}")
        if v["boundary"] == "pml" and not (
            1 <= v["pml_cells"] and 2 * v["pml_cells"] + 4 <= min(self.nx, self.ny)
        ):
            raise ValueError("PML is too thick for this domain")

    @property
    def cells_per_wavelength(self):
        """In vacuum."""
        return (1.0 / self._values["frequency"]) / self.h

    @property
    def min_cells_per_wavelength(self):
        """In the densest material (wavelength shrinks by sqrt(eps_r))."""
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
        nx, ny = self.nx, self.ny

        self.ex = np.zeros((ny + 1, nx), dtype=f32)
        self.ey = np.zeros((ny, nx + 1), dtype=f32)
        self.hz = np.zeros((ny, nx), dtype=f32)

        self._build_update_coefficients()

        self._step_index = 0
        self._source_cache = None
        self._last_current = 0.0

    def step(self, n=1):
        for _ in range(int(n)):
            self._step_once()

        self._source_cache = None

    def _step_once(self):
        ex, ey, hz = self.ex, self.ey, self.hz
        dt = self.dt
        inv_h = np.float32(1.0 / self.h)
        pml = self._pml

        # --- H: n -> n + 1/2
        dey_dx = np.subtract(ey[:, 1:], ey[:, :-1], out=self._tmp_c1)
        dey_dx *= inv_h
        dex_dy = np.subtract(ex[1:, :], ex[:-1, :], out=self._tmp_c2)
        dex_dy *= inv_h

        if pml:
            self._apply_cpml(dey_dx, self._slabs_hx, "x")
            self._apply_cpml(dex_dy, self._slabs_hy, "y")

        dey_dx -= dex_dy
        dey_dx *= np.float32(dt)
        hz -= dey_dx

        if not pml:
            # Boundary E at time n, needed by the Mur condition.
            ex_b0, ex_b1 = ex[0, :].copy(), ex[1, :].copy()
            ex_t0, ex_t1 = ex[-1, :].copy(), ex[-2, :].copy()
            ey_l0, ey_l1 = ey[:, 0].copy(), ey[:, 1].copy()
            ey_r0, ey_r1 = ey[:, -1].copy(), ey[:, -2].copy()

        # --- E: n -> n + 1 (interior edges; outermost edges stay PEC / Mur)
        dhz_dy = np.subtract(hz[1:, :], hz[:-1, :], out=self._tmp_ex)
        dhz_dy *= inv_h
        if pml:
            self._apply_cpml(dhz_dy, self._slabs_ey, "y")

        ex_in = ex[1:-1, :]
        ex_in *= self._ca_ex
        dhz_dy *= self._cb_ex
        ex_in += dhz_dy

        dhz_dx = np.subtract(hz[:, 1:], hz[:, :-1], out=self._tmp_ey)
        dhz_dx *= inv_h
        if pml:
            self._apply_cpml(dhz_dx, self._slabs_ex, "x")

        ey_in = ey[:, 1:-1]
        ey_in *= self._ca_ey
        dhz_dx *= self._cb_ey
        ey_in -= dhz_dx

        # --- Port currents at n + 1/2 (soft source: E -= Cb J, J = I / h)
        current = self._waveform((self._step_index + 0.5) * dt)
        self._last_current = current

        if current != 0.0:
            j = current / self.h
            ex[self._ex_rows, self._ex_cols] -= j * self._port_ex_k
            ey[self._ey_rows, self._ey_cols] -= j * self._port_ey_k

        # --- Mur first-order absorbing boundary (c = 1 at the boundary)
        if not pml:
            h = self.h
            m = (dt - h) / (dt + h)
            ex[0, :] = ex_b1 + m * (ex[1, :] - ex_b0)
            ex[-1, :] = ex_t1 + m * (ex[-2, :] - ex_t0)
            ey[:, 0] = ey_l1 + m * (ey[:, 1] - ey_l0)
            ey[:, -1] = ey_r1 + m * (ey[:, -2] - ey_r0)

        self._step_index += 1

    def _waveform(self, t):
        v = self._values
        return waveform(v["waveform"], t, v["frequency"], v["amplitude"])

    # =====================================================
    # Materials and update coefficients
    # =====================================================

    def _material_maps(self, x, y):
        """eps_r, sigma, pec sampled at positions x, y (arrays, same shape)."""
        eps = np.ones(x.shape, dtype=np.float64)
        sigma = np.zeros(x.shape, dtype=np.float64)
        pec = np.zeros(x.shape, dtype=bool)

        regions = [
            obj for obj in self.scene_model.objects
            if isinstance(obj, (RectRegion, DiskRegion))
        ]

        # Later objects win where regions overlap.
        for region in regions:
            inside = region.contains(x, y)
            m = region.material
            eps[inside] = m.eps_r
            sigma[inside] = m.sigma
            pec[inside] = m.pec

        return eps, sigma, pec

    def _rasterize_materials(self):
        h = self.h
        x0, y0 = self.domain.x_min, self.domain.y_min

        # Edge centers.
        ex_x, ex_y = np.meshgrid(
            x0 + (np.arange(self.nx) + 0.5) * h,
            y0 + np.arange(self.ny + 1) * h,
        )
        ey_x, ey_y = np.meshgrid(
            x0 + np.arange(self.nx + 1) * h,
            y0 + (np.arange(self.ny) + 0.5) * h,
        )
        c_x, c_y = np.meshgrid(
            x0 + (np.arange(self.nx) + 0.5) * h,
            y0 + (np.arange(self.ny) + 0.5) * h,
        )

        self._eps_ex, self._sig_ex, self._pec_ex = self._material_maps(ex_x, ex_y)
        self._eps_ey, self._sig_ey, self._pec_ey = self._material_maps(ey_x, ey_y)
        eps_c, _, pec_c = self._material_maps(c_x, c_y)

        # eps_r map shows PEC as 0 (no meaningful permittivity).
        self._eps_c = np.where(pec_c, 0.0, eps_c)
        self._eps_max = float(max(self._eps_ex.max(), self._eps_ey.max()))

    @staticmethod
    def _edge_coefficients(eps, sigma, pec, dt):
        loss = sigma * dt / (2.0 * eps)
        ca = (1.0 - loss) / (1.0 + loss)
        cb = (dt / eps) / (1.0 + loss)
        ca[pec] = 0.0
        cb[pec] = 0.0
        return ca.astype(np.float32), cb.astype(np.float32)

    def _axis_profile(self, coords, axis):
        """
        CPML recursion coefficients (a, b) at positions `coords` along one
        axis: psi <- b psi + a dF/dx. a = 0 outside the PML (psi stays 0).
        """
        dt, h = self.dt, self.h
        thickness = self._values["pml_cells"] * h

        if axis == "x":
            lo, hi = self.domain.x_min + thickness, self.domain.x_max - thickness
        else:
            lo, hi = self.domain.y_min + thickness, self.domain.y_max - thickness

        depth = np.maximum(np.maximum(lo - coords, coords - hi), 0.0) / thickness

        m = self.PML_GRADING
        sigma_max = self.PML_SIGMA_FACTOR * (m + 1) / h
        sigma = sigma_max * depth ** m
        alpha = np.where(depth > 0.0, self.PML_ALPHA_MAX * (1.0 - depth), 0.0)

        b = np.exp(-(sigma + alpha) * dt)
        with np.errstate(divide="ignore", invalid="ignore"):
            a = np.where(sigma > 0.0, sigma / (sigma + alpha) * (b - 1.0), 0.0)

        return a.astype(np.float32), b.astype(np.float32)

    def _build_update_coefficients(self):
        dt, h = self.dt, self.h
        nx, ny = self.nx, self.ny
        x0, y0 = self.domain.x_min, self.domain.y_min
        f32 = np.float32

        ca_ex, cb_ex = self._edge_coefficients(self._eps_ex, self._sig_ex, self._pec_ex, dt)
        ca_ey, cb_ey = self._edge_coefficients(self._eps_ey, self._sig_ey, self._pec_ey, dt)

        # Interior edges only (outermost edges: PEC behind the PML / Mur).
        self._ca_ex, self._cb_ex = ca_ex[1:-1, :], cb_ex[1:-1, :]
        self._ca_ey, self._cb_ey = ca_ey[:, 1:-1], cb_ey[:, 1:-1]

        # Port edges: E -= Cb J  (Cb includes dt / eps of the local material).
        self._port_ex_k = (cb_ex[self._ex_rows, self._ex_cols] * self._ex_signs).astype(f32)
        self._port_ey_k = (cb_ey[self._ey_rows, self._ey_cols] * self._ey_signs).astype(f32)

        # Scratch buffers for in-place updates.
        self._tmp_c1 = np.empty((ny, nx), dtype=f32)
        self._tmp_c2 = np.empty((ny, nx), dtype=f32)
        self._tmp_ex = np.empty((ny - 1, nx), dtype=f32)
        self._tmp_ey = np.empty((ny, nx - 1), dtype=f32)

        self._pml = self._values["boundary"] == "pml"

        if self._pml:
            x_c = x0 + (np.arange(nx) + 0.5) * h      # Hz columns
            y_c = y0 + (np.arange(ny) + 0.5) * h      # Hz rows
            x_n = x0 + np.arange(1, nx) * h           # interior Ey columns
            y_n = y0 + np.arange(1, ny) * h           # interior Ex rows

            # Each derivative gets CPML strips along its own axis:
            #   dEy/dx at Hz columns, dEx/dy at Hz rows,
            #   dHz/dy at interior Ex rows, dHz/dx at interior Ey columns.
            self._slabs_hx = self._pml_slabs(x_c, "x", ny)
            self._slabs_hy = self._pml_slabs(y_c, "y", nx)
            self._slabs_ey = self._pml_slabs(y_n, "y", nx)
            self._slabs_ex = self._pml_slabs(x_n, "x", ny)

    def _pml_slabs(self, coords, axis, other_len):
        """
        The two PML strips (low and high side) along one axis:
        [(slice, a, b, psi)]. psi only exists where the PML is, so the
        CPML costs ~ (PML area), not the whole grid.
        """
        a, b = self._axis_profile(coords, axis)
        inside = np.nonzero(a != 0.0)[0]
        middle = 0.5 * len(coords)
        slabs = []

        for part in (inside[inside < middle], inside[inside >= middle]):
            if part.size == 0:
                continue

            sl = slice(int(part[0]), int(part[-1]) + 1)
            n = sl.stop - sl.start

            if axis == "x":
                slabs.append((sl, a[sl], b[sl], np.zeros((other_len, n), dtype=np.float32)))
            else:
                slabs.append((sl, a[sl, None], b[sl, None], np.zeros((n, other_len), dtype=np.float32)))

        return slabs

    @staticmethod
    def _apply_cpml(derivative, slabs, axis):
        """In place: psi <- b psi + a dF; dF <- dF + psi (inside the strips)."""
        for sl, a, b, psi in slabs:
            d = derivative[:, sl] if axis == "x" else derivative[sl, :]
            psi *= b
            psi += a * d
            d += psi

    # =====================================================
    # Ports
    # =====================================================

    def _rasterize_ports(self):
        ex_r, ex_c, ex_s = [], [], []
        ey_r, ey_c, ey_s = [], [], []

        for port in self.scene_model.get_objects(Port):
            for kind, row, col, sign in self._port_edges(port):
                if kind == "x":
                    ex_r.append(row); ex_c.append(col); ex_s.append(sign)
                else:
                    ey_r.append(row); ey_c.append(col); ey_s.append(sign)

        self._ex_rows = np.array(ex_r, dtype=np.int64)
        self._ex_cols = np.array(ex_c, dtype=np.int64)
        self._ex_signs = np.array(ex_s, dtype=np.float64)
        self._ey_rows = np.array(ey_r, dtype=np.int64)
        self._ey_cols = np.array(ey_c, dtype=np.int64)
        self._ey_signs = np.array(ey_s, dtype=np.float64)

    def _port_edges(self, port):
        """
        Grid edges along a -> b: 4-connected staircase between the nearest
        nodes, staying as close as possible to the straight segment.
        Yields (kind, row, col, sign): kind "x" -> Ex edge, "y" -> Ey edge.
        """
        d = self.domain
        i0 = int(round((port.a[0] - d.x_min) / self.h))
        j0 = int(round((port.a[1] - d.y_min) / self.h))
        i1 = int(round((port.b[0] - d.x_min) / self.h))
        j1 = int(round((port.b[1] - d.y_min) / self.h))

        for i, j in ((i0, j0), (i1, j1)):
            if not (1 <= i <= self.nx - 1 and 1 <= j <= self.ny - 1):
                raise ValueError(
                    f"Port {port.name!r} must lie inside the domain "
                    "(away from the absorbing boundary)"
                )

        if (i0, j0) == (i1, j1):
            raise ValueError(f"Port {port.name!r} is shorter than one cell")

        n_i, n_j = abs(i1 - i0), abs(j1 - j0)
        di = 1 if i1 > i0 else -1
        dj = 1 if j1 > j0 else -1

        i, j = i0, j0
        steps_i = steps_j = 0

        for _ in range(n_i + n_j):
            move_x = n_j == 0 or (
                n_i > 0 and (steps_i + 0.5) / n_i <= (steps_j + 0.5) / n_j
            )

            if move_x:
                yield "x", j, min(i, i + di), float(di)
                i += di
                steps_i += 1
            else:
                yield "y", min(j, j + dj), i, float(dj)
                j += dj
                steps_j += 1

    # =====================================================
    # Diagnostics and overlays
    # =====================================================

    def energy(self):
        """Total field energy per unit length in z: 1/2 sum(eps E^2 + H^2) h^2."""
        ex = self.ex.astype(np.float64)
        ey = self.ey.astype(np.float64)
        hz = self.hz.astype(np.float64)

        return 0.5 * self.h ** 2 * (
            np.sum(self._eps_ex * ex ** 2)
            + np.sum(self._eps_ey * ey ** 2)
            + np.sum(hz ** 2)
        )

    def port_power(self):
        """
        Power delivered by the ports to the field: P = -integral(J . E) dA.
        J = I / h on each port edge with its direction sign.
        """
        e_along = (
            np.sum(self._ex_signs * self.ex[self._ex_rows, self._ex_cols])
            + np.sum(self._ey_signs * self.ey[self._ey_rows, self._ey_cols])
        )
        return -self._last_current * self.h * float(e_along)

    def diagnostics(self):
        return {
            "W": self.energy(),
            "I port": self._last_current,
            "P port": self.port_power(),
        }

    def overlays(self):
        """Regions the frontend should mark (the PML is not physical space)."""
        if self._values["boundary"] != "pml":
            return []

        d = self.domain
        t = self._values["pml_cells"] * self.h

        return [{
            "kind": "frame",
            "label": "PML",
            "outer": (d.x_min, d.y_min, d.x_max, d.y_max),
            "inner": (d.x_min + t, d.y_min + t, d.x_max - t, d.y_max - t),
        }]

    # =====================================================
    # Field source
    # =====================================================

    def source(self):
        if self._source_cache is None:
            self._source_cache = self._build_source()

        return self._source_cache

    def _build_source(self):
        h = self.h
        x0, y0 = self.domain.x_min, self.domain.y_min

        ex = self.ex.astype(np.float64)
        ey = self.ey.astype(np.float64)
        hz = self.hz.astype(np.float64)
        f32 = np.float32

        # (curl E)z at cell centers: exact Yee differences, = -dHz/dt.
        curl_e = (
            (ey[:, 1:] - ey[:, :-1])
            - (ex[1:, :] - ex[:-1, :])
        ) / h

        # Hz lives half a step behind E (leapfrog). Bring it to E's time
        # for products of E and H (Poynting, energy): H(n) = H(n-1/2) - dt/2 curl E.
        # (Inside the PML this ignores the psi terms - display only.)
        hz_sync = hz - 0.5 * self.dt * curl_e

        # div E and div D at interior NODES: exact Yee differences.
        # div D = free charge; div E additionally shows the BOUND charge at
        # dielectric interfaces.
        div_e = (
            (ex[1:-1, 1:] - ex[1:-1, :-1])
            + (ey[1:, 1:-1] - ey[:-1, 1:-1])
        ) / h

        d_x = self._eps_ex * ex
        d_y = self._eps_ey * ey
        div_d = (
            (d_x[1:-1, 1:] - d_x[1:-1, :-1])
            + (d_y[1:, 1:-1] - d_y[:-1, 1:-1])
        ) / h

        # E at cell centers (average of the two surrounding edges).
        ex_c = 0.5 * (ex[:-1, :] + ex[1:, :])
        ey_c = 0.5 * (ey[:, :-1] + ey[:, 1:])

        # Electric energy per cell from the edges (eps per edge).
        we_x = 0.5 * (self._eps_ex[:-1, :] * ex[:-1, :] ** 2 + self._eps_ex[1:, :] * ex[1:, :] ** 2)
        we_y = 0.5 * (self._eps_ey[:, :-1] * ey[:, :-1] ** 2 + self._eps_ey[:, 1:] * ey[:, 1:] ** 2)

        # Poynting S = E x (Hz z) = (Ey Hz, -Ex Hz); energy density u.
        sx = ey_c * hz_sync
        sy = -ex_c * hz_sync
        u = 0.5 * (we_x + we_y + hz_sync ** 2)

        centers = dict(x0=x0 + 0.5 * h, y0=y0 + 0.5 * h, dx=h, dy=h)
        nodes = dict(x0=x0 + h, y0=y0 + h, dx=h, dy=h)

        # astype() copies: the source is a snapshot, later steps must not
        # mutate it.
        return GridFieldSource(
            quantities=self.QUANTITIES,
            arrays={
                "Ex": GridArray(ex.astype(f32), x0=x0 + 0.5 * h, y0=y0, dx=h, dy=h),
                "Ey": GridArray(ey.astype(f32), x0=x0, y0=y0 + 0.5 * h, dx=h, dy=h),
                "Hz": GridArray(hz_sync.astype(f32), **centers),
                "|E|": GridArray(np.sqrt(ex_c ** 2 + ey_c ** 2).astype(f32), **centers),
                "Sx": GridArray(sx.astype(f32), **centers),
                "Sy": GridArray(sy.astype(f32), **centers),
                "|S|": GridArray(np.sqrt(sx ** 2 + sy ** 2).astype(f32), **centers),
                "u": GridArray(u.astype(f32), **centers),
                "div E": GridArray(div_e.astype(f32), **nodes),
                "div D": GridArray(div_d.astype(f32), **nodes),
                "curl E": GridArray(curl_e.astype(f32), **centers),
                "eps_r": GridArray(self._eps_c.astype(f32), **centers),
            },
            vectors={"E": ("Ex", "Ey"), "S": ("Sx", "Sy")},
        )
