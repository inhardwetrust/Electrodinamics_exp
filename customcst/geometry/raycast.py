# raycast.py
#
# Fast geometric queries on a rectilinear grid: a body's surface crossed by
# a whole family of parallel grid lines at once (the lines along `axis`
# through every (u, v) of two coordinate arrays).
#
# Each triangle is tested only against the lines inside its (u, v) bounding
# box (searchsorted on the sorted line coordinates), so the cost is about
# (number of lines) x (triangles per line), not points x triangles as in
# queries.py. The crossings of every line are kept sorted; questions about
# points on the lines are then binary searches:
#   - inside a closed solid: an odd number of crossings below the point;
#   - on the surface: a crossing within +-delta of the point;
#   - a segment along the line crosses the surface n times.
#
# Lines that run exactly through a triangle edge or vertex (common: grid
# lines sit on body faces, cylinder caps are fans around the axis) would
# count a crossing twice or not at all. The lines are therefore shifted by
# a tiny offset (u, v) = (delta, 0.618 delta) times a sign pattern, which
# no tessellation shares; a point within delta of a face parallel to the
# lines is found by trying the offsets on both sides.
#
# With numba (ENABLED; CUSTOMCST_NUMPY=1 forces numpy, like the FDTD
# kernels) the grid queries count straight on each line's sorted crossings,
# the lines spread over the cores, in the (u, v, axis) layout transposed to
# the grid's at the end. The numpy path packs every crossing as one sorted
# key (line id + the coordinate mapped into [0.25, 0.75]) and binary
# searches the whole grid's points in it; that was most of a Z0 path
# section's ray casting (checks/check_discretize_kernels.py compares the
# two).

import os

import numpy as np

try:
    from numba import njit, prange
    ENABLED = os.environ.get("CUSTOMCST_NUMPY", "") != "1"
except ImportError:                     # pragma: no cover - numba is optional
    ENABLED = False
NUMBA = ENABLED

if NUMBA:
    @njit(parallel=True, cache=True)
    def _count_below(starts, coords, s, out):
        """out[line, k]: crossings of the line below s[k]."""
        for line in prange(len(starts) - 1):
            c = coords[starts[line]:starts[line + 1]]
            for k in range(len(s)):
                out[line, k] = np.searchsorted(c, s[k])

    @njit(parallel=True, cache=True)
    def _count_between(starts, coords, lo, hi, out):
        """out[line, k]: crossings of the line with lo[k] <= coordinate <= hi[k]."""
        for line in prange(len(starts) - 1):
            c = coords[starts[line]:starts[line + 1]]
            for k in range(len(lo)):
                out[line, k] = np.searchsorted(c, hi[k], side="right") - np.searchsorted(c, lo[k])

_GOLDEN = 0.6180339887
# Offsets on both sides of the line in u and in v (asymmetric: never along
# a triangle edge that is diagonal in the plane).
OFFSETS = ((1.0, _GOLDEN), (-1.0, -_GOLDEN), (_GOLDEN, -1.0), (-_GOLDEN, 1.0))

_PAIR_CHUNK = 4_000_000     # triangle x line pairs per batch


