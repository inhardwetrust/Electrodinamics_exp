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
# Waveguide port (discretize.DiscreteWavePort): the port plane is a domain
# end and the line runs on into the CPML (materials are edge-replicated),
# so a wave leaving through the port is absorbed: the port is matched to
# the line by construction, its reference impedance is the line's Z0.
#   - source: a soft sheet on the port plane, E_t += A s(t) e_mode, with
#     A = 2 c0 dt / (sqrt(eps_eff) dd) so the wave toward the line is
#     ~ s(t) volts (the other half runs into the CPML and is lost);
#   - V at the measure plane (a few cells in): the projection of E_t on the
#     mode, V = <E, e> / <e, e> (e = the mode field for 1 V);
#   - I: H of a quasi-TEM line has the pattern of the AIR-filled line,
#     H = I Z0_air / eta0 (n x e_air), so I = eta0 / Z0_air <H, g> / <g, g>,
#     g = n x e_air, n = the direction of the line. Sampled on the two H
#     planes around the measure plane (half steps h0, h1 away) and
#     interpolated linearly to it, (h1 H0 + h0 H1) / (h0 + h1); in the
#     spectrum / cos(beta sqrt(h0 h1)), the interpolation's loss on a wave
#     (exact for h0 = h1, second order otherwise);
#   - <.,.>: sums over the plane's edges weighted by their dual face area.
#     The H edges of a plane sit at the same (u, v) as the E edges.
#   - the spectra V, I are moved back to the port plane by the line's
#     transfer matrix (beta = 2 pi f sqrt(eps_eff) / c0, the measure
#     distance L): V0 = V cos bL + j Z0 I sin bL, I0 = I cos bL + j V / Z0 sin bL.
#
# Excitation: sine-modulated Gaussian (no DC) centered at f0 = (f_min +
# f_max) / 2, its spectrum is 20 dB down at f_min and f_max.
#
# Lumped elements (discretize.DiscreteElement): R, L, C in series or in
# parallel on a column of n edges; each edge carries the element scaled by
# k = l_e / L (R, L times k, C divided by k), so the column sums to the
# element. Edge voltage V = E l_e, current I along +axis, J = I / A_e.
# The element is discretized with the trapezoidal rule (A-stable for any
# R, L, C) as a companion model  I(n+1) = G V(n+1) + I_hist(n), and solved
# together with Ampere's law on its edges (time-centered current):
#   E(n+1) = (E_std - Cb (I_hist + I(n)) / (2A)) / (1 + Cb G l / (2A))
# where E_std is the plain Yee update. Companion models:
#   R: G = 1/R.   C: G = 2C/dt, I_hist = -G V(n) - I_C(n).
#   L: G = dt/2L, I_hist = G V(n) + I_L(n).
#   series: Z = R + 2L/dt + dt/2C, V_hist = -(Z_L I + V_L) + (Z_C I + V_C),
#           G = 1/Z, I_hist = -V_hist / Z.
#
# Speed: with numba installed the H and E updates (curls, CPML, update) run
# as compiled kernels on all CPU cores (solver/fdtd_kernels.py), 4-8x the
# numpy path; sources, lumped elements and port records stay in numpy.
# CUSTOMCST_NUMPY=1 forces the numpy path.

import math
from dataclasses import dataclass, field

import numpy as np

from project.settings import C0, GHZ
from solver import fdtd_kernels as kernels

EPS0 = 8.8541878128e-12
MU0 = 4e-7 * math.pi
ETA0 = MU0 * C0

PML_GRADING = 3
END_DB = -40.0          # a run stops when the field energy fell this far below its peak ...
MAX_PULSES = 30.0       # ... or after this many pulse lengths
PML_SIGMA_FACTOR = 0.8
PML_ALPHA_FACTOR = 0.05


