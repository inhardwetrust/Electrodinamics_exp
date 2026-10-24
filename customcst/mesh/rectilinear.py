# rectilinear.py
#
# Level 2: the compute grid. A non-uniform rectilinear grid is three sorted
# arrays of node coordinates x[], y[], z[] (meters); every line runs through
# the whole domain (as in CST's hexahedral mesh and openEMS).
#
# Per axis:
#   1. Fixpoints: coordinates that must be grid lines.
#      - planar CAD faces of material bodies normal to the axis, and the
#        body extents along the axis. Only planar CAD faces: a facet of a
#        tessellated cylinder can be normal to the axis too (at its top),
#        and those odd coordinates chained the merge of nearby faces (an
#        SMA's round body pulled a ground plane 7 um off its face). A face
#        within 1 deg of the axis normal and under min_step across counts,
#        at its mean coordinate: KiCad exports polygon edges leaning by a
#        few um, and a trace whose walls all lean had no lines across it;
#      - port axes (transverse coordinates), so each port lies on a line;
#        a lumped element's edges too (axis +- its radius), so the cells
#        across its width carry its parallel columns;
#      - the domain ends (model box + padding). A waveguide port's plane
#        is the domain end on its side, with no padding: the line runs
#        through it into the absorber.
#   2. Merge fixpoints closer than min_step into one. Thin bodies (35 um
#      copper) collapse onto one line here: this is where a thin solid
#      becomes a sheet. Inside a cluster the priority is: domain end >
#      dielectric face > conductor face > port axis, so the substrate
#      thickness stays exact and the copper sits on its surface. Two
#      dielectric faces in one cluster (copper between two layers): the
#      face of the thinner layer wins, its relative error would be the
#      larger one (a 15 um inner ground between 0.2 and 1.07 mm of FR4
#      sits on the 0.2 mm layer's face, not halfway).
#   3. Target step per interval between fixpoints:
#        min( lambda(f_max) / N in the densest material spanning it,
#             interval / cells_per_feature ),  but >= min_step.
#   4. Slanted conductor faces (a 45 degree trace, a rotated pad) put no
#      lines: on a grid sized for the straight parts their staircase
#      joins a trace to the pour across a 0.2 mm gap. Each planar
#      conductor face not normal to an axis (by more than min_step across
#      a triangle: KiCad's edges a hair off an axis are straight) casts a
#      ray from each of its triangles along its normal, out (the gap to
#      the nearest other piece of metal: only there can the staircase join
#      two conductors) and in (the width: to its own piece's near-parallel
#      wall, not a via in the pour), counting only faces near facing it
#      and hits beyond min_step; the shorter is the feature f. Along
#      each axis its normal has a component on, over the face's extent
#      +- f, the step is at most max(f / cells_per_feature, min_step).
#      Curved faces (round pad ends, via holes) cast no rays: their
#      extremes are fixpoints already.
#   5. Grading: the step may grow by at most (max_ratio - 1) per unit
#      distance from any interval (or slanted region) with a smaller target:
#        h(x) = min_j ( h_j + (max_ratio - 1) * dist(x, interval_j) ).
#   6. Lines: in each interval, n = ceil(integral dx / h) cells, placed by
#      inverting that integral, so steps follow h(x) and hit both ends.
#
# Accuracy of narrow lines (z0_error_percent): a staircased conductor looks
# wider near its edges, where the field is strongest, so a narrow line's
# capacitance comes out high and its Z0 low. The cells across a trace or a
# gap are interval / max(interval / cells_per_feature, min_step): both
# settings bound them. A 0.3 mm trace with 0.25 mm gaps over 0.2 mm FR4
# (2D mode solver; converged 52.4 Ohm): cells per feature 4: -7 % (the 3D
# model 08 at min step 0.05: 48.8 Ohm), 6: -3 %, 8: -1.5 %, 12: -1.1 %,
# 24: -0.4 %. Good for comparing variants; absolute Z0 needs >= 8.

import math
from dataclasses import dataclass, field

import numpy as np

from project.object_types import CONDUCTOR_TYPES
from project.settings import C0, GHZ, MM, padding_m

AXES = "xyz"

PRIORITY_DOMAIN = 3
PRIORITY_DIELECTRIC = 2
PRIORITY_CONDUCTOR = 1
PRIORITY_PORT = 0

