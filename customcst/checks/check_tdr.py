# check_tdr.py
#
# Time domain reflectometry (solver/tdr.py) against cases known by hand,
# on a 1-10 GHz band of 401 points like the runs':
#   - a single reflection rho exp(-j w T): the low pass impulse peaks at
#     rho at T, the step rises to rho there; the band pass envelope peaks
#     at |rho| at T;
#   - the continuation below f_min: a 5 mm 60 Ohm line ending in a 30 Ohm
#     load reads S(DC) of the load (-0.25) within 0.01, not doubtful; a
#     2 pF DC block 10 mm in, then 50 Ohm (an RC tail mostly below 1 GHz:
#     the band does not determine its DC), is flagged doubtful;
#   - a 20 mm 60 Ohm line between 50 Ohm (cascade_s11 against the exact
#     two-step formula, then the transform): the step reads 60 Ohm in its
#     middle within 2 %, back to 50 behind it, its edges at the line's
#     round trips (half height) within 3 ps;
#   - a 1 mm 60 Ohm pad: smeared to a bump far below 60 Ohm (resolution);
#   - the resolution of the normal window is about 1 / f_max;
#   - line_map: a 5 mm coax (eps 2.1) then a board line (eps 3.0): the
#     time of a point 12 mm in is the sum of both delays, coordinate()
#     inverts time(), the gaps are skipped, a port at the far end maps
#     into the line backwards;
#   - port_origin: along the port's axis; another axis cannot be tied; on
#     a path, the port's center at the path's start, the line's way in.
#
#   python checks/check_tdr.py      (~2 s)

import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

import numpy as np                                                    # noqa: E402

from project.settings import C0                                       # noqa: E402
from solver.tdr import (                                              # noqa: E402
    cascade_s11, harmonic, line_map, port_origin, transform,
)

FAILED = []
F = np.linspace(1e9, 10e9, 401)
PS = 1e-12


