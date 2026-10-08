# mesh.py

import numpy as np


class RectangularMesh2D:
    """
    Regular rectangular mesh.

    Geometry conventions in this version:
        - vector field is sampled at NODES
        - heatmap scalar field is sampled at CELL CENTERS
    """

    def __init__(self, domain, nx, ny):
        if nx <= 0 or ny <= 0:
            raise ValueError("nx and ny must be positive")

        self.domain = domain
        self.nx = int(nx)
        self.ny = int(ny)

    @property
    def dx(self):
        return self.domain.width / self.nx

    @property
    def dy(self):
        return self.domain.height / self.ny

    def grid_x(self):
        return np.linspace(
            self.domain.x_min,
            self.domain.x_max,
            self.nx + 1,
            dtype=np.float32,
        )

    def grid_y(self):
        return np.linspace(
            self.domain.y_min,
            self.domain.y_max,
            self.ny + 1,
            dtype=np.float32,
        )

    def node_positions(self):
        """
        Returns:
            ndarray, shape ((ny + 1) * (nx + 1), 2)
        """

        xx, yy = np.meshgrid(
            self.grid_x(),
            self.grid_y(),
        )

        return np.column_stack((
            xx.ravel(),
            yy.ravel(),
        )).astype(np.float32)

    def cell_centers(self):
        """
        Returns:
            ndarray, shape (ny * nx, 2)
        """

        gx = self.grid_x()
        gy = self.grid_y()

        cx = 0.5 * (gx[:-1] + gx[1:])
        cy = 0.5 * (gy[:-1] + gy[1:])

        xx, yy = np.meshgrid(
            cx,
            cy,
        )

        return np.column_stack((
            xx.ravel(),
            yy.ravel(),
        )).astype(np.float32)

    def reshape_node_field(self, flat_values):
        """
        Flat:
            ((ny + 1) * (nx + 1), ...)
        Structured:
            (ny + 1, nx + 1, ...)
        """

        flat_values = np.asarray(flat_values)

        return flat_values.reshape(
            self.ny + 1,
            self.nx + 1,
            *flat_values.shape[1:],
        )

    def reshape_cell_field(self, flat_values):
        """
        Flat:
            (ny * nx, ...)
        Structured:
            (ny, nx, ...)
        """

        flat_values = np.asarray(flat_values)

        return flat_values.reshape(
            self.ny,
            self.nx,
            *flat_values.shape[1:],
        )

    def grid_segments(self):
        """
        Mesh line segments for overlay visualization.
        """

        gx = self.grid_x()
        gy = self.grid_y()

        segments = []

        for x in gx:
            segments.append([x, gy[0]])
            segments.append([x, gy[-1]])

        for y in gy:
            segments.append([gx[0], y])
            segments.append([gx[-1], y])

        return np.asarray(
            segments,
            dtype=np.float32,
        )