class LineHits:
    """
    Surface crossings of the lines along `axis` through (cu[i], cv[j]) with
    one body; u < v are the other two axes. Line id = i * len(cv) + j.
    offset: (du, dv) added to the line positions (m).
    """

    def __init__(self, body, axis, cu, cv, offset=(0.0, 0.0)):
        self.axis = axis
        self.u, self.v = [a for a in range(3) if a != axis]
        cu = np.asarray(cu, dtype=np.float64) + offset[0]
        cv = np.asarray(cv, dtype=np.float64) + offset[1]
        self.nu, self.nv = len(cu), len(cv)

        lo, hi = body.bounds
        self.lo = lo[axis] - 1e-9
        self.span = (hi[axis] + 1e-9) - self.lo
        lines, coords = self._crossings(body, cu, cv)
        order = np.lexsort((coords, lines))
        self.lines, self.coords = lines[order], coords[order]
        self._keys = None

    @property
    def keys(self):
        """The numpy path's sorted keys: line id + the coordinate's fraction."""
        if self._keys is None:
            self._keys = self.lines + self._fraction(self.coords)
        return self._keys

    def _on_lines(self, kernel, *along):
        """kernel over every line and the 1D arrays `along` -> (nu, nv, len) counts."""
        starts = np.searchsorted(self.lines, np.arange(self.nu * self.nv + 1))
        out = np.empty((self.nu * self.nv, len(along[0])), dtype=np.int64)
        kernel(starts, self.coords, *(np.ascontiguousarray(a, dtype=np.float64) for a in along), out)
        return out.reshape(self.nu, self.nv, -1)

    def below_grid(self, s):
        """(nu, nv, len(s)): crossings below each s on each line (numba)."""
        return self._on_lines(_count_below, s)

    def between_grid(self, lo, hi):
        """(nu, nv, len(lo)): crossings with lo <= coordinate <= hi on each line (numba)."""
        return self._on_lines(_count_between, lo, hi)

    def _fraction(self, s):
        """Coordinate along the line -> [0.25, 0.75] inside the body's extent."""
        f = 0.25 + 0.5 * (np.asarray(s, dtype=np.float64) - self.lo) / self.span
        return np.clip(f, 0.01, 0.99)

    def _crossings(self, body, cu, cv):
        if len(body.triangles) == 0 or len(cu) == 0 or len(cv) == 0:
            return np.zeros(0, dtype=np.int64), np.zeros(0)
        tri = body.vertices[body.triangles]                 # (T, 3, 3)
        pu, pv, pa = tri[..., self.u], tri[..., self.v], tri[..., self.axis]

        # Projected area (twice): triangles parallel to the lines never cross them.
        area = ((pu[:, 1] - pu[:, 0]) * (pv[:, 2] - pv[:, 0]) -
                (pv[:, 1] - pv[:, 0]) * (pu[:, 2] - pu[:, 0]))
        scale = np.maximum(np.ptp(pu, axis=1) * np.ptp(pv, axis=1), 1e-300)
        keep = np.abs(area) > 1e-12 * scale
        pu, pv, pa = pu[keep], pv[keep], pa[keep]

        iu0 = np.searchsorted(cu, pu.min(axis=1), "left")
        iu1 = np.searchsorted(cu, pu.max(axis=1), "right")
        iv0 = np.searchsorted(cv, pv.min(axis=1), "left")
        iv1 = np.searchsorted(cv, pv.max(axis=1), "right")
        nu_t, nv_t = iu1 - iu0, iv1 - iv0
        count = nu_t * nv_t
        nonzero = np.nonzero(count)[0]

        lines, coords = [], []
        # Batches of whole triangles, about _PAIR_CHUNK pairs each.
        cumulative = np.cumsum(count[nonzero])
        start = 0
        while start < len(nonzero):
            done = cumulative[start - 1] if start else 0
            stop = max(start + 1, int(np.searchsorted(cumulative, done + _PAIR_CHUNK, "right")))
            t_ids = nonzero[start:stop]
            start = stop
            c = count[t_ids]
            t = np.repeat(t_ids, c)
            first = np.repeat(np.cumsum(c) - c, c)
            local = np.arange(len(t)) - first
            iu = iu0[t] + local // nv_t[t]
            iv = iv0[t] + local % nv_t[t]
            qu, qv = cu[iu], cv[iv]

            au, bu, cu_ = pu[t, 0], pu[t, 1], pu[t, 2]
            av, bv, cv_ = pv[t, 0], pv[t, 1], pv[t, 2]
            d_c = (bu - au) * (qv - av) - (bv - av) * (qu - au)     # opposite c
            d_a = (cu_ - bu) * (qv - bv) - (cv_ - bv) * (qu - bu)   # opposite a
            d_b = (au - cu_) * (qv - cv_) - (av - cv_) * (qu - cu_)  # opposite b
            inside = ((d_a >= 0) & (d_b >= 0) & (d_c >= 0)) | ((d_a <= 0) & (d_b <= 0) & (d_c <= 0))
            total = d_a + d_b + d_c
            inside &= total != 0
            t, d_a, d_b, d_c, total = t[inside], d_a[inside], d_b[inside], d_c[inside], total[inside]
            coord = (d_a * pa[t, 0] + d_b * pa[t, 1] + d_c * pa[t, 2]) / total
            lines.append(iu[inside] * self.nv + iv[inside])
            coords.append(coord)

        if not lines:
            return np.zeros(0, dtype=np.int64), np.zeros(0)
        return np.concatenate(lines), np.concatenate(coords)

    def count_below(self, line_ids, s):
        """Crossings on each line below coordinate s (arrays broadcast)."""
        line_ids = np.asarray(line_ids)
        return (np.searchsorted(self.keys, line_ids + self._fraction(s), "left") -
                np.searchsorted(self.keys, line_ids.astype(np.float64), "left"))

    def count_between(self, line_ids, s0, s1):
        """Crossings on each line with s0 <= coordinate <= s1."""
        line_ids = np.asarray(line_ids)
        return (np.searchsorted(self.keys, line_ids + self._fraction(s1), "right") -
                np.searchsorted(self.keys, line_ids + self._fraction(s0), "left"))


