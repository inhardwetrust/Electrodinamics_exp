# ports.py
#
# A port is drawn in CAD as a truncated cone between two conductors:
#   - its axis is the port (the path where V is integrated and the source
#     current flows);
#   - the SMALL end is "+", the large end is "-": polarity lives in the CAD.
# Each end must lie inside or on a conductor (PEC body), the two ends on
# different conductors. The active part of the port is the gap: the piece of
# the axis that is outside all conductors (ends drawn on the outer copper
# faces are fine).
#
# Both ends may be on the same PEC body when the axis crosses a gap of it:
# a copper layer imported from KiCad (geometry/kicad_step.py) is one body
# holding both the trace and the ground pour, and a slot feed lies across
# one plane. With no gap the ends are shorted: an error.
#
# The port body itself is only a marker: it is never meshed as a material.
#
# Lumped elements (R, L, C) use the same geometry without polarity
# (polar=False): a plain cylinder is fine, "minus" / "plus" are just the two
# ends in file order.

from dataclasses import dataclass
from typing import Optional

import numpy as np

from geometry.queries import points_in_or_on, touches

GAP_SAMPLES = 2001


@dataclass
class PortGeometry:
    name: str
    error: Optional[str] = None
    minus: Optional[np.ndarray] = None       # end points of the axis (m)
    plus: Optional[np.ndarray] = None
    minus_body: str = ""
    plus_body: str = ""
    gap_minus: Optional[np.ndarray] = None   # the active gap, "-" side -> "+" side
    gap_plus: Optional[np.ndarray] = None

    @property
    def ok(self):
        return self.error is None

    @property
    def gap_length(self):
        return float(np.linalg.norm(self.gap_plus - self.gap_minus))


def port_geometry(body, conductors, polar=True):
    """PortGeometry of a port body; conductors: the PEC bodies of the model."""
    result = PortGeometry(body.name)

    ends = _cone_ends(body, polar)
    if isinstance(ends, str):
        result.error = ends
        return result

    (minus, _), (plus, _) = ends
    result.minus, result.plus = minus, plus
    length = float(np.linalg.norm(plus - minus))
    tolerance = max(1e-7, 1e-4 * length)

    for label, point, attr in (("-", minus, "minus_body"), ("+", plus, "plus_body")):
        touching = [c.name for c in conductors if touches(c, point, tolerance)]

        if not touching:
            result.error = f"{label} end touches no PEC body"
            return result
        if len(touching) > 1:
            result.error = f"{label} end touches several PEC bodies: {', '.join(touching)}"
            return result

        setattr(result, attr, touching[0])

    # The gap: axis samples outside every conductor must form one run.
    s = np.linspace(0.0, 1.0, GAP_SAMPLES)
    samples = minus + s[:, None] * (plus - minus)
    metal = np.zeros(len(s), dtype=bool)
    for c in conductors:
        metal |= points_in_or_on(c, samples, tolerance)

    free = np.nonzero(~metal)[0]
    if len(free) == 0:
        result.error = (f"both ends are on the same conductor ({result.minus_body}), no gap between them"
                        if result.minus_body == result.plus_body else
                        "the axis has no gap between the conductors")
        return result
    if np.any(np.diff(free) != 1):
        result.error = "the axis crosses a third conductor (more than one gap)"
        return result

    # Gap ends: bisect between the last metal and the first free sample.
    def is_metal(t):
        point = minus + t * (plus - minus)
        return any(points_in_or_on(c, point, tolerance)[0] for c in conductors)

    s0 = _bisect(is_metal, s[free[0] - 1], s[free[0]]) if free[0] > 0 else 0.0
    s1 = _bisect(is_metal, s[free[-1] + 1], s[free[-1]]) if free[-1] < len(s) - 1 else 1.0
    result.gap_minus = minus + s0 * (plus - minus)
    result.gap_plus = minus + s1 * (plus - minus)
    return result


def _bisect(is_metal, t_metal, t_free, steps=40):
    """Boundary between a metal and a free parameter value on the axis."""
    for _ in range(steps):
        mid = 0.5 * (t_metal + t_free)
        if is_metal(mid):
            t_metal = mid
        else:
            t_free = mid
    return 0.5 * (t_metal + t_free)


def _cone_ends(body, polar=True):
    """((minus center, r), (plus center, r)) or an error string."""
    circles = body.circles
    shape = "a truncated cone" if polar else "a cylinder or cone"

    if body.kind != "solid" or len(circles) != 2:
        return f"must be {shape} (two circular ends), found {len(circles)} circle(s)"

    (c0, a0, r0), (c1, a1, r1) = ((row[:3], row[3:6], row[6]) for row in circles)
    axis = c1 - c0
    length = np.linalg.norm(axis)

    if length <= 0 or abs(abs(np.dot(a0, a1)) - 1.0) > 1e-6 \
            or abs(abs(np.dot(axis / length, a0)) - 1.0) > 1e-6:
        return "the two circles are not coaxial"

    if not polar:
        return (c0, r0), (c1, r1)

    if abs(r0 - r1) <= 1e-6 * max(r0, r1):
        return "both ends have the same radius: make the + end smaller (truncated cone)"

    return ((c0, r0), (c1, r1)) if r0 > r1 else ((c1, r1), (c0, r0))
