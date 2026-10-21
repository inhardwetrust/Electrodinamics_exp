# port_mode.py
#
# The TEM / quasi-TEM mode of a transmission line, on the cross-section of
# the compute grid where a waveguide port sits (coax, microstrip, CPW ...).
#
# On the port plane (grid line `index` along `axis`), solve the 2D Laplace
# problem  div(eps grad phi) = 0  on the plane's nodes, the way the FDTD
# grid sees the line (so the mode matches the staircased line, not the
# ideal one):
#   - metal nodes: touched by a PEC in-plane E-edge, or carrying a PEC edge
#     along the line. Pieces: metal nodes joined by in-plane PEC edges are
#     at one potential (E = 0 along those edges);
#   - conductors (which body is ground, which is signal): pieces joined
#     where their metal nodes touch only across a cell's diagonal form one
#     conductor (a thin staircased tube breaks into pieces that only touch
#     diagonally). Side by side across a free edge they stay apart: that is
#     a one-cell gap the FDTD grid sees (a narrow coplanar gap, a strip one
#     cell above its ground). The
#     conductor touching the port region's border is ground, the single
#     other conductor inside the region is the signal, conductors wholly
#     outside it float;
#   - potentials: the pieces touching the border are at 0 V, the largest
#     piece of the signal at 1 V. Other pieces float (unknown potential,
#     zero net charge): the FDTD grid joins nothing across a diagonal, so a
#     piece cut off by the staircase is a separate wire there;
#   - each free in-plane edge is a conductance g = eps_r * dual width /
#     length (eps_r of the discretized edge): the discrete Laplacian of the
#     FDTD grid;
#   - a box port (region) is solved on the whole plane: the region picks
#     the conductors, but the line's field does not stop at the box (the
#     FR4 between side grounds and the ground plane runs to the board's
#     edge, the fringe field spreads into the air). Cut at the box, the
#     mode misses that part: V I of the port then gave 5-9 % less than
#     the Poynting flux and S12 != S21 by ~0.4 dB. The plane's edge is a
#     natural boundary (the padding to the absorber);
#   - a circle port (coax) is solved inside the circle only (the shield
#     closes it; nodes outside are left out).
# Results (per unit length, for 1 V):
#   C' = eps0 sum g (dphi)^2,  C0' the same with eps_r = 1,
#   Z0 = 1 / (c0 sqrt(C' C0')),  eps_eff = C' / C0',
#   E on the in-plane edges = -dphi / length (V/m).

from dataclasses import dataclass, field

import numpy as np
import scipy.sparse as sp
from scipy.sparse.csgraph import connected_components
from scipy.sparse.linalg import spsolve

from project.settings import C0

EPS0 = 8.8541878128e-12


@dataclass
class PortMode:
    axis: int
    index: int
    u_axis: int
    v_axis: int
    phi: np.ndarray = None          # (nu+1, nv+1) node potentials, NaN outside the region
    e_u: np.ndarray = None          # (nu, nv+1) E on the u-edges (V/m), 1 V on the signal
    e_v: np.ndarray = None          # (nu+1, nv)
    e_u_air: np.ndarray = None      # the same with all eps_r = 1 (the H pattern of
    e_v_air: np.ndarray = None      # a quasi-TEM line: H does not see eps)
    capacitance: float = 0.0        # F/m with the dielectrics
    capacitance_air: float = 0.0    # F/m with all eps_r = 1
    hot: np.ndarray = None          # (nu+1, nv+1) bool: the nodes of the signal at 1 V
    grounded: np.ndarray = None     # (nu+1, nv+1) bool: the nodes of the grounded pieces
    errors: list = field(default_factory=list)

    @property
    def ok(self):
        return not self.errors

    @property
    def z0(self):
        return 1.0 / (C0 * np.sqrt(self.capacitance * self.capacitance_air))

    @property
    def eps_eff(self):
        return self.capacitance / self.capacitance_air


@dataclass
class _Edges:
    a: np.ndarray       # node ids
    b: np.ndarray
    g: np.ndarray       # conductance with eps_r
    g_air: np.ndarray   # conductance with eps_r = 1
    pec: np.ndarray


