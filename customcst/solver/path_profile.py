# path_profile.py
#
# The Z0 profile along a path drawn in the model (project [z0path], the
# Z0 path mode of the 3D view): a polyline in a plane normal to one axis
# (the board's top), and at points along it the cross-section normal to
# the path. Where a profile along an axis cuts a diagonal trace
# obliquely (a 45 degree run reads ~15 % low: the cut is sqrt(2) wider),
# this one cuts every segment square.
#   - each segment is solved in a frame of its own: the model is rotated
#     so that the segment runs along the third axis (s across it, w along
#     the plane's normal, a along it) and its cross-sections are grid
#     planes there. Each section is discretized as a slab two cells thick,
#     like the convergence tool does (solver/line_profile.py): the solver
#     sees the trace from the geometry, square to it, without the 3D
#     grid's staircase;
#   - the section's own grid is the rotated model's grid at
#     CELLS_PER_FEATURE (the merge distance scaled to it, as the
#     convergence levels do): about converged, not the 3D grid's view.
#     On a 30 degree microstrip it reads the straight line's converged Z0;
#   - the sections are at the faces square to the segment (the rotated
#     grid's fixpoints: a pad's or a taper's edges), between its ends (none
#     at a vertex: there the cross-section is not a line's), at most
#     SECTION_STEP apart. A segment's sections come from a few
#     discretizations: boxes the section's width across and up to
#     CHUNK_LENGTH along, each section a slab two cells thick in one (one
#     per section took seconds each on a board; one per segment, a 22 mm
#     straight run on a 4 mm window needed 65 M cells, out of memory);
#   - each section reaches half_width to either side of the path: the
#     conductors touching its sides (the pour, the planes) are ground, as
#     the model's sides are for the profile along an axis. Wide enough to
#     hold the trace and its gaps, narrow enough not to cut a neighboring
#     line: a conductor on the path's net cut by the side reads as ground;
#   - a window rides along the path: each segment's grid and
#     discretization cover only the box half_width across and beyond its
#     ends, over the model's whole height (bodies outside it are dropped,
#     the slanted refinement looks only inside; build_grid's region). The
#     whole rotated board took 2.3x as long, for the same Z0 within 0.4 Ohm
#     (justswitch_v3). Cut in height too (half_width above and below the
#     plane) it was 30 % faster again, but cut an SMA's coax shield above
#     the board open: 59 Ohm for 49, and the answer moved with the plane;
#   - a section whose discretization (and nets) equals the previous one's
#     on the segment (a uniform run) takes its answer without a solve;
#   - what joins out of the plane counts as in the axis profile: each node
#     of a section takes the 3D net (line_profile.Nets) of the 3D grid's
#     node nearest to it, rotated back;
#   - the coordinate of a section is its distance along the path from its
#     first point (m).

import copy
import hashlib
from dataclasses import dataclass, field, replace

import numpy as np

from geometry.body import Body, Geometry
from mesh.rectilinear import RectilinearGrid, build_grid
from solver.line_profile import Profile, Section, line_impedance

PATH = -1                   # Profile.axis of a path profile
CELLS_PER_FEATURE = 16.0    # the sections' own grid (at least the project's)
SECTION_STEP = 0.1e-3       # m, the longest distance between two sections
CHUNK_LENGTH = 2e-3         # m, the longest stretch of a segment discretized at once
SECTION_GAP = 0.02e-3       # m, the shortest (faces closer than this give one section)


@dataclass
class Path:
    """A polyline in the plane normal to `axis` at `at` (m)."""
    axis: int
    at: float
    points: np.ndarray              # (n, 2) m, (u, v) = the other two axes in order
    half_width: float               # m, the cross-section's reach to either side

    def world(self, uv):
        """(n, 2) in-plane points -> (n, 3) model points on the plane."""
        uv = np.atleast_2d(np.asarray(uv, dtype=float))
        u, v = [a for a in range(3) if a != self.axis]
        out = np.empty((len(uv), 3))
        out[:, u], out[:, v], out[:, self.axis] = uv[:, 0], uv[:, 1], self.at
        return out

    @property
    def length(self):
        return float(np.linalg.norm(np.diff(self.points, axis=0), axis=1).sum()) if len(self.points) > 1 else 0.0


@dataclass
class PathProfile(Profile):
    axis: int = PATH
    points: list = field(default_factory=list)      # (3,) m: each section's point on the path
    across: list = field(default_factory=list)      # (3,) unit: each section's direction across the path
    half_width: float = 0.0


