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
#   - a plane with no signal, or with several (a lumped element, a pad
#     beside the pin), has no Z0: NaN and the reason.

from dataclasses import dataclass, field

import numpy as np

from solver.port_mode import solve_port_mode


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


def line_impedance(materials, axis, index, bounds):
    """Section at grid line `index` along `axis`; bounds: (lo, hi) of the model (m)."""
    u, v = [a for a in range(3) if a != axis]
    lo, hi = bounds
    section = Section(float(materials.grid.lines[axis][index]))
    model = ((lo[u], hi[u]), (lo[v], hi[v]))
    mode = solve_port_mode(materials, axis, index, region=model)
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
                mode = solve_port_mode(materials, axis, index, region=region)
                if mode.ok:
                    break
    if mode.ok:
        section.z0, section.eps_eff = float(mode.z0), float(mode.eps_eff)
    else:
        error = mode.errors[0]
        # The port solver's advice (make the port larger) does not apply here.
        section.error = "no signal conductor in this section" if error.startswith("no signal") else error
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


def impedance_profile(materials, axis, bounds, progress=None):
    """
    Every grid line along `axis` inside the model's extent there.
    progress(done, total) is called after each section; returning False stops.
    """
    lines = materials.grid.lines[axis]
    lo, hi = bounds
    indices = np.nonzero((lines >= lo[axis] - 1e-12) & (lines <= hi[axis] + 1e-12))[0]
    profile = Profile(axis)
    for n, index in enumerate(indices):
        profile.sections.append(line_impedance(materials, axis, int(index), bounds))
        if progress is not None and progress(n + 1, len(indices)) is False:
            break
    return profile
