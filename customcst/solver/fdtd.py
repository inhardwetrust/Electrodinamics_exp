# fdtd.py
#
# Level 4: 3D FDTD on the non-uniform rectilinear grid, SI units.
#
#   mu dH/dt          = -curl E
#   eps dE/dt + sig E =  curl H - J_port
#
# Arrays are [i, j, k] (see discretize/materials.py for the edge layout).
# Steps: primary step d = x[i+1] - x[i] (E-edge lengths, used by curl E),
# dual step dd = (d[i-1] + d[i]) / 2 at interior nodes (H-edge lengths, used
# by curl H). The outer faces of the extended grid are PEC (tangential E = 0).
#
# Absorbing boundary: CPML, `pml_cells` uniform cells added OUTSIDE the
# meshed domain on all six sides (step = the outermost mesh step); material
# arrays are continued into it (edge replication), so a substrate or a ground
# plane that touches the domain boundary keeps going into the absorber.
#   sigma'(d) = sigma'_max d^m,  sigma'_max = 0.8 (m+1) c0 / step   (sigma / eps0)
#   alpha'(d) = alpha'_max (1 - d)
#   psi = b psi + a dF,  dF += psi,   b = exp(-(sigma' + alpha') dt),
#   a = sigma' / (sigma' + alpha') (b - 1)
#
# Lumped port (discretize.DiscretePort): a voltage source V_s(t) in series
# with R, spread over the n edges of its column (total length L): each edge
# carries conductivity sigma_e = L / (R A_e) and source current density
# J_e = sign V_s / (R A_e), A_e = the dual face area of the edge. Then
#   I = (V_s - V) / R,  V = V(+) - V(-) = -sum(sign E_e L_e).
# Recorded each step: V at E times (n dt), the gap current I (circulation of
# H around the port edges, mean over the column) at H times ((n - 1/2) dt).
#
# Excitation: sine-modulated Gaussian (no DC) centered at f0 = (f_min +
# f_max) / 2, its spectrum is 20 dB down at f_min and f_max.

import math
from dataclasses import dataclass, field

import numpy as np

from project.settings import C0, GHZ

EPS0 = 8.8541878128e-12
MU0 = 4e-7 * math.pi

PML_GRADING = 3
PML_SIGMA_FACTOR = 0.8
PML_ALPHA_FACTOR = 0.05


@dataclass
class Pulse:
    f0: float           # Hz
    tau: float          # s
    t0: float           # s

    @classmethod
    def for_band(cls, f_min, f_max):
        f0 = 0.5 * (f_min + f_max)
        half_band = 0.5 * (f_max - f_min)
        # Spectrum of exp(-(t/tau)^2) ~ exp(-(pi f tau)^2): 0.1 at +-half_band.
        tau = math.sqrt(math.log(10.0)) / (math.pi * half_band)
        return cls(f0, tau, 4.0 * tau)

    def __call__(self, t):
        x = (t - self.t0) / self.tau
        return np.exp(-x * x) * np.sin(2 * math.pi * self.f0 * (t - self.t0))

    @property
    def duration(self):
        return 2.0 * self.t0


@dataclass
class PortRecord:
    name: str
    v: list = field(default_factory=list)     # V at E times
    i: list = field(default_factory=list)     # I at H times