def solve_port_mode(materials, axis, index, region=None, circle=None):
    """
    region: ((u0, u1), (v0, v1)) in meters on the plane (u, v = the other
    two axes in increasing order), the port face's extent: it picks the
    conductors, the mode is solved on the whole plane; or
    circle: (center_u, center_v, radius) in meters, solved inside it only;
    None = the whole plane, ground = conductors on the plane's edge.
    """
    lines = materials.grid.lines
    u_axis, v_axis = [a for a in range(3) if a != axis]
    mode = PortMode(axis, index, u_axis, v_axis)
    lu, lv = lines[u_axis], lines[v_axis]

    def plane(array):
        take = [slice(None)] * 3
        take[axis] = index
        return array[tuple(take)]

    du, dv = np.diff(lu), np.diff(lv)
    ddu, ddv = _dual(du), _dual(dv)

    # Nodes of the region.
    uu, vv = np.meshgrid(lu, lv, indexing="ij")
    inside = np.ones(uu.shape, dtype=bool)
    box = inside                                    # nodes that pick the conductors
    if region is not None:
        (u0, u1), (v0, v1) = region
        tol = 1e-9
        box = (uu >= u0 - tol) & (uu <= u1 + tol) & (vv >= v0 - tol) & (vv <= v1 + tol)
    if circle is not None:
        cu, cv, radius = circle
        inside &= (uu - cu) ** 2 + (vv - cv) ** 2 <= (radius * (1 + 1e-6) + 1e-9) ** 2
        box = inside
    node_id = -np.ones(uu.shape, dtype=np.int64)
    node_id[inside] = np.arange(np.count_nonzero(inside))
    n = int(inside.sum())

    # In-plane edges with both ends in the region.
    parts = []
    for along_u in (True, False):
        e_axis = u_axis if along_u else v_axis
        pec = plane(materials.pec[e_axis])
        eps = plane(materials.eps_r[e_axis]).astype(float)
        i, j = np.indices(pec.shape)
        a = node_id[i, j]
        b = node_id[i + 1, j] if along_u else node_id[i, j + 1]
        geo = ddv[None, :] / du[:, None] if along_u else ddu[:, None] / dv[None, :]
        geo = np.broadcast_to(geo, pec.shape)
        ok = (a >= 0) & (b >= 0)
        parts.append(_Edges(a[ok], b[ok], (eps * geo)[ok], geo[ok], pec[ok]))
    edges = _Edges(*(np.concatenate([getattr(p, f) for p in parts])
                     for f in ("a", "b", "g", "g_air", "pec")))

    # Metal nodes: on an in-plane PEC edge, or with a PEC edge along the line.
    pa, pb = edges.a[edges.pec], edges.b[edges.pec]
    on_metal = np.zeros(n, dtype=bool)
    on_metal[pa] = on_metal[pb] = True
    along = materials.pec[axis]
    axial = np.zeros(uu.shape, dtype=bool)
    for k in (index - 1, index):
        if 0 <= k < along.shape[axis]:
            take = [slice(None)] * 3
            take[axis] = k
            axial |= along[tuple(take)]
    on_metal |= axial[inside]

    # Pieces (joined by PEC edges) and conductors (pieces joined where their
    # metal nodes touch diagonally).
    graph = sp.coo_matrix((np.ones(len(pa)), (pa, pb)), shape=(n, n))
    _, piece = connected_components(graph, directed=False)
    conductor = _conductors(node_id, on_metal, pa, pb, n)

    border = on_metal & (box & ~_interior(box))[inside]
    ground = set(conductor[border])
    signal = sorted(set(conductor[on_metal & box[inside]]) - ground)

    if not ground:
        mode.errors.append("no conductor touches the port border (no ground / outer conductor)")
        return mode
    if len(signal) != 1:
        mode.errors.append(
            "no signal conductor inside the port: every conductor touches its border (make the "
            "port larger than the signal trace, with the air around it)" if not signal else
            f"{len(signal)} separate signal conductors (multi-conductor ports not supported yet)"
        )
        return mode

    signal_pieces, counts = np.unique(piece[on_metal & (conductor == signal[0])], return_counts=True)
    hot_piece = signal_pieces[np.argmax(counts)]
    grounded_pieces = set(piece[border])

    # Unknowns: one per metal piece, one per free node.
    supernode = np.where(on_metal, piece, -1)
    ids, supernode[on_metal] = np.unique(supernode[on_metal], return_inverse=True)
    n_pieces = len(ids)
    free = np.nonzero(~on_metal)[0]
    supernode[free] = n_pieces + np.arange(len(free))
    m = n_pieces + len(free)
    fixed = np.zeros(m, dtype=bool)
    value = np.zeros(m)
    for k, p in enumerate(ids):
        if p in grounded_pieces:
            fixed[k] = True
        elif p == hot_piece:
            fixed[k] = True
            value[k] = 1.0

    mode.hot = np.zeros(uu.shape, dtype=bool)
    mode.hot[inside] = on_metal & (piece == hot_piece)
    mode.grounded = np.zeros(uu.shape, dtype=bool)
    mode.grounded[inside] = on_metal & np.isin(piece, list(grounded_pieces))

    sa, sb = supernode[edges.a], supernode[edges.b]
    phi, mode.capacitance = _solve(sa, sb, edges.g, ~edges.pec & (sa != sb), m, fixed, value)
    phi_air, mode.capacitance_air = _solve(sa, sb, edges.g_air, ~edges.pec & (sa != sb), m, fixed, value)

    def fields(values):
        full = np.full(uu.shape, np.nan)
        full[inside] = values[supernode]
        e_u = -(full[1:, :] - full[:-1, :]) / du[:, None]
        e_v = -(full[:, 1:] - full[:, :-1]) / dv[None, :]
        return full, np.nan_to_num(e_u), np.nan_to_num(e_v)   # 0 on edges leaving the region

    mode.phi, mode.e_u, mode.e_v = fields(phi)
    _, mode.e_u_air, mode.e_v_air = fields(phi_air)
    return mode


