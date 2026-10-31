# section.py
#
# A section of the model by a plane normal to an axis (like CST's or
# SolidWorks' section view), for checking the geometry in the program:
#   - fill: which body each point of the plane lies in, on a regular pixel
#     grid over the model's extent (ray casting along an in-plane axis,
#     geometry/raycast.py inside_on_grid). Sampled a hair inside the kept
#     half, so a plane exactly on a face (the mesh lines sit on faces)
#     shows the body on the kept side. Later bodies paint over earlier
#     ones: the caller orders dielectrics before conductors. The pixels
#     are not square: max_pixels along each in-plane axis, so a vertical
#     section of a board (60 mm along, 7 mm high) has ~8 um pixels in z
#     and its 35 um copper is ~4 rows of its own color (square pixels of
#     ~0.07 mm missed it: the trace's end came out green). A body still
#     thinner than two pixels across one axis paints the pixels its exact
#     outline passes through (after the fill, in the same order): at least
#     one pixel of its own color, a hair wider than it is;
#   - outline: the exact intersection of each body's triangles with the
#     plane, as segments: a 15 um copper layer is far thinner than a pixel
#     in a vertical section, the outline still shows it (and sheets, which
#     have no inside).
# Pure numpy (meters), no VisPy: view/geometry_view.py draws the result.

import numpy as np

from geometry.raycast import inside_on_grid

MAX_PIXELS = 900            # along the longer side of the section image
SIDE_OFFSET = 1e-7          # m: the fill is sampled this far inside the kept half


def plane_axes(axis):
    """(u, v): the two in-plane axes, in increasing order."""
    u, v = [a for a in range(3) if a != axis]
    return u, v


def section_fill(bodies, axis, coordinate, lo, hi, keep_below=True, max_pixels=MAX_PIXELS,
                 background=(0.0, 0.0, 0.0, 0.0)):
    """
    bodies: [(Body, rgb)] in paint order. lo, hi: the extent (m) to cover.
    Returns (rgba float32 (nv, nu, 4), (u0, u1), (v0, v1)): row j is v,
    column i is u; `background` where no body is.
    """
    u, v = plane_axes(axis)
    nu = max_pixels if hi[u] > lo[u] else 1                      # each axis its own pixel size
    nv = max_pixels if hi[v] > lo[v] else 1
    cu = lo[u] + (np.arange(nu) + 0.5) * (hi[u] - lo[u]) / nu      # pixel centers
    cv = lo[v] + (np.arange(nv) + 0.5) * (hi[v] - lo[v]) / nv
    at = coordinate - SIDE_OFFSET if keep_below else coordinate + SIDE_OFFSET

    image = np.empty((nv, nu, 4), dtype=np.float32)
    image[:] = background
    cut = []
    for body, rgb in bodies:
        b_lo, b_hi = body.bounds
        if body.kind != "solid" or not b_lo[axis] <= at <= b_hi[axis]:
            continue
        cut.append((body, rgb))
        coords = [None, None, None]
        coords[axis] = np.array([at])
        coords[u], coords[v] = cu, cv
        inside = inside_on_grid(body, u, coords)                  # rays in the plane
        mask = np.moveaxis(inside, (v, u), (0, 1)).reshape(nv, nu)
        image[mask] = (*rgb, 1.0)
    du, dv = (hi[u] - lo[u]) / nu, (hi[v] - lo[v]) / nv
    for body, rgb in cut:                                         # thin bodies: their outline's pixels
        segments = section_outline(body, axis, at)
        if not len(segments):
            continue
        span = segments.reshape(-1, 3).max(axis=0) - segments.reshape(-1, 3).min(axis=0)
        if span[u] >= 2 * du and span[v] >= 2 * dv:
            continue                                              # the fill shows it already
        a, b = segments[:, 0], segments[:, 1]
        steps = int(np.ceil(np.max(np.abs((b - a)[:, [u, v]]) / np.array([du, dv]))) * 2) + 1
        t = np.linspace(0.0, 1.0, max(steps, 2))[None, :, None]
        points = (a[:, None, :] + t * (b - a)[:, None, :]).reshape(-1, 3)
        i = np.clip(((points[:, u] - lo[u]) / du).astype(int), 0, nu - 1)
        j = np.clip(((points[:, v] - lo[v]) / dv).astype(int), 0, nv - 1)
        image[j, i] = (*rgb, 1.0)
    return image, (lo[u], hi[u]), (lo[v], hi[v])


def section_outline(body, axis, coordinate):
    """(S, 2, 3) segments (m) where the body's triangles cross the plane."""
    tri = body.vertices[body.triangles]                          # (T, 3, 3)
    d = tri[:, :, axis] - coordinate
    # A vertex exactly on the plane counts as above: no zero-length pieces.
    above = d >= 0
    crossing = above.any(axis=1) & ~above.all(axis=1)
    tri, d, above = tri[crossing], d[crossing], above[crossing]
    if not len(tri):
        return np.zeros((0, 2, 3))

    points = []
    for a, b in ((0, 1), (1, 2), (2, 0)):
        cut = above[:, a] != above[:, b]
        t = np.where(cut, d[:, a] / np.where(cut, d[:, a] - d[:, b], 1.0), 0.0)
        p = tri[:, a] + t[:, None] * (tri[:, b] - tri[:, a])
        points.append((cut, p))
    # Each crossing triangle has exactly two cut edges.
    segments = np.zeros((len(tri), 2, 3))
    filled = np.zeros(len(tri), dtype=int)
    for cut, p in points:
        rows = np.nonzero(cut)[0]
        segments[rows, filled[rows]] = p[rows]
        filled[rows] += 1
    segments[:, :, axis] = coordinate
    return segments[filled == 2]