class FdtdSolver:
    def __init__(self, materials, f_min_ghz, f_max_ghz, active_port=None,
                 pml_cells=8, courant=0.95):
        self.materials = materials
        self.pml = int(pml_cells)
        self.pulse = Pulse.for_band(f_min_ghz * GHZ, f_max_ghz * GHZ)

        names = [p.name for p in materials.ports]
        if active_port is not None and active_port not in names:
            raise ValueError(f"no port named {active_port!r} (have {names})")
        self.active_port = active_port

        self._build_grid()
        self.dt = courant * self._dt_max()
        self._build_coefficients()
        self._build_ports()
        self._build_cpml()
        self.reset()

    # =====================================================
    # Grid
    # =====================================================

    def _build_grid(self):
        n = self.pml
        self.lines = []
        self.inner = []          # (first, last) line index of the meshed domain
        for lines in self.materials.grid.lines:
            d_lo, d_hi = lines[1] - lines[0], lines[-1] - lines[-2]
            lo = lines[0] - d_lo * np.arange(n, 0, -1)
            hi = lines[-1] + d_hi * np.arange(1, n + 1)
            self.lines.append(np.concatenate((lo, lines, hi)))
            self.inner.append((n, n + len(lines) - 1))

        self.shape = tuple(len(l) - 1 for l in self.lines)
        self.d = [np.diff(l) for l in self.lines]                         # primary
        self.dd = [0.5 * (d[1:] + d[:-1]) for d in self.d]                 # dual, interior nodes

    def _dt_max(self):
        h = [d.min() for d in self.d]
        return 1.0 / (C0 * math.sqrt(sum(1.0 / s ** 2 for s in h)))

    @staticmethod
    def _along(values, axis):
        shape = [1, 1, 1]
        shape[axis] = len(values)
        return np.asarray(values, dtype=np.float32).reshape(shape)

    def _pad(self, array):
        return np.pad(array, self.pml, mode="edge")

    # =====================================================
    # Materials -> update coefficients
    # =====================================================

    def _build_coefficients(self):
        m = self.materials
        dt = self.dt
        self.eps = [self._pad(a).astype(np.float64) * EPS0 for a in m.eps_r]
        sigma = [self._pad(a).astype(np.float64) for a in m.sigma]
        pec = [self._pad(a) for a in m.pec]
        mu = [self._pad(a).astype(np.float64) * MU0 for a in m.mu_r]

        # Port conductivity sigma_e = L / (R A_e) on the port edges.
        self._port_edges = []
        for port in m.ports:
            edges = port.edges + self.pml
            area = self._dual_area(port.axis, edges)
            sigma[port.axis][tuple(edges.T)] += port.length / (port.resistance * area)
            self._port_edges.append((port, edges, area))

        self.ca, self.cb = [], []
        for axis in range(3):
            loss = sigma[axis] * dt / (2.0 * self.eps[axis])
            ca = (1.0 - loss) / (1.0 + loss)
            cb = (dt / self.eps[axis]) / (1.0 + loss)
            ca[pec[axis]] = 0.0
            cb[pec[axis]] = 0.0
            self.ca.append(ca.astype(np.float32))
            self.cb.append(cb.astype(np.float32))
        self.db = [(dt / a).astype(np.float32) for a in mu]

        f32 = np.float32
        self.inv_d = [self._along(1.0 / d, a) for a, d in enumerate(self.d)]
        self.inv_dd = [self._along(1.0 / dd, a) for a, dd in enumerate(self.dd)]
        self._interior = [
            tuple(slice(None) if b == a else slice(1, -1) for b in range(3)) for a in range(3)
        ]
        self._f32 = f32

    def _dual_area(self, axis, edges):
        """Dual face area of E-edges along `axis` at node indices of the other axes."""
        u, v = [a for a in range(3) if a != axis]
        return self._dual_length(u, edges[:, u]) * self._dual_length(v, edges[:, v])

    def _dual_length(self, axis, nodes):
        d = self.d[axis]
        left = np.where(nodes > 0, d[np.maximum(nodes - 1, 0)], 0.0)
        right = np.where(nodes < len(d), d[np.minimum(nodes, len(d) - 1)], 0.0)
        return 0.5 * (left + right)

    # =====================================================
    # Ports
    # =====================================================

    def _build_ports(self):
        self.records = [PortRecord(p.name) for p, _, _ in self._port_edges]
        self._source = None
        for port, edges, area in self._port_edges:
            if port.name == self.active_port:
                # E -= Cb J,  J = sign V_s / (R A_e)
                coef = self.cb[port.axis][tuple(edges.T)] * port.sign / (port.resistance * area)
                self._source = (port.axis, tuple(edges.T), coef.astype(np.float32))

    def _port_voltage(self, port, edges):
        e = self.e[port.axis][tuple(edges.T)].astype(np.float64)
        lengths = self.d[port.axis][edges[:, port.axis]]
        return -port.sign * float(np.sum(e * lengths))

    def _port_current(self, port, edges):
        """Mean over the column of the H circulation around each edge (- -> +)."""
        a = port.axis
        u, v = [b for b in range(3) if b != a]
        hu, hv = self.h[u], self.h[v]
        i, j, k = edges.T
        idx = [i, j, k]

        def at(arr, shift_axis):
            index = list(idx)
            index[shift_axis] = index[shift_axis] - 1
            return arr[tuple(idx)].astype(np.float64), arr[tuple(index)].astype(np.float64)

        # curl_a H * A = (dHv/du - dHu/dv) * dd_u * dd_v
        hv1, hv0 = at(hv, u)
        hu1, hu0 = at(hu, v)
        ddu = self._dual_length(u, idx[u])
        ddv = self._dual_length(v, idx[v])
        sign = 1.0 if (u, v) in ((0, 1), (1, 2), (2, 0)) else -1.0
        circulation = sign * ((hv1 - hv0) * ddv - (hu1 - hu0) * ddu)
        return port.sign * float(np.mean(circulation))

    # =====================================================
    # CPML
    # =====================================================

    def _profile(self, axis, coords):
        first, last = self.inner[axis]
        lo, hi = self.lines[axis][first], self.lines[axis][last]
        d_lo, d_hi = self.d[axis][0], self.d[axis][-1]
        t_lo, t_hi = self.pml * d_lo, self.pml * d_hi

        depth = np.zeros_like(coords)
        step = np.ones_like(coords)
        below, above = coords < lo, coords > hi
        depth[below] = (lo - coords[below]) / t_lo
        depth[above] = (coords[above] - hi) / t_hi
        step[below], step[above] = d_lo, d_hi

        m = PML_GRADING
        sigma = PML_SIGMA_FACTOR * (m + 1) * C0 / step * depth ** m
        alpha = np.where(depth > 0, PML_ALPHA_FACTOR * C0 / step * (1.0 - depth), 0.0)
        b = np.exp(-(sigma + alpha) * self.dt)
        with np.errstate(divide="ignore", invalid="ignore"):
            a = np.where(sigma > 0, sigma / (sigma + alpha) * (b - 1.0), 0.0)
        return a, b

    def _slabs(self, axis, coords, shape):
        """[(index, a, b, psi)] for the two absorbing ends along `axis`."""
        a, b = self._profile(axis, coords)
        active = np.nonzero(a != 0.0)[0]
        slabs = []
        half = 0.5 * len(coords)
        for part in (active[active < half], active[active >= half]):
            if part.size == 0:
                continue
            sl = slice(int(part[0]), int(part[-1]) + 1)
            index = [slice(None)] * 3
            index[axis] = sl
            pshape = list(shape)
            pshape[axis] = sl.stop - sl.start
            slabs.append((tuple(index), self._along(a[sl], axis), self._along(b[sl], axis),
                          np.zeros(pshape, dtype=np.float32)))
        return slabs

    def _build_cpml(self):
        nx, ny, nz = self.shape
        centers = [0.5 * (l[1:] + l[:-1]) for l in self.lines]
        nodes = [l[1:-1] for l in self.lines]
        hs = [(nx + 1, ny, nz), (nx, ny + 1, nz), (nx, ny, nz + 1)]     # H shapes

        def e_interior_shape(a):
            return tuple(n if b == a else n - 1 for b, n in enumerate(self.shape))

        self.cp_h = {}
        self.cp_e = {}
        for f in range(3):              # field component
            for d in range(3):          # derivative direction
                if d == f:
                    continue
                self.cp_h[f, d] = self._slabs(d, centers[d], hs[f])
                self.cp_e[f, d] = self._slabs(d, nodes[d], e_interior_shape(f))

    @staticmethod
    def _apply_cpml(derivative, slabs):
        for index, a, b, psi in slabs:
            part = derivative[index]
            psi *= b
            psi += a * part
            part += psi

    # =====================================================
    # Time stepping
    # =====================================================

    def reset(self):
        nx, ny, nz = self.shape
        f32 = np.float32
        self.e = [np.zeros((nx, ny + 1, nz + 1), f32), np.zeros((nx + 1, ny, nz + 1), f32),
                  np.zeros((nx + 1, ny + 1, nz), f32)]
        self.h = [np.zeros((nx + 1, ny, nz), f32), np.zeros((nx, ny + 1, nz), f32),
                  np.zeros((nx, ny, nz + 1), f32)]
        for slabs in list(self.cp_h.values()) + list(self.cp_e.values()):
            for _, _, _, psi in slabs:
                psi[...] = 0.0
        self.step_index = 0
        self.records = [PortRecord(r.name) for r in self.records]

    @property
    def t(self):
        return self.step_index * self.dt

    def step(self, n=1):
        for _ in range(int(n)):
            self._update_h()
            self._update_e()
            self.step_index += 1
            self._record()

    def _diff(self, array, axis, inverse):
        """Forward difference along `axis` times the inverse step."""
        hi = [slice(None)] * 3
        lo = [slice(None)] * 3
        hi[axis] = slice(1, None)
        lo[axis] = slice(0, -1)
        return (array[tuple(hi)] - array[tuple(lo)]) * inverse

    def _update_h(self):
        e, h = self.e, self.h
        # H_f -= Db (d E_w / d_v - d E_v / d_w) for (f, v, w) cyclic.
        for f, v, w in ((0, 1, 2), (1, 2, 0), (2, 0, 1)):
            d1 = self._diff(e[w], v, self.inv_d[v])
            d2 = self._diff(e[v], w, self.inv_d[w])
            self._apply_cpml(d1, self.cp_h[f, v])
            self._apply_cpml(d2, self.cp_h[f, w])
            h[f] -= self.db[f] * (d1 - d2)

    def _update_e(self):
        e, h = self.e, self.h
        # E_f = Ca E_f + Cb (d H_w / d_v - d H_v / d_w), interior edges only.
        for f, v, w in ((0, 1, 2), (1, 2, 0), (2, 0, 1)):
            hw = h[w][self._cut(f, v, w)]
            hv = h[v][self._cut(f, w, v)]
            d1 = self._diff(hw, v, self.inv_dd[v])
            d2 = self._diff(hv, w, self.inv_dd[w])
            self._apply_cpml(d1, self.cp_e[f, v])
            self._apply_cpml(d2, self.cp_e[f, w])
            interior = self._interior[f]
            target = e[f][interior]
            target *= self.ca[f][interior]
            target += self.cb[f][interior] * (d1 - d2)

        if self._source is not None:
            axis, index, coef = self._source
            vs = float(self.pulse((self.step_index + 0.5) * self.dt))
            e[axis][index] -= coef * np.float32(vs)

    @staticmethod
    def _cut(f, d, other):
        """
        Slice of the H component that varies along `d` for the E_f update:
        all along f and d, interior (1:-1) along the remaining axis `other`'s
        complement. The H component normal to `other` lives on cell centers
        along d and f... (see shapes): keep [:] on f and d, [1:-1] on the third.
        """
        index = [slice(None)] * 3
        third = 3 - f - d
        index[third] = slice(1, -1)
        return tuple(index)

    def _record(self):
        for record, (port, edges, _) in zip(self.records, self._port_edges):
            record.v.append(self._port_voltage(port, edges))
            record.i.append(self._port_current(port, edges))

    def energy(self):
        """Total field energy (J) inside the meshed domain + PML."""
        we = sum(np.sum(self.eps[a] * self.e[a].astype(np.float64) ** 2 * self._e_volume(a))
                 for a in range(3))
        return 0.5 * we      # electric part: enough for a decay criterion

    def _e_volume(self, axis):
        if not hasattr(self, "_vol_cache"):
            self._vol_cache = {}
        if axis not in self._vol_cache:
            u, v = [a for a in range(3) if a != axis]
            lu = self._dual_length(u, np.arange(self.shape[u] + 1))
            lv = self._dual_length(v, np.arange(self.shape[v] + 1))
            vol = self._along(self.d[axis], axis) * self._along(lu, u) * self._along(lv, v)
            self._vol_cache[axis] = vol.astype(np.float64)
        return self._vol_cache[axis]

    def run(self, max_time=None, end_db=-40.0, check_every=50, progress=None):
        """
        Steps until the energy fell end_db below its peak (after the pulse),
        or max_time (s; default 30 pulse lengths). progress(t, level_db) is
        called at each check; returning False stops the run.
        """
        max_time = max_time or 30.0 * self.pulse.duration
        peak = 0.0
        while self.t < max_time:
            self.step(check_every)
            w = self.energy()
            peak = max(peak, w)
            level = 10.0 * math.log10(w / peak) if w > 0 and peak > 0 else -300.0
            if progress is not None and progress(self.t, level) is False:
                return "stopped"
            if self.t > self.pulse.duration and level < end_db:
                return "converged"
        return "max_time"

    # =====================================================
    # Results
    # =====================================================

    def spectra(self, frequencies):
        """{port: (V(f), I(f))} by DFT, I shifted for its half-step time."""
        f = np.asarray(frequencies, dtype=np.float64)[:, None]
        out = {}
        for record in self.records:
            n = len(record.v)
            t_v = np.arange(1, n + 1) * self.dt
            t_i = t_v - 0.5 * self.dt
            v = np.exp(-2j * np.pi * f * t_v[None]) @ np.asarray(record.v) * self.dt
            i = np.exp(-2j * np.pi * f * t_i[None]) @ np.asarray(record.i) * self.dt
            out[record.name] = (v, i)
        return out

    def excitation_spectrum(self, frequencies):
        f = np.asarray(frequencies, dtype=np.float64)[:, None]
        n = self.step_index
        t = (np.arange(n) + 0.5) * self.dt
        return np.exp(-2j * np.pi * f * t[None]) @ self.pulse(t) * self.dt