def _solve(a, b, g, active, n, fixed, value):
    """Graph Laplacian with Dirichlet nodes; returns (phi, eps0 * sum g dphi^2)."""
    a, b, w = a[active], b[active], g[active]
    lap = sp.coo_matrix(
        (np.concatenate((w, w, -w, -w)),
         (np.concatenate((a, b, a, b)), np.concatenate((a, b, b, a)))),
        shape=(n, n),
    ).tocsr()

    free_nodes = np.nonzero(~fixed)[0]
    fixed_nodes = np.nonzero(fixed)[0]
    a_ff = lap[free_nodes][:, free_nodes]
    rhs = -(lap[free_nodes][:, fixed_nodes] @ value[fixed_nodes])
    # Free nodes with no edges at all would make the matrix singular.
    empty = np.asarray(a_ff.diagonal()) == 0
    a_ff = (a_ff + sp.diags(empty.astype(float))).tocsc()

    phi = value.copy()
    phi[free_nodes] = spsolve(a_ff, rhs)
    energy = float(np.sum(w * (phi[a] - phi[b]) ** 2))
    return phi, EPS0 * energy


def _dual(d):
    out = np.zeros(len(d) + 1)
    out[:-1] += 0.5 * d
    out[1:] += 0.5 * d
    return out


def _conductors(node_id, on_metal, pa, pb, n):
    """
    Conductor label per node: metal nodes joined by PEC edges, or touching
    only across a cell's diagonal (the cell's other two corners free: a
    staircased tube's pieces). Two metal nodes side by side with a FREE
    edge between them stay apart: that edge is a real gap in the FDTD grid
    (a one-cell coplanar gap). Nor does a diagonal join them when a third
    corner is metal (a strip one cell above a ground plane): joining those
    shorted the signal to the ground.
    """
    metal = np.zeros(node_id.shape, dtype=bool)
    metal[node_id >= 0] = on_metal[node_id[node_id >= 0]]
    m00, m11 = metal[:-1, :-1], metal[1:, 1:]
    m10, m01 = metal[1:, :-1], metal[:-1, 1:]
    main = m00 & m11 & ~m10 & ~m01                  # (i, j) - (i + 1, j + 1)
    anti = m10 & m01 & ~m00 & ~m11                  # (i + 1, j) - (i, j + 1)
    a = np.concatenate([pa, node_id[:-1, :-1][main], node_id[1:, :-1][anti]])
    b = np.concatenate([pb, node_id[1:, 1:][main], node_id[:-1, 1:][anti]])
    graph = sp.coo_matrix((np.ones(len(a)), (a, b)), shape=(n, n))
    _, labels = connected_components(graph, directed=False)
    return np.where(on_metal, labels + 1, 0)


def _interior(inside):
    """Nodes of the region whose 4 neighbours are all in the region."""
    p = np.pad(inside, 1, constant_values=False)
    return inside & p[2:, 1:-1] & p[:-2, 1:-1] & p[1:-1, 2:] & p[1:-1, :-2]