def grid_ids(axis, n_u, n_v, n_axis):
    """
    (line ids, along-axis index) broadcast to the [i, j, k] layout of an
    array whose shape is n_axis along `axis` and n_u, n_v along the others.
    """
    u, v = [a for a in range(3) if a != axis]
    shape = [1, 1, 1]
    shape[u] = n_u
    iu = np.arange(n_u).reshape(shape)
    shape = [1, 1, 1]
    shape[v] = n_v
    iv = np.arange(n_v).reshape(shape)
    shape = [1, 1, 1]
    shape[axis] = n_axis
    ia = np.arange(n_axis).reshape(shape)
    return iu * n_v + iv, ia


def _to_grid(array, axis):
    """(u, v, axis) layout -> the grid's (x, y, z)."""
    u, v = [a for a in range(3) if a != axis]
    return np.moveaxis(array, (0, 1, 2), (u, v, axis))


def inside_on_grid(body, axis, coords, delta=0.0):
    """
    Bool array (len(coords[0]), len(coords[1]), len(coords[2])): grid points
    strictly inside a solid (sheets: all False). The rays run along `axis`.
    """
    shape = tuple(len(c) for c in coords)
    if body.kind != "solid":
        return np.zeros(shape, dtype=bool)
    u, v = [a for a in range(3) if a != axis]
    d = max(delta, 1e-12)
    hits = LineHits(body, axis, coords[u], coords[v], (d * OFFSETS[0][0], d * OFFSETS[0][1]))
    if ENABLED:
        return _to_grid(hits.below_grid(np.asarray(coords[axis], dtype=float)) % 2 == 1, axis)
    ids, ia = grid_ids(axis, len(coords[u]), len(coords[v]), len(coords[axis]))
    s = np.asarray(coords[axis])[ia]
    return (hits.count_below(ids, s) % 2 == 1) & np.ones(shape, dtype=bool)


def in_or_on_grid(body, axis, coords, delta):
    """
    Bool array: grid points inside the solid or within ~delta of its surface
    (across the lines: one of the four offset line sets is inside; along
    the lines: a crossing within +-delta).
    """
    shape = tuple(len(c) for c in coords)
    u, v = [a for a in range(3) if a != axis]
    if ENABLED:
        s = np.asarray(coords[axis], dtype=float)
        result = np.zeros((len(coords[u]), len(coords[v]), len(s)), dtype=bool)
        for ou, ov in OFFSETS:
            hits = LineHits(body, axis, coords[u], coords[v], (delta * ou, delta * ov))
            result |= hits.between_grid(s - delta, s + delta) > 0
            if body.kind == "solid":
                result |= hits.below_grid(s) % 2 == 1
        return _to_grid(result, axis)
    ids, ia = grid_ids(axis, len(coords[u]), len(coords[v]), len(coords[axis]))
    s = np.asarray(coords[axis])[ia] * np.ones(shape)
    result = np.zeros(shape, dtype=bool)
    for ou, ov in OFFSETS:
        hits = LineHits(body, axis, coords[u], coords[v], (delta * ou, delta * ov))
        result |= hits.count_between(ids, s - delta, s + delta) > 0
        if body.kind == "solid":
            result |= hits.count_below(ids, s) % 2 == 1
    return result


def crossings_on_grid(body, axis, coords, half_length, delta, all_offsets=False):
    """
    Int array: crossings of the segment [s - below, s + above] along `axis`
    through every grid point (s = the point's coordinate). half_length: a
    number (below = above), or (below, above) as 1D arrays along `axis`.
    all_offsets: the most crossings over the four offset line sets, so a
    segment through the very border of a sheet counts as crossing it.
    """
    shape = tuple(len(c) for c in coords)
    u, v = [a for a in range(3) if a != axis]
    if ENABLED:
        s = np.asarray(coords[axis], dtype=float)
        if np.isscalar(half_length):
            below = above = half_length
        else:
            below, above = (np.asarray(h, dtype=float) for h in half_length)
        result = np.zeros((len(coords[u]), len(coords[v]), len(s)), dtype=np.int64)
        for ou, ov in (OFFSETS if all_offsets else OFFSETS[:1]):
            hits = LineHits(body, axis, coords[u], coords[v], (delta * ou, delta * ov))
            result = np.maximum(result, hits.between_grid(s - below, s + above))
        return _to_grid(result, axis)
    ids, ia = grid_ids(axis, len(coords[u]), len(coords[v]), len(coords[axis]))
    s = np.asarray(coords[axis])[ia] * np.ones(shape)
    if np.isscalar(half_length):
        below = above = half_length
    else:
        below, above = (np.asarray(h)[ia] * np.ones(shape) for h in half_length)
    result = np.zeros(shape, dtype=np.int64)
    for ou, ov in (OFFSETS if all_offsets else OFFSETS[:1]):
        hits = LineHits(body, axis, coords[u], coords[v], (delta * ou, delta * ov))
        result = np.maximum(result, hits.count_between(ids, s - below, s + above))
    return result
