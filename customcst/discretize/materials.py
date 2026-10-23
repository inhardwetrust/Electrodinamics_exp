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
#     arithmetic mean is the right one). Cells inside a thick conductor are
#     left out: the field there is zero, they hold no material of their
#     own, and counting them as air pulled eps down on every free edge
#     next to curved metal (a coax: eps_eff 2.065 instead of 2.1), so a
#     via in a drilled hole and a via drawn over solid substrate differed.
#     An edge with metal all around keeps the plain mean (it is PEC anyway).
#   - mu_r on H-edges: harmonic mean of the 2 cells along the dual edge,
#     weighted by the half steps (normal B is continuous).
#   - PEC on E-edges: the edge midpoint is inside (or on) a conductor, OR a
#     short segment through the midpoint ACROSS the edge (+- tolerance along
#     each of the two perpendicular axes) passes THROUGH a thin conductor:
#     it crosses the surface at least twice (enters and leaves within the
#     segment); for a sheet (no inside) one crossing is enough.
#     tolerance ~ the mesh min_step, so a thin body whose faces were merged
#     onto the edge's grid plane becomes a sheet of PEC edges there. The
#     reach is also kept under half the neighbouring cells: cells can be
#     finer than min_step (a 0.2 mm board split in two 0.1 mm cells with a
#     0.14 mm tolerance), and a segment reaching past the next grid line
#     caught the copper and the ground plane from there: the whole board
#     became metal. Each thin body belongs to its nearest grid line only.
#     A single crossing of a THICK body's surface does not count: an edge
#     just outside a curved conductor must stay free, or every curved
#     conductor would grow by the tolerance (a coax pin got 0.2 mm fatter,
#     Z0 -24 %). The tolerance is never applied along the edge either: an
#     edge that only points at nearby metal (a port column) stays free.
#     A thick conductor is filled: interior edges are PEC too (same fields
#     outside, and no leaks through staircase gaps).
#     Closure, per conductor: every edge between two of its metal nodes
#     (nodes its PEC edges touch) is PEC too, so no metal node has a free
#     edge along a line (see _close_metal).
#   - sigma from the loss tangent: sigma = 2 pi f_ref eps0 eps_r tan_d
#     (exact at f_ref only: a constant sigma is a frequency-independent
#     conductivity, so the effective tan_d scales as f_ref / f).
#   - ports: the edges along the port axis whose midpoints lie in the gap.
#     Both column ends must touch PEC edges, none of the column may be PEC.
#   - lumped elements: the same column when the axis is along x, y or z,
#     plus the parallel columns within the element's radius (its drawn
#     cylinder: a 0402 is 0.5 mm wide, not one edge; one edge column is
#     a thin wire, 0.55 nH over a 1 mm copper bridge across a 1 mm gap,
#     the 1 mm wide element 0.08 nH: check_lumped). Each runs
#     along a grid line inside the cylinder: free edges through the
#     middle of the gap, grown toward both ends until a node touches
#     metal; it counts if its ends lie on the two pieces of metal the
#     central column joins (a column off a rounded trace tip, or above
#     the copper, finds no metal and is left out). N columns in parallel,
#     each N times the element (solver/fdtd.py). An axis slanted in a grid plane (a rotated 0402 on a board) gets a
#     staircase of edges instead: from the node nearest the - end to the
#     one nearest the + end, each step along whichever of the two axes
#     keeps the step's midpoint nearer the axis line. Its edges whose
#     midpoints project into the gap carry the element (the same rules as
#     a column), each its share l_e / (path length). The path is longer
#     than the gap (Manhattan), the element's value is exact. A staircase
#     stays one path.
#   - waveguide ports: the grid plane of the port (a domain end) and the
#     line's mode on it (solver/port_mode.py), found on the discretized
#     materials, so it is the mode of the staircased line the FDTD sees.
#   - shorts: the nodes joined by PEC edges form pieces of metal. A
#     waveguide port whose signal and ground nodes are in one piece, or a
#     port or element whose two column ends are, is listed in `shorts`.
#     Usually a gap narrower than the mesh resolves (min step auto on a
#     KiCad board: 0.145 mm against 0.25 mm gaps joined the SMA's pin to
#     its shell), but it can be meant (a shorted stub, a PIFA's short
#     pin): the caller asks before running, it does not refuse.

