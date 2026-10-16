# materials.py
#
# Level 3: what the solver sees. Geometry x RectilinearGrid -> per-edge
# material values on the Yee grid (staircase, first version).
#
# Index convention: arrays are [i, j, k] = (x, y, z); node (i, j, k) is at
# (x[i], y[j], z[k]); nx, ny, nz = cell counts.
#   E-edges (primary edges):
#     Ex (nx,   ny+1, nz+1)  from node (i,j,k) to (i+1,j,k)
#     Ey (nx+1, ny,   nz+1)
#     Ez (nx+1, ny+1, nz  )
#   H-edges (dual edges, through the primary face centers):
#     Hx (nx+1, ny,   nz  )  from cell (i-1,j,k) center to cell (i,j,k) center
#     Hy (nx,   ny+1, nz  )
#     Hz (nx,   ny,   nz+1)
#
# Rules:
#   - cells: material at the cell center; dielectrics in body order, later
#     wins. Conductors are not cell materials (cells inside thick metal are
#     only marked for display).
#   - eps_r, sigma on E-edges: average over the (up to 4) cells around the
#     edge, weighted by their share of the dual face (tangential E: the
#     arithmetic mean is the right one).
#   - mu_r on H-edges: harmonic mean of the 2 cells along the dual edge,
#     weighted by the half steps (normal B is continuous).
#   - PEC on E-edges: the edge midpoint is inside (or on) a conductor, OR a
#     short segment through the midpoint ACROSS the edge (+- tolerance along
#     each of the two perpendicular axes) crosses the conductor's surface.
#     tolerance ~ the mesh min_step, so a thin body whose faces were merged
#     onto the edge's grid plane becomes a sheet of PEC edges there. The
#     tolerance is never applied along the edge: an edge that only points
#     at nearby metal (a port column next to a sheet) stays free.
#     A thick conductor is filled: interior edges are PEC too (same fields
#     outside, and no leaks through staircase gaps).
#   - sigma from the loss tangent: sigma = 2 pi f_ref eps0 eps_r tan_d
#     (exact at f_ref only: a constant sigma is a frequency-independent
#     conductivity, so the effective tan_d scales as f_ref / f).
#   - ports: the edges along the port axis whose midpoints lie in the gap.
#     Both column ends must touch PEC edges, none of the column may be PEC.

import math
from dataclasses import dataclass, field

import numpy as np

from geometry.queries import points_in_or_on, points_inside, segments_hit_surface
from project.object_types import CONDUCTOR_TYPES
from project.settings import GHZ

EPS0 = 8.8541878128e-12
AXES = "xyz"


@dataclass
class DiscretePort:
    name: str
    axis: int                   # edge direction
    sign: int                   # +1: "-" -> "+" along +axis, -1: along -axis
    edges: np.ndarray           # (n, 3) edge indices [i, j, k] in the E array of `axis`
    resistance: float           # Ohm
    length: float               # m, sum of the edge lengths


@dataclass
class MaterialGrid:
    grid: object
    eps_r: tuple                # (Ex, Ey, Ez)-shaped float32 arrays
    sigma: tuple                # S/m, same shapes
    mu_r: tuple                 # (Hx, Hy, Hz)-shaped float32 arrays
    pec: tuple                  # bool, E-edge shapes
    dielectric: tuple           # bool, E-edge shapes: touches a dielectric cell (display)
    cell_body: np.ndarray       # (nx, ny, nz) int: -1 = background, else body index
    bodies: list                # body names for cell_body
    ports: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    errors: list = field(default_factory=list)


def edge_shape(shape, axis):
    """Shape of the E-edge array along `axis` for cell counts `shape`."""
    return tuple(n if a == axis else n + 1 for a, n in enumerate(shape))


