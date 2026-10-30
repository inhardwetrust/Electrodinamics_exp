# sketch_tools.py
#
# Drawing tools of sketch mode. The view hands a tool its mouse events
# (GeometryView.set_tool): move(pos) without a button, click(pos) for a left
# click (a press and release within CLICK_PX), escape(). Middle drags still
# turn the camera meanwhile.
#
# SketchTool, the common part: snapping and the marker where a click would
# put a point (bigger and green when it snaps). The cursor snaps to the
# sketch's points and its origin within SNAP_PX; elsewhere a point lies
# where the ray through the cursor meets the sketch's plane, rounded to 1 um.
# Off the points, a line, circle, spline, generated curve, or the outline
# of a polyline or an offset within CURVE_SNAP_PX takes the point
# (curve_snap(): the nearest place on it, exact; the window's point drag
# uses it too). Off those, alignment hints (align(), Inventor's inference lines): a
# new point within ALIGN_PX of the vertical or horizontal through another
# point (or the sketch origin) takes that x or y exactly, and a dashed guide
# runs from that point to it (GeometryView.set_guides); the window's point
# drag uses them too. The value is copied, no constraint is made (yet).
# A position is an anchor: ("point", name) of an existing point or
# ("xy", (x, y) mm) of a new one.
#
# LineTool (Sketch > Line), as Inventor's Line (TraceTool, Sketch > Polyline, draws the
# same way the center line of a wide polyline):
#   - every click puts a point; from the second one on, each click adds the
#     line from the last point (one undo step each);
#   - the preview: the line from the last point to the marker;
#   - a click on the chain's first point closes it and ends the chain; a
#     click on the last point (or a double click) ends it;
#   - Esc ends the chain; Esc with no chain leaves the tool (on_exit).
#   add_line(start, end) (the window) makes the points and the line and
#   returns (start name, end name).
#
# CircleTool (Sketch > Circle): a center, then the radius (see the class).
#
# PasteTool (Ctrl+V): the copied geometry follows the cursor, a click puts it.
#
# PointTool (Sketch > Point), as Inventor's Point: every click puts an
# explicit point (add_point((x, y) mm), the window); a click on an existing
# point puts nothing; Esc leaves the tool.

import numpy as np

from vispy import scene

from model.curves import edge_function

from view.picking import _project, _ray

SNAP_PX = 8.0
CURVE_SNAP_PX = 6.0             # a new or dragged point lands on a line or circle this close
ALIGN_PX = 6.0                  # alignment hints catch within this many pixels
ROUND_MM = 1e-3                 # new points are rounded to 1 um
PREVIEW_COLOR = (1.0, 0.85, 0.3, 1.0)
SNAP_COLOR = (0.25, 1.0, 0.35, 1.0)
MM = 1e-3


def align(xy, points, tolerance, exclude=(), fixed=(False, False)):
    """
    Alignment hints (Inventor's inference lines): `xy` (mm) moved onto the
    vertical and / or horizontal through another point when it is within
    `tolerance` (mm) of it.
    points    {name: (2,) mm} of the sketch; the sketch origin counts too
    exclude   names not to align with (the point being dragged)
    fixed     (x, y): an axis that must not move (a formula)
    Returns (aligned (2,) mm, [(from (2,) mm, to (2,) mm)] the guides).
    """
    xy = np.array(xy, dtype=float)
    candidates = [np.zeros(2)] + [np.asarray(p, float) for n, p in points.items() if n not in exclude]
    sources = [None, None]
    for axis in (0, 1):
        if fixed[axis]:
            continue
        best = tolerance
        for c in candidates:
            gap = abs(c[axis] - xy[axis])
            if gap <= best:
                best, sources[axis] = gap, c
        if sources[axis] is not None:
            xy[axis] = sources[axis][axis]
    guides = [(source.copy(), xy.copy()) for source in sources
              if source is not None and not np.allclose(source, xy)]
    return xy, guides