import math
from dataclasses import dataclass, field

import numpy as np
import scipy.sparse as sp
from scipy.sparse.csgraph import connected_components

from geometry.raycast import crossings_on_grid, in_or_on_grid, inside_on_grid
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

    @property
    def axes(self):
        return np.full(len(self.edges), self.axis)

    @property
    def ends(self):
        """The two end nodes of the column [i, j, k]."""
        first = self.edges[np.argmin(self.edges[:, self.axis])].copy()
        last = self.edges[np.argmax(self.edges[:, self.axis])].copy()
        last[self.axis] += 1
        return first, last


@dataclass
class DiscreteElement:
    """
    Lumped R, L, C on a path of E-edges (see solver/fdtd.py): a column, or
    a staircase for a slanted axis. Values in SI; 0 = that part is absent
    (series: shorted, parallel: open).
    """
    name: str
    axes: np.ndarray            # (n,) each edge's direction
    edges: np.ndarray           # (n, 3) in the E array of its axis, in path order from the - end
    signs: np.ndarray           # (n,) +1: the path runs along +axis on that edge
    length: float               # m, sum of the edge lengths (of the central path)
    ends: tuple                 # the central path's first and last node [i, j, k]
    topology: str               # "series" | "parallel"
    r: float                    # Ohm
    l: float                    # H
    c: float                    # F
    column: np.ndarray = None   # (n,) each edge's parallel path (0: the central one)
    column_length: np.ndarray = None    # (columns,) m, each path's length

    @property
    def columns(self):
        return len(self.column_length)


@dataclass
class DiscreteWavePort:
    """
    A waveguide port on the grid plane `index` along `axis` (0 or the last
    line: a domain end); the line runs from it toward `direction` * axis.
    `measure` is the plane where V and I are sampled (a few cells into the
    line, away from the source); results are moved back to the port plane.
    """
    name: str
    axis: int
    direction: int
    index: int
    measure: int
    mode: object                # PortMode

    @property
    def resistance(self):
        """Reference impedance: the line's own Z0."""
        return float(self.mode.z0)


MEASURE_CELLS = 3


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
    elements: list = field(default_factory=list)       # DiscreteElement
    waveports: list = field(default_factory=list)      # DiscreteWavePort
    shorts: list = field(default_factory=list)         # str: ports / elements shorted by metal
    warnings: list = field(default_factory=list)
    errors: list = field(default_factory=list)


def edge_shape(shape, axis):
    """Shape of the E-edge array along `axis` for cell counts `shape`."""
    return tuple(n if a == axis else n + 1 for a, n in enumerate(shape))