SAMPLES_PER_INTERVAL = 400
RAY_CHUNK = 64              # rays per batch against all conductor triangles
RAY_EPSILON = 1e-7          # m: hits nearer than this are the ray's own face
RAY_FACING = math.cos(math.radians(45))     # a gap ray's hit counts on a face within 45 deg of facing it
WIDTH_FACING = math.cos(math.radians(15))   # a width ray's: the piece's own wall, near parallel
SLANTED_BUCKET = 1.25       # slanted regions with steps within this ratio are united
ALIGNED = math.cos(math.radians(1))         # a planar face within 1 deg of an axis normal is a fixpoint

ACCURATE_CELLS_PER_FEATURE = 8      # below: the Z0 accuracy notice
Z0_ERROR = ((4, 7.0), (6, 3.0), (8, 1.5), (12, 1.1), (24, 0.4), (48, 0.0))   # (cells, % low)


def z0_error_percent(cells_per_feature):
    """Rough Z0 underestimate (percent) of a narrow line at this setting (see the header)."""
    n, e = zip(*Z0_ERROR)
    return float(np.interp(math.log(max(cells_per_feature, 1.0)), np.log(n), e))


@dataclass
class RectilinearGrid:
    lines: tuple            # (x[], y[], z[]) node coordinates, meters

    @property
    def shape(self):
        """Cell counts (nx, ny, nz)."""
        return tuple(len(l) - 1 for l in self.lines)

    @property
    def cell_count(self):
        return int(np.prod(self.shape))

    @property
    def bounds(self):
        return (np.array([l[0] for l in self.lines]), np.array([l[-1] for l in self.lines]))

    def steps(self, axis):
        return np.diff(self.lines[axis])

    def time_step(self):
        """FDTD Courant limit for the smallest cell: c dt <= 1/sqrt(sum 1/h^2)."""
        h = [self.steps(a).min() for a in range(3)]
        return 1.0 / (C0 * math.sqrt(sum(1.0 / s ** 2 for s in h)))


@dataclass
class MeshResult:
    grid: RectilinearGrid = None
    fixpoints: list = field(default_factory=list)   # per axis: kept fixpoints (m)
    merged: list = field(default_factory=list)      # per axis: [(kept, [dropped...])]
    warnings: list = field(default_factory=list)
    error: str = ""
    min_step: float = 0.0                           # merge distance actually used (m)
    slanted: list = field(default_factory=list)     # per axis: [(lo, hi, step m)] refined regions
    slanted_faces: int = 0                          # slanted conductor triangles that refine
    slanted_feature: float = 0.0                    # their narrowest feature (m)

    @property
    def ok(self):
        return self.grid is not None


def build_grid(geometry, project, ports=None, waveports=None, vias=(), region=None):
    """
    MeshResult for the project's typed bodies; untyped bodies are ignored.
    ports: {name: PortGeometry} of everything that needs a grid line on its
    axis (ports and lumped elements).
    waveports: {name: WavePortGeometry}: their planes end the domain.
    vias: PEC bodies of the assigned vias (geometry.holes.via_body).
    region: (lo, hi) (m): the grid of that box only (it ends the domain
    where it is inside the model; the slanted faces outside it are not
    looked at). The Z0 path's sections use it (solver/path_profile.py).
    """
    result = MeshResult()
    mesh = project.settings["mesh"]
    f_max = project.settings["simulation"]["f_max"] * GHZ

    materials = []          # (body, sqrt(eps_r mu_r) or None for conductors)
    untyped = []
    for body in geometry.bodies:
        assignment = project.get(body.name)
        if assignment is None:
            untyped.append(body.name)
        elif assignment.type in CONDUCTOR_TYPES:
            materials.append((body, None))
        elif assignment.type == "dielectric":
            v = assignment.values
            materials.append((body, math.sqrt(v["eps_r"] * v["mu_r"])))

    materials += [(body, None) for body in vias]

    if untyped:
        result.warnings.append(f"ignored (no type): {', '.join(untyped)}")
    if not materials:
        result.error = "no bodies with a material type"
        return result

    lo = np.min([b.bounds[0] for b, _ in materials], axis=0)
    hi = np.max([b.bounds[1] for b, _ in materials], axis=0)
    pad = padding_m(project.settings)
    lo, hi = lo - pad, hi + pad
    if region is not None:
        lo, hi = np.maximum(lo, region[0]), np.minimum(hi, region[1])
    for w in (waveports or {}).values():
        if w.ok:
            if w.direction > 0:
                lo[w.axis] = w.plane
            else:
                hi[w.axis] = w.plane

    n_per_lambda = mesh["cells_per_wavelength"]
    densest = max([n for _, n in materials if n is not None] + [1.0])
    h_lambda_min = C0 / (f_max * densest) / n_per_lambda
    min_step = mesh["min_step"] * MM if mesh["min_step"] > 0 else h_lambda_min / 10.0
    result.min_step = min_step

    valid_ports = [p for p in (ports or {}).values() if p.ok]
    result.slanted = [[], [], []]
    if mesh["slanted"]:
        _slanted(result, [b for b, n in materials if n is None], mesh["cells_per_feature"], min_step,
                 C0 / f_max / n_per_lambda, region)

    lines = []
    for axis in range(3):
        points = _fixpoints(axis, materials, valid_ports, lo[axis], hi[axis], min_step)
        kept, merged = _merge(points, min_step)
        result.fixpoints.append(np.array([p for p, _ in kept]))
        result.merged.append(merged)

        targets = _interval_targets(
            axis, result.fixpoints[axis], materials, f_max, n_per_lambda,
            mesh["cells_per_feature"], min_step,
        )
        lines.append(_place_lines(result.fixpoints[axis], targets, mesh["max_ratio"], result.slanted[axis]))

    result.grid = RectilinearGrid(tuple(lines))
    return result