def discretize(mesh, geometry, project, ports=None):
    """mesh: a MeshResult (its grid and the merge distance it used)."""
    grid = mesh.grid
    lines = grid.lines
    shape = grid.shape
    h = [np.diff(l) for l in lines]
    centers = [0.5 * (l[1:] + l[:-1]) for l in lines]
    tolerance = 0.99 * mesh.min_step     # merged thin bodies are within this of their line

    names, conductors, dielectrics = [], [], []
    for body in geometry.bodies:
        assignment = project.get(body.name)
        if assignment is None:
            continue
        if assignment.type in CONDUCTOR_TYPES:
            conductors.append(body)
            names.append(body.name)
        elif assignment.type == "dielectric":
            dielectrics.append((body, assignment.values))
            names.append(body.name)

    warnings = []
    f_min, f_max = (project.settings["simulation"][k] for k in ("f_min", "f_max"))

    # --- cells -------------------------------------------------------------
    cell_eps = np.ones(shape)
    cell_sigma = np.zeros(shape)
    cell_mu = np.ones(shape)
    cell_body = np.full(shape, -1, dtype=np.int32)
    cx, cy, cz = np.meshgrid(*centers, indexing="ij")
    cell_points = np.stack((cx, cy, cz), axis=-1).reshape(-1, 3)

    for body, values in dielectrics:
        inside = _inside_box_filtered(body, cell_points).reshape(shape)
        cell_eps[inside] = values["eps_r"]
        cell_mu[inside] = values["mu_r"]
        cell_sigma[inside] = 2 * math.pi * values["f_ref"] * GHZ * EPS0 * values["eps_r"] * values["tan_d"]
        cell_body[inside] = names.index(body.name)

        if values["eps_r"] == 1.0 and values["mu_r"] == 1.0 and values["tan_d"] == 0.0:
            warnings.append(f"{body.name}: εr = μr = 1, tan δ = 0: the same as air")

        if values["tan_d"] > 0 and not (f_min <= values["f_ref"] <= f_max):
            warnings.append(
                f"{body.name}: tan δ is fitted at f ref = {values['f_ref']:g} GHz, outside "
                f"{f_min:g}-{f_max:g} GHz (losses scale as f ref / f)"
            )

    for body in conductors:
        inside = _inside_box_filtered(body, cell_points).reshape(shape)
        cell_body[inside] = names.index(body.name)

    # --- E-edges: eps, sigma, PEC ------------------------------------------
    is_dielectric = np.zeros(shape)
    for body, _ in dielectrics:
        is_dielectric[cell_body == names.index(body.name)] = 1.0

    eps_r, sigma, pec, dielectric = [], [], [], []
    for axis in range(3):
        eps_r.append(_edge_average(cell_eps, axis, h).astype(np.float32))
        dielectric.append(_edge_average(is_dielectric, axis, h) > 0)
        sigma.append(_edge_average(cell_sigma, axis, h).astype(np.float32))

        mid = _edge_midpoints(lines, axis)
        metal = np.zeros(len(mid), dtype=bool)
        for body in conductors:
            metal |= _edges_in_metal(body, mid, axis, tolerance)
        pec.append(metal.reshape(edge_shape(shape, axis)))

    # --- H-edges: mu --------------------------------------------------------
    mu_r = tuple(_dual_harmonic(cell_mu, axis, h).astype(np.float32) for axis in range(3))

    result = MaterialGrid(
        grid=grid, eps_r=tuple(eps_r), sigma=tuple(sigma), mu_r=mu_r, pec=tuple(pec),
        dielectric=tuple(dielectric),
        cell_body=cell_body, bodies=names, warnings=warnings,
    )

    for port in (ports or {}).values():
        if port.ok:
            _discretize_port(result, port, project.get(port.name).values["impedance"])

    return result


def _edges_in_metal(body, midpoints, axis, tolerance):
    """PEC rule for E-edges along `axis` (see the header)."""
    result = np.zeros(len(midpoints), dtype=bool)
    lo, hi = body.bounds
    near = np.nonzero(np.all((midpoints >= lo - tolerance) & (midpoints <= hi + tolerance), axis=1))[0]
    if len(near) == 0:
        return result

    m = midpoints[near]
    hit = points_in_or_on(body, m, 1e-6 * tolerance)       # inside, or exactly on a face
    for u in range(3):
        if u == axis:
            continue
        offset = np.zeros(3)
        offset[u] = tolerance
        rest = ~hit
        if rest.any():
            hit[rest] |= segments_hit_surface(body, m[rest] - offset, m[rest] + offset)

    result[near] = hit
    return result


def _inside_box_filtered(body, points):
    result = np.zeros(len(points), dtype=bool)
    lo, hi = body.bounds
    idx = np.nonzero(np.all((points >= lo) & (points <= hi), axis=1))[0]
    if len(idx):
        result[idx] = points_inside(body, points[idx])
    return result


def _edge_midpoints(lines, axis):
    coords = [0.5 * (l[1:] + l[:-1]) if a == axis else l for a, l in enumerate(lines)]
    g = np.meshgrid(*coords, indexing="ij")
    return np.stack(g, axis=-1).reshape(-1, 3)