def curve_snap(xy, result, tolerance, exclude=()):
    """
    The nearest place on a line, circle or curve of the sketch within
    `tolerance` (mm) of `xy` (mm): ((2,) mm, entity name), or None. For a
    line, the foot of the perpendicular (clamped to the segment); for a
    circle, the point on it along the radius through xy; for a spline, a
    generated curve or the outline of a polyline or an offset (named by its
    entity), the nearest point of its kernel edges (refined on the exact
    curve). `exclude`: entity names that do not attract (what a dragged
    point belongs to).
    """
    xy = np.asarray(xy, dtype=float)
    best = None
    for line in result.lines:
        if line.name in exclude:
            continue
        a, b = result.points[line.start], result.points[line.end]
        ab = b - a
        t = float(np.clip(np.dot(xy - a, ab) / max(float(np.dot(ab, ab)), 1e-18), 0.0, 1.0))
        foot = a + t * ab
        gap = float(np.linalg.norm(xy - foot))
        if gap <= tolerance and (best is None or gap < best[0]):
            best = (gap, foot, line.name)
    for circle in result.circles:
        if circle.name in exclude:
            continue
        center, radius = result.points[circle.center], result.radii[circle.name]
        away = xy - center
        r = float(np.linalg.norm(away))
        if r < 1e-12:
            continue                            # at the center: no direction
        gap = abs(r - radius)
        if gap <= tolerance and (best is None or gap < best[0]):
            best = (gap, center + away * (radius / r), circle.name)
    for owner, edges in _snap_edges(result):
        if owner in exclude:
            continue
        for edge in edges:
            hit = _edge_foot(edge, xy, tolerance)
            if hit is not None and (best is None or hit[0] < best[0]):
                best = (hit[0], hit[1], owner)
    return None if best is None else (best[1], best[2])


def _snap_edges(result):
    """[(entity name, kernel edges)] of the curves drawn besides lines and circles."""
    out = [(c.name, result.shapes[c.name].edges)
           for c in getattr(result, "curves", []) + getattr(result, "splines", []) + getattr(result, "arcs", [])
           if c.name in result.shapes]
    out += [(name, [e for _, edges, _ in loops for e in edges]) for name, loops in getattr(result, "offsets", {}).items()]
    out += [(name, edges) for name, (edges, _) in getattr(result, "outlines", {}).items()]
    return out


def _edge_foot(edge, xy, tolerance, samples=128):
    """(gap, (2,) mm) of the point of a kernel edge nearest to xy if within tolerance (mm), else None."""
    at, t0, t1 = edge_function(edge)
    s = np.linspace(0.0, 1.0, samples + 1)
    path = at(t0 + s * (t1 - t0))
    if np.any(xy < path.min(axis=0) - tolerance) or np.any(xy > path.max(axis=0) + tolerance):
        return None
    k = int(np.argmin(np.linalg.norm(path - xy, axis=1)))
    if np.linalg.norm(path[k] - xy) > 2 * tolerance + np.linalg.norm(path[1] - path[0]):
        return None
    u = s[k]
    for _ in range(30):                         # Gauss-Newton on |g(u) - xy|^2
        h = 1e-7
        lo, hi = max(u - h, 0.0), min(u + h, 1.0)
        g = at(np.array([t0 + u * (t1 - t0)]))[0]
        d = (at(np.array([t0 + hi * (t1 - t0)]))[0] - at(np.array([t0 + lo * (t1 - t0)]))[0]) / (hi - lo)
        step = float((xy - g) @ d) / max(float(d @ d), 1e-30)
        u = float(np.clip(u + step, 0.0, 1.0))
        if abs(step) < 1e-13:
            break
    foot = at(np.array([t0 + u * (t1 - t0)]))[0]
    gap = float(np.linalg.norm(foot - xy))
    return (gap, foot) if gap <= tolerance else None