# =====================================================
# Fixpoints
# =====================================================

def _fixpoints(axis, materials, ports, lo, hi, min_step):
    """[(coordinate, priority, size)] along one axis; size: the body's extent (tie-break)."""
    points = [(lo, PRIORITY_DOMAIN, math.inf), (hi, PRIORITY_DOMAIN, math.inf)]

    for body, n in materials:
        priority = PRIORITY_CONDUCTOR if n is None else PRIORITY_DIELECTRIC
        b_lo, b_hi = body.bounds
        size = float(b_hi[axis] - b_lo[axis])
        points += [(b_lo[axis], priority, size), (b_hi[axis], priority, size)]

        # Planar CAD faces normal to this axis (exact for boxes, caps of
        # cylinders), or a hair off it: KiCad's polygon edges lean by a few
        # um, at their mean coordinate.
        v = body.vertices[body.triangles]                       # (T, 3, 3)
        normal = np.cross(v[:, 1] - v[:, 0], v[:, 2] - v[:, 0])
        length = np.linalg.norm(normal, axis=1)
        flat = (length > 0) & body.planar
        aligned = np.zeros(len(v), dtype=bool)
        aligned[flat] = np.abs(normal[flat, axis]) >= ALIGNED * length[flat]
        aligned &= np.ptp(v[:, :, axis], axis=1) < min_step
        for c in np.unique(np.round(v[aligned, :, axis].mean(axis=1), 12)):
            points.append((float(c), priority, size))

    for port in ports:
        direction = port.plus - port.minus
        if np.abs(direction[axis]) <= 1e-9 * np.linalg.norm(direction):
            points.append((float(port.minus[axis]), PRIORITY_PORT, math.inf))   # transverse: one line
            if port.radius > 0:     # a lumped element's width: lines across it for its columns
                points += [(float(port.minus[axis]) + s * port.radius, PRIORITY_PORT, math.inf) for s in (-1, 1)]

    return [(c, p, s) for c, p, s in points if lo <= c <= hi]


def _merge(points, min_step):
    """
    Clusters of fixpoints closer than min_step become one: the highest
    priority wins, then the thinnest body (mean on ties). points:
    [(coord, priority, size)]. Returns (kept [(coord, priority)],
    merged [(kept coord, [dropped coords])]).
    """
    points = sorted(points)
    clusters = [[points[0]]]
    for point in points[1:]:
        if point[0] - clusters[-1][-1][0] < min_step:
            clusters[-1].append(point)
        else:
            clusters.append([point])

    kept, merged = [], []
    for cluster in clusters:
        top = max(p for _, p, _ in cluster)
        thinnest = min(size for _, p, size in cluster if p == top)
        winners = [c for c, p, size in cluster if p == top and size == thinnest]
        coord = float(np.mean(winners))
        kept.append((coord, top))

        dropped = sorted({round(c, 12) for c, _, _ in cluster} - {round(coord, 12)})
        if dropped:
            merged.append((coord, dropped))

    return kept, merged


