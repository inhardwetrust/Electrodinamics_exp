# waveports.py
#
# A waveguide port is drawn in CAD as a short cylinder (an extruded circle)
# standing on the end face of a line, outside the model:
#   - its axis must be along x, y or z (the line's direction);
#   - the cap that touches the model is the port plane, the other cap
#     points away from it: the port feeds the line in the direction
#     outside cap -> touching cap;
#   - its radius bounds the port region on the plane: everything inside the
#     circle belongs to the port's cross-section; the conductor that reaches
#     the circle (the coax shield) is ground, the one inside is the signal.
# The port plane becomes the domain boundary: the line runs on through it
# into the absorber (solver/fdtd.py), as if it went on forever.
#
# The port body itself is only a marker: it is never meshed as a material.

from dataclasses import dataclass
from typing import Optional

import numpy as np

from geometry.queries import points_in_or_on

AXES = "xyz"


@dataclass
class WavePortGeometry:
    name: str
    error: Optional[str] = None
    axis: int = 0               # normal of the port plane
    direction: int = 1          # +1: the line runs from the plane toward +axis
    plane: float = 0.0          # coordinate of the port plane along `axis` (m)
    center: Optional[np.ndarray] = None      # center of the port circle (m)
    radius: float = 0.0         # m

    @property
    def ok(self):
        return self.error is None


def waveport_geometry(body, materials):
    """WavePortGeometry of a port cylinder; materials: the typed (PEC, dielectric) bodies."""
    result = WavePortGeometry(body.name)
    circles = body.circles

    if len(circles) != 2:
        result.error = f"expected a cylinder (2 circular edges), found {len(circles)} circles"
        return result

    c0, c1 = circles[0, :3], circles[1, :3]
    r0, r1 = circles[0, 6], circles[1, 6]
    if abs(r0 - r1) > 1e-6 * max(r0, r1):
        result.error = "the two circles differ: draw a cylinder, not a cone"
        return result

    path = c1 - c0
    length = float(np.linalg.norm(path))
    if length == 0:
        result.error = "the two circles coincide"
        return result
    axis = int(np.argmax(np.abs(path)))
    if np.linalg.norm(np.delete(path, axis)) > 1e-6 * length:
        result.error = "the cylinder axis is not along x, y or z"
        return result

    tolerance = max(1e-7, 1e-4 * length)
    touching = [any(points_in_or_on(m, c, tolerance)[0] for m in materials) for c in (c0, c1)]
    if touching[0] == touching[1]:
        result.error = ("neither cap touches the model" if not touching[0] else
                        "both caps touch the model: put the port outside, on the line's end face")
        return result

    inner, outer = (c0, c1) if touching[0] else (c1, c0)
    result.axis = axis
    result.direction = 1 if inner[axis] > outer[axis] else -1
    result.plane = float(inner[axis])
    result.center = inner
    result.radius = float(r0)

    # The model must lie on the inner side of the plane.
    for m in materials:
        lo, hi = m.bounds
        beyond = (result.plane - lo[axis]) if result.direction > 0 else (hi[axis] - result.plane)
        if beyond > tolerance:
            result.error = (f"{m.name} extends past the port plane "
                            f"({AXES[axis]} = {result.plane * 1e3:.4g} mm)")
            return result

    return result
