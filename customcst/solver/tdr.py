# tdr.py
#
# Time domain reflectometry from a finished run's S_jj, like a VNA's time
# domain transform: no new simulation. Where along the line the
# reflections come from, how large, and of which sign.
#
# Low pass (the step and the impulse response):
#   - the band f_min..f_max (uniform, as the runs store it) is put on the
#     harmonic grid k df, k = 0..f_max/df, and continued down to DC (the
#     FDTD pulse has no DC) by causality: the structure answers only for
#     a while after t = 0 (its support: where the band pass envelope,
#     which needs no DC, is above SUPPORT_LEVEL of its peak), so the
#     missing bins are the ones (least squares, DC real) that leave the
#     windowed response nil outside the support. Exact for any reflection
#     inside it, however far or large (a polynomial fit of the low end
#     failed on a reflection 300 ps away: its phase turns 2 rad by 1 GHz).
#     It needs 2 f_min T_support of a few at most (1 GHz, 1 ns: fine).
#     A response with a slow tail mostly below f_min (a DC block: an RC
#     tail of 0.2 ns behind 2 pF) is not determined by the band: S(DC)
#     came out 0.83 for 1. The fit is repeated with a shorter and a
#     longer reach (lobe 3 / 8, the support's level / 10): their spread
#     of S(DC) is the uncertainty, and above DC_DOUBT the step's level is
#     not to be trusted (use band pass there);
#   - a Kaiser window over -f_max..f_max (beta: 0 minimum, 6 normal, 13
#     maximum, like a VNA) trades sharpness for ringing;
#   - the inverse FFT is the impulse response (scaled so that a single
#     reflection rho reads rho at its peak), its running sum the step
#     response (it ends at S(DC)); the step reads as an impedance
#     Z = R (1 + rho) / (1 - rho), R the port's reference;
#   - time is the round trip: a reflection d into the line comes at
#     t = 2 d sqrt(eps_eff) / c0.
# Band pass (any band, no DC needed, e.g. behind a DC block): the
# envelope |h| of the impulse response over the band alone, windowed
# over it; where, and how large, not the sign.
#
# Resolution: the step's 10-90 % rise (about 1 / f_max for the normal
# window, 1/10 GHz ~ 1 cm of round trip on a board): two reflections
# closer than that merge, and a short pad reads as a smeared bump lower
# than its own Z0.
#
# On the Z0 profile (solver/line_profile.py, solver/path_profile.py):
#   - each section's eps_eff gives the delay from the port's plane,
#     tau(u) = integral sqrt(eps_eff) / c0 du along the line (a coax,
#     then a board: each with its own speed), so the time maps to the
#     profile's own coordinate;
#   - the profile as a cascade of short uniform lines (lossless, Z0 and
#     eps_eff of the sections, terminated in R) gives the S11 a TDR would
#     see of the quasi-static profile; through the same transform it is
#     comparable with the run's: where they differ, the 3D field differs
#     from the cross-sections (a pin, a connector's launch, a lumped
#     element).

import math
from dataclasses import dataclass

import numpy as np

from project.settings import C0

WINDOWS = {"Minimum": 0.0, "Normal": 6.0, "Maximum": 13.0}     # Kaiser beta
TIME_STEP = 0.5e-12         # s: the longest sample step of a response
SUPPORT_LEVEL = 3e-3        # of the envelope's peak: where the structure's response ends
FIT_BETA = 10.0             # Kaiser beta of the continuation's fit (sidelobes ~ -70 dB)
FIT_LOBE = 5.0              # the fit's main lobe: +- FIT_LOBE / f_max around the support
DC_DOUBT = 0.03             # spread of S(DC) between the fits above which the step's level is uncertain
CASCADE_STEP = 0.02e-3      # m: the longest piece of the profile's cascade


