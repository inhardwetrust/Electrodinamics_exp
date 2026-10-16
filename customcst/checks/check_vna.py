# check_vna.py
#
# VNA formats against hand-computable cases:
#   - Z = R -> Gamma = 0, SWR = 1; Z = 2R -> Gamma = 1/3, SWR = 2;
#     Z = R/2 -> Gamma = -1/3, SWR = 2; gamma_to_z inverts z_to_gamma;
#   - a series inductor / capacitor shows up as L / C at the marker;
#   - group delay of an ideal delay line exp(-j w tau) is tau;
#   - Touchstone round trip (2-port, RI) is exact to the printed digits.
#
#   python checks/check_vna.py

import math
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

import numpy as np                                                    # noqa: E402

from solver.vna import (                                              # noqa: E402
    gamma_to_z, group_delay, marker_readout, read_touchstone_2port,
    reactance_element, swr, write_touchstone, z_to_gamma,
)

FAILED = []


def check(label, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {label} {detail}")
    if not ok:
        FAILED.append(label)


def main():
    r = 50.0
    for z, g_expected, swr_expected in ((50, 0, 1), (100, 1 / 3, 2), (25, -1 / 3, 2)):
        g = z_to_gamma(complex(z), r)
        check(f"Z = {z} Ω: Γ = {g_expected:+.3f}, SWR = {swr_expected}",
              abs(g - g_expected) < 1e-12 and abs(swr(g) - swr_expected) < 1e-12,
              f"Γ = {g:.4f}, SWR = {swr(g):.4f}")

    zs = np.array([10 + 20j, 50 - 75j, 200 + 0j, 1 + 1j])
    check("gamma_to_z inverts z_to_gamma", np.allclose(gamma_to_z(z_to_gamma(zs, r), r), zs))

    f = 2.4e9
    w = 2 * math.pi * f
    check("X = wL -> L", reactance_element(w * 2e-9, f) == "L = 2 nH", reactance_element(w * 2e-9, f))
    check("X = -1/(wC) -> C", reactance_element(-1 / (w * 3e-12), f) == "C = 3 pF",
          reactance_element(-1 / (w * 3e-12), f))
    text = marker_readout("Smith", z_to_gamma(50 + 1j * w * 1e-9, r), f, r, 0, 0)
    check("marker readout shows Z and L", "Z = 50.0 + j15.1 Ω" in text and "L = 1 nH" in text, text)

    freqs = np.linspace(1e9, 5e9, 401)
    tau = 0.3e-9
    delay = group_delay(np.exp(-1j * 2 * math.pi * freqs * tau), freqs)
    check("group delay of exp(-j w tau) = tau", np.allclose(delay, tau, rtol=1e-6),
          f"{delay.mean() * 1e9:.6f} ns")

    s = {(0, 0): 0.1 * np.exp(1j * freqs / 1e9), (1, 0): 0.9 * np.exp(-2j * freqs / 1e9),
         (0, 1): 0.9 * np.exp(-2j * freqs / 1e9), (1, 1): -0.05 + 0.02j * np.ones_like(freqs)}
    path = os.path.join(tempfile.mkdtemp(), "t.s2p")
    write_touchstone(path, freqs, s, ["p1", "p2"], r, "test")
    f2, s2 = read_touchstone_2port(path)
    ok = np.allclose(f2, freqs) and all(np.allclose(s2[k], s[k], atol=1e-8) for k in s)
    check("Touchstone 2-port round trip (S11 S21 S12 S22 order)", ok)
    os.remove(path)

    print("FAILED: " + ", ".join(FAILED) if FAILED else "all checks passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
