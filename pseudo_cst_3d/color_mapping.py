# color_mapping.py
#
# Scales and palettes shared by the GPU and CPU heatmap paths.
# Palettes are 256-entry LUTs: the GPU path samples them as a texture,
# the CPU path indexes them, so both produce identical colors.

import numpy as np

from vispy.color import get_colormap


LUT_SIZE = 256


# =====================================================
# Scales
# =====================================================

def apply_scale(scale, values, linthresh=1.0):
    """NumPy version of the scale transforms. NaN stays NaN."""
    values = np.asarray(values, dtype=np.float64)

    if scale == "linear":
        return values

    with np.errstate(divide="ignore", invalid="ignore"):
        if scale == "log":
            return np.log10(np.maximum(np.abs(values), 1e-30))

        if scale == "symlog":
            return np.sign(values) * np.log10(
                1.0 + np.abs(values) / float(linthresh)
            )

    raise ValueError(f"Unknown scale {scale!r}")


def glsl_scale(scale, linthresh=1.0):
    """Body of `float scale_value(float v)` matching apply_scale()."""
    if scale == "linear":
        return "return v;"

    if scale == "log":
        return "return log(max(abs(v), 1e-30)) / log(10.0);"

    if scale == "symlog":
        return (
            "return sign(v) * log(1.0 + abs(v) / %.9e) / log(10.0);"
            % float(linthresh)
        )

    raise ValueError(f"Unknown scale {scale!r}")


def compute_clim(values, percentiles=(2.0, 98.0), symmetric=False):
    """Percentile color limits of (already scaled) values."""
    values = np.asarray(values)
    values = values[np.isfinite(values)]

    if values.size == 0:
        return (-1.0, 1.0) if symmetric else (0.0, 1.0)

    lo = float(np.percentile(values, percentiles[0]))
    hi = float(np.percentile(values, percentiles[1]))

    if symmetric:
        hi = max(abs(lo), abs(hi))
        lo = -hi

    if hi <= lo:
        hi = lo + 1.0

    return (lo, hi)


def running_clim(previous, current, decay, scale, symmetric=False):
    """
    Peak-hold limits for time-varying fields.

    The upper limit jumps up immediately and otherwise falls as if the
    field AMPLITUDE were multiplied by `decay` per update:
        log scale   -> hi drops by log10(decay) decades
        linear      -> hi *= decay
        symlog      -> hi *= decay (approximation)
    The lower limit follows the upper one (log: same number of decades
    below it; otherwise the same ratio), so a field that momentarily drops
    to ~0 cannot drag the lower limit to -30 decades.

    previous: (lo, hi); current: (lo, hi) of this frame or None (no data).
    """
    prev_lo, prev_hi = previous
    decay = float(decay)

    if scale == "log":
        decayed_hi = prev_hi + np.log10(decay)
    else:
        decayed_hi = prev_hi * decay if prev_hi > 0.0 else prev_hi

    hi = decayed_hi if current is None else max(current[1], decayed_hi)

    if symmetric:
        hi = max(hi, 1e-30)
        return (-hi, hi)

    if scale == "log":
        return (hi - (prev_hi - prev_lo), hi)

    ratio = prev_lo / prev_hi if prev_hi > 0.0 else 0.0
    return (hi * ratio, hi)


# =====================================================
# Palettes
# =====================================================

# Moreland "cool to warm": blue (negative) - light gray (zero) - red (positive).
_DIVERGING_STOPS = np.array([0.0, 0.25, 0.5, 0.75, 1.0])
_DIVERGING_RGB = np.array([
    [0.230, 0.299, 0.754],
    [0.552, 0.690, 0.996],
    [0.865, 0.865, 0.865],
    [0.958, 0.604, 0.483],
    [0.706, 0.016, 0.150],
])


def _diverging_lut():
    t = np.linspace(0.0, 1.0, LUT_SIZE)
    rgb = np.stack(
        [np.interp(t, _DIVERGING_STOPS, _DIVERGING_RGB[:, c]) for c in range(3)],
        axis=1,
    )
    return np.column_stack((rgb, np.ones(LUT_SIZE)))


# Green (negative) - black (zero) - red (positive). Made for a black
# background: where the field is ~0 the view stays dark, waves glow.
_GREEN_RED_STOPS = np.array([0.0, 0.25, 0.5, 0.75, 1.0])
_GREEN_RED_RGB = np.array([
    [0.35, 1.00, 0.45],
    [0.05, 0.55, 0.15],
    [0.00, 0.00, 0.00],
    [0.62, 0.05, 0.04],
    [1.00, 0.30, 0.22],
])


# Magnitudes, like dipole_sim: no field = black, then green -> yellow ->
# orange -> red as the field gets stronger (the hue walks the color wheel
# from green to red, which is why it passes through yellow).
_HEAT_STOPS = np.array([0.0, 0.15, 0.35, 0.6, 0.8, 1.0])
_HEAT_RGB = np.array([
    [0.00, 0.00, 0.00],
    [0.00, 0.25, 0.12],
    [0.05, 0.75, 0.10],
    [0.95, 0.95, 0.10],
    [1.00, 0.55, 0.05],
    [1.00, 0.05, 0.00],
])

# Signed: purple (negative) - black (zero) - yellow (positive).
_PURPLE_YELLOW_STOPS = np.array([0.0, 0.25, 0.5, 0.75, 1.0])
_PURPLE_YELLOW_RGB = np.array([
    [0.80, 0.40, 1.00],
    [0.38, 0.08, 0.55],
    [0.00, 0.00, 0.00],
    [0.55, 0.45, 0.02],
    [1.00, 0.92, 0.20],
])


def _interpolated_lut(stops, rgb):
    t = np.linspace(0.0, 1.0, LUT_SIZE)
    channels = [np.interp(t, stops, rgb[:, c]) for c in range(3)]
    return np.column_stack(channels + [np.ones(LUT_SIZE)])


_PALETTE_BUILDERS = {
    "heat": lambda: _interpolated_lut(_HEAT_STOPS, _HEAT_RGB),
    "purple_yellow": lambda: _interpolated_lut(_PURPLE_YELLOW_STOPS, _PURPLE_YELLOW_RGB),
    "green_red": lambda: _interpolated_lut(_GREEN_RED_STOPS, _GREEN_RED_RGB),
    "viridis": lambda: get_colormap("viridis").map(
        np.linspace(0.0, 1.0, LUT_SIZE)
    ),
    "diverging": _diverging_lut,
}

_LUT_CACHE = {}


def palette_names():
    return list(_PALETTE_BUILDERS)


def get_lut(name):
    """RGBA float32 LUT of shape (LUT_SIZE, 4)."""
    if name not in _LUT_CACHE:
        if name not in _PALETTE_BUILDERS:
            raise ValueError(
                f"Unknown palette {name!r}; available: {palette_names()}"
            )

        _LUT_CACHE[name] = np.asarray(
            _PALETTE_BUILDERS[name](), dtype=np.float32
        )

    return _LUT_CACHE[name]


def map_to_rgba(scaled_values, clim, lut):
    """CPU colorization; NaN -> fully transparent."""
    lo, hi = clim
    t = np.clip((scaled_values - lo) / (hi - lo), 0.0, 1.0)
    index = (np.nan_to_num(t) * (LUT_SIZE - 1) + 0.5).astype(np.int32)

    rgba = lut[index]
    rgba[~np.isfinite(scaled_values), 3] = 0.0
    return rgba