@dataclass
class Response:
    t: np.ndarray               # s, round trip from the port's plane
    impulse: np.ndarray         # rho per reflection (low pass), |rho| envelope (band pass)
    step: np.ndarray = None     # rho of the step (low pass only)
    rise: float = float("nan")  # s: 10-90 % rise of the step (low pass), half-width of the peak (band pass)
    dc: complex = complex("nan")    # S at DC (low pass)
    dc_spread: float = 0.0      # its uncertainty (see the header); 0: the band reaches DC

    @property
    def doubtful(self):
        return self.dc_spread > DC_DOUBT

    def impedance(self, r_ref):
        """The step as an impedance (low pass)."""
        with np.errstate(divide="ignore", invalid="ignore"):
            return r_ref * (1.0 + self.step) / (1.0 - self.step)


def harmonic(frequencies, s):
    """
    (df, S on k df for k = 0..K, spread of S(DC)): the band on its
    harmonic grid, continued down to DC (see the header).
    frequencies: increasing, uniform (Hz).
    """
    f = np.asarray(frequencies, dtype=float)
    s = np.asarray(s, dtype=complex)
    df = (f[-1] - f[0]) / (len(f) - 1)
    k = np.arange(int(math.floor(f[-1] / df * (1 + 1e-9))) + 1)
    grid = k * df
    out = np.interp(grid, f, s.real) + 1j * np.interp(grid, f, s.imag)
    missing = int(np.count_nonzero(grid < f[0] * (1 - 1e-9)))
    spread = 0.0
    if missing:
        reach = support(f, s, SUPPORT_LEVEL)
        out[:missing] = _continued(out, missing, df, reach, FIT_LOBE)
        others = [_continued(out, missing, df, reach, 3.0)[0], _continued(out, missing, df, reach, 8.0)[0],
                  _continued(out, missing, df, support(f, s, SUPPORT_LEVEL / 10), FIT_LOBE)[0]]
        spread = float(max(abs(d - out[0]) for d in others))
    return df, out, spread


def support(frequencies, s, level=SUPPORT_LEVEL):
    """s: how long after t = 0 the structure answers (the band pass envelope above `level` of its peak)."""
    r = band_pass(frequencies, s, FIT_BETA)
    above = np.nonzero(r.impulse >= level * r.impulse.max())[0]
    return float(r.t[above[-1]]) if len(above) else 0.0


def _continued(g, missing, df, t_support, lobe):
    """
    The first `missing` bins of g (k df from DC), chosen so that the
    windowed response is nil outside [-lobe, t_support + lobe] (lobe in
    1 / f_max; least squares; DC real). g: the harmonic spectrum, its
    known bins set.
    """
    k_max = len(g) - 1
    w = np.kaiser(2 * k_max + 1, FIT_BETA)[k_max:]
    n = 1 << int(math.ceil(math.log2(8 * k_max)))
    t = np.arange(n) / (n * df)
    t = np.where(t < 0.5 / df, t, t - 1.0 / df)             # the period from -T/2
    lobe = lobe / (k_max * df)
    outside = (t < -lobe) | (t > min(t_support + lobe, 0.4 / df))
    known = g.copy()
    known[:missing] = 0.0
    h = np.fft.irfft(known * w, n)[outside]
    angle = 2.0 * math.pi * np.outer(t[outside] * df, np.arange(missing))
    columns = [np.full(len(h), w[0] / n)]                  # DC: real
    for b in range(1, missing):
        columns += [2.0 * w[b] / n * np.cos(angle[:, b]), -2.0 * w[b] / n * np.sin(angle[:, b])]
    m = np.stack(columns, axis=1)
    scale = np.linalg.norm(m, axis=0)
    x = np.linalg.lstsq(m / scale, -h, rcond=1e-10)[0] / scale
    return np.r_[x[0], x[1::2] + 1j * x[2::2]]


def _uniform(frequencies, s):
    """The band on a uniform grid (runs store one; anything else is resampled)."""
    f = np.asarray(frequencies, dtype=float)
    d = np.diff(f)
    if np.allclose(d, d[0], rtol=1e-6):
        return f, np.asarray(s, dtype=complex)
    g = np.linspace(f[0], f[-1], len(f))
    return g, np.interp(g, f, s.real) + 1j * np.interp(g, f, s.imag)


