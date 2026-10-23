# line_profile.py
#
# Characteristic impedance of the line at any cross-section of the model,
# like a TDR picture or CST's 2D port solver run at many planes: the
# quasi-TEM mode (solver/port_mode.py) on the grid plane normal to `axis`
# at a grid line, the way the FDTD grid sees the structure.
#   - the region that picks the conductors is the model's extent on the
#     plane (geometry bounds): a conductor reaching the model's side
#     (ground planes, a connector shell) is ground, the single other
#     conductor is the signal;
#   - Z0 is the line's own impedance, of a uniform line with this cross
#     section: exact on uniform stretches, only indicative on a taper or a
#     step (the field there is not transverse). It is not the input
#     impedance (that is the 3D run's Z in);
#   - what joins out of the plane counts too (Nets: the 3D pieces of
#     metal): a conductor tied to a ground beyond the plane (a GND pin to
#     the pour, a paddle on its vias) is ground; the signal is the conductor
#     a port, an element or a wave port is on, the other ones float (the
#     unconnected pads of a chip beside its pin). With no port in the model
#     the single other conductor is the signal;
#   - a plane with no signal, or with several (a lumped element, two lines),
#     has no Z0: NaN and the reason.
#
# Convergence (z0_convergence): the same plane on its own grids, finer and
# finer (cells per feature 4, 8, 16, the merge distance scaled with them;
# 32 only if 16 has not converged: its plane has ~4x the nodes of 16 and
# took most of the time), without touching the 3D grid. Each grid is the
# project's grid at that setting, cut to a slab of two cells around the
# plane, so only the plane is discretized. The staircase converges
# unevenly (a line's edges fall on the grid lines differently at each
# level), so the answer is the mean of the last two levels, +- half their
# difference; converged when that is within 1 %. On the KiCad boards the
# 3D default (4) read a 0.3 mm trace as 51.7 Ohm, converged 54. The slab
# has no 3D connections of its own: its nodes take the nets of the 3D
# grid's nearest nodes.

import copy
from dataclasses import dataclass, field, replace

import numpy as np

from mesh.rectilinear import RectilinearGrid, build_grid
from solver.port_mode import solve_port_mode

CONVERGENCE_LEVELS = (4, 8, 16, 32)     # cells per feature
CONVERGED = 0.01                        # relative half difference of the last two levels


@dataclass
class Section:
    coordinate: float               # m, along the axis
    z0: float = float("nan")        # Ohm
    eps_eff: float = float("nan")
    error: str = ""


@dataclass
class Profile:
    axis: int
    sections: list = field(default_factory=list)

    @property
    def coordinates(self):
        return np.array([s.coordinate for s in self.sections])

    @property
    def z0(self):
        return np.array([s.z0 for s in self.sections])

    @property
    def eps_eff(self):
        return np.array([s.eps_eff for s in self.sections])


@dataclass
class Nets:
    """The 3D pieces of metal of a MaterialGrid (see the header)."""
    lines: tuple                    # the grid lines it was found on
    label: np.ndarray               # (nx+1, ny+1, nz+1) int: the node's piece, -1 off the metal
    driven: frozenset               # pieces with a port, an element or a wave port's signal on them

    def plane(self, axis, coordinate, lines_u, lines_v):
        """(len(lines_u), len(lines_v)) labels at the nearest nodes of the plane."""
        u, v = [a for a in range(3) if a != axis]
        take = [slice(None)] * 3
        take[axis] = _nearest(self.lines[axis], [coordinate])[0]
        on_plane = self.label[tuple(take)]
        return on_plane[np.ix_(_nearest(self.lines[u], lines_u), _nearest(self.lines[v], lines_v))]


def pec_nets(materials):
    """Nets of the materials' PEC edges."""
    from discretize.materials import pec_pieces     # discretize imports solver code
    nodes = tuple(n + 1 for n in materials.grid.shape)
    piece = pec_pieces(materials)
    on_metal = np.zeros(nodes, dtype=bool)
    for axis, pec in enumerate(materials.pec):
        on_metal[tuple(slice(0, n) for n in pec.shape)] |= pec
        end = [slice(None)] * 3
        end[axis] = slice(1, None)
        on_metal[tuple(end)] |= pec
    label = np.where(on_metal.ravel(), piece, -1).reshape(nodes)

    driven = set()
    for item in materials.ports + materials.elements:
        driven.update(int(label[tuple(end)]) for end in item.ends)
    for port in materials.waveports:
        mode = port.mode
        take = [slice(None)] * 3
        take[port.axis] = port.index
        on_plane = label[tuple(take)]                # (u, v): the other two axes in order
        driven.update(int(n) for n in np.unique(on_plane[mode.hot]))
    driven.discard(-1)
    return Nets(tuple(materials.grid.lines), label, frozenset(driven))


def _nearest(lines, values):
    values = np.asarray(values, dtype=float)
    k = np.clip(np.searchsorted(lines, values), 1, len(lines) - 1)
    return np.where(values - lines[k - 1] <= lines[k] - values, k - 1, k)


