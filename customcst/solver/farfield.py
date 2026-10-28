# farfield.py
#
# Far-field monitor: the radiation pattern of an antenna from one FDTD run.
#
# A closed box (the Huygens surface) a few cells inside the meshed domain
# (FAR_FIELD_GAP from its faces, so outside everything the padding put air
# around) collects, during the run, the Fourier transforms of the tangential
# E and H on its six faces at the far-field frequencies. After the run the
# equivalence principle turns them into surface currents (outward normal n)
#   J = n x H,  M = -n x E
# whose radiation integrals give the far field in every direction r:
#   N = sum J e^{jk r.r'} dS,  L = sum M e^{jk r.r'} dS
#   r E_theta = -jk/(4 pi) (L_phi + eta0 N_theta)
#   r E_phi   =  jk/(4 pi) (L_theta - eta0 N_phi)        (e^{-jkr} dropped)
#   U = |r E|^2 / (2 eta0)   (radiation intensity, W/sr in the run's units)
# The box must be in air (eta0, k of vacuum); a body crossing it is reported.
#
# On the Yee grid a face lies on a node plane. Its tangential E edges lie in
# that plane, its tangential H half a cell to either side: H is interpolated
# to the plane, and all four components are averaged to the face's cell
# centers. E is sampled at n dt, H at (n - 1/2) dt, each with its own phase.
#
# The transforms are taken every `stride` steps, SAMPLES_PER_PERIOD samples
# per period of the highest frequency the pulse carries (f0 + 3 tau-widths,
# where its spectrum is ~1e-9 of the peak): the sum is then the same as with
# every step, at a fraction of the cost.
#
# The radiation integral sums the currents of patches up to lambda / 20
# across (cells of a face binned, at their area-weighted centroid): the
# fine cells near the structure would make it slow, and a patch that small
# changes the far field by well under 1 %.
#
# Powers, in the same (DFT) units as the port's V and I:
#   p_rad  = 1/2 Re sum (E x H*) . n dS over the box (radiated)
#   p_acc  = (|a|^2 - |b|^2) / 2 at the excited port (accepted)
#   p_inc  = |a|^2 / 2 (incident)
# so directivity D = 4 pi U / P_rad (P_rad = U integrated over the sphere),
# gain G = 4 pi U / p_acc, realized gain = 4 pi U / p_inc, efficiency
# p_rad / p_acc. theta is measured from +z, phi from +x toward +y.

import math

import numpy as np

from project.settings import C0
from solver.sparams import port_waves

MU0 = 4e-7 * math.pi
ETA0 = MU0 * C0
EPS0 = 1.0 / (MU0 * C0 * C0)

FAR_FIELD_GAP = 2           # cells between the box and the meshed domain's faces
SAMPLES_PER_PERIOD = 8      # DFT samples per period of the pulse's highest frequency
PATCH_PER_WAVELENGTH = 20   # radiation integral: patches up to lambda / this across
THETA_STEP = 1.0            # degrees, the stored pattern's grid
PHI_STEP = 2.0
DIRECTION_CHUNK = 2048      # directions per block of the radiation integral


def _cyclic(axis):
    """(u, v) with (u, v, axis) right-handed: e_axis x e_u = e_v."""
    return (axis + 1) % 3, (axis + 2) % 3


def _take(array, index):
    """array[index] with index {axis: int | slice}; the result's axes in (u, v) order."""
    full = tuple(index.get(a, slice(None)) for a in range(3))
    return array[full]