def segment_frame(path, k):
    """
    Rotation R (3 x 3) of segment k: R @ model = (s, w, a), rows = the
    model directions of s (across, to the left of the segment), w (the
    plane's normal) and a (along the segment).
    """
    p0, p1 = path.world(path.points[k:k + 2])
    along = p1 - p0
    along /= np.linalg.norm(along)
    normal = np.zeros(3)
    normal[path.axis] = 1.0
    return np.array([np.cross(normal, along), normal, along])


def rotated_body(body, rotation):
    """The body in the frame `rotation` (rows: the new axes in model coordinates)."""
    r = np.asarray(rotation, dtype=float)

    def turn(points):
        return np.asarray(points, dtype=float) @ r.T

    circles = body.circles.copy()
    circles[:, 0:3], circles[:, 3:6] = turn(circles[:, 0:3]), turn(circles[:, 3:6])
    holes = body.holes.copy()
    holes[:, 0:3], holes[:, 3:6] = turn(holes[:, 0:3]), turn(holes[:, 3:6])
    return Body(body.name, body.kind, turn(body.vertices), body.triangles, turn(body.normals),
                turn(body.edges.reshape(-1, 3)).reshape(-1, 2, 3), corners=turn(body.corners),
                circles=circles, holes=holes, planar=body.planar, color=body.color,
                pieces=[rotated_body(p, r) for p in body.pieces] if body.pieces else None)


class _SectionNets:
    """Nets of a rotated section, read from the 3D grid's nets (line_profile.Nets)."""

    def __init__(self, nets, rotation, a):
        self.nets, self.rotation, self.a = nets, rotation, a
        self.driven = nets.driven
        self._plane = None

    def plane(self, _axis, _coordinate, lines_s, lines_w):
        if self._plane is None:         # the same section's lines each time
            s, w = np.meshgrid(lines_s, lines_w, indexing="ij")
            local = np.stack([s.ravel(), w.ravel(), np.full(s.size, self.a)], axis=1)
            self._plane = self.nets.at(local @ self.rotation).reshape(s.shape)
        return self._plane


def path_profile(geometry, project, path, vias=(), nets=None, progress=None):
    """
    PathProfile of `path` (Path). vias: the assigned via bodies; nets:
    pec_nets of the 3D grid (None: each section alone). progress(done,
    total, what) after each section and each step of a segment's setup
    (its grid: total 0, the count is not known yet; its discretization);
    returning False stops.
    """
    from discretize.materials import discretize      # discretize imports solver code
    profile = PathProfile(half_width=path.half_width)
    plans, start = [], 0.0
    segments = len(path.points) - 1
    for k in range(segments):
        if progress is not None and progress(0, 0, f"segment {k + 1}/{segments}: grid") is False:
            return profile
        rotation = segment_frame(path, k)
        p0, p1 = path.world(path.points[k:k + 2])
        a0, a1 = float(rotation[2] @ p0), float(rotation[2] @ p1)
        s_c, w_at = float(rotation[0] @ p0), float(rotation[1] @ p0)
        reach = path.half_width
        region = (np.array([s_c - reach, -np.inf, a0 - reach]),     # the whole height
                  np.array([s_c + reach, np.inf, a1 + reach]))
        frame = Geometry(_inside([rotated_body(b, rotation) for b in geometry.bodies], region),
                         geometry.source)
        frame_vias = _inside([rotated_body(b, rotation) for b in vias], region)
        mesh = build_grid(frame, _fine(project), {}, None, frame_vias, region)
        positions = _positions(mesh.fixpoints[2] if mesh.ok else np.zeros(0), a0, a1)
        plans.append((rotation, frame, frame_vias, mesh, p0, start, a0, positions))
        start += a1 - a0

    total = sum(len(plan[-1]) for plan in plans)
    done = 0
    for k, (rotation, frame, frame_vias, mesh, p0, start, a0, positions) in enumerate(plans):
        segment = f"segment {k + 1}/{segments}"
        s_c, w_at = float(rotation[0] @ p0), float(rotation[1] @ p0)
        chunks = _chunks(positions)
        previous = (None, None)
        for n, a in enumerate(positions):
            if mesh.ok and chunks and n == chunks[0][0]:
                first, last = chunks.pop(0)
                setup = None
                if progress is not None:
                    def setup(_step, _steps, what, done=done):
                        progress(done, total, f"{segment}: discretizing {what}")
                materials, indices, bounds = _segment(frame, frame_vias, project, mesh, s_c,
                                                      positions[first:last], path.half_width, discretize, setup)
                indices = np.r_[np.zeros(first, dtype=int), indices]        # by the segment's position index
            if mesh.ok:
                joins = _SectionNets(nets, rotation, a) if nets is not None else None
                key = _section_key(materials, int(indices[n]), joins)
                if key == previous[0]:
                    section = replace(previous[1])      # the same cross-section: the same answer
                else:
                    section = line_impedance(materials, 2, int(indices[n]), bounds, joins)
                    previous = (key, section)
            else:
                section = Section(0.0, error=mesh.error)
            section.coordinate = start + a - a0
            profile.sections.append(section)
            profile.points.append(rotation.T @ np.array([s_c, w_at, a]))
            profile.across.append(rotation[0].copy())
            done += 1
            if progress is not None and progress(done, total, segment) is False:
                return profile
    return profile


