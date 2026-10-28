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
# Off the points, alignment hints (align(), Inventor's inference lines): a
# new point within ALIGN_PX of the vertical or horizontal through another
# point (or the sketch origin) takes that x or y exactly, and a dashed guide
# runs from that point to it (GeometryView.set_guides); the window's point
# drag uses them too. The value is copied, no constraint is made (yet).
# A position is an anchor: ("point", name) of an existing point or
# ("xy", (x, y) mm) of a new one.
#
# PolylineTool (Sketch > Line), as Inventor's Line:
#   - every click puts a point; from the second one on, each click adds the
#     line from the last point (one undo step each);
#   - the preview: the line from the last point to the marker;
#   - a click on the chain's first point closes it and ends the chain; a
#     click on the last point (or a double click) ends it;
#   - Esc ends the chain; Esc with no chain leaves the tool (on_exit).
#   add_line(start, end) (the window) makes the points and the line and
#   returns (start name, end name).
#
# PointTool (Sketch > Point), as Inventor's Point: every click puts an
# explicit point (add_point((x, y) mm), the window); a click on an existing
# point puts nothing; Esc leaves the tool.

import numpy as np

from vispy import scene

from view.picking import _project, _ray

SNAP_PX = 8.0
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
        """(anchor, (2,) mm, snapped) for canvas pixel `pos`, or None off the plane; sets self.guides."""
        self.guides = []
        result = self.sketch_result()
        frame = result.frame
        transform = self.view._transform()
        names = list(result.points)
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
        local = np.round(local / ROUND_MM) * ROUND_MM
        sources = dict(result.points)
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


class PolylineTool(SketchTool):
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
