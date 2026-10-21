# vna.py
#
# VNA-style views of an S-matrix (no Qt): the usual display formats, marker
# readouts and Touchstone export. Everything is derived from the S
# parameters of one finished simulation.
#
#   LogMag      20 log10 |S|                    dB
#   LinMag      |S|
#   Phase       arg S                           deg, wrapped to (-180, 180]
#   Group delay -d(unwrapped phase)/d(omega)    s
#   SWR         (1 + |S|) / (1 - |S|)           reflection traces only
#   Smith       Gamma = S_ii in the unit circle; Z = R (1 + G) / (1 - G)

import math

import numpy as np

FORMATS = ("LogMag", "Phase", "Group delay", "SWR", "LinMag", "Smith", "Z in")


def logmag(s):
    return 20.0 * np.log10(np.maximum(np.abs(s), 1e-300))


def phase_deg(s, unwrap=False):
    """Phase in degrees: in (-180, 180], or continuous along frequency (unwrap)."""
    angle = np.angle(s)
    return np.degrees(np.unwrap(angle) if unwrap else angle)


def group_delay(s, frequencies):
    """-d phi / d omega with the phase unwrapped along frequency (seconds)."""
    phi = np.unwrap(np.angle(s))
    return -np.gradient(phi, 2.0 * math.pi * np.asarray(frequencies))


def swr(s):
    m = np.abs(s)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(m < 1.0, (1.0 + m) / (1.0 - m), np.inf)


def gamma_to_z(gamma, r_ref):
    with np.errstate(divide="ignore", invalid="ignore"):
        return r_ref * (1.0 + gamma) / (1.0 - gamma)


def z_to_gamma(z, r_ref):
    return (z - r_ref) / (z + r_ref)


def renormalize(s, r_old, r_new):
    """
    S-matrix referred to new (real) port impedances, like a VNA's port
    impedance setting or CST's "normalize to fixed impedance".
    s: {(j, k): (F,) array}, the full n x n matrix; r_old: (n,) Ohm; r_new: Ohm.
    Power waves (Kurokawa), real references:
      G_i = (r_new - r_old_i) / (r_new + r_old_i),  A_i = 2 sqrt(r_new r_old_i) / (r_new + r_old_i)
      S' = A^-1 (S - G) (I - G S)^-1 A
    Returns the same dict layout.
    """
    r_old = np.asarray(r_old, dtype=float)
    n = len(r_old)
    m = np.stack([np.stack([np.asarray(s[j, k]) for k in range(n)], axis=-1) for j in range(n)], axis=-2)
    g = (r_new - r_old) / (r_new + r_old)
    a = 2.0 * np.sqrt(r_new * r_old) / (r_new + r_old)
    eye = np.eye(n)
    out = (m - np.diag(g)) @ np.linalg.inv(eye - g[:, None] * m)          # G S: row i times g_i
    out = out * a[None, :] / a[:, None]                                    # A^-1 (...) A
    return {(j, k): out[:, j, k] for j in range(n) for k in range(n)}


def reactance_element(x, frequency):
    """'L = 1.23 nH' for X > 0, 'C = 4.56 pF' for X < 0 (series equivalent)."""
    w = 2.0 * math.pi * frequency
    if abs(x) < 1e-12:
        return "X = 0"
    if x > 0:
        return f"L = {x / w * 1e9:.3g} nH"
    return f"C = {-1.0 / (w * x) * 1e12:.3g} pF"


def is_reflection(j, k):
    return j == k


def marker_readout(fmt, s, frequency, r_ref, j, k):
    """One text line for trace S_jk at the marker (value of S at that f)."""
    name = f"S{j + 1}{k + 1}"
    mag_db = float(logmag(s))
    ang = float(phase_deg(s))
    text = f"{name}: {mag_db:6.2f} dB  {ang:7.1f}°"
    if is_reflection(j, k):
        z = complex(gamma_to_z(s, r_ref))
        text += f"  SWR {float(swr(s)):.3f}  Z = {z.real:.1f} {'+' if z.imag >= 0 else '−'} j{abs(z.imag):.1f} Ω"
        text += f"  ({reactance_element(z.imag, frequency)})"
    return text


def write_touchstone(path, frequencies, s, ports, r_ref, comment=""):
    """
    Touchstone v1 (.sNp), RI format, frequency in GHz.
    s: {(j, k): array}. 2-port order per line: S11 S21 S12 S22; N-port: row
    by row (S_j1 ... S_jN), 4 pairs per line.
    """
    n = len(ports)
    lines = [f"! customcst: {comment}".rstrip(), f"! ports: {', '.join(ports)}",
             f"# GHz S RI R {r_ref:g}"]
    for m, f in enumerate(frequencies):
        if n == 2:
            order = [(0, 0), (1, 0), (0, 1), (1, 1)]
        else:
            order = [(j, k) for j in range(n) for k in range(n)]
        values = [s[jk][m] for jk in order]
        pairs = [f"{v.real: .9e} {v.imag: .9e}" for v in values]
        if n <= 2:
            lines.append(f"{f / 1e9:.9g} " + " ".join(pairs))
        else:
            for row in range(n):
                chunk = pairs[row * n:(row + 1) * n]
                for c in range(0, len(chunk), 4):
                    prefix = f"{f / 1e9:.9g} " if (row == 0 and c == 0) else "  "
                    lines.append(prefix + " ".join(chunk[c:c + 4]))
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")


def read_touchstone_2port(path):
    """Minimal reader for what write_touchstone writes (2 ports, RI, GHz)."""
    freqs, rows = [], []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.split("!")[0].strip()
            if not line or line.startswith("#"):
                continue
            values = [float(v) for v in line.split()]
            freqs.append(values[0] * 1e9)
            c = [complex(values[1 + 2 * n], values[2 + 2 * n]) for n in range(4)]
            rows.append(c)
    rows = np.array(rows)
    return np.array(freqs), {(0, 0): rows[:, 0], (1, 0): rows[:, 1], (0, 1): rows[:, 2], (1, 1): rows[:, 3]}
