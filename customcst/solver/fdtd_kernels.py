# fdtd_kernels.py
#
# The Yee updates of solver/fdtd.py compiled with numba: each field
# component in one pass (both curl terms, their CPML and the update), the
# outer index spread over the CPU cores. The plain numpy path does the same
# arithmetic in ~10 whole-array passes with temporaries on one core; this
# is the hot loop of every run.
#
# Same math and order as the numpy path (see fdtd.py), float32 throughout:
#   H_f -= Db (dE_w/dv - dE_v/dw)
#   E_f  = Ca E_f + Cb (dH_w/dv - dH_v/dw)        (interior edges only)
# with the CPML on each derivative d along axis a:
#   psi = b psi + a d,  d += psi
# CPML memory is kept only in the absorbing layers: `pm` maps a line index
# along the derivative's axis to its slot in psi (-1 = not in the CPML),
# psi has the field's shape with that axis compressed to the slots.
# E derivatives are taken at interior nodes: their CPML index is node - 1
# (the numpy path's coordinates `nodes = lines[1:-1]`).
#
# AVAILABLE is False without numba; CUSTOMCST_NUMPY=1 forces the numpy path
# (for comparing the two).

import os

import numpy as np

try:
    from numba import njit, prange
    AVAILABLE = os.environ.get("CUSTOMCST_NUMPY", "") != "1"
except ImportError:                     # pragma: no cover - numba is optional
    AVAILABLE = False