def _size(df):
    """FFT length: a sample step of at most TIME_STEP."""
    return 1 << int(math.ceil(math.log2(1.0 / (df * TIME_STEP))))


def low_pass(frequencies, s, beta=WINDOWS["Normal"]):
    """Response (impulse, step) of S_jj over its band, from t = 0 on."""
    f, s = _uniform(frequencies, s)
    df, g, spread = harmonic(f, s)
    k = len(g) - 1
    w = np.kaiser(2 * k + 1, beta)[k:]
    n = max(_size(df), 2 * k + 2)
    h = np.fft.irfft(g * w, n)
    weight = w[0] + 2.0 * w[1:].sum()           # a pure reflection's peak: rho
    t = np.arange(n) / (n * df)
    # Running sum from the middle of the period (t < 0 wraps to the end):
    # the windowed impulse is two-sided around each reflection.
    half = n // 2
    order = np.r_[half:n, 0:half]
    step = np.cumsum(h[order])[n - half:]
    return Response(t[:half], h[:half] * n / weight, step[:half],
                    _rise(np.fft.irfft(w, n)) / (n * df), complex(g[0]), spread)


def _rise(h_one):
    """10-90 % rise (samples) of the step of a flat spectrum: the window's own."""
    n = len(h_one)
    half = n // 2
    step = np.cumsum(np.r_[h_one[half:], h_one[:half]])
    step = step / step[-1]
    lo, hi = np.argmax(step >= 0.1), np.argmax(step >= 0.9)
    return float(hi - lo)


def band_pass(frequencies, s, beta=WINDOWS["Normal"]):
    """Response with the envelope |h| over the band alone (no DC: any band)."""
    f, s = _uniform(frequencies, s)
    df = (f[-1] - f[0]) / (len(f) - 1)
    w = np.kaiser(len(f), beta)
    n = max(_size(df), 2 * len(f))
    first = int(round(f[0] / df))
    spectrum = np.zeros(n, dtype=complex)
    spectrum[first:first + len(f)] = s * w
    h = np.abs(np.fft.ifft(spectrum)) * n / w.sum()
    half = n // 2
    ref = np.abs(np.fft.ifft(np.r_[w, np.zeros(n - len(w))])) * n / w.sum()
    width = float(np.count_nonzero(np.r_[ref[n - half:], ref[:half]] >= 0.5))
    return Response(np.arange(half) / (n * df), h[:half], rise=width / (n * df))


def transform(frequencies, s, mode="low pass", beta=WINDOWS["Normal"]):
    return (low_pass if mode == "low pass" else band_pass)(frequencies, s, beta)


# =====================================================
# Along the Z0 profile
# =====================================================

@dataclass
class LineMap:
    """The line from the port's plane along a profile (u: distance into the line, m)."""
    u: np.ndarray               # m, increasing, from 0 (the port's plane)
    z0: np.ndarray              # Ohm at u (finite)
    eps_eff: np.ndarray         # at u (finite)
    origin: float               # the profile's coordinate of the port's plane (m)
    sign: int                   # +1: the coordinate grows into the line

    @property
    def delay(self):
        """One-way delay (s) at each u."""
        speed = np.sqrt(self.eps_eff) / C0
        return np.r_[0.0, np.cumsum(0.5 * (speed[1:] + speed[:-1]) * np.diff(self.u))]

    def coordinate(self, t):
        """The profile's coordinate (m) of round trip t (s); past the profile's end at its last speed."""
        one_way = 0.5 * np.asarray(t, dtype=float)
        delay = self.delay
        u = np.interp(one_way, delay, self.u)
        beyond = one_way > delay[-1]
        u[beyond] = self.u[-1] + (one_way[beyond] - delay[-1]) * C0 / math.sqrt(self.eps_eff[-1])
        return self.origin + self.sign * u

    def time(self, coordinate):
        """Round trip (s) to the profile's coordinate (m), inside the mapped line."""
        u = self.sign * (np.asarray(coordinate, dtype=float) - self.origin)
        return 2.0 * np.interp(u, self.u, self.delay, left=np.nan, right=np.nan)


