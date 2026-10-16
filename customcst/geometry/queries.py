# queries.py
#
# Geometric questions answered from the triangles alone (numpy, no OCP):
# is a point inside a solid, does a point touch a body. The same ray /
# triangle intersection will later drive the voxelizer (grid lines x
# surfaces).

import numpy as np

# An irrational-ish direction: rays practically never hit a triangle edge or
# vertex exactly, which would break the crossing parity.
_RAY = np.array([0.5773503, 0.5773491, 0.5773519])
_RAY /= np.linalg.norm(_RAY)

_CHUNK = 2_000_000      # points x triangles per batch (memory bound)


def points_inside(body, points):
    """
    Bool array: points strictly inside a closed solid (ray parity).
    Sheets have no inside: always False. Points exactly on the surface may
    go either way; use touches() when "on" must count.
    """
    points = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    inside = np.zeros(len(points), dtype=bool)

    if body.kind != "solid" or len(body.triangles) == 0:
        return inside

    lo, hi = body.bounds
    candidates = np.nonzero(np.all((points >= lo) & (points <= hi), axis=1))[0]
    if len(candidates) == 0:
        return inside

    a, b, c = (body.vertices[body.triangles[:, n]] for n in range(3))
    e1, e2 = b - a, c - a
    pvec = np.cross(_RAY, e2)
    det = np.einsum("ij,ij->i", e1, pvec)
    ok = np.abs(det) > 1e-30
    a, e1, e2, pvec, det = a[ok], e1[ok], e2[ok], pvec[ok], det[ok]
    inv = 1.0 / det

    step = max(1, _CHUNK // max(1, len(a)))
    for start in range(0, len(candidates), step):
        idx = candidates[start:start + step]
        tvec = points[idx, None, :] - a[None]                       # (P, T, 3)
        u = np.einsum("ptj,tj->pt", tvec, pvec) * inv
        qvec = np.cross(tvec, e1[None])
        v = (qvec @ _RAY) * inv
        t = np.einsum("ptj,tj->pt", qvec, e2) * inv
        hit = (u >= 0.0) & (v >= 0.0) & (u + v <= 1.0) & (t > 0.0)
        inside[idx] = (hit.sum(axis=1) % 2) == 1

    return inside


def segments_hit_surface(body, starts, ends):
    """Bool array: segment start -> end crosses or touches the body's surface."""
    return segment_crossings(body, starts, ends) > 0


def segment_crossings(body, starts, ends):
    """
    Int array: how many times each segment start -> end crosses the body's
    surface (Moller-Trumbore, segment parameter in [0, 1]). Hits at the same
    point of the segment (a shared triangle edge or vertex) count once.
    """
    starts = np.asarray(starts, dtype=np.float64).reshape(-1, 3)
    ends = np.asarray(ends, dtype=np.float64).reshape(-1, 3)
    hit = np.zeros(len(starts), dtype=np.int64)

    if len(body.triangles) == 0:
        return hit

    lo, hi = body.bounds
    seg_lo, seg_hi = np.minimum(starts, ends), np.maximum(starts, ends)
    idx = np.nonzero(np.all((seg_hi >= lo) & (seg_lo <= hi), axis=1))[0]
    if len(idx) == 0:
        return hit

    a, b, c = (body.vertices[body.triangles[:, n]] for n in range(3))
    e1, e2 = b - a, c - a
    eps = 1e-12

    step = max(1, _CHUNK // max(1, len(a)))
    for start in range(0, len(idx), step):
        chunk = idx[start:start + step]
        o = starts[chunk, None, :]                                  # (P, 1, 3)
        d = (ends[chunk] - starts[chunk])[:, None, :]               # (P, 1, 3)
        pvec = np.cross(d, e2[None])                                # (P, T, 3)
        det = np.einsum("ptj,tj->pt", pvec, e1)
        ok = np.abs(det) > 1e-30
        inv = np.where(ok, 1.0 / np.where(ok, det, 1.0), 0.0)
        tvec = o - a[None]
        u = np.einsum("ptj,ptj->pt", tvec, pvec) * inv
        qvec = np.cross(tvec, e1[None])
        v = np.einsum("ptj,ptj->pt", d, qvec) * inv
        t = np.einsum("ptj,tj->pt", qvec, e2) * inv
        inside = (u >= -eps) & (v >= -eps) & (u + v <= 1 + eps) & (t >= -eps) & (t <= 1 + eps)
        mask = ok & inside
        # Distinct crossing positions along each segment.
        t_hit = np.where(mask, np.round(t, 9), np.nan)
        t_sorted = np.sort(t_hit, axis=1)
        first = np.isfinite(t_sorted[:, :1])
        new = np.isfinite(t_sorted[:, 1:]) & (np.diff(t_sorted, axis=1) > 0)
        hit[chunk] = first[:, 0].astype(np.int64) + new.sum(axis=1)

    return hit


def points_in_or_on(body, points, tolerance):
    """
    Bool array: inside the solid, or within `tolerance` of its surface.
    This is the robust "belongs to the body" test: points exactly on a face
    (a port drawn on the board's end face) make ray parity unreliable, the
    distance settles them. Sheets: within `tolerance` of the surface.
    """
    points = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    result = np.zeros(len(points), dtype=bool)

    lo, hi = body.bounds
    near_box = np.all((points >= lo - tolerance) & (points <= hi + tolerance), axis=1)
    idx = np.nonzero(near_box)[0]
    if len(idx) == 0:
        return result

    candidates = points[idx]
    near = surface_distances(body, candidates) <= tolerance
    result[idx] = near | points_inside(body, candidates)
    return result


def touches(body, point, tolerance):
    return bool(points_in_or_on(body, point, tolerance)[0])


def surface_distances(body, points):
    """(P,) distance from each point to the nearest triangle (exact)."""
    points = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    result = np.full(len(points), np.inf)

    if len(body.triangles) == 0:
        return result

    a, b, c = (body.vertices[body.triangles[:, n]] for n in range(3))
    normal = np.cross(b - a, c - a)
    area2 = np.linalg.norm(normal, axis=1)
    good = area2 > 0
    unit = np.zeros_like(normal)
    unit[good] = normal[good] / area2[good, None]

    step = max(1, _CHUNK // (8 * len(a)))
    for start in range(0, len(points), step):
        p = points[start:start + step, None, :]                     # (P, 1, 3)

        # Nearest point on the three edges...
        best = np.full((p.shape[0], len(a)), np.inf)
        for p0, p1 in ((a, b), (b, c), (c, a)):
            d = p1 - p0
            dd = np.maximum(np.einsum("ij,ij->i", d, d), 1e-300)
            s = np.clip(np.einsum("ptj,tj->pt", p - p0, d) / dd, 0.0, 1.0)
            best = np.minimum(best, np.linalg.norm(p0 + s[..., None] * d - p, axis=2))

        # ... or its projection on the plane, if that lands inside.
        h = np.einsum("ptj,tj->pt", p - a, unit)
        q = p - h[..., None] * unit
        inside = _in_triangle(q, a, b, c) & good
        best = np.where(inside, np.minimum(best, np.abs(h)), best)

        result[start:start + step] = best.min(axis=1)

    return result


def _in_triangle(q, a, b, c):
    """q: (P, T, 3) points in the planes of triangles a, b, c: (T, 3)."""
    v0, v1, v2 = c - a, b - a, q - a
    d00 = np.einsum("tj,tj->t", v0, v0)
    d01 = np.einsum("tj,tj->t", v0, v1)
    d11 = np.einsum("tj,tj->t", v1, v1)
    d02 = np.einsum("ptj,tj->pt", v2, v0)
    d12 = np.einsum("ptj,tj->pt", v2, v1)
    den = d00 * d11 - d01 * d01
    den = np.where(np.abs(den) > 1e-300, den, 1e-300)
    u = (d11 * d02 - d01 * d12) / den
    v = (d00 * d12 - d01 * d02) / den
    return (u >= 0) & (v >= 0) & (u + v <= 1)