def radius_snap(center, radius, points, tolerance, exclude=()):
    """
    A circle around `center` (2,) mm with `radius` (mm) taken through a
    point of the sketch whose distance from it is within `tolerance` (mm) of
    the radius, wherever on the circle it lies (the nearest such point): (the
    new radius, (2,) mm of the point), or None. points: {name: (2,) mm}; the
    sketch origin counts too; `exclude`: names that do not count (the
    circle's own center).
    """
    center = np.asarray(center, dtype=float)
    candidates = [np.zeros(2)] + [np.asarray(p, float) for n, p in points.items() if n not in exclude]
    best = None
    for p in candidates:
        r = float(np.linalg.norm(p - center))
        gap = abs(r - radius)
        if r > ROUND_MM and gap <= tolerance and (best is None or gap < best[0]):
            best = (gap, r, p)
    return None if best is None else (best[1], best[2])


def fmt_mm(value):
    """A coordinate (mm) as the formula text of a new point: rounded to 1 um, no '-0'."""
    text = f"{round(value / ROUND_MM) * ROUND_MM + 0.0:.3f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


class SketchTool:
    name = ""

    def __init__(self, view, sketch, sketch_result, on_exit):
        """
        sketch_result()  the current SketchResult of the sketch (it changes after every edit)
        on_exit()        the tool was left (Esc)
        """
        self.view = view
        self.sketch = sketch
        self.sketch_result = sketch_result
        self.on_exit = on_exit
        self.preview = None         # (start (2,) mm or None, end (2,) mm, snapped anchor or None)
        self.guides = []            # alignment hints of the last snap: [(from, to) (2,) mm]
        self.curve = None           # the line or circle the last snap landed on
        parent = view.view.scene
        self.line = scene.visuals.Line(color=PREVIEW_COLOR, width=2.0, parent=parent)
        self.marker = scene.visuals.Markers(parent=parent)
        for visual in (self.line, self.marker):
            visual.set_gl_state("translucent", depth_test=False)
            visual.order = 30
            visual.visible = False

    def remove(self):
        self.line.parent = None
        self.marker.parent = None
        self.view.set_guides(None, [])

    def _xy(self, anchor):
        """(2,) mm of an anchor, or None if its point is gone (undo)."""
        if anchor is None:
            return None
        if anchor[0] == "xy":
            return np.asarray(anchor[1], dtype=float)
        return self.sketch_result().points.get(anchor[1])

    def snap(self, pos):
        """
        (anchor, (2,) mm, snapped) for canvas pixel `pos`, or None off the
        plane. Snaps in this order: a point (SNAP_PX), a line or circle
        (CURVE_SNAP_PX, self.curve names it), else the alignment hints
        (self.guides).
        """
        self.guides = []
        self.curve = None
        result = self.sketch_result()
        frame = result.frame
        transform = self.view._transform()
        names = list(result.shown_points())
        pending = self._pending()
        targets = [("point", n) for n in names] + [anchor for anchor, _ in pending] + [("xy", (0.0, 0.0))]
        xy = np.array([result.points[n] for n in names] + [p for _, p in pending] + [[0.0, 0.0]])
        world = frame.to_world(xy * MM) / MM
        pixels, front = _project(transform, world)
        distance = np.linalg.norm(pixels - np.asarray(pos[:2], float), axis=1)
        distance[~front] = np.inf
        k = int(np.argmin(distance))
        if distance[k] <= SNAP_PX:
            return targets[k], xy[k], True
        origin, direction = _ray(transform, pos)
        normal = frame.normal
        facing = float(np.dot(direction, normal))
        if abs(facing) < 1e-9:
            return None                         # the plane is seen edge-on
        t = float(np.dot(frame.origin / MM - origin, normal)) / facing
        hit = origin + t * direction
        local = frame.to_sketch(hit[None] * MM)[0] / MM
        on_curve = curve_snap(local, result, CURVE_SNAP_PX * self.view._mm_per_px())
        if on_curve is not None:               # onto a line or circle (exact, not rounded)
            self.curve = on_curve[1]
            return ("xy", (float(on_curve[0][0]), float(on_curve[0][1]))), on_curve[0], True
        local = np.round(local / ROUND_MM) * ROUND_MM
        sources = result.shown_points()
        sources.update({f"pending {i}": p for i, (_, p) in enumerate(pending)})
        local, self.guides = align(local, sources, ALIGN_PX * self.view._mm_per_px())
        return ("xy", (float(local[0]), float(local[1]))), local, False

    def _pending(self):
        """[(anchor, (2,) mm)]: points the tool holds that are not in the sketch yet (snap and align to them)."""
        return []

    def _show(self, pos, start_xy=None):
        """Marker (and preview line from start_xy) for the cursor at `pos`; returns the snap or None."""
        hit = self.snap(pos)
        if hit is None:
            self.marker.visible = self.line.visible = False
            self.preview = None
            self.view.set_guides(None, [])
            return None
        anchor, xy, snapped = hit
        frame = self.sketch_result().frame
        end = frame.to_world(xy[None] * MM)[0] / MM
        self.marker.set_data(end[None].astype(np.float32), size=11 if snapped else 7, edge_width=1.0,
                             face_color=SNAP_COLOR if snapped else PREVIEW_COLOR, edge_color=(0, 0, 0, 1))
        self.marker.visible = True
        if start_xy is not None:
            start = frame.to_world(start_xy[None] * MM)[0] / MM
            self.line.set_data(pos=np.array([start, end], np.float32))
        self.line.visible = start_xy is not None
        self.preview = (start_xy, xy, anchor if snapped else None)
        self.view.set_guides(frame, self.guides)
        return hit

    def move(self, pos):
        self._show(pos)

    def click(self, pos):
        pass

    def escape(self):
        self.on_exit()
        return True