def discretize(mesh, geometry, project, ports=None, elements=None, waveports=None, vias=(),
               progress=None):
    """
    mesh: a MeshResult (its grid and the merge distance it used).
    ports / elements: {name: PortGeometry} of the port and lumped bodies.
    waveports: {name: WavePortGeometry}.
    vias: PEC bodies of the assigned vias (geometry.holes.via_body), extra
    conductors that are not bodies of the STEP file.
    progress(done, total, what): called after each step (a body's cells, a
    conductor's edges, the ports), for a progress bar.
    """
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

    for body in vias:
        conductors.append(body)
        names.append(body.name)

    total = len(dielectrics) + 2 * len(conductors) + 1
    done = 0

    def step(what):
        nonlocal done
        done += 1
        if progress is not None:
            progress(done, total, what)

    warnings = []
    f_min, f_max = (project.settings["simulation"][k] for k in ("f_min", "f_max"))

    # --- cells -------------------------------------------------------------
    cell_eps = np.ones(shape)
    cell_sigma = np.zeros(shape)
    cell_mu = np.ones(shape)
    cell_body = np.full(shape, -1, dtype=np.int32)

    for body, values in dielectrics:
        inside = _cells_inside(body, centers)
        cell_eps[inside] = values["eps_r"]
        cell_mu[inside] = values["mu_r"]
        cell_sigma[inside] = 2 * math.pi * values["f_ref"] * GHZ * EPS0 * values["eps_r"] * values["tan_d"]
        cell_body[inside] = names.index(body.name)
        step(body.name)

        if values["eps_r"] == 1.0 and values["mu_r"] == 1.0 and values["tan_d"] == 0.0:
            warnings.append(f"{body.name}: εr = μr = 1, tan δ = 0: the same as air")

        if values["tan_d"] > 0 and not (f_min <= values["f_ref"] <= f_max):
            warnings.append(
                f"{body.name}: tan δ is fitted at f ref = {values['f_ref']:g} GHz, outside "
                f"{f_min:g}-{f_max:g} GHz (losses scale as f ref / f)"
            )

    in_metal = np.zeros(shape, dtype=bool)
    for body in conductors:
        inside = _cells_inside(body, centers)
        cell_body[inside] = names.index(body.name)
        in_metal |= inside
        step(body.name)

    # --- E-edges: eps, sigma, PEC ------------------------------------------
    is_dielectric = np.zeros(shape)
    for body, _ in dielectrics:
        is_dielectric[cell_body == names.index(body.name)] = 1.0

    eps_r, sigma, pec, dielectric = [], [], [], []
    for axis in range(3):
        eps_r.append(_edge_average(cell_eps, axis, h, in_metal).astype(np.float32))
        dielectric.append(_edge_average(is_dielectric, axis, h, in_metal) > 0)
        sigma.append(_edge_average(cell_sigma, axis, h, in_metal).astype(np.float32))
        pec.append(np.zeros(edge_shape(shape, axis), dtype=bool))

    for body in conductors:
        # Per piece: a KiCad layer is one body holding the pour and the
        # trace in its clearance; closed as one, a gap one cell wide
        # (a trace's rounded end) was bridged into a short.
        for piece in body.pieces or [body]:
            own = [_edges_in_metal(piece, lines, axis, tolerance) for axis in range(3)]
            _close_metal(own, shape)
            for axis in range(3):
                pec[axis] |= own[axis]
        step(body.name)

    # --- H-edges: mu --------------------------------------------------------
    mu_r = tuple(_dual_harmonic(cell_mu, axis, h).astype(np.float32) for axis in range(3))

    result = MaterialGrid(
        grid=grid, eps_r=tuple(eps_r), sigma=tuple(sigma), mu_r=mu_r, pec=tuple(pec),
        dielectric=tuple(dielectric),
        cell_body=cell_body, bodies=names, warnings=warnings,
    )

    for port in (ports or {}).values():
        if port.ok:
            _discretize_port(result, port, project.get(port.name).values["impedance"], mesh.min_step)

    cache = {}

    def pieces():
        if "pieces" not in cache:
            cache["pieces"] = pec_pieces(result)
        return cache["pieces"]

    for element in (elements or {}).values():
        if element.ok:
            _discretize_element(result, element, project.get(element.name).values, mesh.min_step, pieces)

    for waveport in (waveports or {}).values():
        if waveport.ok:
            _discretize_waveport(result, waveport, mesh.min_step)
    _find_shorts(result, mesh.min_step, pieces)
    step("ports")

    return result


def _discretize_waveport(result, port, snap):
    from solver.port_mode import solve_port_mode      # solver -> discretize, not the other way

    lines = result.grid.lines[port.axis]
    index = 0 if port.direction > 0 else len(lines) - 1
    if abs(lines[index] - port.plane) > snap + 1e-12:
        result.errors.append(f"{port.name}: the port plane is not at the domain boundary")
        return
    measure = index + port.direction * MEASURE_CELLS
    if not 0 < measure < len(lines) - 1:
        result.errors.append(f"{port.name}: the line is shorter than {MEASURE_CELLS + 1} cells")
        return

    u, v = [a for a in range(3) if a != port.axis]
    if port.region is not None:                     # a box on a PCB edge
        mode = solve_port_mode(result, port.axis, index, region=port.region)
    else:                                           # a cylinder on a coax
        mode = solve_port_mode(result, port.axis, index,
                               circle=(port.center[u], port.center[v], port.radius))
    if not mode.ok:
        result.errors.append(f"{port.name}: {mode.errors[0]}")
        return
    result.waveports.append(DiscreteWavePort(port.name, port.axis, port.direction, index, measure, mode))