class FarFieldBox:
    """Collects the tangential fields on the Huygens box during a run (see the header)."""

    def __init__(self, solver, frequencies, gap=FAR_FIELD_GAP):
        self.frequencies = np.asarray(frequencies, dtype=np.float64)
        self.omega = 2.0 * math.pi * self.frequencies
        pulse = solver.pulse
        half_band = math.sqrt(math.log(10.0)) / (math.pi * pulse.tau)
        f_top = pulse.f0 + 3.0 * half_band
        self.stride = max(1, int(1.0 / (SAMPLES_PER_PERIOD * f_top * solver.dt)))
        self.dt = solver.dt

        lo = [first + gap for first, _ in solver.inner]
        hi = [last - gap for _, last in solver.inner]
        if any(h - l < 2 for l, h in zip(lo, hi)):
            raise ValueError("the domain is too small for a far-field box")
        self.lo, self.hi = lo, hi

        self.faces = []
        for axis in range(3):
            u, v = _cyclic(axis)
            for node, sign in ((lo[axis], -1.0), (hi[axis], 1.0)):
                self.faces.append(self._face(solver, axis, u, v, node, sign))
        self.cut = self._material_on_box(solver)
        self.reset()

    def _face(self, solver, axis, u, v, node, sign):
        lines = solver.lines
        cu = 0.5 * (lines[u][self.lo[u]:self.hi[u]] + lines[u][self.lo[u] + 1:self.hi[u] + 1])
        cv = 0.5 * (lines[v][self.lo[v]:self.hi[v]] + lines[v][self.lo[v] + 1:self.hi[v] + 1])
        du = np.diff(lines[u][self.lo[u]:self.hi[u] + 1])
        dv = np.diff(lines[v][self.lo[v]:self.hi[v] + 1])
        d_a = solver.d[axis]
        below, above = d_a[node - 1], d_a[node]       # the cells on either side of the plane
        return {
            "axis": axis, "u": u, "v": v, "node": node, "sign": sign,
            "position": float(lines[axis][node]),
            "cu": cu, "cv": cv, "area": du[:, None] * dv[None, :],
            # H at the plane from the cells node-1 and node (linear in the distance)
            "h_weights": (above / (below + above), below / (below + above)),
            "cells_u": slice(self.lo[u], self.hi[u]), "cells_v": slice(self.lo[v], self.hi[v]),
            "nodes_u": slice(self.lo[u], self.hi[u] + 1), "nodes_v": slice(self.lo[v], self.hi[v] + 1),
        }

    def _material_on_box(self, solver):
        """E edges in the box's faces that are not plain vacuum (a body crosses the box)."""
        count = 0
        for face in self.faces:
            a, u, v, node = face["axis"], face["u"], face["v"], face["node"]
            for comp, along in ((u, "cells_u"), (v, "cells_v")):
                other = "nodes_v" if comp == u else "nodes_u"
                index = {a: node, comp: face[along], (v if comp == u else u): face[other]}
                eps = _take(solver.eps[comp], index)
                cb = _take(solver.cb[comp], index)
                count += int(np.count_nonzero((np.abs(eps / EPS0 - 1.0) > 1e-6) | (cb == 0)))
        return count

    def reset(self):
        n_f = len(self.frequencies)
        for face in self.faces:
            shape = (n_f,) + face["area"].shape
            for key in ("e_u", "e_v", "h_u", "h_v"):
                face[key] = np.zeros(shape, np.complex128)

    # =====================================================
    # During the run
    # =====================================================

    def sample(self, solver):
        n = solver.step_index
        if n % self.stride:
            return
        weight = self.stride * self.dt
        phase_e = np.exp(-1j * self.omega * n * self.dt) * weight
        phase_h = np.exp(-1j * self.omega * (n - 0.5) * self.dt) * weight
        for face in self.faces:
            e_u, e_v, h_u, h_v = self._fields(solver, face)
            face["e_u"] += phase_e[:, None, None] * e_u[None]
            face["e_v"] += phase_e[:, None, None] * e_v[None]
            face["h_u"] += phase_h[:, None, None] * h_u[None]
            face["h_v"] += phase_h[:, None, None] * h_v[None]

    @staticmethod
    def _uv(array, u, v):
        """A 2D slice with axes in increasing index order -> (u, v) order."""
        return array if u < v else array.T

    def _fields(self, solver, face):
        """E_u, E_v, H_u, H_v at the face's cell centers (float64, (nu, nv))."""
        a, u, v, node = face["axis"], face["u"], face["v"], face["node"]
        e, h = solver.e, solver.h
        # E_u: cells along u, nodes along v -> mean of the two v nodes
        e_u = self._uv(_take(e[u], {a: node, u: face["cells_u"], v: face["nodes_v"]}), u, v).astype(np.float64)
        e_u = 0.5 * (e_u[:, 1:] + e_u[:, :-1])
        e_v = self._uv(_take(e[v], {a: node, u: face["nodes_u"], v: face["cells_v"]}), u, v).astype(np.float64)
        e_v = 0.5 * (e_v[1:, :] + e_v[:-1, :])
        w0, w1 = face["h_weights"]
        # H_u: nodes along u, cells along v and along the normal (node-1, node)
        h_u = sum(w * self._uv(_take(h[u], {a: k, u: face["nodes_u"], v: face["cells_v"]}), u, v)
                  .astype(np.float64) for w, k in ((w0, node - 1), (w1, node)))
        h_u = 0.5 * (h_u[1:, :] + h_u[:-1, :])
        h_v = sum(w * self._uv(_take(h[v], {a: k, u: face["cells_u"], v: face["nodes_v"]}), u, v)
                  .astype(np.float64) for w, k in ((w0, node - 1), (w1, node)))
        h_v = 0.5 * (h_v[:, 1:] + h_v[:, :-1])
        return e_u, e_v, h_u, h_v

    # =====================================================
    # After the run
    # =====================================================

    def radiated_power(self):
        """(F,) 1/2 Re sum (E x H*) . n dS over the box."""
        total = np.zeros(len(self.frequencies))
        for face in self.faces:
            # (E x H*) . e_axis = E_u H_v* - E_v H_u*
            flux = face["e_u"] * np.conj(face["h_v"]) - face["e_v"] * np.conj(face["h_u"])
            total += 0.5 * face["sign"] * np.real(np.sum(flux * face["area"][None], axis=(1, 2)))
        return total

    def _patches(self, index, wavelength):
        """(positions (P, 3), J dS (P, 3), M dS (P, 3)) of the box at frequency `index`."""
        size = wavelength / PATCH_PER_WAVELENGTH
        positions, currents_j, currents_m = [], [], []
        for face in self.faces:
            a, u, v, s = face["axis"], face["u"], face["v"], face["sign"]
            area = face["area"]
            # J = n x H: J_u = -s H_v, J_v = s H_u;  M = -n x E: M_u = s E_v, M_v = -s E_u
            parts = {
                "j_u": -s * face["h_v"][index], "j_v": s * face["h_u"][index],
                "m_u": s * face["e_v"][index], "m_v": -s * face["e_u"][index],
            }
            bu = np.floor((face["cu"] - face["cu"][0] + 0.5 * size) / size).astype(int)
            bv = np.floor((face["cv"] - face["cv"][0] + 0.5 * size) / size).astype(int)
            nv = bv.max() + 1
            labels = (bu[:, None] * nv + bv[None, :]).ravel()
            count = labels.max() + 1
            w = area.ravel()
            total = np.bincount(labels, w, count)
            used = total > 0
            centroid_u = np.bincount(labels, w * np.repeat(face["cu"], len(face["cv"])), count)[used] / total[used]
            centroid_v = np.bincount(labels, w * np.tile(face["cv"], len(face["cu"])), count)[used] / total[used]
            pos = np.zeros((int(used.sum()), 3))
            pos[:, a] = face["position"]
            pos[:, u] = centroid_u
            pos[:, v] = centroid_v
            sums = {}
            for key, values in parts.items():
                flat = (values * area).ravel()
                sums[key] = (np.bincount(labels, flat.real, count) + 1j * np.bincount(labels, flat.imag, count))[used]
            j = np.zeros((len(pos), 3), np.complex128)
            m = np.zeros((len(pos), 3), np.complex128)
            j[:, u], j[:, v] = sums["j_u"], sums["j_v"]
            m[:, u], m[:, v] = sums["m_u"], sums["m_v"]
            positions.append(pos)
            currents_j.append(j)
            currents_m.append(m)
        return np.concatenate(positions), np.concatenate(currents_j), np.concatenate(currents_m)

    def pattern(self, theta, phi):
        """
        (r E_theta, r E_phi), each (F, len(theta)) complex, in the directions
        (theta[i], phi[i]) in radians.
        """
        theta, phi = np.asarray(theta, float), np.asarray(phi, float)
        st, ct, sp, cp = np.sin(theta), np.cos(theta), np.sin(phi), np.cos(phi)
        r_hat = np.stack([st * cp, st * sp, ct], axis=1)
        t_hat = np.stack([ct * cp, ct * sp, -st], axis=1)
        p_hat = np.stack([-sp, cp, np.zeros_like(phi)], axis=1)
        out_t = np.zeros((len(self.frequencies), len(theta)), np.complex128)
        out_p = np.zeros_like(out_t)
        for k, f in enumerate(self.frequencies):
            wavelength = C0 / f
            wavenumber = 2.0 * math.pi / wavelength
            pos, j, m = self._patches(k, wavelength)
            for start in range(0, len(theta), DIRECTION_CHUNK):
                block = slice(start, start + DIRECTION_CHUNK)
                phase = np.exp(1j * wavenumber * (r_hat[block] @ pos.T))
                n_vec, l_vec = phase @ j, phase @ m
                n_t = np.sum(n_vec * t_hat[block], axis=1)
                n_p = np.sum(n_vec * p_hat[block], axis=1)
                l_t = np.sum(l_vec * t_hat[block], axis=1)
                l_p = np.sum(l_vec * p_hat[block], axis=1)
                factor = 1j * wavenumber / (4.0 * math.pi)
                out_t[k, block] = -factor * (l_p + ETA0 * n_t)
                out_p[k, block] = factor * (l_t - ETA0 * n_p)
        return out_t, out_p