class PointTool(SketchTool):
    name = "Point"

    def __init__(self, view, sketch, sketch_result, add_point, on_exit):
        """add_point((x, y) mm) makes an explicit point (the window)."""
        super().__init__(view, sketch, sketch_result, on_exit)
        self.add_point = add_point

    def click(self, pos):
        hit = self.snap(pos)
        if hit is None or hit[0][0] == "point":
            return                              # off the plane, or a point is there already
        self.add_point(hit[0][1])
        self.move(pos)


class LineTool(SketchTool):
    name = "Line"

    def __init__(self, view, sketch, sketch_result, add_line, on_exit):
        """add_line(a, b) makes the line between two anchors; returns (start name, end name)."""
        super().__init__(view, sketch, sketch_result, on_exit)
        self.add_line = add_line
        self.first = None           # the chain's first point: anchor
        self.last = None            # its last point: anchor

    def _same(self, a, b):
        if a is None or b is None:
            return False
        if a[0] == "point" and b[0] == "point":
            return a[1] == b[1]
        xa, xb = self._xy(a), self._xy(b)
        return xa is not None and xb is not None and np.allclose(xa, xb, atol=ROUND_MM / 2)

    def _pending(self):
        if self.last is not None and self.last[0] == "xy":
            return [(self.last, np.asarray(self.last[1], float))]     # the chain's first click
        return []

    def move(self, pos):
        if self._xy(self.last) is None:
            self.first = self.last = None       # its point was undone
        self._show(pos, self._xy(self.last))

    def click(self, pos):
        hit = self.snap(pos)
        if hit is None:
            return
        anchor = hit[0]
        if self._xy(self.last) is None:
            self.first = self.last = anchor     # the chain's first point (nothing made yet)
            self.move(pos)
            return
        if self._same(anchor, self.last):       # a click on the last point ends the chain
            self.end_chain()
            return
        closing = self._same(anchor, self.first)
        if anchor[0] == "point" and self.last[0] == "point" and any(
                {line.start, line.end} == {anchor[1], self.last[1]} for line in self.sketch_result().lines):
            self.end_chain()                    # that line exists already (back to the first point of one)
            return
        start, end = self.add_line(self.last, anchor)
        if self.first[0] == "xy":
            self.first = ("point", start)       # the first point exists now
        if closing:
            self.end_chain()
        else:
            self.last = ("point", end)
            self.move(pos)

    def end_chain(self):
        self.first = self.last = None
        self.line.visible = False
        if self.preview is not None:
            self.preview = (None, self.preview[1], self.preview[2])
        self.view.canvas.update()

    def escape(self):
        """Esc: ends the chain, or leaves the tool when there is none."""
        if self.last is not None:
            self.end_chain()
        else:
            self.on_exit()
        return True