def _edges_in_metal(body, lines, axis, tolerance):
    """
    PEC rule for the E-edges along `axis` (see the header), as an edge-shaped
    bool array. Ray casting along the grid lines (geometry/raycast.py): the
    midpoints lie on lines along `axis` through the nodes of the other two
    axes; the cross segments lie on lines along those axes.
    """
    coords = [0.5 * (l[1:] + l[:-1]) if a == axis else l for a, l in enumerate(lines)]
    delta = 1e-6 * tolerance
    full = np.zeros(tuple(len(c) for c in coords), dtype=bool)
    # Only the grid points near the body: nothing beyond its box plus the
    # reach (<= tolerance) can be hit. Rays across the whole domain made
    # every body cost the same (~6 s for a 12-triangle box at 1.8 M cells).
    box = _box(body, coords, tolerance + 2 * delta)
    if box is None:
        return full
    sub = [c[b] for c, b in zip(coords, box)]
    hit = in_or_on_grid(body, axis, sub, delta)             # inside, or exactly on a face
    needed = 1 if body.kind == "sheet" else 2               # through a thin solid: in + out
    for across in range(3):
        if across != axis:
            gaps = 0.4999 * np.diff(lines[across])
            reach = (np.minimum(tolerance, np.r_[np.inf, gaps])[box[across]],
                     np.minimum(tolerance, np.r_[gaps, np.inf])[box[across]])
            hit |= crossings_on_grid(body, across, sub, reach, delta,
                                     all_offsets=body.kind == "sheet") >= needed
    full[box] = hit
    return full


def _cells_inside(body, centers):
    """Bool (nx, ny, nz): cell centers strictly inside the body (rays along x)."""
    full = np.zeros(tuple(len(c) for c in centers), dtype=bool)
    box = _box(body, centers, 0.0)
    if box is not None:
        full[box] = inside_on_grid(body, 0, [c[b] for c, b in zip(centers, box)])
    return full


def _box(body, coords, margin):
    """Per axis the slice of coords within the body's bounds +- margin; None if empty."""
    lo, hi = body.bounds
    box = []
    for a, c in enumerate(coords):
        i0 = int(np.searchsorted(c, lo[a] - margin - 1e-12, side="left"))
        i1 = int(np.searchsorted(c, hi[a] + margin + 1e-12, side="right"))
        if i1 <= i0:
            return None
        box.append(slice(i0, i1))
    return tuple(box)


def _close_metal(edges, shape):
    """
    Closure of one conductor's PEC edges (in place): its metal nodes are the
    nodes its PEC edges touch, and every edge between two of its metal
    nodes is PEC too. Without it a node on a curved surface could have PEC
    edges across the line but a free edge along it: the solver then sees a
    conductor that carries no current along the line there, and the TEM
    mode of the staircased line is broken (a coax pin came out 11 % off in
    Z0). Per body (per piece of a multi-piece body), so a gap between two
    conductors is never closed.
    """
    nodes = np.zeros(tuple(n + 1 for n in shape), dtype=bool)
    for axis, pec in enumerate(edges):
        lo = [slice(None)] * 3
        hi = [slice(None)] * 3
        lo[axis] = slice(0, -1)
        hi[axis] = slice(1, None)
        nodes[tuple(lo)] |= pec
        nodes[tuple(hi)] |= pec
    for axis in range(3):
        lo = [slice(None)] * 3
        hi = [slice(None)] * 3
        lo[axis] = slice(0, -1)
        hi[axis] = slice(1, None)
        edges[axis] |= nodes[tuple(lo)] & nodes[tuple(hi)]


def _edge_average(cell_values, axis, h, skip=None):
    """
    Value on each E-edge along `axis`: weighted mean over the cells around
    it. Weight of a cell = its quarter of the dual face (h_u / 2 * h_v / 2).
    skip: bool cells left out of the mean (thick metal); an edge with only
    skipped cells around it takes the plain mean.
    """
    if skip is not None and skip.any():
        plain = _edge_average(cell_values, axis, h)
        kept = _edge_average(np.where(skip, 0.0, cell_values), axis, h)
        share = _edge_average((~skip).astype(float), axis, h)       # weight of the kept cells
        with np.errstate(invalid="ignore", divide="ignore"):
            return np.where(share > 1e-12, kept / np.maximum(share, 1e-300), plain)

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


