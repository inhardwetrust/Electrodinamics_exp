# model_builder.py
#
# ModelSpec (model_spec.py) -> scene + field source / simulation.

from dataclasses import dataclass

from domain import SimulationDomain2D
from fdtd import FdtdTE2D
from fdtd3d import FdtdYee3D
from field import AnalyticPointChargeField, grid_source_from_point_charge_result
from mesh import RectangularMesh2D
from model_spec import ModelError
from scene_model import SceneModel
from simulation import OscillatingDipoleSimulation
from solver import PointChargeFieldSolver


@dataclass
class BuiltModel:
    scene_model: SceneModel
    source: object          # FieldSource; None for 3D (viewed through slices)
    simulation: object      # Simulation or None (static models)
    mesh: object            # solver mesh for the overlay, or None
    cell_size: float        # base step of the display lattices


def build(spec):
    """May raise ModelError (bad values the file format cannot catch)."""
    try:
        return _BUILDERS[spec.solver](spec, _scene(spec))
    except ModelError:
        raise
    except ValueError as error:
        raise ModelError(f"{spec.path.name}: {error}") from None


def _scene(spec):
    scene = SceneModel()
    for obj in spec.objects:
        scene.add(obj)
    return scene


def _domain_2d(spec):
    (x0, y0), (x1, y1) = spec.domain["bounds"]
    return SimulationDomain2D(x0, x1, y0, y1)


def _fdtd_cell_size(spec):
    d = spec.domain
    if "cell_size" in d:
        return float(d["cell_size"])
    frequency = float(spec.excitation.get("frequency", 1.0))
    return 1.0 / frequency / float(d["cells_per_wavelength"])


def _charge_physics(spec):
    cell = float(spec.domain["cell_size"])
    k = float(spec.numerics.get("k", 1.0))
    softening = float(spec.numerics.get("softening", 0.2 * cell))
    return cell, k, softening


# =====================================================
# One builder per solver
# =====================================================

def _build_analytic(spec, scene):
    cell, k, softening = _charge_physics(spec)
    source = AnalyticPointChargeField(scene_model=scene, k=k, softening_radius=softening)
    return BuiltModel(scene, source, None, None, cell)


def _build_grid(spec, scene):
    cell, k, softening = _charge_physics(spec)
    domain = _domain_2d(spec)
    mesh = RectangularMesh2D(
        domain=domain,
        nx=round(domain.width / cell),
        ny=round(domain.height / cell),
    )
    result = PointChargeFieldSolver(k=k, softening_radius=softening).solve(scene_model=scene, mesh=mesh)
    source = grid_source_from_point_charge_result(mesh, result)
    return BuiltModel(scene, source, None, mesh, cell)


def _build_oscillating(spec, scene):
    static = _build_grid(spec, scene)
    ex = spec.excitation

    simulation = OscillatingDipoleSimulation(
        scene_model=scene,
        base_source=static.source,
        period=float(ex.get("period", 4.0)),
        steps_per_period=int(ex.get("steps_per_period", 240)),
    )
    if "amplitude" in ex:
        simulation.set_parameter("amplitude", float(ex["amplitude"]))

    return BuiltModel(scene, simulation.source(), simulation, static.mesh, static.cell_size)


def _build_fdtd2d(spec, scene):
    ex, bnd = spec.excitation, spec.boundary

    if ex.get("port_resistance", 0):
        raise ModelError(f"{spec.path.name}: port_resistance is supported by the 3D solver only")

    simulation = FdtdTE2D(
        scene_model=scene,
        domain=_domain_2d(spec),
        cell_size=_fdtd_cell_size(spec),
        courant=float(spec.numerics.get("courant", 0.5)),
        waveform=str(ex.get("waveform", "sine")),
        frequency=float(ex.get("frequency", 1.0)),
        amplitude=float(ex.get("amplitude", 1.0)),
        boundary=str(bnd.get("kind", "pml")),
        pml_cells=int(bnd.get("cells", 12)),
    )
    return BuiltModel(scene, simulation.source(), simulation, simulation.mesh, simulation.h)


def _build_fdtd3d(spec, scene):
    ex, bnd = spec.excitation, spec.boundary

    if bnd.get("kind", "pml") != "pml":
        raise ModelError(f"{spec.path.name}: the 3D solver supports only boundary kind = \"pml\"")

    simulation = FdtdYee3D(
        scene_model=scene,
        bounds=spec.domain["bounds"],
        cell_size=_fdtd_cell_size(spec),
        courant=float(spec.numerics.get("courant", 0.5)),
        waveform=str(ex.get("waveform", "sine")),
        frequency=float(ex.get("frequency", 1.0)),
        amplitude=float(ex.get("amplitude", 1.0)),
        pml_cells=int(bnd.get("cells", 10)),
        port_resistance=float(ex.get("port_resistance", 0.0)),
    )
    return BuiltModel(scene, None, simulation, None, simulation.h)


_BUILDERS = {
    "analytic": _build_analytic,
    "grid": _build_grid,
    "oscillating": _build_oscillating,
    "fdtd2d": _build_fdtd2d,
    "fdtd3d": _build_fdtd3d,
}