def _chunks(positions):
    """[(first, last)]: runs of positions at most CHUNK_LENGTH long, each discretized on its own."""
    chunks, first = [], 0
    for n in range(1, len(positions) + 1):
        if n == len(positions) or positions[n] - positions[first] > CHUNK_LENGTH:
            chunks.append((first, n))
            first = n
    return chunks


def _inside(bodies, region):
    """The bodies whose bounding box touches the box region (lo, hi)."""
    lo, hi = region
    return [b for b in bodies if np.all(b.bounds[0] <= hi) and np.all(b.bounds[1] >= lo)]


def _section_key(materials, index, nets):
    """
    A digest of all the section solver reads at grid line `index` along a
    (line_profile.line_impedance, port_mode.solve_port_mode): the in-plane
    PEC and eps edges, the PEC edges along a on either side, the nets.
    """
    digest = hashlib.blake2b(digest_size=16)
    for array in (materials.pec[0], materials.pec[1], materials.eps_r[0], materials.eps_r[1]):
        digest.update(np.ascontiguousarray(array[:, :, index]).tobytes())
    along = materials.pec[2]
    for k in (index - 1, index):
        digest.update(np.ascontiguousarray(along[:, :, k]).tobytes() if 0 <= k < along.shape[2] else b"-")
    if nets is not None:
        lines = materials.grid.lines
        digest.update(np.ascontiguousarray(nets.plane(2, None, lines[0], lines[1])).tobytes())
    return digest.digest()


def _fine(project):
    """The project with the sections' grid settings (see the header)."""
    fine = copy.copy(project)
    fine.settings = {name: dict(values) for name, values in project.settings.items()}
    own = project.settings["mesh"]["cells_per_feature"]
    level = max(CELLS_PER_FEATURE, own)
    fine.settings["mesh"]["cells_per_feature"] = level
    fine.settings["mesh"]["min_step"] = project.settings["mesh"]["min_step"] * own / level
    return fine


def _positions(fixpoints, a0, a1):
    """
    The faces square to the segment strictly between a0 and a1 (at least
    SECTION_GAP apart), gaps filled to at most SECTION_STEP.
    """
    inner = []
    for f in np.sort(fixpoints[(fixpoints > a0 + SECTION_GAP) & (fixpoints < a1 - SECTION_GAP)]):
        if not inner or f - inner[-1] >= SECTION_GAP:
            inner.append(f)
    ends = np.concatenate([[a0], inner, [a1]])
    out = []
    for lo, hi in zip(ends[:-1], ends[1:]):
        n = int(np.ceil((hi - lo) / SECTION_STEP - 1e-9))
        out.extend(lo + (hi - lo) * np.arange(1, n) / n)
        out.append(hi)
    return np.array(out[:-1])                       # not a1: the vertex or the path's end


def _segment(frame, vias, project, mesh, s_c, positions, half_width, discretize, progress=None):
    """
    All sections of a segment from one discretization (rotated frame): the
    window s_c +- half_width across, the grid's lines along w, and along a
    each position with a line a step to either side (the slab of each
    section). Returns (materials, the positions' line indices, bounds).
    """
    step = mesh.min_step
    lo_s, hi_s = s_c - half_width, s_c + half_width
    s = mesh.grid.lines[0]
    s = s[(s > lo_s + 0.5 * step) & (s < hi_s - 0.5 * step)]
    a = np.sort(np.concatenate([positions - step, positions, positions + step]))
    a = a[np.concatenate([[True], np.diff(a) > 0.5 * step])]
    lines = (np.concatenate([[lo_s], s, [hi_s]]), mesh.grid.lines[1], a)
    materials = discretize(replace(mesh, grid=RectilinearGrid(lines)), frame, project, vias=vias,
                           progress=progress)
    w = mesh.grid.lines[1]
    bounds = (np.array([lo_s, w[0], a[0]]), np.array([hi_s, w[-1], a[-1]]))
    return materials, np.abs(a[None, :] - positions[:, None]).argmin(axis=1), bounds
