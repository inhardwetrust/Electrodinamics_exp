# check_fdtd_cavity.py
#
# Level 4, closed problems (no PML, no geometry import):
#   - rectangular PEC cavity a x b x d on a deliberately NON-uniform grid:
#     the TE101 resonance must match c/2 sqrt(1/a^2 + 1/d^2);
#   - full filling with eps_r = 4 scales f by 1/sqrt(eps_r);
#   - half filling along x (eps_r = 4 for x < a/2, interface parallel to E):
#     an exact 1D problem, Ey = A sin(k1 x) | B sin(k2 (a - x)), giving
#     k1 cot(k1 a/2) = -k2 cot(k2 a/2), k_i^2 = eps_i k0^2 - (pi/d)^2;
#   - energy is conserved after the source is gone (lossless, R -> huge).
#
#   python checks/check_fdtd_cavity.py

import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

import numpy as np                                                   # noqa: E402

from discretize.materials import DiscretePort, MaterialGrid, edge_shape   # noqa: E402
from mesh.rectilinear import RectilinearGrid                         # noqa: E402
from project.settings import C0                                      # noqa: E402
from solver.fdtd import FdtdSolver                                   # noqa: E402

FAILED = []
MM = 1e-3


def check(label, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {label} {detail}")
    if not ok:
        FAILED.append(label)


def graded_lines(length, n, ratio):
    """n cells over [0, length], steps growing geometrically to the middle."""
    half = n // 2
    steps = ratio ** np.arange(half)
    steps = np.concatenate((steps, steps[::-1]))[:n]
    if len(steps) < n:
        steps = np.append(steps, steps[-1])
    steps *= length / steps.sum()
    return np.concatenate(([0.0], np.cumsum(steps)))


def cavity(a, b, d, eps_fill=None, n=(24, 12, 18), ratio=1.12, resistance=1e9):
    lines = (graded_lines(a, n[0], ratio), graded_lines(b, n[1], ratio), graded_lines(d, n[2], ratio))
    grid = RectilinearGrid(lines)
    shape = grid.shape

    # Cells: optional dielectric filling (callable of the cell centers x, y, z).
    cell_eps = np.ones(shape)
    if eps_fill is not None:
        centers = [0.5 * (l[1:] + l[:-1]) for l in lines]
        xc, yc, zc = np.meshgrid(*centers, indexing="ij")
        cell_eps = np.where(eps_fill(xc, yc, zc), EPS_R, 1.0)

    from discretize.materials import _dual_harmonic, _edge_average
    h = [np.diff(l) for l in lines]
    eps_r = tuple(_edge_average(cell_eps, ax, h).astype(np.float32) for ax in range(3))
    sigma = tuple(np.zeros(edge_shape(shape, ax), np.float32) for ax in range(3))
    pec = tuple(np.zeros(edge_shape(shape, ax), bool) for ax in range(3))
    mu_r = tuple(_dual_harmonic(np.ones(shape), ax, h).astype(np.float32) for ax in range(3))

    # A probe/source port: one Ey edge near (a/3, b/2, d/3) (TE101 has Ey).
    i = int(np.argmin(np.abs(lines[0] - a / 3)))
    k = int(np.argmin(np.abs(lines[2] - d / 3)))
    j = shape[1] // 2
    port = DiscretePort("p", 1, 1, np.array([[i, j, k]]), resistance, float(h[1][j]))
    return MaterialGrid(grid, eps_r, sigma, mu_r, pec, tuple(p.copy() for p in pec),
                        np.full(shape, -1), [], ports=[port])


EPS_R = 4.0


def resonance(materials, f_lo, f_hi, steps=12000):
    solver = FdtdSolver(materials, f_lo / 1e9, f_hi / 1e9, active_port="p", pml_cells=0)
    solver.step(steps)
    f = np.linspace(f_lo, f_hi, 4000)
    v, _ = solver.spectra(f)["p"]
    # No division by the pulse spectrum: near the band edges it would blow
    # up the truncation side lobes of a strong (lossless) resonance.
    spectrum = np.abs(v)
    k = int(np.argmax(spectrum))
    # Parabolic refinement of the peak.
    if 0 < k < len(f) - 1:
        y0, y1, y2 = np.log(spectrum[k - 1:k + 2])
        k = k + 0.5 * (y0 - y2) / (y0 - 2 * y1 + y2)
    return float(np.interp(k, np.arange(len(f)), f)), solver


def half_filled_x(a, d):
    """Lowest root of k1 cot(k1 a/2) + k2 cot(k2 a/2) = 0 (k2 may be imaginary)."""
    from scipy.optimize import brentq

    kz = math.pi / d

    def g(f):
        k0 = 2 * math.pi * f / C0
        k1 = math.sqrt(EPS_R * k0 ** 2 - kz ** 2)
        k2sq = k0 ** 2 - kz ** 2
        left = k1 / math.tan(k1 * a / 2)
        if k2sq >= 0:
            k2 = math.sqrt(k2sq)
            right = k2 / math.tan(k2 * a / 2)
        else:
            q = math.sqrt(-k2sq)                 # sin -> sinh: q coth(q a/2)
            right = q / math.tanh(q * a / 2)
        return left + right

    f_lo = C0 * kz / (2 * math.pi * math.sqrt(EPS_R)) * 1.0001
    f_full = C0 / 2 * math.sqrt(1 / a ** 2 + 1 / d ** 2)
    fs = np.linspace(f_lo, f_full, 4000)
    values = [g(f) for f in fs]
    for f0, f1, v0, v1 in zip(fs[:-1], fs[1:], values[:-1], values[1:]):
        if v0 * v1 < 0 and abs(v0) < 1e6 and abs(v1) < 1e6:
            return brentq(g, f0, f1)
    raise RuntimeError("no root")


def main():
    a, b, d = 40 * MM, 15 * MM, 30 * MM
    f101 = C0 / 2 * math.sqrt(1 / a ** 2 + 1 / d ** 2)

    f, solver = resonance(cavity(a, b, d), 0.7 * f101, 1.25 * f101)
    check("empty cavity on a graded grid: TE101", abs(f / f101 - 1) < 5e-3,
          f"{f / 1e9:.4f} GHz vs {f101 / 1e9:.4f} GHz ({(f / f101 - 1) * 100:+.2f} %)")
    steps = [np.diff(l) for l in solver.lines]
    ratio = max(np.max(np.maximum(s[1:] / s[:-1], s[:-1] / s[1:])) for s in steps)
    check("  (the grid really is non-uniform)", ratio > 1.1, f"step ratio {ratio:.2f}")

    # Energy after the pulse: a lossless cavity keeps it (port R = 1e9 Ohm).
    # Only the electric part is computed, which oscillates between E and H:
    # compare averages over many periods.
    def mean_energy():
        samples = []
        for _ in range(200):
            solver.step(7)
            samples.append(solver.energy())
        return np.mean(samples)

    w0 = mean_energy()
    solver.step(6000)
    w1 = mean_energy()
    check("lossless cavity keeps its energy", abs(w1 / w0 - 1) < 0.02,
          f"<W_E> later / earlier = {w1 / w0:.4f}")

    ff, _ = resonance(cavity(a, b, d, eps_fill=lambda x, y, z: np.ones_like(x, bool)),
                      0.7 * f101 / 2, 1.25 * f101 / 2)
    check("fully filled eps_r = 4: f / 2", abs(ff / (f101 / 2) - 1) < 1e-2,
          f"{ff / 1e9:.4f} GHz vs {f101 / 2e9:.4f} GHz")

    expected = half_filled_x(a, d)
    fh, _ = resonance(cavity(a, b, d, eps_fill=lambda x, y, z: x < a / 2),
                      0.8 * expected, 1.2 * expected)
    check("half filled along x: exact transcendental solution", abs(fh / expected - 1) < 1e-2,
          f"{fh / 1e9:.4f} vs {expected / 1e9:.4f} GHz ({(fh / expected - 1) * 100:+.2f} %)")

    print("FAILED: " + ", ".join(FAILED) if FAILED else "all checks passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