@dataclass
class Pulse:
    f0: float           # Hz
    tau: float          # s
    t0: float           # s

    @classmethod
    def for_band(cls, f_min, f_max, trim=False):
        """
        trim: start and end the pulse at center -+ 3 tau (envelope 1.2e-4 of
        its peak, -78 dB in energy) instead of -+ 4 tau (1.1e-7): about a
        quarter of the pulse length less to compute; the tails cut off are
        below the -40 dB stop level anyway.
        """
        f0 = 0.5 * (f_min + f_max)
        half_band = 0.5 * (f_max - f_min)
        # Spectrum of exp(-(t/tau)^2) ~ exp(-(pi f tau)^2): 0.1 at +-half_band.
        tau = math.sqrt(math.log(10.0)) / (math.pi * half_band)
        return cls(f0, tau, (3.0 if trim else 4.0) * tau)

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
                 pml_cells=8, courant=0.95, trim_pulse=False):
        self.materials = materials
        self.pml = int(pml_cells)
        self.pulse = Pulse.for_band(f_min_ghz * GHZ, f_max_ghz * GHZ, trim=trim_pulse)

        names = [p.name for p in materials.ports] + [w.name for w in materials.waveports]
        if active_port is not None and active_port not in names:
            raise ValueError(f"no port named {active_port!r} (have {names})")
        self.active_port = active_port

        self._build_grid()
        self.dt = courant * self._dt_max()
        self._build_coefficients()
        self._build_elements()
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
    # Lumped elements
    # =====================================================

    def _build_elements(self):
        """Per-edge companion-model constants of every lumped element."""
        dt = self.dt
        self._elements = []
        for element in self.materials.elements:
            edges = element.edges + self.pml
            axis = element.axis
            index = tuple(edges.T)
            length = self.d[axis][edges[:, axis]]
            area = self._dual_area(axis, edges)
            k = length / element.length                    # share of the column

            e = {"name": element.name, "axis": axis, "index": index,
                 "l": length, "area": area, "topology": element.topology,
                 "cb": self.cb[axis][index].astype(np.float64)}

            if element.topology == "parallel":
                e["g_r"] = (1.0 / (element.r * k)) if element.r > 0 else np.zeros_like(k)
                e["g_c"] = (2.0 * element.c / k / dt) if element.c > 0 else np.zeros_like(k)
                e["g_l"] = (dt / (2.0 * element.l * k)) if element.l > 0 else np.zeros_like(k)
                e["g"] = e["g_r"] + e["g_c"] + e["g_l"]
            else:
                z_r = element.r * k
                z_l = (2.0 * element.l * k / dt) if element.l > 0 else np.zeros_like(k)
                z_c = (dt / (2.0 * element.c / k)) if element.c > 0 else np.zeros_like(k)
                z = z_r + z_l + z_c
                if np.any(z <= 0):
                    # R = L = C = 0 in series: a short -> PEC edges.
                    self.ca[axis][index] = 0.0
                    self.cb[axis][index] = 0.0
                    continue
                e.update(z_l=z_l, z_c=z_c, z=z, g=1.0 / z)

            self._elements.append(e)

    def _reset_elements(self):
        for e in self._elements:
            n = len(e["l"])
            for key in ("i", "v", "i_c", "i_l", "v_l", "v_c"):
                e[key] = np.zeros(n)

    def _update_elements(self):
        for e in self._elements:
            field = self.e[e["axis"]]
            e_std = field[e["index"]].astype(np.float64)
            i0, v0 = e["i"], e["v"]

            if e["topology"] == "parallel":
                i_hist = (-e["g_c"] * v0 - e["i_c"]) + (e["g_l"] * v0 + e["i_l"])
            else:
                v_hist = -(e["z_l"] * i0 + e["v_l"]) + (e["z_c"] * i0 + e["v_c"])
                i_hist = -v_hist / e["z"]

            factor = e["cb"] / (2.0 * e["area"])
            e1 = (e_std - factor * (i_hist + i0)) / (1.0 + factor * e["g"] * e["l"])
            v1 = e1 * e["l"]
            i1 = e["g"] * v1 + i_hist

            if e["topology"] == "parallel":
                e["i_c"] = e["g_c"] * (v1 - v0) - e["i_c"]
                e["i_l"] = e["g_l"] * (v1 + v0) + e["i_l"]
            else:
                e["v_l"] = e["z_l"] * (i1 - i0) - e["v_l"]
                e["v_c"] = e["z_c"] * (i1 + i0) + e["v_c"]

            e["i"], e["v"] = i1, v1
            field[e["index"]] = e1.astype(np.float32)

    def element_state(self):
        """{element: (V, I)} now: total column voltage and mean current."""
        return {e["name"]: (float(np.sum(e["v"])), float(np.mean(e["i"]))) for e in self._elements}

    # =====================================================
    # Ports
    # =====================================================

    def _build_ports(self):
        self.records = [PortRecord(p.name) for p, _, _ in self._port_edges]
        self._sources = []      # (axis, index, coef): E[axis][index] -= coef * V_s
        for port, edges, area in self._port_edges:
            if port.name == self.active_port:
                # E -= Cb J,  J = sign V_s / (R A_e)
                coef = self.cb[port.axis][tuple(edges.T)] * port.sign / (port.resistance * area)
                self._sources.append((port.axis, tuple(edges.T), coef.astype(np.float32)))

        self._waveports = [self._build_waveport(w) for w in self.materials.waveports]
        self.records += [PortRecord(w["port"].name) for w in self._waveports]

    def _build_waveport(self, port):
        p = self.pml
        a, mode = port.axis, port.mode
        u, v = mode.u_axis, mode.v_axis
        lines = self.materials.grid.lines
        du, dv = np.diff(lines[u]), np.diff(lines[v])
        ddu, ddv = _dual(du), _dual(dv)
        w_u = du[:, None] * ddv[None, :]            # dual faces of the u-edges (nu, nv+1)
        w_v = ddu[:, None] * dv[None, :]            # (nu+1, nv)

        def plane(index, shape):
            # In-plane slice of an extended array (u, v in domain index ranges).
            s = [None] * 3
            s[a] = index
            s[u] = slice(p, p + shape[0])
            s[v] = slice(p, p + shape[1])
            return tuple(s)

        cyclic = 1.0 if (a, u, v) in ((0, 1, 2), (1, 2, 0), (2, 0, 1)) else -1.0
        n = port.direction * cyclic
        g_v = n * mode.e_u_air                      # (n x e_air)_v, at the u-edge positions
        g_u = -n * mode.e_v_air                     # (n x e_air)_u, at the v-edge positions
        z0_air = 1.0 / (C0 * mode.capacitance_air)

        i0, im = port.index + p, port.measure + p
        lines_a = self.lines[a]
        h0 = 0.5 * (lines_a[im] - lines_a[im - 1])      # node -> H planes
        h1 = 0.5 * (lines_a[im + 1] - lines_a[im])
        w = {
            "port": port, "u": u, "v": v,
            "e_plane": (plane(im, w_u.shape), plane(im, w_v.shape)),
            # H planes im - 1/2 and im + 1/2 (H index k sits between nodes k and k + 1)
            "h_planes": [(plane(k, w_u.shape), plane(k, w_v.shape)) for k in (im - 1, im)],
            "h_weights": (h1 / (h0 + h1), h0 / (h0 + h1)),
            "e_u": (mode.e_u * w_u).astype(np.float32), "e_v": (mode.e_v * w_v).astype(np.float32),
            "e_norm": float(np.sum(mode.e_u ** 2 * w_u) + np.sum(mode.e_v ** 2 * w_v)),
            "g_u": (g_u * w_v).astype(np.float32), "g_v": (g_v * w_u).astype(np.float32),
            "i_scale": ETA0 / z0_air / float(np.sum(g_v ** 2 * w_u) + np.sum(g_u ** 2 * w_v)),
            "length": abs(lines_a[im] - lines_a[i0]),
            "h_half": math.sqrt(h0 * h1),
        }
        if port.name == self.active_port:
            dd = 0.5 * (lines_a[i0 + 1] - lines_a[i0 - 1])
            amp = 2.0 * C0 * self.dt / (math.sqrt(mode.eps_eff) * dd)
            for comp, field in ((u, mode.e_u), (v, mode.e_v)):
                self._sources.append((comp, plane(i0, field.shape), (-amp * field).astype(np.float32)))
        return w

    def _waveport_vi(self, w):
        u, v = w["u"], w["v"]
        e_u_plane, e_v_plane = w["e_plane"]
        voltage = (np.sum(self.e[u][e_u_plane] * w["e_u"], dtype=np.float64) +
                   np.sum(self.e[v][e_v_plane] * w["e_v"], dtype=np.float64)) / w["e_norm"]
        current = 0.0
        for (at_u, at_v), weight in zip(w["h_planes"], w["h_weights"]):
            # H_v sits at the u-edge positions, H_u at the v-edge positions.
            current += weight * (np.sum(self.h[v][at_u] * w["g_v"], dtype=np.float64) +
                                 np.sum(self.h[u][at_v] * w["g_u"], dtype=np.float64))
        return float(voltage), float(current * w["i_scale"])

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
        self._fast = kernels.AVAILABLE
        if self._fast:
            es = [(nx, ny + 1, nz + 1), (nx + 1, ny, nz + 1), (nx + 1, ny + 1, nz)]   # E shapes
            for f in range(3):
                for d in range(3):
                    if d != f:
                        self.cp_h[f, d] = self._compressed(d, centers[d], hs[f])
                        self.cp_e[f, d] = self._compressed(d, nodes[d], es[f])
            self._inv_d1 = [(1.0 / d).astype(np.float32) for d in self.d]
            self._inv_dd1 = [(1.0 / dd).astype(np.float32) for dd in self.dd]
            return
        for f in range(3):              # field component
            for d in range(3):          # derivative direction
                if d == f:
                    continue
                self.cp_h[f, d] = self._slabs(d, centers[d], hs[f])
                self.cp_e[f, d] = self._slabs(d, nodes[d], e_interior_shape(f))

    def _compressed(self, axis, coords, shape):
        """(slot map, a, b, psi) for the kernels: psi only in the absorbing layers."""
        a, b = self._profile(axis, coords)
        active = a != 0.0
        slots = np.full(len(coords), -1, dtype=np.int64)
        slots[active] = np.arange(int(active.sum()))
        pshape = list(shape)
        pshape[axis] = max(int(active.sum()), 1)
        return (slots, a.astype(np.float32), b.astype(np.float32),
                np.zeros(pshape, dtype=np.float32))

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
        if self._fast:
            for _, _, _, psi in list(self.cp_h.values()) + list(self.cp_e.values()):
                psi[...] = 0.0
        else:
            for slabs in list(self.cp_h.values()) + list(self.cp_e.values()):
                for _, _, _, psi in slabs:
                    psi[...] = 0.0
        self.step_index = 0
        self.records = [PortRecord(r.name) for r in self.records]
        self._reset_elements()

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
        if self._fast:
            inv, cp, db = self._inv_d1, self.cp_h, self.db
            kernels.update_hx(h[0], e[1], e[2], db[0], inv[1], inv[2], *cp[0, 1], *cp[0, 2])
            kernels.update_hy(h[1], e[2], e[0], db[1], inv[2], inv[0], *cp[1, 2], *cp[1, 0])
            kernels.update_hz(h[2], e[0], e[1], db[2], inv[0], inv[1], *cp[2, 0], *cp[2, 1])
            return
        # H_f -= Db (d E_w / d_v - d E_v / d_w) for (f, v, w) cyclic.
        for f, v, w in ((0, 1, 2), (1, 2, 0), (2, 0, 1)):
            d1 = self._diff(e[w], v, self.inv_d[v])
            d2 = self._diff(e[v], w, self.inv_d[w])
            self._apply_cpml(d1, self.cp_h[f, v])
            self._apply_cpml(d2, self.cp_h[f, w])
            h[f] -= self.db[f] * (d1 - d2)

    def _update_e(self):
        e, h = self.e, self.h
        if self._fast:
            inv, cp, ca, cb = self._inv_dd1, self.cp_e, self.ca, self.cb
            kernels.update_ex(e[0], h[1], h[2], ca[0], cb[0], inv[1], inv[2], *cp[0, 1], *cp[0, 2])
            kernels.update_ey(e[1], h[2], h[0], ca[1], cb[1], inv[2], inv[0], *cp[1, 2], *cp[1, 0])
            kernels.update_ez(e[2], h[0], h[1], ca[2], cb[2], inv[0], inv[1], *cp[2, 0], *cp[2, 1])
        else:
            self._update_e_numpy()

        if self._sources:
            vs = np.float32(self.pulse((self.step_index + 0.5) * self.dt))
            for axis, index, coef in self._sources:
                e[axis][index] -= coef * vs

        if self._elements:
            self._update_elements()

    def _update_e_numpy(self):
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
        for record, w in zip(self.records[len(self._port_edges):], self._waveports):
            v, i = self._waveport_vi(w)
            record.v.append(v)
            record.i.append(i)

    def energy(self):
        """Total field energy (J) inside the meshed domain + PML."""
        if self._fast:
            if not hasattr(self, "_energy_weight"):
                self._energy_weight = [self.eps[a] * self._e_volume(a) for a in range(3)]
            we = sum(kernels.weighted_square_sum(self.e[a], self._energy_weight[a]) for a in range(3))
            return 0.5 * we
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

    def run(self, max_time=None, end_db=END_DB, check_every=50, progress=None):
        """
        Steps until the energy fell end_db below its peak (after the pulse),
        or max_time (s; default MAX_PULSES pulse lengths). progress(t,
        level_db) is called at each check; returning False stops the run.
        """
        max_time = max_time or MAX_PULSES * self.pulse.duration
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

        for w in self._waveports:
            port = w["port"]
            v, i = out[port.name]
            beta = 2 * np.pi * f[:, 0] * math.sqrt(port.mode.eps_eff) / C0
            i = i / np.cos(beta * w["h_half"])
            z0, bl = port.resistance, beta * w["length"]
            out[port.name] = (v * np.cos(bl) + 1j * z0 * i * np.sin(bl),
                              i * np.cos(bl) + 1j * v / z0 * np.sin(bl))
        return out

    def reference_impedances(self):
        """{port name: Ohm}: R of the lumped ports, the line's Z0 of the waveguide ports."""
        out = {p.name: p.resistance for p in self.materials.ports}
        out.update({w.name: w.resistance for w in self.materials.waveports})
        return out

    def excitation_spectrum(self, frequencies):
        f = np.asarray(frequencies, dtype=np.float64)[:, None]
        n = self.step_index
        t = (np.arange(n) + 0.5) * self.dt
        return np.exp(-2j * np.pi * f * t[None]) @ self.pulse(t) * self.dt


def _dual(d):
    """Dual lengths at the nodes of a line with steps d (half steps at the ends)."""
    out = np.zeros(len(d) + 1)
    out[:-1] += 0.5 * d
    out[1:] += 0.5 * d
    return out