def _edge_average(cell_values, axis, h):
    """
    Value on each E-edge along `axis`: weighted mean over the cells around
    it. Weight of a cell = its quarter of the dual face (h_u / 2 * h_v / 2).
    """
    u, v = [a for a in range(3) if a != axis]
    wu = np.pad(h[u] / 2, 1)            # zero weight outside the domain
    wv = np.pad(h[v] / 2, 1)

    pad = [(0, 0)] * 3
    pad[u], pad[v] = (1, 1), (1, 1)
    values = np.pad(cell_values, pad)

    shape_w = [1, 1, 1]
    shape_w[u] = len(wu)
    weights_u = wu.reshape(shape_w)
    shape_w = [1, 1, 1]
    shape_w[v] = len(wv)
    weights_v = wv.reshape(shape_w)
    weight = weights_u * weights_v * np.ones_like(values)
    weighted = values * weight

    def neighbors(a):
        # Sum over the 2 x 2 cells sharing each edge: padded index p and p+1.
        s = 0
        for du in (0, 1):
            for dv in (0, 1):
                index = [slice(None)] * 3
                index[u] = slice(du, a.shape[u] - 1 + du)
                index[v] = slice(dv, a.shape[v] - 1 + dv)
                s = s + a[tuple(index)]
        return s

    return neighbors(weighted) / neighbors(weight)


def _dual_harmonic(cell_values, axis, h):
    """Value on each H-edge along `axis`: harmonic mean of the 2 cells along it."""
    w = np.pad(h[axis] / 2, 1)
    pad = [(0, 0)] * 3
    pad[axis] = (1, 1)
    inv = np.pad(1.0 / cell_values, pad)
    shape_w = [1, 1, 1]
    shape_w[axis] = len(w)
    w = w.reshape(shape_w)

    lo = [slice(None)] * 3
    hi = [slice(None)] * 3
    lo[axis] = slice(0, -1)
    hi[axis] = slice(1, None)
    lo, hi = tuple(lo), tuple(hi)

    w_lo, w_hi = np.broadcast_to(w, inv.shape)[lo], np.broadcast_to(w, inv.shape)[hi]
    return (w_lo + w_hi) / (w_lo * inv[lo] + w_hi * inv[hi])


def _discretize_port(result, port, resistance):
    lines = result.grid.lines
    direction = port.plus - port.minus
    axis = int(np.argmax(np.abs(direction)))

    if np.linalg.norm(np.delete(direction, axis)) > 1e-9 * np.linalg.norm(direction):
        result.errors.append(f"{port.name}: the port axis is not along x, y or z")
        return

    # The column: the grid line through the port axis (a fixpoint, so exact).
    index = []
    for a in range(3):
        if a == axis:
            continue
        i = int(np.argmin(np.abs(lines[a] - port.minus[a])))
        if abs(lines[a][i] - port.minus[a]) > 1e-9:
            result.errors.append(f"{port.name}: the axis is not on a grid line ({AXES[a]})")
            return
        index.append(i)

    along = lines[axis]
    mids = 0.5 * (along[1:] + along[:-1])
    g0, g1 = sorted((port.gap_minus[axis], port.gap_plus[axis]))
    n = np.nonzero((mids > g0) & (mids < g1))[0]
    if len(n) == 0:
        result.errors.append(f"{port.name}: the gap is shorter than one cell")
        return

    edges = np.zeros((len(n), 3), dtype=np.int64)
    edges[:, axis] = n
    others = [a for a in range(3) if a != axis]
    edges[:, others[0]], edges[:, others[1]] = index

    pec = result.pec[axis]
    if pec[tuple(edges.T)].any():
        result.errors.append(f"{port.name}: part of the port column is PEC (short circuit)")
        return

    for end, node_index in (("first", n[0]), ("last", n[-1] + 1)):
        node = [0, 0, 0]
        node[axis] = node_index
        node[others[0]], node[others[1]] = index
        if not _node_on_metal(result.pec, node, result.grid.shape):
            result.errors.append(f"{port.name}: the {end} end of the column does not touch PEC")
            return

    sign = 1 if direction[axis] > 0 else -1
    length = float(along[n[-1] + 1] - along[n[0]])
    result.ports.append(DiscretePort(port.name, axis, sign, edges, resistance, length))


def _node_on_metal(pec, node, shape):
    """True if any E-edge touching the node is PEC."""
    for axis in range(3):
        for offset in (-1, 0):
            index = list(node)
            index[axis] += offset
            if 0 <= index[axis] < shape[axis] and pec[axis][tuple(index)]:
                return True
    return False