def _column(result, item, snap, kind):
    """
    The edge column of a port or lumped element along its axis:
    (axis, sign, edges, length) or None (an error is recorded).
    The axis must lie on grid lines; it is snapped to the nearest line
    within `snap` (the mesh merge distance), e.g. an element drawn on the
    top of 35 um copper whose face was merged onto the substrate line.
    """
    lines = result.grid.lines
    direction = item.plus - item.minus
    axis = int(np.argmax(np.abs(direction)))

    if np.linalg.norm(np.delete(direction, axis)) > 1e-9 * np.linalg.norm(direction):
        result.errors.append(f"{item.name}: the {kind} axis is not along x, y or z")
        return None

    index = []
    for a in range(3):
        if a == axis:
            continue
        i = int(np.argmin(np.abs(lines[a] - item.minus[a])))
        if abs(lines[a][i] - item.minus[a]) > snap + 1e-12:
            result.errors.append(f"{item.name}: the axis is not on a grid line ({AXES[a]})")
            return None
        index.append(i)

    along = lines[axis]
    mids = 0.5 * (along[1:] + along[:-1])
    g0, g1 = sorted((item.gap_minus[axis], item.gap_plus[axis]))
    n = np.nonzero((mids > g0) & (mids < g1))[0]
    if len(n) == 0:
        result.errors.append(f"{item.name}: the gap is shorter than one cell")
        return None

    edges = np.zeros((len(n), 3), dtype=np.int64)
    edges[:, axis] = n
    others = [a for a in range(3) if a != axis]
    edges[:, others[0]], edges[:, others[1]] = index

    if result.pec[axis][tuple(edges.T)].any():
        result.errors.append(f"{item.name}: part of the {kind} column is PEC (short circuit)")
        return None

    for end, node_index in (("first", n[0]), ("last", n[-1] + 1)):
        node = [0, 0, 0]
        node[axis] = node_index
        node[others[0]], node[others[1]] = index
        if not _node_on_metal(result.pec, node, result.grid.shape):
            result.errors.append(f"{item.name}: the {end} end of the column does not touch PEC")
            return None

    sign = 1 if direction[axis] > 0 else -1
    length = float(along[n[-1] + 1] - along[n[0]])
    return axis, sign, edges, length


def _discretize_port(result, port, resistance, snap):
    column = _column(result, port, snap, "port")
    if column is not None:
        axis, sign, edges, length = column
        result.ports.append(DiscretePort(port.name, axis, sign, edges, resistance, length))


def _staircase(result, item, snap):
    """
    The edge path of a lumped element whose axis is slanted in a grid
    plane (see the header): (axes, edges, signs, length, ends) or None (an
    error is recorded).
    """
    lines = result.grid.lines
    direction = item.plus - item.minus
    size = np.linalg.norm(direction)
    varying = [a for a in range(3) if abs(direction[a]) > 1e-9 * size]
    if len(varying) == 3:
        result.errors.append(f"{item.name}: the element axis must lie in a plane normal to x, y or z")
        return None
    fixed = next(a for a in range(3) if a not in varying)
    i_fixed = int(np.argmin(np.abs(lines[fixed] - item.minus[fixed])))
    if abs(lines[fixed][i_fixed] - item.minus[fixed]) > snap + 1e-12:
        result.errors.append(f"{item.name}: the axis is not on a grid line ({AXES[fixed]})")
        return None

    def nearest(point):
        node = [0, 0, 0]
        node[fixed] = i_fixed
        for a in varying:
            node[a] = int(np.argmin(np.abs(lines[a] - point[a])))
        return node

    def position(node):
        return np.array([lines[a][node[a]] for a in range(3)])

    unit = direction / size

    def off_line(point):
        d = point - item.minus
        return np.linalg.norm(d - np.dot(d, unit) * unit)

    node, target = nearest(item.minus), nearest(item.plus)
    path = []                               # (axis, edge [i, j, k], sign, midpoint)
    while node != target:
        best = None
        for a in varying:
            if node[a] == target[a]:
                continue
            sign = 1 if target[a] > node[a] else -1
            step = list(node)
            step[a] += sign
            mid = 0.5 * (position(node) + position(step))
            if best is None or off_line(mid) < best[0]:
                edge = list(node)
                edge[a] = min(node[a], step[a])
                best = (off_line(mid), a, edge, sign, mid, step)
        _, a, edge, sign, mid, node = best
        path.append((a, edge, sign, mid))

    t0, t1 = sorted(np.dot(p - item.minus, unit) for p in (item.gap_minus, item.gap_plus))
    path = [p for p in path if t0 < np.dot(p[3] - item.minus, unit) < t1]
    if not path:
        result.errors.append(f"{item.name}: the gap is shorter than one cell")
        return None
    axes = np.array([a for a, _, _, _ in path])
    edges = np.array([e for _, e, _, _ in path], dtype=np.int64)
    signs = np.array([s for _, _, s, _ in path])
    if any(result.pec[a][tuple(e)] for a, e in zip(axes, edges)):
        result.errors.append(f"{item.name}: part of the element path is PEC (short circuit)")
        return None

    first = list(edges[0])
    last = list(edges[-1])
    last[axes[-1]] += 1
    if signs[0] < 0:
        first[axes[0]] += 1
    if signs[-1] < 0:
        last[axes[-1]] -= 1
    for end, node in (("first", first), ("last", last)):
        if not _node_on_metal(result.pec, node, result.grid.shape):
            result.errors.append(f"{item.name}: the {end} end of the path does not touch PEC")
            return None

    length = float(sum(lines[a][e[a] + 1] - lines[a][e[a]] for a, e in zip(axes, edges)))
    return axes, edges, signs, length, (np.array(first), np.array(last))


