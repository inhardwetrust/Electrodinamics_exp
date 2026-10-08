# display_grid.py

import math
from dataclasses import dataclass

import numpy as np


def lattice_step(base_step, units_per_pixel, target_px):
    """
    World step ~target_px on screen, restricted to base_step * 2^k.

    Power-of-two levels make every coarser lattice a subset of every finer
    one: zooming out drops every second node, the rest stay where they were.
    Sample count therefore depends on screen size, not on zoom.
    """
    wanted = float(target_px) * float(units_per_pixel)
    level = int(round(math.log2(wanted / float(base_step))))
    return float(base_step) * (2.0 ** level)


@dataclass(frozen=True)
class LatticeGrid:
    """
    Rectangular block of the global lattice (index * step).

    Display-only: says where the renderer samples the field.
    It has nothing to do with the solver mesh.
    """

    step: float
    ix0: int
    iy0: int
    nx: int  # node count along x
    ny: int  # node count along y

    @classmethod
    def covering(cls, x_min, x_max, y_min, y_max, step):
        ix0 = int(math.floor(x_min / step))
        ix1 = int(math.ceil(x_max / step))
        iy0 = int(math.floor(y_min / step))
        iy1 = int(math.ceil(y_max / step))

        return cls(
            step=float(step),
            ix0=ix0,
            iy0=iy0,
            nx=ix1 - ix0 + 1,
            ny=iy1 - iy0 + 1,
        )

    @property
    def x_min(self):
        return self.ix0 * self.step

    @property
    def y_min(self):
        return self.iy0 * self.step

    @property
    def x_max(self):
        return (self.ix0 + self.nx - 1) * self.step

    @property
    def y_max(self):
        return (self.iy0 + self.ny - 1) * self.step

    def contains(self, x_min, x_max, y_min, y_max):
        return (
            self.x_min <= x_min
            and x_max <= self.x_max
            and self.y_min <= y_min
            and y_max <= self.y_max
        )

    def node_positions(self):
        """Returns array (ny, nx, 2); row 0 is y_min."""
        xs = (self.ix0 + np.arange(self.nx)) * self.step
        ys = (self.iy0 + np.arange(self.ny)) * self.step

        xx, yy = np.meshgrid(xs, ys)
        return np.stack((xx, yy), axis=-1)