if AVAILABLE:
    _jit = njit(parallel=True, fastmath=False, cache=True)

    @_jit
    def update_hx(hx, ey, ez, db, inv_dy, inv_dz,
                  pm_y, a_y, b_y, psi_y, pm_z, a_z, b_z, psi_z):
        nx1, ny, nz = hx.shape
        for i in prange(nx1):
            for j in range(ny):
                py = pm_y[j]
                for k in range(nz):
                    d1 = (ez[i, j + 1, k] - ez[i, j, k]) * inv_dy[j]
                    if py >= 0:
                        p = b_y[j] * psi_y[i, py, k] + a_y[j] * d1
                        psi_y[i, py, k] = p
                        d1 += p
                    d2 = (ey[i, j, k + 1] - ey[i, j, k]) * inv_dz[k]
                    pz = pm_z[k]
                    if pz >= 0:
                        p = b_z[k] * psi_z[i, j, pz] + a_z[k] * d2
                        psi_z[i, j, pz] = p
                        d2 += p
                    hx[i, j, k] -= db[i, j, k] * (d1 - d2)

    @_jit
    def update_hy(hy, ez, ex, db, inv_dz, inv_dx,
                  pm_z, a_z, b_z, psi_z, pm_x, a_x, b_x, psi_x):
        nx, ny1, nz = hy.shape
        for i in prange(nx):
            px = pm_x[i]
            for j in range(ny1):
                for k in range(nz):
                    d1 = (ex[i, j, k + 1] - ex[i, j, k]) * inv_dz[k]
                    pz = pm_z[k]
                    if pz >= 0:
                        p = b_z[k] * psi_z[i, j, pz] + a_z[k] * d1
                        psi_z[i, j, pz] = p
                        d1 += p
                    d2 = (ez[i + 1, j, k] - ez[i, j, k]) * inv_dx[i]
                    if px >= 0:
                        p = b_x[i] * psi_x[px, j, k] + a_x[i] * d2
                        psi_x[px, j, k] = p
                        d2 += p
                    hy[i, j, k] -= db[i, j, k] * (d1 - d2)

    @_jit
    def update_hz(hz, ex, ey, db, inv_dx, inv_dy,
                  pm_x, a_x, b_x, psi_x, pm_y, a_y, b_y, psi_y):
        nx, ny, nz1 = hz.shape
        for i in prange(nx):
            px = pm_x[i]
            for j in range(ny):
                py = pm_y[j]
                for k in range(nz1):
                    d1 = (ey[i + 1, j, k] - ey[i, j, k]) * inv_dx[i]
                    if px >= 0:
                        p = b_x[i] * psi_x[px, j, k] + a_x[i] * d1
                        psi_x[px, j, k] = p
                        d1 += p
                    d2 = (ex[i, j + 1, k] - ex[i, j, k]) * inv_dy[j]
                    if py >= 0:
                        p = b_y[j] * psi_y[i, py, k] + a_y[j] * d2
                        psi_y[i, py, k] = p
                        d2 += p
                    hz[i, j, k] -= db[i, j, k] * (d1 - d2)

    @_jit
    def update_ex(ex, hy, hz, ca, cb, inv_ddy, inv_ddz,
                  pm_y, a_y, b_y, psi_y, pm_z, a_z, b_z, psi_z):
        nx, ny1, nz1 = ex.shape
        for i in prange(nx):
            for j in range(1, ny1 - 1):
                py = pm_y[j - 1]
                for k in range(1, nz1 - 1):
                    d1 = (hz[i, j, k] - hz[i, j - 1, k]) * inv_ddy[j - 1]
                    if py >= 0:
                        p = b_y[j - 1] * psi_y[i, py, k] + a_y[j - 1] * d1
                        psi_y[i, py, k] = p
                        d1 += p
                    d2 = (hy[i, j, k] - hy[i, j, k - 1]) * inv_ddz[k - 1]
                    pz = pm_z[k - 1]
                    if pz >= 0:
                        p = b_z[k - 1] * psi_z[i, j, pz] + a_z[k - 1] * d2
                        psi_z[i, j, pz] = p
                        d2 += p
                    ex[i, j, k] = ca[i, j, k] * ex[i, j, k] + cb[i, j, k] * (d1 - d2)

    @_jit
    def update_ey(ey, hz, hx, ca, cb, inv_ddz, inv_ddx,
                  pm_z, a_z, b_z, psi_z, pm_x, a_x, b_x, psi_x):
        nx1, ny, nz1 = ey.shape
        for i in prange(1, nx1 - 1):
            px = pm_x[i - 1]
            for j in range(ny):
                for k in range(1, nz1 - 1):
                    d1 = (hx[i, j, k] - hx[i, j, k - 1]) * inv_ddz[k - 1]
                    pz = pm_z[k - 1]
                    if pz >= 0:
                        p = b_z[k - 1] * psi_z[i, j, pz] + a_z[k - 1] * d1
                        psi_z[i, j, pz] = p
                        d1 += p
                    d2 = (hz[i, j, k] - hz[i - 1, j, k]) * inv_ddx[i - 1]
                    if px >= 0:
                        p = b_x[i - 1] * psi_x[px, j, k] + a_x[i - 1] * d2
                        psi_x[px, j, k] = p
                        d2 += p
                    ey[i, j, k] = ca[i, j, k] * ey[i, j, k] + cb[i, j, k] * (d1 - d2)

    @_jit
    def update_ez(ez, hx, hy, ca, cb, inv_ddx, inv_ddy,
                  pm_x, a_x, b_x, psi_x, pm_y, a_y, b_y, psi_y):
        nx1, ny1, nz = ez.shape
        for i in prange(1, nx1 - 1):
            px = pm_x[i - 1]
            for j in range(1, ny1 - 1):
                py = pm_y[j - 1]
                for k in range(nz):
                    d1 = (hy[i, j, k] - hy[i - 1, j, k]) * inv_ddx[i - 1]
                    if px >= 0:
                        p = b_x[i - 1] * psi_x[px, j, k] + a_x[i - 1] * d1
                        psi_x[px, j, k] = p
                        d1 += p
                    d2 = (hx[i, j, k] - hx[i, j - 1, k]) * inv_ddy[j - 1]
                    if py >= 0:
                        p = b_y[j - 1] * psi_y[i, py, k] + a_y[j - 1] * d2
                        psi_y[i, py, k] = p
                        d2 += p
                    ez[i, j, k] = ca[i, j, k] * ez[i, j, k] + cb[i, j, k] * (d1 - d2)

    @_jit
    def weighted_square_sum(field, weight):
        """sum(weight * field^2) in float64 (the field energy check)."""
        n0 = field.shape[0]
        partial = np.zeros(n0)
        for i in prange(n0):
            acc = 0.0
            for j in range(field.shape[1]):
                for k in range(field.shape[2]):
                    x = float(field[i, j, k])
                    acc += weight[i, j, k] * x * x
            partial[i] = acc
        return partial.sum()