def line_impedance(materials, axis, index, bounds, nets=None):
    """
    Section at grid line `index` along `axis`; bounds: (lo, hi) of the model
    (m); nets: Nets of the 3D grid (pec_nets), None = the plane alone.
    """
    u, v = [a for a in range(3) if a != axis]
    lo, hi = bounds
    section = Section(float(materials.grid.lines[axis][index]))
    model = ((lo[u], hi[u]), (lo[v], hi[v]))
    joins = {}
    if nets is not None:
        joins = dict(nets=nets.plane(axis, section.coordinate, materials.grid.lines[u],
                                     materials.grid.lines[v]), driven=nets.driven)
    mode = solve_port_mode(materials, axis, index, region=model, **joins)
    if not mode.ok and mode.errors[0].startswith("no conductor touches"):
        # Nothing reaches the model's side here (a connector's shell beyond
        # the board, a KiCad board's copper pulled back from its edge): the
        # metal's own extent on the plane picks the ground. A trace on the
        # outer layer touches that extent too (top or bottom): then the
        # extent is opened across one direction, so only what spans the
        # other one (the pour, the planes) is ground.
        extent = _metal_extent(materials, axis, index)
        if extent is not None:
            for region in (extent, (extent[0], model[1]), (model[0], extent[1])):
                mode = solve_port_mode(materials, axis, index, region=region, **joins)
                if mode.ok:
                    break
    if mode.ok:
        section.z0, section.eps_eff = float(mode.z0), float(mode.eps_eff)
    else:
        error = mode.errors[0]
        # The port solver's advice (make the port larger) does not apply here.
        section.error = "no signal conductor in this section" if error.startswith("no signal conductor inside") else error
    return section


def _metal_extent(materials, axis, index):
    """((u0, u1), (v0, v1)) of the PEC edges on the plane, or None."""
    lines = materials.grid.lines
    u, v = [a for a in range(3) if a != axis]
    nodes = []
    for e_axis in (u, v):
        take = [slice(None)] * 3
        take[axis] = index
        hit = np.nonzero(materials.pec[e_axis][tuple(take)])
        if len(hit[0]):
            iu, iv = hit                                    # edge start nodes
            nodes.append((lines[u][iu], lines[v][iv]))
            end_u = iu + (e_axis == u)
            end_v = iv + (e_axis == v)
            nodes.append((lines[u][end_u], lines[v][end_v]))
    if not nodes:
        return None
    cu = np.concatenate([n[0] for n in nodes])
    cv = np.concatenate([n[1] for n in nodes])
    return (cu.min(), cu.max()), (cv.min(), cv.max())


def impedance_profile(materials, axis, bounds, progress=None, nets=None):
    """
    Every grid line along `axis` inside the model's extent there.
    progress(done, total) is called after each section; returning False stops.
    nets: pec_nets(materials) if already found.
    """
    if nets is None:
        nets = pec_nets(materials)
    lines = materials.grid.lines[axis]
    lo, hi = bounds
    indices = np.nonzero((lines >= lo[axis] - 1e-12) & (lines <= hi[axis] + 1e-12))[0]
    profile = Profile(axis)
    for n, index in enumerate(indices):
        profile.sections.append(line_impedance(materials, axis, int(index), bounds, nets))
        if progress is not None and progress(n + 1, len(indices)) is False:
            break
    return profile


@dataclass
class Level:
    cells_per_feature: float
    min_step: float                 # m, the merge distance used
    section: Section
    nodes: int                      # nodes on the plane


@dataclass
class Convergence:
    axis: int
    coordinate: float               # m
    levels: list = field(default_factory=list)

    @property
    def estimate(self):
        """(Z0, +- Ohm) from the last two levels with a Z0, or None."""
        z = [lv.section.z0 for lv in self.levels if np.isfinite(lv.section.z0)]
        if len(z) < 2:
            return None
        return 0.5 * (z[-1] + z[-2]), 0.5 * abs(z[-1] - z[-2])

    @property
    def converged(self):
        e = self.estimate
        return e is not None and e[1] <= CONVERGED * e[0]


def z0_convergence(geometry, project, axis, coordinate, min_step, cells_per_feature,
                   waveports=None, vias=(), levels=CONVERGENCE_LEVELS, progress=None, nets=None):
    """
    Z0 at the plane normal to `axis` at `coordinate` (m) on grids of each
    level. min_step / cells_per_feature: the project's (its merge distance
    in m as the 3D grid used it); the merge distance scales as 1 / level.
    progress(done, total) after each level; returning False stops.
    nets: pec_nets of the 3D grid, for what joins out of the plane.
    """
    from discretize.materials import discretize      # discretize imports solver code
    result = Convergence(axis, float(coordinate))
    for n, level in enumerate(levels):
        trial = copy.copy(project)
        trial.settings = {name: dict(values) for name, values in project.settings.items()}
        step = min_step * cells_per_feature / level
        trial.settings["mesh"]["cells_per_feature"] = float(level)
        trial.settings["mesh"]["min_step"] = step / 1e-3
        mesh = build_grid(geometry, trial, {}, waveports, vias)
        if not mesh.ok:
            result.levels.append(Level(level, step, Section(float(coordinate), error=mesh.error), 0))
            break
        lines = list(mesh.grid.lines)
        lines[axis] = np.array([coordinate - step, coordinate, coordinate + step])
        slab = replace(mesh, grid=RectilinearGrid(tuple(lines)))
        materials = discretize(slab, geometry, trial, vias=vias)
        section = line_impedance(materials, axis, 1, geometry.bounds, nets)
        u, v = [len(lines[a]) for a in range(3) if a != axis]
        result.levels.append(Level(level, step, section, u * v))
        if progress is not None and progress(n + 1, len(levels)) is False:
            break
        if len(result.levels) >= 3 and result.converged:
            break
    return result