def _discretize_element(result, element, values, snap, pieces):
    direction = element.plus - element.minus
    if np.count_nonzero(np.abs(direction) > 1e-9 * np.linalg.norm(direction)) == 1:
        column = _column(result, element, snap, "element")
        if column is None:
            return
        axis, sign, edges, length = column
        port = DiscretePort(element.name, axis, sign, edges, 0.0, length)       # for its ends
        ends = port.ends
        runs = [edges] + _parallel_columns(result, element, axis, edges, ends, pieces())
        lengths = [length] + [float(_lines_sum(result.grid.lines[axis], run[:, axis])) for run in runs[1:]]
        column = np.concatenate([np.full(len(run), k) for k, run in enumerate(runs)])
        edges = np.concatenate(runs)
        path = (np.full(len(edges), axis), edges, np.full(len(edges), sign), length, ends)
    else:
        path = _staircase(result, element, snap)
        if path is None:
            return
        column, lengths = np.zeros(len(path[1]), dtype=np.int64), [path[3]]
    axes, edges, signs, length, ends = path
    r, l, c = values["r"], values["l"] * 1e-9, values["c"] * 1e-12
    if values["topology"] == "series" and r == 0 and l == 0 and c == 0:
        result.warnings.append(f"{element.name}: series R = L = C = 0 is a plain short")
    if values["topology"] == "parallel" and r == 0 and l == 0 and c == 0:
        result.warnings.append(f"{element.name}: parallel R = L = C = 0 is an open circuit")
    result.elements.append(DiscreteElement(
        element.name, axes, edges, signs, length, ends, values["topology"], r, l, c,
        column, np.array(lengths),
    ))


def _lines_sum(lines, index):
    """Sum of the steps lines[i + 1] - lines[i] over the edge indices."""
    return np.sum(lines[index + 1] - lines[index])


