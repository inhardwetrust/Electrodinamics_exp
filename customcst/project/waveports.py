# waveports.py
#
# A waveguide port is drawn in CAD as a short body standing on the end face
# of a line, outside the model:
#   - a cylinder (an extruded circle) for a coax: the port region on the
#     plane is the circle;
#   - a box for a PCB line (microstrip, coplanar): the region is the
#     rectangle of the face on the board edge. It must cover the line's
#     field (several line widths around the trace, air above and below)
#     and the ground must reach its border: the conductor that touches the
#     region's border is ground, the one inside is the signal.
# Common to both:
#   - the axis must be along x, y or z (the line's direction);
#   - the face that touches the model is the port plane, the opposite one
#     points away from it: the port feeds the line in the direction
#     outside face -> touching face;
# The port plane becomes the domain boundary: the line runs on through it
# into the absorber (solver/fdtd.py), as if it went on forever.
#
# The port body itself is only a marker: it is never meshed as a material.

from dataclasses import dataclass
from typing import Optional

import numpy as np

from geometry.queries import points_in_or_on

AXES = "xyz"
FACE_SAMPLES = 9            # per side: points tested on a box face for contact


@dataclass
class WavePortGeometry:
    name: str
    error: Optional[str] = None
    axis: int = 0               # normal of the port plane
    direction: int = 1          # +1: the line runs from the plane toward +axis
    plane: float = 0.0          # coordinate of the port plane along `axis` (m)
    center: Optional[np.ndarray] = None      # center of the port face (m)
    radius: float = 0.0         # m, cylinder ports
    region: Optional[tuple] = None           # ((u0, u1), (v0, v1)) m, box ports (u < v: the other axes)

    @property
    def ok(self):
        return self.error is None

    @property
    def shape(self):
        return "rectangle" if self.region is not None else "circle"


def waveport_geometry(body, materials):
    """WavePortGeometry of a port body; materials: the typed (PEC, dielectric) bodies."""
    if len(body.circles) == 0:
        return _box_port(body, materials)
    return _cylinder_port(body, materials)


def _cylinder_port(body, materials):
    result = WavePortGeometry(body.name)
    circles = body.circles

    if len(circles) != 2:
        result.error = (f"expected a cylinder (2 circular edges) or a box, "
                        f"found {len(circles)} circles")
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
    result.center = inner
    result.radius = float(r0)
    return _place(result, axis, inner[axis], outer[axis], materials, tolerance)


def _box_port(body, materials):
    result = WavePortGeometry(body.name)
    lo, hi = body.bounds
    size = hi - lo
    if body.kind != "solid" or np.any(size <= 0):
        result.error = "expected a box (or a cylinder for a coax port)"
        return result
    if abs(body.volume - np.prod(size)) > 1e-3 * np.prod(size):
        result.error = "not a box aligned with x, y, z (its volume differs from its bounding box)"
        return result

    tolerance = max(1e-7, 1e-4 * float(size.min()))
    candidates = []
    for axis in range(3):
        u, v = [a for a in range(3) if a != axis]
        # Inner points only: a box as wide as the board touches the board's
        # edges with the borders of its side faces, which is not contact.
        gu = _face_samples(lo[u], hi[u], [m.bounds for m in materials], u)
        gv = _face_samples(lo[v], hi[v], [m.bounds for m in materials], v)
        uu, vv = np.meshgrid(gu, gv, indexing="ij")
        touching = []
        for coordinate in (lo[axis], hi[axis]):
            points = np.zeros((uu.size, 3))
            points[:, axis], points[:, u], points[:, v] = coordinate, uu.ravel(), vv.ravel()
            touching.append(any(points_in_or_on(m, points, tolerance).any() for m in materials))
        if touching[0] != touching[1]:
            candidates.append((axis, touching[0]))

    if not candidates:
        result.error = "no face of the box touches the model (put it on the line's end face)"
        return result
    if len(candidates) > 1:
        result.error = ("more than one face of the box touches the model: "
                        "it must stand outside the model, on the line's end face only")
        return result

    axis, low_face = candidates[0]
    u, v = [a for a in range(3) if a != axis]
    inner, outer = (lo[axis], hi[axis]) if low_face else (hi[axis], lo[axis])
    result.region = ((float(lo[u]), float(hi[u])), (float(lo[v]), float(hi[v])))
    result.center = 0.5 * (lo + hi)
    result.center[axis] = inner
    return _place(result, axis, inner, outer, materials, tolerance)


def _face_samples(lo, hi, bounds, axis):
    """
    Sample coordinates strictly inside (lo, hi) along one side of a box
    face: the midpoints between the model's body bounds (and an even
    FACE_SAMPLES split), so a thin board or copper layer between two bounds
    always gets a point (an even 9 x 9 grid missed a 0.2 mm board).
    """
    cuts = [lo, hi] + list(lo + (np.arange(1, FACE_SAMPLES) / FACE_SAMPLES) * (hi - lo))
    for b_lo, b_hi in bounds:
        cuts += [c for c in (b_lo[axis], b_hi[axis]) if lo < c < hi]
    cuts = np.unique(cuts)
    return 0.5 * (cuts[1:] + cuts[:-1])


def _place(result, axis, inner, outer, materials, tolerance):
    """Port plane, direction, and the check that the model lies on the inner side."""
    result.axis = axis
    result.direction = 1 if inner > outer else -1
    result.plane = float(inner)
    for m in materials:
        lo, hi = m.bounds
        beyond = (result.plane - lo[axis]) if result.direction > 0 else (hi[axis] - result.plane)
        if beyond > tolerance:
            result.error = (f"{m.name} extends past the port plane "
                            f"({AXES[axis]} = {result.plane * 1e3:.4g} mm)")
            return result
    return result