class CircleTool(SketchTool):
    """
    Sketch > Circle, as Inventor's Center Point Circle: a click puts the
    center (snapping and alignment as for points), the preview circle follows
    the cursor, a second click sets the radius (taken through a point that
    lies within SNAP_PX of the circle: radius_snap()); add_circle(center anchor,
    radius mm) (the window) makes it. Esc drops a center not used yet, then
    leaves the tool.
    """

    name = "Circle"

    def __init__(self, view, sketch, sketch_result, add_circle, on_exit):
        super().__init__(view, sketch, sketch_result, on_exit)
        self.add_circle = add_circle
        self.center = None          # anchor of the center, once clicked
        self.radius = None          # of the preview (mm)
        self.through = None         # (2,) mm: the point the preview radius snapped to

    def _pending(self):
        if self.center is not None and self.center[0] == "xy":
            return [(self.center, np.asarray(self.center[1], float))]
        return []

    def move(self, pos):
        center = self._xy(self.center)
        if self.center is not None and center is None:
            self.center = None                  # its point was undone
        if center is None:
            self._show(pos)
            return
        hit = self._show(pos)
        self.line.visible = False
        if hit is None:
            return
        self.radius = float(np.linalg.norm(hit[1] - center))
        self.through = None
        through = radius_snap(center, self.radius, self._points_but_center(), SNAP_PX * self.view._mm_per_px())
        if through is not None:                 # the circle through a point nearby
            self.radius, self.through = through
            frame = self.sketch_result().frame
            spot = frame.to_world(self.through[None] * MM)[0] / MM
            self.marker.set_data(spot[None].astype(np.float32), size=11, edge_width=1.0, face_color=SNAP_COLOR,
                                 edge_color=(0, 0, 0, 1))
        if self.radius <= 0:
            return
        t = np.linspace(0.0, 2.0 * np.pi, 97)
        ring = center + self.radius * np.stack([np.cos(t), np.sin(t)], axis=1)
        frame = self.sketch_result().frame
        self.line.set_data(pos=(frame.to_world(ring * MM) / MM).astype(np.float32))
        self.line.visible = True
        self.preview = (center, hit[1], hit[0] if hit[2] else None)
        self.view.canvas.update()

    def click(self, pos):
        hit = self.snap(pos)
        if hit is None:
            return
        center = self._xy(self.center)
        if center is None:
            self.center = hit[0]
            self.move(pos)
            return
        radius = float(np.linalg.norm(hit[1] - center))
        through = radius_snap(center, radius, self._points_but_center(), SNAP_PX * self.view._mm_per_px())
        if through is not None:
            radius = through[0]
        if radius < ROUND_MM:
            return                              # a click on the center: no circle
        self.add_circle(self.center, radius)
        self.center = None
        self.radius = None
        self.line.visible = False
        self.move(pos)

    def _points_but_center(self):
        points = self.sketch_result().shown_points()
        if self.center is not None and self.center[0] == "point":
            points.pop(self.center[1], None)
        return points

    def escape(self):
        if self.center is not None:
            self.center = None
            self.line.visible = False
            self.view.canvas.update()
        else:
            self.on_exit()
        return True