# =====================================================
# Slanted conductor faces
# =====================================================

def _slanted(result, conductors, cells_per_feature, min_step, h_air, region=None):
    """
    Fills result.slanted: per axis the regions around slanted planar
    conductor faces and their step (see the header, step 4). Regions whose
    step is no finer than the air wavelength's are dropped. region: only
    the triangles touching that box (lo, hi) count, as faces and targets.
    """
    solids = [p for body in conductors for p in (body.pieces or [body])]
    if not solids:
        return
    tri = np.concatenate([p.vertices[p.triangles] for p in solids])          # (T, 3, 3) targets
    owner = np.concatenate([np.full(len(p.triangles), k) for k, p in enumerate(solids)])
    masks = [_slanted_mask(p, min_step) for p in solids]
    if region is not None:
        near = [_touching(p.vertices[p.triangles], region) for p in solids]
        masks = [m & t for m, t in zip(masks, near)]
        near = np.concatenate(near)
        tri, owner = tri[near], owner[near]
    faces = np.concatenate([p.vertices[p.triangles][m] for p, m in zip(solids, masks)])
    if not len(faces):
        return
    own = np.concatenate([np.full(np.count_nonzero(m), k) for k, m in enumerate(masks)])
    normal = np.cross(faces[:, 1] - faces[:, 0], faces[:, 2] - faces[:, 0])
    normal /= np.linalg.norm(normal, axis=1)[:, None]
    origin = faces.mean(axis=1)

    # The gap: to another piece of metal only (a trace and its pour, two
    # pads), where the staircase can join them. The width: to the piece's
    # own parallel wall only (a trace's other edge), not to a via standing
    # in the pour or across a pour's slanted corner. A feature under the
    # min step is not resolved (as fixpoints merge): um-sized notches of a
    # polygonized pour outline. A real gap that narrow shows up as a short
    # on the mesh.
    out = _ray_hits(origin, normal, tri, (owner, own, False), RAY_FACING)
    inward = _ray_hits(origin, -normal, tri, (owner, own, True), WIDTH_FACING)
    feature = np.minimum(np.where(out >= min_step, out, math.inf), np.where(inward >= min_step, inward, math.inf))
    step = np.maximum(feature / cells_per_feature, min_step)
    keep = step < h_air
    result.slanted_faces = int(keep.sum())
    result.slanted_feature = float(feature[keep].min()) if keep.any() else 0.0

    for axis in range(3):
        spans = keep & (np.abs(normal[:, axis]) > 1e-6)
        if spans.any():
            lo = faces[spans, :, axis].min(axis=1) - feature[spans]
            hi = faces[spans, :, axis].max(axis=1) + feature[spans]
            result.slanted[axis] = _unite(lo, hi, step[spans])


def _touching(tri, region):
    """(T,) bool: the triangles (T, 3, 3) whose bounding box touches the box region (lo, hi)."""
    lo, hi = region
    return np.all((tri.min(axis=1) <= hi) & (tri.max(axis=1) >= lo), axis=1)


def _slanted_mask(body, min_step):
    """
    Triangles of a body on planar CAD faces normal to no axis. A triangle
    whose spread along its normal's main axis is under min_step is
    straight for the grid (KiCad's polygon edges a hair off an axis).
    """
    v = body.vertices[body.triangles]
    normal = np.cross(v[:, 1] - v[:, 0], v[:, 2] - v[:, 0])
    length = np.linalg.norm(normal, axis=1)
    flat = (length > 0) & (body.planar if body.planar is not None else True)
    main = np.argmax(np.abs(normal), axis=1)
    spread = np.ptp(v[np.arange(len(v)), :, main], axis=1)
    return flat & (spread >= min_step)