def intensity(e_theta, e_phi):
    """Radiation intensity U = |r E|^2 / (2 eta0)."""
    return (np.abs(e_theta) ** 2 + np.abs(e_phi) ** 2) / (2.0 * ETA0)


def sphere_power(u, theta, phi):
    """
    The integral of U over the sphere, U (F, n_theta, n_phi) on the grid
    theta (0..pi) x phi (0..2pi, without the closing 2pi): trapezoid in theta,
    the periodic sum in phi.
    """
    d_phi = 2.0 * math.pi / len(phi)
    ring = np.sum(u, axis=2) * d_phi * np.sin(theta)[None, :]
    return np.trapezoid(ring, theta, axis=1) if hasattr(np, "trapezoid") else np.trapz(ring, theta, axis=1)


def figures(ff):
    """
    Per frequency: directivity, gain and realized gain (dBi, at the
    maximum), the radiation efficiency, and the direction of the maximum
    (theta, phi degrees), from the arrays of far_field().
    """
    u = intensity(ff["e_theta"].astype(np.complex128), ff["e_phi"].astype(np.complex128))
    out = []
    for k in range(len(ff["frequencies"])):
        peak = np.unravel_index(int(np.argmax(u[k])), u[k].shape)
        u_max = float(u[k][peak])
        out.append({
            "frequency": float(ff["frequencies"][k]),
            "directivity": 10.0 * math.log10(4.0 * math.pi * u_max / ff["p_sphere"][k]),
            "gain": 10.0 * math.log10(4.0 * math.pi * u_max / ff["p_acc"][k]) if ff["p_acc"][k] > 0 else float("nan"),
            "realized": 10.0 * math.log10(4.0 * math.pi * u_max / ff["p_inc"][k]),
            "efficiency": float(ff["p_box"][k] / ff["p_acc"][k]) if ff["p_acc"][k] > 0 else float("nan"),
            "theta": float(ff["theta"][peak[0]]), "phi": float(ff["phi"][peak[1]]),
        })
    return out