def check(label, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {label} {detail}")
    if not ok:
        FAILED.append(label)


def line_s11(f, sections, load, r=50.0):
    """Exact S11 of uniform lines [(Z0, eps_eff, length m)] from the port, ending in `load`."""
    z = np.full(len(f), complex(load))
    for z0, e, length in reversed(sections):
        t = 1j * np.tan(2 * math.pi * f * math.sqrt(e) / C0 * length)
        z = z0 * (z + z0 * t) / (z0 + z * t)
    return (z - r) / (z + r)


def main():
    # A single reflection.
    rho, delay = 0.2, 300 * PS
    s = rho * np.exp(-2j * math.pi * F * delay)
    lp = transform(F, s)
    check("a far reflection: S(DC) not doubtful", not lp.doubtful, f"spread {lp.dc_spread:.4f}")
    peak = int(np.argmax(lp.impulse))
    check("low pass impulse: peak rho at T", abs(lp.impulse[peak] - rho) < 0.004 and abs(lp.t[peak] - delay) < 1 * PS,
          f"{lp.impulse[peak]:.4f} at {lp.t[peak] / PS:.1f} ps")
    at = lambda t: float(np.interp(t, lp.t, lp.step))                  # noqa: E731
    check("low pass step: 0 before T, rho after, half at T",
          abs(at(delay - 150 * PS)) < 0.005 and abs(at(delay + 150 * PS) - rho) < 0.005
          and abs(at(delay) - rho / 2) < 0.01,
          f"{at(delay - 150 * PS):.4f} / {at(delay):.4f} / {at(delay + 150 * PS):.4f}")
    bp = transform(F, s, "band pass")
    peak = int(np.argmax(bp.impulse))
    check("band pass envelope: peak |rho| at T", abs(bp.impulse[peak] - rho) < 0.004 and abs(bp.t[peak] - delay) < 1 * PS,
          f"{bp.impulse[peak]:.4f} at {bp.t[peak] / PS:.1f} ps")
    check("resolution of the normal window ~ 1 / f_max", 0.6 / F[-1] < lp.rise < 1.4 / F[-1],
          f"rise {lp.rise / PS:.1f} ps, band pass width {bp.rise / PS:.1f} ps")

    # Below f_min: the load's DC value.
    s = line_s11(F, [(60.0, 3.0, 5e-3)], 30.0)
    _, g, spread = harmonic(F, s)
    check("continued to DC: S(DC) of the load", abs(g[0] - (-0.25)) < 0.01 and not transform(F, s).doubtful,
          f"{g[0].real:+.4f} (exact -0.2500), spread {spread:.4f}")
    w = 2 * math.pi * F
    zl = 50.0 + 1.0 / (1j * w * 2e-12)
    t = 1j * np.tan(w * math.sqrt(3.0) / C0 * 10e-3)
    zin = 50.0 * (zl + 50.0 * t) / (50.0 + zl * t)
    r = transform(F, (zin - 50.0) / (zin + 50.0))
    check("continued to DC: a DC block is doubtful", r.doubtful, f"S(DC) {r.dc.real:+.4f} ± {r.dc_spread:.3f} (exact +1)")

    # A long 60 Ohm line between 50 Ohm, through the cascade.
    e, length = 3.0, 20e-3
    lead = 10e-3
    line = line_map([0.0, lead - 1e-6, lead, lead + length - 1e-6, lead + length, 40e-3],
                    [50, 50, 60, 60, 50, 50], [e] * 6, 0.0, 1)
    s_cascade = cascade_s11(line, F, 50.0)
    s_exact = line_s11(F, [(50.0, e, lead), (60.0, e, length), (50.0, e, 40e-3 - lead - length)], 50.0)
    check("cascade_s11 = the exact steps", np.max(np.abs(s_cascade - s_exact)) < 0.01,
          f"max |ΔS| {np.max(np.abs(s_cascade - s_exact)):.4f}")
    lp = transform(F, s_exact)
    z = lp.impedance(50.0)
    t_in = 2 * lead * math.sqrt(e) / C0
    t_out = 2 * (lead + length) * math.sqrt(e) / C0
    middle = float(np.interp(0.5 * (t_in + t_out), lp.t, z))
    after = float(np.interp(t_out + 200 * PS, lp.t, z))
    check("60 Ohm line: 60 in its middle, 50 behind it", abs(middle - 60) < 1.2 and abs(after - 50) < 1.5,
          f"{middle:.2f} Ω, behind {after:.2f} Ω")
    rising = lp.t[np.argmax(z >= 55.0)]
    falling = lp.t[np.argmax((lp.t > t_in + 50 * PS) & (z <= 55.0))]
    check("its edges at the round trips (half height)", abs(rising - t_in) < 3 * PS and abs(falling - t_out) < 3 * PS,
          f"{rising / PS:.1f} / {falling / PS:.1f} ps, exact {t_in / PS:.1f} / {t_out / PS:.1f}")

    # A 1 mm pad: smeared.
    s = line_s11(F, [(50.0, e, lead), (60.0, e, 1e-3), (50.0, e, 20e-3)], 50.0)
    z = transform(F, s).impedance(50.0)
    check("1 mm 60 Ohm pad: a bump far below 60 Ohm", 50.3 < np.max(z) < 54.0, f"peak {np.max(z):.2f} Ω")

    # Delays along a coax and a board.
    c = np.r_[np.linspace(0, 5e-3, 11), 5e-3 + 1e-6, np.linspace(5.1e-3, 20e-3, 30)]
    eps = np.where(c < 5e-3 + 1e-7, 2.1, 3.0)
    z0 = np.full(len(c), 50.0)
    z0[15] = np.nan                                             # a gap
    m = line_map(c, z0, eps, 0.0, 1)
    exact = 2 * (5e-3 * math.sqrt(2.1) + 7e-3 * math.sqrt(3.0)) / C0
    t12 = float(m.time(12e-3))
    check("line_map: coax then board, the delays add", abs(t12 - exact) < 0.2 * PS,
          f"{t12 / PS:.2f} ps, exact {exact / PS:.2f}")
    check("line_map: coordinate inverts time", abs(float(m.coordinate(np.array([t12]))[0]) - 12e-3) < 1e-6)
    back = line_map(c, z0, eps, 20e-3, -1)
    exact = 2 * (8e-3 * math.sqrt(3.0)) / C0
    check("line_map: a port at the far end, backwards", abs(float(back.time(12e-3)) - exact) < 0.2 * PS
          and abs(float(back.coordinate(np.array([exact]))[0]) - 12e-3) < 1e-6)

    # Ports on profiles.
    check("port_origin along its axis", port_origin(0, None, None, 0, -1, 7e-3, None) == (7e-3, -1, 0.0))
    check("port_origin: another axis cannot be tied", port_origin(1, None, None, 0, 1, 0.0, None) is None)
    pts = np.array([[1e-3 * k, 2e-3, 0.5e-3] for k in range(11)])
    origin = port_origin(-1, pts[:, 0] - 1e-3, pts, 0, 1, 1e-3, [1e-3, 2e-3, 0.6e-3])
    check("port_origin on a path: at its start, into it",
          origin is not None and abs(origin[0]) < 1e-9 and origin[1] == 1 and abs(origin[2] - 0.1e-3) < 1e-9,
          f"{origin}")

    print("\nall ok" if not FAILED else f"\nFAILED: {', '.join(FAILED)}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