def _parallel_columns(result, element, axis, central, ends, pieces):
    """
    The element's columns beside the central one (see the header): the
    grid lines along `axis` within its radius of the axis, each a run of
    free edges through the gap's middle edge, grown toward both ends until
    a node touches metal, inside the element's length, its two ends on the
    pieces of metal the central column joins. [(m, 3) edge arrays].
    """
    lines = result.grid.lines
    shape = result.grid.shape
    nodes = tuple(n + 1 for n in shape)
    u, v = [a for a in range(3) if a != axis]
    center = element.minus
    radius = element.radius
    if radius <= 0:
        return []

    def piece(node):
        return pieces[np.ravel_multi_index(tuple(node), nodes)]

    joined = sorted((piece(ends[0]), piece(ends[1])))
    lo_end, hi_end = sorted((element.minus[axis], element.plus[axis]))
    n_lo = int(np.searchsorted(lines[axis], lo_end - 1e-12, side="left"))
    n_hi = int(np.searchsorted(lines[axis], hi_end + 1e-12, side="right")) - 1
    middle = int(central[len(central) // 2, axis])
    near_u = np.nonzero(np.abs(lines[u] - center[u]) <= radius * (1 + 1e-9))[0]
    near_v = np.nonzero(np.abs(lines[v] - center[v]) <= radius * (1 + 1e-9))[0]
    home = (central[0, u], central[0, v])

    columns = []
    for a in near_u:
        for b in near_v:
            if (a, b) == home or (lines[u][a] - center[u]) ** 2 + (lines[v][b] - center[v]) ** 2 > radius ** 2 * (1 + 1e-9):
                continue
            index = [a, b]
            line = [0, 0, 0]
            line[u], line[v], line[axis] = a, b, slice(None)
            free = ~result.pec[axis][tuple(line)]

            def node(i):
                n = [0, 0, 0]
                n[u], n[v], n[axis] = index[0], index[1], i
                return n

            def metal(i):
                return _node_on_metal(result.pec, node(i), shape)

            if not free[middle]:
                continue
            first = middle
            while not metal(first) and first - 1 >= n_lo and free[first - 1]:
                first -= 1
            last = middle + 1
            while not metal(last) and last + 1 <= n_hi and free[last]:
                last += 1
            if not (metal(first) and metal(last)):
                continue
            ends_here = (piece(node(first)), piece(node(last)))
            if ends_here[0] == ends_here[1] or sorted(ends_here) != joined:
                continue
            edges = np.zeros((last - first, 3), dtype=np.int64)
            edges[:, axis] = np.arange(first, last)
            edges[:, u], edges[:, v] = a, b
            columns.append(edges)
    return columns


def pec_pieces(result):
    """Per node (raveled) the label of its piece of metal: nodes joined by PEC edges."""
    nodes = tuple(n + 1 for n in result.grid.shape)
    a, b = [], []
    for axis, pec in enumerate(result.pec):
        start = np.nonzero(pec)
        end = list(start)
        end[axis] = start[axis] + 1
        a.append(np.ravel_multi_index(start, nodes))
        b.append(np.ravel_multi_index(tuple(end), nodes))
    a, b = np.concatenate(a), np.concatenate(b)
    size = int(np.prod(nodes))
    graph = sp.coo_matrix((np.ones(len(a), dtype=np.int8), (a, b)), shape=(size, size))
    return connected_components(graph, directed=False)[1]


def _find_shorts(result, min_step, pieces):
    """Fills result.shorts (see the header)."""
    items = result.ports + result.elements + result.waveports
    if not items:
        return
    nodes = tuple(n + 1 for n in result.grid.shape)
    piece = pieces()
    hint = f"a gap narrower than the mesh resolves? (merge distance {min_step / 1e-3:.3g} mm, Mesh > min step)"

    for item in result.ports + result.elements:
        first, last = item.ends
        if piece[np.ravel_multi_index(tuple(first), nodes)] == piece[np.ravel_multi_index(tuple(last), nodes)]:
            what = "port" if item in result.ports else "element"
            result.shorts.append(f"{item.name}: both ends of the {what} are one piece of metal "
                                 f"({'shorted' if what == 'port' else 'bypassed'}); {hint}")
    for port in result.waveports:
        mode = port.mode
        u, v = np.meshgrid(np.arange(nodes[mode.u_axis]), np.arange(nodes[mode.v_axis]), indexing="ij")
        node = [None, None, None]
        node[port.axis], node[mode.u_axis], node[mode.v_axis] = np.full(u.shape, port.index), u, v
        on_plane = np.ravel_multi_index(tuple(node), nodes)
        hot, grounded = set(piece[on_plane[mode.hot]]), set(piece[on_plane[mode.grounded]])
        if hot & grounded:
            result.shorts.append(f"{port.name}: the signal conductor and ground are one piece of metal "
                                 f"(a short); {hint}")


def _node_on_metal(pec, node, shape):
    """True if any E-edge touching the node is PEC."""
    for axis in range(3):
        for offset in (-1, 0):
            index = list(node)
            index[axis] += offset
            if 0 <= index[axis] < shape[axis] and pec[axis][tuple(index)]:
                return True
    return False
