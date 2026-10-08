# waveforms.py
#
# Port excitation signals shared by the 2D and 3D FDTD solvers.

import math

WAVEFORMS = ("sine", "gaussian", "modulated_gaussian")


def waveform(kind, t, frequency, amplitude):
    """Port current I(t)."""
    f = float(frequency)
    a = float(amplitude)

    if kind == "sine":
        # Smooth start over two periods avoids a broadband switch-on click.
        ramp_time = 2.0 / f
        ramp = (
            0.5 * (1.0 - math.cos(math.pi * t / ramp_time))
            if t < ramp_time
            else 1.0
        )
        return a * ramp * math.sin(2.0 * math.pi * f * t)

    if kind == "gaussian":
        # Broadband pulse; spectrum width ~ f. Moves net charge (DC content).
        tau = 0.5 / f
        t0 = 4.0 * tau
        return a * math.exp(-((t - t0) / tau) ** 2)

    if kind == "modulated_gaussian":
        # Band around f, no DC content.
        tau = 1.0 / f
        t0 = 4.0 * tau
        return (
            a
            * math.sin(2.0 * math.pi * f * (t - t0))
            * math.exp(-((t - t0) / tau) ** 2)
        )

    raise ValueError(f"Unknown waveform {kind!r}; available: {WAVEFORMS}")
