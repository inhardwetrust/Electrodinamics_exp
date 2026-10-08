# field3d.py
#
# 3D field data. The frontend never draws volumes: it looks at 3D fields
# through slice planes (slicing.py), which turn them into ordinary 2D
# FieldSources - so every 2D layer (heatmap, arrows, clim...) is reused.

from dataclasses import dataclass

import numpy as np

AXES = ("x", "y", "z")
AXIS_INDEX = {"x": 0, "y": 1, "z": 2}


@dataclass
class GridArray3D:
    """
    One scalar quantity on its own regular 3D grid (cubic cells of size h).

    values: (nz, ny, nx); index [k, j, i] is at origin + (i, j, k) * h.
    Each quantity carries its own origin, so Yee half-cell offsets are exact.
    """

    values: np.ndarray
    origin: tuple
    h: float

    def __post_init__(self):
        self.values = np.asarray(self.values, dtype=np.float32)

        if self.values.ndim != 3 or min(self.values.shape) < 2:
            raise ValueError("GridArray3D needs a (nz, ny, nx) array, each >= 2")

        self._flat = np.ascontiguousarray(self.values).reshape(-1)

    def sample(self, points):
        """Trilinear interpolation at points (..., 3); NaN outside."""
        points = np.asarray(points, dtype=np.float64)
        nz, ny, nx = self.values.shape
        h = float(self.h)

        f = [(points[..., a] - self.origin[a]) / h for a in range(3)]
        sizes = (nx, ny, nz)

        inside = np.ones(points.shape[:-1], dtype=bool)
        index = []
        frac = []

        for a in range(3):
            inside &= (f[a] >= 0.0) & (f[a] <= sizes[a] - 1)
            i = np.clip(np.floor(f[a]).astype(np.int64), 0, sizes[a] - 2)
            index.append(i)
            frac.append(np.clip(f[a] - i, 0.0, 1.0))

        i, j, k = index
        tx, ty, tz = frac
        base = (k * ny + j) * nx + i

        v = self._flat
        out = np.zeros(points.shape[:-1], dtype=np.float64)

        for dk, wz in ((0, 1.0 - tz), (1, tz)):
            for dj, wy in ((0, 1.0 - ty), (1, ty)):
                for di, wx in ((0, 1.0 - tx), (1, tx)):
                    out += np.take(v, base + (dk * ny + dj) * nx + di) * (wx * wy * wz)

        out[~inside] = np.nan
        return out


class Field3D:
    """
    Snapshot of a 3D state: named scalar grids (components on their Yee
    positions, material maps...). Vectors are named component triples.
    """

    def __init__(self, arrays, vectors, bounds):
        self.arrays = dict(arrays)            # {name: GridArray3D}
        self.vectors = dict(vectors)          # {"E": ("Ex", "Ey", "Ez"), ...}
        self.bounds = bounds                  # ((x0, y0, z0), (x1, y1, z1))

    def sample(self, name, points):
        return self.arrays[name].sample(points)