def summary(ff):
    """Text lines of figures(), one per frequency, plus a warning if a body crosses the box."""
    lines = [f"{x['frequency'] / 1e9:g} GHz: D {x['directivity']:.2f} dBi, G {x['gain']:.2f} dBi, "
             f"realized {x['realized']:.2f} dBi, efficiency {100 * x['efficiency']:.1f} %, "
             f"max at theta {x['theta']:g}, phi {x['phi']:g}" for x in figures(ff)]
    if int(ff["cut_edges"]):
        lines.append(f"⚠ a body crosses the far-field box ({int(ff['cut_edges'])} edges in material): "
                     "more padding (Simulation > padding) for a reliable pattern")
    return lines


def far_field(box, solver):
    """
    The pattern on the THETA_STEP x PHI_STEP grid and the powers, as a dict of
    arrays for the result file (see the header for the quantities).
    """
    theta = np.radians(np.arange(0.0, 180.0 + 0.5 * THETA_STEP, THETA_STEP))
    phi = np.radians(np.arange(0.0, 360.0 - 0.5 * PHI_STEP, PHI_STEP))
    tt, pp = np.meshgrid(theta, phi, indexing="ij")
    e_t, e_p = box.pattern(tt.ravel(), pp.ravel())
    shape = (len(box.frequencies), len(theta), len(phi))
    e_t, e_p = e_t.reshape(shape), e_p.reshape(shape)

    a, b = port_waves(solver, box.frequencies)[solver.active_port]
    return {
        "frequencies": box.frequencies,
        "theta": np.degrees(theta), "phi": np.degrees(phi),
        "e_theta": e_t.astype(np.complex64), "e_phi": e_p.astype(np.complex64),
        "p_sphere": sphere_power(intensity(e_t, e_p), theta, phi),
        "p_box": box.radiated_power(),
        "p_acc": 0.5 * (np.abs(a) ** 2 - np.abs(b) ** 2),
        "p_inc": 0.5 * np.abs(a) ** 2,
        "cut_edges": np.array(box.cut),
        "stride": np.array(box.stride),
    }