class PasteTool(SketchTool):
    """
    Ctrl+V in sketch mode: the copied geometry (window's clipboard: points
    (2,) mm, lines and circles between them) hangs on the cursor by its
    anchor (the middle of its extent), which snaps like a new point; a click
    puts it there (paste(offset mm), the window) and leaves the tool; Esc
    leaves without pasting.
    """

    name = "Paste"

    def __init__(self, view, sketch, sketch_result, clip, paste, on_exit):
        super().__init__(view, sketch, sketch_result, on_exit)
        self.clip = clip
        self.paste = paste
        self.offset = None          # (2,) mm of the last preview
        self.shape = scene.visuals.Line(connect="segments", color=PREVIEW_COLOR, width=2.0,
                                        parent=view.view.scene)
        self.shape.set_gl_state("translucent", depth_test=False)
        self.shape.order = 30
        self.shape.visible = False

    def remove(self):
        super().remove()
        self.shape.parent = None

    def preview_xy(self, offset):
        """(K, 2, 2) the copied lines and circles moved by `offset`, in the sketch (mm)."""
        points = {n: p["xy"] + offset for n, p in self.clip["points"].items()}
        segments = [[points[a], points[b]] for a, b, _ in self.clip["lines"]]
        t = np.linspace(0.0, 2.0 * np.pi, 65)
        for center, radius, _, _ in self.clip["circles"]:
            ring = points[center] + radius * np.stack([np.cos(t), np.sin(t)], axis=1)
            segments += [[p, q] for p, q in zip(ring[:-1], ring[1:])]
        for names, *_ in self.clip.get("polylines", []):           # their center lines
            segments += [[points[p], points[q]] for p, q in zip(names[:-1], names[1:])]
        for names, _ in self.clip.get("splines", []):
            from model.curves import spline_function
            at, length = spline_function(np.array([points[p] for p in names]))
            curve = at(np.linspace(0.0, length, 32 * len(names) + 1))
            segments += [[p, q] for p, q in zip(curve[:-1], curve[1:])]
        return np.array(segments).reshape(-1, 2, 2)

    def move(self, pos):
        hit = self._show(pos)
        if hit is None:
            self.shape.visible = False
            return
        self.offset = hit[1] - self.clip["anchor"]
        segments = self.preview_xy(self.offset)
        if len(segments):
            frame = self.sketch_result().frame
            world = frame.to_world(segments.reshape(-1, 2) * MM) / MM
            self.shape.set_data(pos=world.astype(np.float32))
        self.shape.visible = len(segments) > 0
        self.view.canvas.update()

    def click(self, pos):
        hit = self.snap(pos)
        if hit is None:
            return
        self.paste(hit[1] - self.clip["anchor"])
        self.on_exit()


class TraceTool(LineTool):
    """
    Sketch > Polyline: drawn like Line, a click per point, but the chain is
    the center line of one wide polyline (add_line(a, b), the window: the
    first click pair makes the polyline, each next one extends it).
    """

    name = "Polyline"


class SplineTool(LineTool):
    """
    Sketch > Spline: a click per point, as Line; the chain is one
    interpolating spline through them (add_line(a, b), the window: the first
    pair makes it, each next one extends it; a click on its first point
    closes it, smooth all around). The preview is the spline with the cursor
    as its next point.
    """

    name = "Spline"

    def _chain(self):
        """(2,) mm points of the spline being drawn, up to its last point (or just the pending first click)."""
        if self.last is None:
            return []
        if self.last[0] == "xy":
            return [np.asarray(self.last[1], float)]
        result = self.sketch_result()
        for w in reversed(getattr(result, "splines", [])):
            if w.points[-1] == self.last[1] and all(p in result.points for p in w.points):
                return [result.points[p] for p in w.points]
        return [result.points[self.last[1]]] if self.last[1] in result.points else []

    def _show(self, pos, start_xy=None):
        hit = super()._show(pos, start_xy)
        chain = self._chain()
        if hit is None or start_xy is None or len(chain) < 2:
            return hit
        from model.curves import CurveError, spline_function
        try:
            at, length = spline_function(np.array(chain + [hit[1]]))
        except (CurveError, np.linalg.LinAlgError):
            return hit
        frame = self.sketch_result().frame
        xy = at(np.linspace(0.0, length, 32 * len(chain) + 1))
        self.line.set_data(pos=(frame.to_world(xy * MM) / MM).astype(np.float32))
        return hit


