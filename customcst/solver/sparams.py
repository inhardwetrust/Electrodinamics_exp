# sparams.py
#
# Port quantities from a finished FDTD run.
#
# Each port's reference impedance is its own R. With V = V(+) - V(-) and I
# the current delivered into the structure through the + terminal (the
# solver's gap current), the power waves are
#   a = (V + R I) / (2 sqrt R)     toward the structure
#   b = (V - R I) / (2 sqrt R)     out of the structure
# and S_j,k = b_j / a_k for the run in which port k was excited.
# Z_in(f) = V / I at the excited port (X > 0 inductive).

import numpy as np


def port_waves(solver, frequencies):
    """{port name: (a(f), b(f))}."""
    resistances = solver.reference_impedances()
    out = {}
    for name, (v, i) in solver.spectra(frequencies).items():
        r = resistances[name]
        out[name] = ((v + r * i) / (2 * np.sqrt(r)), (v - r * i) / (2 * np.sqrt(r)))
    return out


def s_column(solver, frequencies):
    """{port name: S_port,active(f)} for the solver's excited port."""
    waves = port_waves(solver, frequencies)
    a_active = waves[solver.active_port][0]
    return {name: b / a_active for name, (_, b) in waves.items()}


def input_impedance(solver, frequencies):
    v, i = solver.spectra(frequencies)[solver.active_port]
    return v / i


def db(x):
    return 20.0 * np.log10(np.maximum(np.abs(x), 1e-300))
