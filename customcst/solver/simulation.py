# simulation.py
#
# A run without the GUI: the same steps the window takes, shared by it
# (view/main_window.py, view/sim_runner.py) and the console runner
# (run.py), so a run made on another machine is the same run:
#   prepare(step)  STEP + project file -> ports, elements, waveguide
#                  ports, vias, grid, materials (Model);
#   problem(...)   why it cannot be simulated, or None;
#   parameters()   what a run is computed with (the Results list's rows);
#   simulate()     one FDTD run per excited port -> SimulationResult.
# The result is saved next to the model (solver/result_file.py) with the
# project's fingerprint: copied back into model.results/ beside the same
# STEP and TOML, the window lists it as current.

import time
from dataclasses import dataclass, field

import numpy as np

from discretize.materials import discretize
from geometry.holes import find_holes, via_body
from geometry.step_io import read_step
from mesh.rectilinear import build_grid
from project.project_file import Project
from project.settings import EXCITE_ALL
from solver.fdtd import FdtdSolver
from solver.result_file import SimulationResult
from solver.sparams import input_impedance, s_column

FREQUENCY_POINTS = 401
MM = 1e-3


@dataclass
class Model:
    geometry: object
    project: Project
    holes: list = field(default_factory=list)
    ports: dict = field(default_factory=dict)
    elements: dict = field(default_factory=dict)
    waveports: dict = field(default_factory=dict)
    via_bodies: list = field(default_factory=list)
    mesh: object = None
    materials: object = None


def prepare(step_path, progress=None):
    """
    The model at `step_path` (its project file beside it) ready to run:
    read, ports and elements found, meshed, discretized. progress(done,
    total, what) as discretize's. Raises on an unreadable STEP or project
    file (ProjectError); a bad mesh is left in model.mesh (see problem()).
    """
    geometry = read_step(step_path)
    project = Project.load(step_path)
    model = Model(geometry, project, holes=find_holes(geometry))
    model.ports = project.ports(geometry)
    model.elements = project.elements(geometry)
    model.waveports = project.waveports(geometry)
    model.via_bodies = [via_body(h) for h in project.vias(model.holes)]
    model.mesh = build_grid(geometry, project, {**model.ports, **model.elements}, model.waveports,
                            model.via_bodies)
    if model.mesh.ok:
        model.materials = discretize(model.mesh, geometry, project, model.ports, model.elements,
                                     model.waveports, model.via_bodies, progress=progress)
    return model


def port_names(materials):
    """The valid ports in S-matrix order (lumped, then waveguide)."""
    return [p.name for p in materials.ports] + [w.name for w in materials.waveports]


def problem(project, mesh, materials):
    """Why the model cannot be simulated, or None (shorts are a question, not a problem)."""
    if mesh is None or not mesh.ok:
        return f"no mesh ({mesh.error if mesh else 'no project'})"
    if materials is None:
        return "no materials"
    if materials.errors:
        return materials.errors[0]
    if not materials.ports and not materials.waveports:
        return "no valid port"
    excite = project.settings["simulation"]["excite"]
    if excite != EXCITE_ALL and excite not in port_names(materials):
        return (f"the excited port {excite!r} is not a valid port of the model "
                f"(Simulation > excite: choose another one, or all ports)")
    return None


def parameters(project, mesh, materials, via_bodies):
    """What a run is computed with, for the Results list (display only)."""
    grid = mesh.grid
    steps = np.concatenate([grid.steps(a) for a in range(3)])
    dielectrics = {}
    for name, a in sorted(project.assignments.items()):
        if a.type == "dielectric":
            v = a.values
            dielectrics[name] = (f"{a.material}: " if a.material else "") + \
                f"εr {v['eps_r']:g}, tan δ {v['tan_d']:g}"
    ports = [f"{p.name} {p.resistance:.4g} Ω" for p in materials.ports] + \
            [f"{w.name} {w.resistance:.4g} Ω (line)" for w in materials.waveports]
    elements = [f"{e.name}: {e.topology} " + ", ".join(
                    [f"R {e.r:g} Ω"] * (e.r > 0) + [f"L {e.l * 1e9:g} nH"] * (e.l > 0)
                    + [f"C {e.c * 1e12:g} pF"] * (e.c > 0)) for e in materials.elements]
    excite = project.settings["simulation"]["excite"]
    return {
        "excite": "all ports" if excite == EXCITE_ALL else excite,
        "elements": elements,
        "simulation": dict(project.settings["simulation"]),
        "mesh": dict(project.settings["mesh"]),
        "grid": f"{' × '.join(str(n) for n in grid.shape)} = {grid.cell_count:,}".replace(",", " ")
                + f" cells, step {steps.min() / MM:.3g}–{steps.max() / MM:.3g} mm",
        "dielectrics": dielectrics,
        "vias": len(via_bodies),
        "ports": ports,
    }


def simulate(materials, f_min, f_max, trim_pulse=False, end_db=-40.0, excite=None, progress=None,
             started=None):
    """
    One run per excited port (excite: names, None = all), the others
    terminated: the S columns and Z_in. progress(port, t, level_db,
    pulse_duration) at each energy check; returning False stops: None is
    returned. started(port, solver) when a port's solver is built.
    """
    begin = time.perf_counter()
    ports = list(materials.ports) + list(materials.waveports)
    names = [p.name for p in ports]
    f = np.linspace(f_min, f_max, FREQUENCY_POINTS) * 1e9
    result = SimulationResult(
        f, names, [p.resistance for p in ports],
        f"FDTD, {f_min:g}-{f_max:g} GHz, grid {materials.grid.shape}",
    )
    result.eps_eff = [getattr(getattr(p, "mode", None), "eps_eff", float("nan")) for p in ports]

    for k, active in enumerate(names):
        if excite is not None and active not in excite:
            continue                    # not excited: its column of S stays empty
        solver = FdtdSolver(materials, f_min, f_max, active_port=active, trim_pulse=trim_pulse)
        if started is not None:
            started(active, solver)
        start = time.perf_counter()

        def report(t, level, active=active, solver=solver):
            return True if progress is None else progress(active, t, level, solver.pulse.duration) is not False

        status = solver.run(end_db=end_db, progress=report)
        if status == "stopped":
            return None

        column = s_column(solver, f)
        for j, name in enumerate(names):
            result.s[j, k] = column[name]
        result.z_in[active] = input_impedance(solver, f)
        result.info.append(
            f"{active}: {status}, {solver.step_index} steps ({solver.t * 1e9:.2f} ns), "
            f"{time.perf_counter() - start:.1f} s, {int(np.prod(solver.shape)):,} cells with PML"
        )

    result.elapsed = time.perf_counter() - begin
    return result