class ArcTool(SketchTool):
    """
    Sketch > Arc. method "center" (as Inventor's Center Point Arc): a click
    puts the center, a second one the start (the radius and where it begins),
    the preview arc follows the cursor counter-clockwise, a third click its
    end (the angle). method "three" (Three Point Arc): a click puts the
    start, a second one the end, the preview arc runs through the cursor, a
    third click a point on it. add_arc(method, anchors) (the window) makes
    it: "center": (center, start, end) anchors; "three": (start, end,
    through). Esc drops the clicks so far, then leaves the tool.
    """

    name = "Arc"

    def __init__(self, view, sketch, sketch_result, add_arc, on_exit, method="center"):
        super().__init__(view, sketch, sketch_result, on_exit)
        self.add_arc = add_arc
        self.method = method
        self.anchors = []           # the clicks so far (anchors)

    def _pending(self):
        return [(a, np.asarray(a[1], float)) for a in self.anchors if a[0] == "xy"]

    def _arc_points(self, cursor):
        """(N, 2) mm: the preview arc with the cursor as the next click, or None."""
        xy = [self._xy(a) for a in self.anchors]
        if any(p is None for p in xy):
            self.anchors = []                   # a point of it was undone
            return None
        if self.method == "center" and len(xy) == 2:
            center, start = xy
            radius = float(np.linalg.norm(start - center))
            if radius < ROUND_MM:
                return None
            a0 = np.arctan2(*(start - center)[::-1])
            sweep = (np.arctan2(*(cursor - center)[::-1]) - a0) % (2 * np.pi)
            t = a0 + np.linspace(0.0, sweep, 65)
            return center + radius * np.stack([np.cos(t), np.sin(t)], axis=1)
        if self.method == "three" and len(xy) == 2:
            from model.curves import edge_function

            start, end = xy
            cross = (cursor - start)[0] * (end - start)[1] - (cursor - start)[1] * (end - start)[0]
            if abs(cross) < 1e-9:
                return np.array([start, end])
            at, t0, t1 = edge_function(("arc", start, cursor, end))
            return at(np.linspace(t0, t1, 65))
        if len(xy) == 1:
            return np.array([xy[0], cursor])    # (a radius, or a chord)
        return None

    def move(self, pos):
        hit = self._show(pos)
        self.line.visible = False
        if hit is None or not self.anchors:
            self.view.canvas.update()
            return
        path = self._arc_points(hit[1])
        if path is not None and len(path) > 1:
            frame = self.sketch_result().frame
            self.line.set_data(pos=(frame.to_world(path * MM) / MM).astype(np.float32))
            self.line.visible = True
        self.view.canvas.update()

    def click(self, pos):
        hit = self.snap(pos)
        if hit is None:
            return
        xy = [self._xy(a) for a in self.anchors]
        if any(np.linalg.norm(hit[1] - p) < ROUND_MM for p in xy if p is not None):
            return                              # the same place twice: nothing
        self.anchors.append(hit[0])
        if len(self.anchors) == 3:
            anchors, self.anchors = self.anchors, []
            self.add_arc(self.method, anchors)
            self.line.visible = False
        self.move(pos)

    def escape(self):
        if self.anchors:
            self.anchors = []
            self.line.visible = False
            self.view.canvas.update()
        else:
            self.on_exit()
        return True
