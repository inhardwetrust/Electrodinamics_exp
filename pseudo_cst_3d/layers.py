# layers.py
#
# Pure configuration of what to show and how. No VisPy here: the same
# objects will later drive the Qt control panel.

from dataclasses import dataclass, field

from params import Parameter


@dataclass
class ClimPolicy:
    """
    How the color limits of a scalar layer are chosen.

    mode:
        "fixed"   -> (lo, hi) as given
        "initial" -> percentiles of the view when the layer is created,
                     then frozen (colors stay comparable while navigating)
        "view"    -> percentiles of the current view, refreshed every time
                     the view settles after zoom/pan
        "running" -> for time-varying fields: percentiles of the current
                     view on every data update, combined with the previous
                     limits decaying by `decay` per update. Holds the peak
                     while a wave passes and slowly adapts afterwards.
                     Starts from (lo, hi) so a zero field at t = 0 is fine.

    Limits apply AFTER the layer's scale transform (e.g. to log10|E|).
    symmetric: lo = -hi, so zero sits in the palette middle (signed fields).
    """

    mode: str = "initial"
    lo: float = 0.0
    hi: float = 1.0
    percentiles: tuple = (2.0, 98.0)
    symmetric: bool = False
    decay: float = 0.99


@dataclass
class ScalarLayer:
    """
    Heatmap of a scalar quantity.

    scale:    "linear" | "log" (log10|v|) | "symlog"
              symlog = sign(v) * log10(1 + |v| / linthresh): keeps the sign
              of signed fields while compressing their huge dynamic range.
    colormap: "viridis" (magnitudes) | "diverging" (signed, zero = middle)
    """

    quantity: str
    colormap: str = "viridis"
    scale: str = "linear"
    linthresh: float = 1.0
    clim: ClimPolicy = field(default_factory=ClimPolicy)
    visible: bool = True


@dataclass
class VectorLayer:
    """
    Fixed-length arrows showing the DIRECTION of a vector quantity, with
    three levels of strength (length stays a UI choice, not the value):

        strong  |v| >= weak_fraction * ref   -> full arrow
        weak    |v| >= zero_fraction * ref   -> short, faded arrow
        zero    below                         -> small ring (or nothing);
                if the field points through the plane instead (3D slices),
                a dot / cross marks out of / into the screen

    |v| is what the arrow can show (the in-plane projection on a slice);
    ref is the 98th percentile of the FULL magnitude ("|name|" when the
    source has it) over the visible arrows - so a strong field crossing
    the plane reads as "zero in-plane, pointing out of the plane".
    """

    quantity: str
    spacing_px: float = 70.0
    length_px: float = 30.0
    color: str = "white"
    line_width: float = 2.0
    head_type: str = "stealth"
    head_size: float = 8.0
    weak_fraction: float = 0.25
    zero_fraction: float = 0.05
    weak_length_scale: float = 0.5
    show_zero_markers: bool = True
    show_normal_markers: bool = True
    visible: bool = True


# =====================================================
# Schemas (drive the Qt layer panel)
# =====================================================

SCALES = ("linear", "log", "symlog")
CLIM_MODES = ("running", "initial", "view", "fixed")
ARROW_COLORS = ("white", "black", "yellow", "cyan", "magenta")


def scalar_layer_parameters(source, palettes):
    quantities = tuple(q.name for q in source.quantities() if q.kind == "scalar")

    return [
        Parameter("visible", "Visible", "bool"),
        Parameter("quantity", "Quantity", "choice", choices=quantities),
        Parameter("scale", "Scale", "choice", choices=SCALES),
        Parameter(
            "linthresh", "Linear range", "float", minimum=1e-9, maximum=1e9,
            decimals=6, enabled_when=("scale", ("symlog",)),
        ),
        Parameter("colormap", "Palette", "choice", choices=tuple(palettes)),
        Parameter("clim.mode", "Limits", "choice", choices=CLIM_MODES),
        Parameter("clim.symmetric", "Symmetric (zero in middle)", "bool"),
        Parameter(
            "clim.lo", "Lower limit", "float", minimum=-1e12, maximum=1e12,
            decimals=6, enabled_when=("clim.mode", ("fixed",)),
        ),
        Parameter(
            "clim.hi", "Upper limit", "float", minimum=-1e12, maximum=1e12,
            decimals=6, enabled_when=("clim.mode", ("fixed",)),
        ),
        Parameter(
            "clim.decay", "Peak decay / frame", "float", minimum=0.5,
            maximum=1.0, step=0.001, decimals=4,
            enabled_when=("clim.mode", ("running",)),
        ),
    ]


def vector_layer_parameters(source):
    quantities = tuple(q.name for q in source.quantities() if q.kind == "vector")

    return [
        Parameter("visible", "Visible", "bool"),
        Parameter("quantity", "Quantity", "choice", choices=quantities),
        Parameter("spacing_px", "Spacing", "float", minimum=15, maximum=300, step=5, decimals=0, unit="px"),
        Parameter("length_px", "Length", "float", minimum=5, maximum=150, step=1, decimals=0, unit="px"),
        Parameter("weak_fraction", "Weak below (fraction of peak)", "float", minimum=0.0, maximum=1.0, step=0.01, decimals=3),
        Parameter("zero_fraction", "Zero below (fraction of peak)", "float", minimum=0.0, maximum=1.0, step=0.01, decimals=3),
        Parameter("weak_length_scale", "Weak arrow length", "float", minimum=0.1, maximum=1.0, step=0.05, decimals=2, unit="x"),
        Parameter("show_zero_markers", "Mark ~zero (ring)", "bool"),
        Parameter("show_normal_markers", "Mark through-plane (dot / cross)", "bool"),
        Parameter("color", "Color", "choice", choices=ARROW_COLORS),
        Parameter("line_width", "Line width", "float", minimum=1, maximum=8, step=0.5, decimals=1, unit="px"),
        Parameter("head_size", "Head size", "float", minimum=2, maximum=30, step=1, decimals=0, unit="px"),
    ]