def line_map(coordinates, z0, eps_eff, origin, sign):
    """
    LineMap of a profile (its coordinates, Z0, eps_eff) from the port's
    plane at `origin`, into the line toward `sign`. Sections without a Z0
    (gaps) are left out; the stretch from the plane to the first section
    takes that section's line. None when no section lies in the line.
    """
    c = np.asarray(coordinates, dtype=float)
    z, e = np.asarray(z0, dtype=float), np.asarray(eps_eff, dtype=float)
    u = sign * (c - origin)
    keep = (u >= -1e-9) & np.isfinite(z) & np.isfinite(e) & (e > 0)
    if not np.any(keep):
        return None
    u, z, e = u[keep], z[keep], e[keep]
    order = np.argsort(u)
    u, z, e = np.maximum(u[order], 0.0), z[order], e[order]
    if u[0] > 0:
        u, z, e = np.r_[0.0, u], np.r_[z[0], z], np.r_[e[0], e]
    return LineMap(u, z, e, float(origin), int(sign))


def cascade_s11(line, frequencies, r_ref, z_load=None):
    """
    S11 (reference r_ref) of the line as a cascade of uniform lossless
    pieces at most CASCADE_STEP long (Z0, eps_eff interpolated between the
    sections), terminated in z_load (default r_ref).
    """
    f = np.asarray(frequencies, dtype=float)
    length = float(line.u[-1])
    if length <= 0:
        return np.zeros(len(f), dtype=complex)
    count = max(1, int(math.ceil(length / CASCADE_STEP)))
    edges = np.linspace(0.0, length, count + 1)
    middle = 0.5 * (edges[1:] + edges[:-1])
    z = np.interp(middle, line.u, line.z0)
    e = np.interp(middle, line.u, line.eps_eff)
    piece = np.diff(edges)
    zin = np.full(len(f), complex(r_ref if z_load is None else z_load))
    beta = 2.0 * math.pi * f / C0
    for k in range(count - 1, -1, -1):             # from the load back to the port
        tan = 1j * np.tan(beta * math.sqrt(e[k]) * piece[k])
        zin = z[k] * (zin + z[k] * tan) / (z[k] + zin * tan)
    return (zin - r_ref) / (zin + r_ref)


def port_origin(profile_axis, coordinates, points, port_axis, port_direction, port_plane, port_center):
    """
    (origin, sign, offset) of a port on a profile: the coordinate of its
    plane, the way into the line, and how far (m) the port lies off the
    profile (0 along an axis). None when they cannot be tied: a profile
    along another axis than the port's.
    profile_axis: 0..2, or < 0 for a path (points: each section's (3,) point).
    """
    if profile_axis >= 0:
        if profile_axis != port_axis:
            return None
        return float(port_plane), int(port_direction), 0.0
    p = np.asarray(points, dtype=float)
    c = np.asarray(coordinates, dtype=float)
    if len(p) < 2:
        return None
    center = np.asarray(port_center, dtype=float)
    distance = np.linalg.norm(p - center, axis=1)
    nearest = int(np.argmin(distance))
    # Along the polyline of the section points: the coordinate of the
    # point nearest the port's center, the line's direction there.
    a, b = (nearest, nearest + 1) if nearest + 1 < len(p) else (nearest - 1, nearest)
    tangent = (p[b] - p[a]) / max(c[b] - c[a], 1e-12)
    x = c[a] + float(np.dot(center - p[a], tangent) / max(np.dot(tangent, tangent), 1e-24))
    lo, hi = float(c.min()), float(c.max())
    x = min(max(x, lo), hi)
    into = np.zeros(3)
    into[port_axis] = port_direction
    along = float(np.dot(tangent / max(np.linalg.norm(tangent), 1e-24), into))
    if abs(along) > 0.3:
        sign = 1 if along > 0 else -1
    else:                   # the port's line turns onto the path: toward its far end
        sign = 1 if x - lo <= hi - x else -1
    return x, sign, float(distance[nearest])
