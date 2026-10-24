# kernels.py
#
# The discretization's whole-grid passes compiled with numba (like the FDTD
# kernels, solver/fdtd_kernels.py): the edge average of a cell value
# (materials._edge_average) in one pass over the edges, the 2 x 2 cells
# around each edge read in place. The numpy path pads the grid and adds
# four shifted copies, four times per field when thick metal is skipped
# (the plain mean, the kept cells, their share); on the Z0 path's
# sections that was a third of the discretization.
#
# Same arithmetic in the same order as the numpy path, so the results are
# bit-identical (checks/check_discretize_kernels.py).
#
# AVAILABLE is False without numba; CUSTOMCST_NUMPY=1 forces the numpy
# path. ENABLED can be cleared at run time (the check compares the two).

import os

import numpy as np

try:
    from numba import njit, prange
    AVAILABLE = os.environ.get("CUSTOMCST_NUMPY", "") != "1"
except ImportError:                     # pragma: no cover - numba is optional
    AVAILABLE = False

ENABLED = AVAILABLE


if AVAILABLE:
    @njit(cache=True)
    def _edge_value(values, skip, use_skip, axis, wu, wv, a, p, q):
        """
        One edge: `a` its cell along the axis, (p, q) its node across. The
        four cells (du, dv) in the numpy path's order (0, 0), (0, 1),
        (1, 0), (1, 1); a cell outside the domain weighs nothing.
        """
        sw = 0.0            # sum of the weights
        swv = 0.0           # of weight x value
        swk = 0.0           # of weight x value over the kept cells
        sws = 0.0           # of the kept cells' weights
        for du in range(2):
            for dv in range(2):
                w = wu[p + du] * wv[q + dv]
                cu = p + du - 1
                cv = q + dv - 1
                val = 0.0
                skipped = False
                if w != 0.0:
                    if axis == 0:
                        val = values[a, cu, cv]
                        skipped = use_skip and skip[a, cu, cv]
                    elif axis == 1:
                        val = values[cu, a, cv]
                        skipped = use_skip and skip[cu, a, cv]
                    else:
                        val = values[cu, cv, a]
                        skipped = use_skip and skip[cu, cv, a]
                sw += w
                swv += val * w
                if use_skip:
                    swk += (0.0 if skipped else val) * w
                    sws += (0.0 if skipped else 1.0) * w
        plain = swv / sw
        if not use_skip:
            return plain
        kept = swk / sw
        share = sws / sw
        return kept / max(share, 1e-300) if share > 1e-12 else plain

    @njit(parallel=True, fastmath=False, cache=True)
    def _edge_average(values, skip, use_skip, axis, wu, wv, out):
        """
        out: edge-shaped (cells along `axis`, nodes across). wu, wv: the
        half cell widths across, padded with a zero at both ends.
        """
        n0, n1, n2 = out.shape
        for i in prange(n0):
            for j in range(n1):
                for k in range(n2):
                    if axis == 0:
                        out[i, j, k] = _edge_value(values, skip, use_skip, axis, wu, wv, i, j, k)
                    elif axis == 1:
                        out[i, j, k] = _edge_value(values, skip, use_skip, axis, wu, wv, j, i, k)
                    else:
                        out[i, j, k] = _edge_value(values, skip, use_skip, axis, wu, wv, k, i, j)


def edge_average(cell_values, axis, h, skip=None):
    """materials._edge_average on numba (same arguments, float64 result)."""
    shape = cell_values.shape
    u, v = [a for a in range(3) if a != axis]
    out_shape = [n + 1 for n in shape]
    out_shape[axis] = shape[axis]
    out = np.empty(out_shape)
    use_skip = skip is not None and bool(skip.any())
    if not use_skip:
        skip = np.zeros((1, 1, 1), dtype=bool)          # not read
    wu = np.pad(np.asarray(h[u], dtype=float) / 2, 1)
    wv = np.pad(np.asarray(h[v], dtype=float) / 2, 1)
    _edge_average(np.ascontiguousarray(cell_values, dtype=np.float64), skip, use_skip, axis, wu, wv, out)
    return out