def _ray_hits(origins, directions, tri, pieces, facing_min):
    """
    Distance along each ray to the nearest triangle (Moller-Trumbore) that
    faces along the ray within acos(facing_min) (an opposite wall, a
    neighbor's wall), inf if none. pieces: (piece of each triangle, piece
    of each ray, same): a ray hits only its own piece (same) or only the
    others. Without the facing rule, a ray from a triangle next to a
    corner of a polygonized pour outline hits the corner's other wall a
    few um away.
    """
    v0 = tri[:, 0]
    e1, e2 = tri[:, 1] - v0, tri[:, 2] - v0
    facing = np.cross(e1, e2)
    facing /= np.maximum(np.linalg.norm(facing, axis=1), 1e-300)[:, None]
    out = np.full(len(origins), math.inf)
    for start in range(0, len(origins), RAY_CHUNK):
        o = origins[start:start + RAY_CHUNK]
        d = directions[start:start + RAY_CHUNK]
        owner, own, same = pieces
        may = (owner[None] == own[start:start + RAY_CHUNK, None]) == same
        p = np.cross(d[:, None], e2[None])                                  # (R, T, 3)
        det = np.einsum("rtk,tk->rt", p, e1)
        ok = np.abs(det) > 1e-30
        inv = np.where(ok, 1.0 / np.where(ok, det, 1.0), 0.0)
        s = o[:, None] - v0[None]
        u = np.einsum("rtk,rtk->rt", s, p) * inv
        q = np.cross(s, e1[None])
        w = np.einsum("rtk,rk->rt", q, d) * inv
        t = np.einsum("rtk,tk->rt", q, e2) * inv
        parallel = np.abs(d @ facing.T) >= facing_min
        hit = may & ok & parallel & (u >= 0) & (w >= 0) & (u + w <= 1) & (t > RAY_EPSILON)
        out[start:start + RAY_CHUNK] = np.where(hit, t, math.inf).min(axis=1)
    return out


def _unite(lo, hi, step):
    """[(lo, hi, step)]: overlapping regions with steps within SLANTED_BUCKET united (the finest step)."""
    regions = []
    bucket = np.floor(np.log(step) / math.log(SLANTED_BUCKET))
    for b in np.unique(bucket):
        sel = bucket == b
        h = float(step[sel].min())
        order = np.argsort(lo[sel])
        for a, z in zip(lo[sel][order], hi[sel][order]):
            if regions and regions[-1][2] == h and a <= regions[-1][1]:
                regions[-1][1] = max(regions[-1][1], z)
            else:
                regions.append([float(a), float(z), h])
    return [tuple(r) for r in regions]


# =====================================================
# Steps
# =====================================================

def _interval_targets(axis, fixpoints, materials, f_max, n_per_lambda, cells_per_feature, min_step):
    """Target step for each interval between consecutive fixpoints."""
    targets = []
    for a, b in zip(fixpoints[:-1], fixpoints[1:]):
        n = 1.0                             # air
        for body, refr in materials:
            if refr is None:
                continue
            b_lo, b_hi = body.bounds
            if b_lo[axis] < b and b_hi[axis] > a:      # body spans part of this slab
                n = max(n, refr)

        h = C0 / (f_max * n) / n_per_lambda
        h = min(h, (b - a) / cells_per_feature)
        targets.append(max(h, min_step))
    return np.array(targets)


def _place_lines(fixpoints, targets, max_ratio, slanted=()):
    growth = max_ratio - 1.0
    a_all, b_all = fixpoints[:-1], fixpoints[1:]
    # The slanted regions grade like intervals.
    extra = np.array(slanted, dtype=float).reshape(-1, 3)
    a_h, b_h = np.concatenate((a_all, extra[:, 0])), np.concatenate((b_all, extra[:, 1]))
    h_all = np.concatenate((targets, extra[:, 2]))

    def h_of(x):
        # Distance from each x to each interval (0 inside it).
        d = np.maximum(0.0, np.maximum(a_h[None] - x[:, None], x[:, None] - b_h[None]))
        return np.min(h_all[None] + growth * d, axis=1)

    lines = [fixpoints[0]]
    for a, b in zip(a_all, b_all):
        x = np.linspace(a, b, SAMPLES_PER_INTERVAL)
        density = 1.0 / h_of(x)
        cumulative = np.concatenate(([0.0], np.cumsum(0.5 * (density[1:] + density[:-1]) * np.diff(x))))
        n = max(1, int(math.ceil(cumulative[-1] - 1e-9)))
        inner = np.interp(np.arange(1, n) * cumulative[-1] / n, cumulative, x)
        lines.extend(inner)
        lines.append(b)

    return np.array(lines)
