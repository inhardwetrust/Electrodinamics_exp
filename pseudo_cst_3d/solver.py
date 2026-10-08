# solver.py

from dataclasses import dataclass, field

import numpy as np

from field import point_charge_e_field, point_charge_potential
from scene_model import PointCharge


@dataclass
class SolverResult:
    """
    Generic container for solver outputs.

    Example fields in this version:
        "E_nodes"      -> shape (ny + 1, nx + 1, 2)
        "E_mag_cells"  -> shape (ny, nx)
        "V_cells"      -> shape (ny, nx)
    """

    mesh: object
    fields: dict = field(default_factory=dict)

    def add_field(self, name, data):
        self.fields[name] = np.asarray(data)

    def get_field(self, name):
        return self.fields[name]


class PointChargeFieldSolver:
    """
    Simple analytic point-charge field solver.

    This is not a full numerical field solver like CST.
    But it is already a meaningful prototype for our renderer:

        - vectors at mesh NODES
        - scalar heatmap values at CELL CENTERS

    Formula:
        E = k * q * r / |r|^3
        V = k * q / |r|

    A small softening radius is used to avoid singular blow-ups exactly
    at or very near a charge position.
    """

    def __init__(self, k=1.0, softening_radius=1e-3):
        self.k = float(k)
        self.softening_radius = float(softening_radius)

    def solve(self, scene_model, mesh):
        node_positions = mesh.node_positions()
        cell_positions = mesh.cell_centers()

        e_nodes_flat = self._compute_e_field(
            scene_model=scene_model,
            positions=node_positions,
        )

        e_cells_flat = self._compute_e_field(
            scene_model=scene_model,
            positions=cell_positions,
        )

        v_cells_flat = self._compute_potential(
            scene_model=scene_model,
            positions=cell_positions,
        )

        e_mag_cells_flat = np.linalg.norm(
            e_cells_flat,
            axis=1,
        )

        result = SolverResult(
            mesh=mesh,
        )

        result.add_field(
            "E_nodes",
            mesh.reshape_node_field(e_nodes_flat),
        )

        result.add_field(
            "E_mag_cells",
            mesh.reshape_cell_field(e_mag_cells_flat),
        )

        result.add_field(
            "V_cells",
            mesh.reshape_cell_field(v_cells_flat),
        )

        return result

    def _compute_e_field(self, scene_model, positions):
        return point_charge_e_field(
            charges=scene_model.get_objects(PointCharge),
            points=positions,
            k=self.k,
            softening_radius=self.softening_radius,
        ).astype(np.float32)

    def _compute_potential(self, scene_model, positions):
        return point_charge_potential(
            charges=scene_model.get_objects(PointCharge),
            points=positions,
            k=self.k,
            softening_radius=self.softening_radius,
        ).astype(np.float32)
