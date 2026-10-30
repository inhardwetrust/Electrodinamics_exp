# geometry_view.py
#
# VisPy 3D view of a Geometry: shaded bodies, CAD edges, axes, bounding box.
# A trimmed copy of customcst/view/geometry_view.py (the camera, bodies,
# edges, headlight, axes gizmo, standard views, picking); the simulator's
# overlays (mesh, ports, section) are left out.
#
# The scene is drawn in millimeters (bodies are stored in meters): the usual
# CAD unit, and it keeps the camera numbers human.
#
# Sheets have no inside, so they are drawn two-sided: a reversed copy of the
# triangles with its own vertices (shared vertices would average the two
# normals to zero).
#
# Navigation (TrackballCamera, CAD-style like Inventor's orbit):
#   - middle drag rotates as a virtual trackball about the camera center;
#   - Ctrl + middle drag pans (Space + left drag too, for a touchpad);
#   - the wheel or a right drag zooms;
#   - a left drag moves nothing: the left button selects (and will draw).
#
# Datums (set_datums): the part's work planes, axes and points (the origin
# for now), drawn as in Inventor: a plane is a translucent square with its
# outline, an axis a line, a point a dot. Their size follows the model:
# DATUM_HALF_MIN for an empty part, else a bit more than the bodies' reach
# from the origin.
#
# Hover and selection: moving the mouse (no button) over a shown datum, or
# a body's face, highlights it green (on_hover(key)); a click selects it
# (a face: ("face", body, CAD face index), drawn blue over the body)
# (on_select(key)), a click on nothing clears the selection. Keys are
# ("datum", name), ("body", name) (from the browser) or ("face", body, index). set_hover / select do the same from
# the browser; a hovered datum is drawn even when hidden (as in Inventor,
# where hovering a hidden plane in the browser shows it).
#
# Picking a plane (set_pick_filter): while a command waits for a plane
# (Start 2D Sketch), only datums of the given kinds hover and click, the
# bodies are not selectable, and the `forced` datums are drawn and pickable
# even when hidden (Inventor shows the origin planes for this).
#
# Sketch mode (set_sketch, look_at): the sketch's horizontal (u, red) and
# vertical (v, green) through its origin, endless (no grid),
# and the camera looking at the plane: u to the right, v up, the normal
# toward the viewer.
#
# Sketches (set_sketches): every sketch shows its origin as a point
# (Inventor's projected center point; all an empty sketch shows) and its
# lines. Outside sketch mode they are keyed ("feature", name): hovering any
# of them turns the whole sketch green, a click selects the sketch (the
# origin's datums win where they coincide). In sketch mode (set_sketch with
# the sketch's name) the edited sketch's lines and points are keyed
# ("entity", sketch, "L1" / "P1") and hover / select one by one; its points
# are drawn as dots; other sketches are not picked meanwhile.
#
# Selection: `selection` is a list of keys (`selected`: the last one, or
# None). In sketch mode (no tool on), as in Inventor:
#   - a click selects a line, or a point together with every point at the
#     same place (the ends of different lines, explicit points); Shift +
#     click adds or removes (a coincident group as one);
#   - a left drag from empty space draws a box: left to right (solid) takes
#     what lies wholly inside, right to left (dashed) what lies inside or
#     crosses it; Shift adds to the selection.
#
# Dragging (on_drag): a left drag that starts on a point or line of the
# edited sketch moves the whole selection if it is selected, else selects it
# (with its coincident points) and moves that: on_drag(keys, moved, phase)
# gets the move in the sketch's plane from the press (mm; exact in
# perspective: both are ray hits on the plane), phase "start", "move"...,
# then "end" on release or "cancel" on Esc. A press and release within
# CLICK_PX stays a click.
#
# Profiles (set_profile_mode): while a feature command picks profiles
# (Extrude), the closed regions of the sketches are shown as fills: hidden
# until hovered (green), blue when selected; a click toggles one
# (on_profiles(keys)); nothing else hovers or selects meanwhile. A region is
# found under the cursor by the ray's hit on its sketch's plane (the nearest
# such hit wins).
#
# Preview (set_preview): what a feature command would add (see-through
# amber) or cut away (red), until it is applied or cancelled.
#
# Alignment hints (set_guides): dashed lines in the sketch's plane, from a
# point to the one being placed or dragged that took its x or y.
#
# Tools (set_tool, view/sketch_tools.py): while a drawing tool is on, mouse
# moves without a button, left clicks and Esc go to it, not to hovering and
# selecting; middle drags still turn the camera. Delete (on_delete) is for
# the window: it deletes the selection.
#
# Measure mode (set_measure): a left click without a drag (the cursor moved
# less than CLICK_PX) picks a point of the model (view/picking.py: a CAD
# vertex, a point on an edge, or on a face). Two points, A and B:
#   - a click sets or moves A; Shift + click sets or moves B (Shift + drag
#     still pans, a drag rotates); a click on nothing changes nothing;
#   - a double click on nothing, or Esc, removes both;
#   - the markers (A yellow, B cyan) and the line between them are drawn
#     through the bodies; on_pick([A, B]) is called after every change.
#
# Standard views (look_from, view/view_buttons.py): the camera reset to
# the model as Reset view does, looking along an axis from one side. Top
# is from +up; the sides follow the CAD habit for the up axis (z up: Front
# from -Y, Right from +X; y up: Front from +Z, Right from +X). VisPy's
# turntable turns its azimuth with the up axis, so each view tries the
# axis-aligned orientations and keeps the one whose camera looks from that
# side with the right axis up the screen. Orthographic (set_orthographic):
# fov 0, no perspective.

import numpy as np

from vispy import scene
from vispy.util import keys, transforms

from model.part import Datum
from model.sketch import CIRCLE_SEGMENTS
from view.picking import Picker, _project, _ray, pick_datum

SCENE_UNIT = 1e-3      # one scene unit = 1 mm

DEFAULT_COLORS = {
    "solid": (0.62, 0.66, 0.74),
    "sheet": (0.85, 0.55, 0.25),
}
EDGE_COLOR = (0.08, 0.08, 0.1, 1.0)
SELECTED_EDGE_COLOR = (1.0, 0.85, 0.2, 1.0)
BOX_COLOR = (0.45, 0.45, 0.5, 1.0)
BACKGROUND = (0.13, 0.14, 0.17, 1.0)
XRAY_ALPHA = 0.3
SKETCH_MODE_ALPHA = 0.8     # the bodies while a sketch is edited: a little see-through
PICK_COLORS = ((1.0, 0.85, 0.1, 1.0), (0.2, 0.9, 1.0, 1.0))   # points A, B; black rim
PICK_LINE_COLOR = (1.0, 1.0, 1.0, 0.9)
DATUM_COLOR = (0.95, 0.7, 0.25, 1.0)              # work features: amber
DATUM_FILL_ALPHA = 0.12
HOVER_COLOR = (0.25, 1.0, 0.35, 1.0)              # pre-highlight: green
SELECTED_COLOR = (0.35, 0.65, 1.0, 1.0)           # selection: blue
DATUM_HALF_MIN = 10.0       # mm: half the side of an origin plane of an empty part
AXIS_OVERHANG = 1.2         # an axis reaches this times past the planes
SKETCH_POINT_COLOR = (0.85, 0.88, 0.95, 1.0)      # a sketch's origin point
SKETCH_LINE_COLOR = (0.85, 0.88, 0.95, 1.0)       # its lines
SKETCH_DOT_COLOR = (0.95, 0.95, 1.0, 1.0)         # its points, in sketch mode
GUIDE_COLOR = (0.55, 0.85, 1.0, 0.9)              # alignment hints (dashed)
GUIDE_DASH_PX, GUIDE_GAP_PX = 6.0, 4.0
PROFILE_HOVER_COLOR = (0.25, 1.0, 0.35, 0.35)      # a profile under the cursor
PROFILE_SELECTED_COLOR = (0.35, 0.65, 1.0, 0.45)  # a selected profile
PREVIEW_COLOR = (0.95, 0.7, 0.25, 0.45)           # what a feature command would add (see-through)
PREVIEW_CUT_COLOR = (1.0, 0.3, 0.25, 0.45)        # what it would cut away
FACE_HOVER_COLOR = (0.25, 1.0, 0.35, 0.55)        # a body face under the cursor
FACE_SELECTED_COLOR = (0.35, 0.65, 1.0, 0.6)      # a selected body face
SNAP_MARKER_COLOR = (0.25, 1.0, 0.35, 1.0)        # where a drag snapped (green, as the tools' marker)
BOX_COLOR_WINDOW = (0.45, 0.75, 1.0, 1.0)         # the selection box: solid, wholly inside
BOX_COLOR_CROSSING = (0.45, 1.0, 0.6, 1.0)        # dashed: inside or crossing
COINCIDENT_MM = 1e-6        # points this close are at the same place
CONSTRUCTION_DASH_PX, CONSTRUCTION_GAP_PX = 8.0, 5.0     # construction lines: dashed
CROSS_GAP_PX = 3.0          # an explicit point's cross: its arms run from this far from the point
CROSS_ARM_PX = 9.0          # to this far (pixels)
SKETCH_AXIS_COLORS = ((1.0, 0.45, 0.4, 0.9), (0.45, 0.9, 0.45, 0.9))    # the sketch's x, y
SKETCH_AXIS_REACH = 1000.0  # the sketch's axes reach this many model sizes each way (endless)
CLICK_PX = 4                # a press and release closer than this is a click, not a drag
# The axes gizmo in the lower left corner: X Y Z turning with the view.
GIZMO_LENGTH = 28           # px, one axis
GIZMO_MARGIN = 46           # px from the corner to its origin
GIZMO_COLORS = ((1.0, 0.35, 0.35, 1.0), (0.4, 0.9, 0.4, 1.0), (0.45, 0.65, 1.0, 1.0))   # as XYZAxis


def _skip_redundant_qt_swap(canvas):
    """
    Qt 6 + this AMD driver: VisPy's explicit swapBuffers() after each draw
    blocks for ~2 frames inside a QOpenGLWidget, which composites by itself.
    (Same workaround as customcst and pseudo_cst_3d.)
    """
    try:
        from PySide6.QtOpenGLWidgets import QOpenGLWidget
    except ImportError:
        return

    if isinstance(canvas._backend, QOpenGLWidget):
        canvas._backend._vispy_swap_buffers = lambda: None


def box_segments(lo, hi):
    corners = np.array([[(hi if (n >> a) & 1 else lo)[a] for a in range(3)] for n in range(8)])
    pairs = [(0, 1), (1, 3), (3, 2), (2, 0), (4, 5), (5, 7), (7, 6), (6, 4),
             (0, 4), (1, 5), (2, 6), (3, 7)]
    return np.array([corners[i] for pair in pairs for i in pair], dtype=np.float32)


def _dash_polyline(points, dash, gap, most=20000):
    """
    (K, 2, 3) dashes along a polyline (N, 3): `dash` long every dash + gap,
    measured along it (a dash may turn a corner: then it is two pieces).
    At most `most` dashes: a very long line (zoomed far in) gets longer ones.
    """
    lengths = np.linalg.norm(np.diff(points, axis=0), axis=1)
    total = float(lengths.sum())
    period = dash + gap
    if total <= 0 or period <= 0:
        return np.zeros((0, 2, 3))
    if total / period > most:
        dash, period = dash * total / (period * most), total / most
    out = []
    s0 = 0.0
    for a, b, length in zip(points[:-1], points[1:], lengths):
        if length <= 0:
            continue
        d = (b - a) / length
        k = int(np.floor(s0 / period))
        while k * period < s0 + length:
            lo, hi = max(k * period, s0), min(k * period + dash, s0 + length)
            if hi > lo:
                out.append([a + (lo - s0) * d, a + (hi - s0) * d])
            k += 1
        s0 += length
    return np.array(out).reshape(-1, 2, 3)


def _segment_meets_box(p, q, lo, hi):
    """Does the 2D segment p-q touch the box lo..hi (Liang-Barsky clipping)?"""
    t0, t1 = 0.0, 1.0
    d = q - p
    for axis in (0, 1):
        for edge, sign in ((lo[axis], -1.0), (hi[axis], 1.0)):
            denominator = sign * d[axis]
            numerator = sign * (edge - p[axis])
            if abs(denominator) < 1e-12:
                if numerator < 0:
                    return False                # parallel and outside
                continue
            t = numerator / denominator
            if denominator > 0:
                t1 = min(t1, t)
            else:
                t0 = max(t0, t)
    return t0 <= t1


class TrackballCamera(scene.TurntableCamera):
    """
    CAD-style free rotation (as the middle-button orbit in SolidWorks or
    Inventor), on top of VisPy's TurntableCamera so the "up" axis mapping,
    zoom and projection stay the same.

    Orientation: a free 4x4 rotation matrix (`_rotation`), not azimuth /
    elevation. Middle drag = virtual trackball (Shoemake): the press and the
    current cursor are lifted onto a sphere over the view center, the model
    turns about the axis perpendicular to both. Dragging through the middle
    tilts about a screen axis; dragging around the rim rolls about the view
    direction. The pivot is the camera center (moved by panning).

    Pan: Ctrl + middle drag, or Space + left drag (`space_down` is set by
    the view's key handlers); the motion follows the screen axes in any
    orientation. A left drag otherwise does nothing (VisPy's own left-drag
    rotation and Shift + left pan are swallowed); the right drag and the
    wheel zoom as in VisPy.

    Matrices are VisPy's row-vector convention: camera coords
    v_cam @ A @ T(pos) @ R @ S @ T(center) = world (see
    Base3DRotationCamera._update_camera_pos); a rotation about a screen
    axis is Q in "A-space", so R_new = Q @ R.
    """

    space_down = False
    on_change = None                # called after every view change
    on_turn = None                  # also called after every view change (the view's axes gizmo)
    TRACKBALL_RADIUS = 0.8          # fraction of the half view size

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._rotation = None
        self._last = None
        self._pan_start = None

    def view_changed(self):
        super().view_changed()
        if self.on_turn is not None:
            self.on_turn()
        if self.on_change is not None:
            self.on_change()

    def state(self):
        """{"center": [x, y, z] (scene units, mm), "distance": scale factor, "rotation": 3x3 rows}."""
        rotation = self._rotation if self._rotation is not None else super()._get_rotation_tr()
        return {"center": [float(v) for v in self.center[:3]], "distance": float(self.scale_factor),
                "rotation": [float(v) for v in np.asarray(rotation)[:3, :3].ravel()]}

    def set_view_state(self, center, distance, rotation):
        """The inverse of state(). Not `set_state`: that is VisPy's (BaseCamera.reset calls it)."""
        matrix = np.eye(4)
        matrix[:3, :3] = np.asarray(rotation, dtype=float).reshape(3, 3)
        self._rotation = matrix
        self.center = tuple(float(v) for v in center)
        self.scale_factor = float(distance)
        self.view_changed()

    def viewbox_key_event(self, event):
        """
        No camera keys: VisPy's Backspace reset knows nothing of the free
        rotation (Reset view and the view buttons do that), and Backspace is
        left to the window.
        """

    def set_orientation(self, elevation, azimuth):
        """Reset to a turntable-style view (up axis vertical)."""
        self._rotation = None
        self.elevation, self.azimuth, self.roll = elevation, azimuth, 0.0
        self._rotation = super()._get_rotation_tr()
        self.view_changed()

    def _get_rotation_tr(self):
        if self._rotation is None:
            return super()._get_rotation_tr()
        return self._rotation

    # -------------------------------------------------

    def viewbox_mouse_event(self, event):
        if event.handled or not self.interactive:
            return

        if event.type == "mouse_press":
            self._last = None
            self._pan_start = None

        if event.type == "mouse_move" and event.press_event is not None \
                and (1 in event.buttons or 3 in event.buttons) and 2 not in event.buttons:
            modifiers = event.mouse_event.modifiers
            if 3 in event.buttons:              # the middle button (the wheel)
                if keys.CONTROL in modifiers:
                    self._pan(event)
                elif not modifiers:
                    self._trackball(event)
            elif self.space_down:
                self._pan(event)
            # Moves are left unhandled, like in the stock cameras. Note:
            # VisPy drops moves that come < 10 ms after the previous one;
            # the trackball works from the last DELIVERED position and the
            # pan from the press point, so nothing is lost.
            return

        super().viewbox_mouse_event(event)

    def _sphere(self, pos):
        w, h = self._viewbox.size
        r = self.TRACKBALL_RADIUS * 0.5 * min(w, h)
        x = (pos[0] - 0.5 * w) / r
        y = -(pos[1] - 0.5 * h) / r             # screen y grows downward
        d2 = x * x + y * y
        if d2 <= 1.0:
            return np.array([x, y, np.sqrt(1.0 - d2)])
        d = np.sqrt(d2)
        return np.array([x / d, y / d, 0.0])     # outside: on the rim (roll)

    def _trackball(self, event):
        pos = np.asarray(event.mouse_event.pos[:2], dtype=float)
        if self._last is None:
            self._last = np.asarray(event.mouse_event.press_event.pos[:2], dtype=float)
        v1, v2 = self._sphere(self._last), self._sphere(pos)
        self._last = pos

        axis_cam = np.cross(v1, v2)
        norm = np.linalg.norm(axis_cam)
        if norm < 1e-12:
            return
        angle = np.degrees(np.arctan2(norm, np.dot(v1, v2)))

        # Screen axis -> A-space (the camera's dims mapping).
        up, forward, right = self._get_dim_vectors()
        a_map = np.array([right, up, -forward], dtype=float)   # rows: x, y, z camera axes
        axis = (axis_cam / norm) @ a_map

        if self._rotation is None:
            self._rotation = super()._get_rotation_tr()
        q = transforms.rotate(-angle, axis)
        self._rotation = q @ self._rotation
        self.view_changed()

    def _pan(self, event):
        press = event.mouse_event.press_event
        if self._pan_start is None or self._pan_start[0] is not press:
            self._pan_start = (press, np.array(self.center, dtype=float))
        # scale_factor spans the shorter side of the view (see
        # PerspectiveCamera._update_transform): the model at the pivot depth
        # follows the cursor exactly.
        units_per_px = self._scale_factor / min(self._viewbox.size)
        d = (np.asarray(press.pos[:2], float) - np.asarray(event.mouse_event.pos[:2], float))
        d = d * units_per_px
        d[1] *= -1                                       # screen y grows downward
        m = self.transform.matrix[:3, :3]                # camera -> world directions
        move = np.array([d[0], d[1], 0.0]) @ m
        move /= max(np.linalg.norm(m[0]), 1e-30)         # keep world units
        self.center = tuple(self._pan_start[1] + move)


class _DatumVisual:
    """A work plane (fill + outline), axis (line) or point (dot)."""

    def __init__(self, datum, half, parent, color=DATUM_COLOR):
        self.datum = datum
        self.color = color
        center = datum.origin / SCENE_UNIT
        self.fill = None
        if datum.kind == "plane":
            corners = np.array([center + half * (su * datum.u + sv * datum.v)
                                for su, sv in ((-1, -1), (1, -1), (1, 1), (-1, 1))], dtype=np.float32)
            self.fill = scene.visuals.Mesh(vertices=corners, faces=np.array([[0, 1, 2], [0, 2, 3]], np.uint32),
                                           color=(*DATUM_COLOR[:3], DATUM_FILL_ALPHA), parent=parent)
            # Translucent, drawn after the bodies; pushed back like the bodies' faces so
            # the outline and the axes lying in the plane win the depth test.
            self.fill.set_gl_state("translucent", depth_test=True, cull_face=False,
                                   polygon_offset_fill=True, polygon_offset=(1.0, 1.0))
            self.fill.order = 5
            self.line = scene.visuals.Line(pos=np.vstack([corners, corners[:1]]), color=DATUM_COLOR,
                                           width=1.5, parent=parent)
            self.half = half
        elif datum.kind == "axis":
            self.half = AXIS_OVERHANG * half
            ends = np.array([center - self.half * datum.direction, center + self.half * datum.direction],
                            np.float32)
            self.line = scene.visuals.Line(pos=ends, color=DATUM_COLOR, width=1.5, parent=parent)
        else:
            self.half = 0.0
            self.line = scene.visuals.Markers(parent=parent)
            self._center = center.astype(np.float32)[None]
            # The planes cross at the point and their near halves would hide it.
            self.line.set_gl_state("translucent", depth_test=False)
        self.line.order = 7 if datum.kind == "point" else 6

    def remove(self):
        for visual in (self.fill, self.line):
            if visual is not None:
                visual.parent = None

    def set_style(self, visible, state):
        """state: None, "hover" or "selected"."""
        color = {"hover": HOVER_COLOR, "selected": SELECTED_COLOR}.get(state, self.color)
        self.shown_color = color
        shown = visible or state == "hover"
        if self.datum.kind == "point":
            self.line.set_data(self._center, size=7 if state is None else 10, edge_width=1.0,
                               face_color=color, edge_color=(0, 0, 0, 1))
        else:
            self.line.set_data(color=color, width=1.5 if state is None else 3.0)
        self.line.visible = shown
        if self.fill is not None:
            self.fill.color = (*color[:3], DATUM_FILL_ALPHA if state is None else 2 * DATUM_FILL_ALPHA)
            self.fill.visible = shown


class _SketchVisual:
    """
    A sketch's lines and circles (always; construction ones dashed), its
    explicit points (a dot in a gapped cross, always) and the other points
    (dots, in sketch mode). The crosses and the dashes lie in the sketch's
    plane and keep their size in pixels: draw_screen_sized(mm per pixel)
    after every camera change.
    """

    def __init__(self, name, result, parent):
        self.name = name
        self.line_names = [line.name for line in result.lines]
        self.circle_names = [c.name for c in result.circles]
        polylines = getattr(result, "polylines", [])
        self.polyline_names = [w.name for w in polylines]
        # Lines, then each circle as CIRCLE_SEGMENTS pieces, then each wide polyline's outline (owned by
        # the polyline) and its center segments (owned by "W1.1", "W1.2"...); seg_owner: each segment's.
        blocks = [result.segments_mm()] + [result.circle_segments_mm(n) for n in self.circle_names]
        self.seg_owner = self.line_names + [n for n in self.circle_names for _ in range(CIRCLE_SEGMENTS)]
        centers = []
        for w in polylines:
            ring = result.outline_segments_mm(w.name)
            blocks.append(ring)
            self.seg_owner += [w.name] * len(ring)
            blocks.append(result.center_segments_mm(w.name))
            centers += [f"{w.name}.{k}" for k in range(1, len(w.points))]
            self.seg_owner += centers[-(len(w.points) - 1):]
        curves = getattr(result, "curves", [])
        for c in curves:                                # generated curves, as their sampled polylines
            pieces = result.curve_segments_mm(c.name)
            blocks.append(pieces)
            self.seg_owner += [c.name] * len(pieces)
        splines = [w for w in getattr(result, "splines", []) if w.name in result.shapes]
        for w in splines:                               # splines, the same way
            pieces = result.curve_segments_mm(w.name)
            blocks.append(pieces)
            self.seg_owner += [w.name] * len(pieces)
        for name in getattr(result, "offsets", {}):     # offset bands, owned by their entity (a click picks it)
            pieces = result.offset_segments_mm(name)
            blocks.append(pieces)
            self.seg_owner += [name] * len(pieces)
        self.segments = np.concatenate(blocks) if blocks else np.zeros((0, 2, 3))     # (K, 2, 3) mm
        # Dashed: construction lines, circles, curves and splines, and the polylines' center lines.
        self.construction = {e.name for e in result.lines + result.circles + curves + splines
                             if e.construction} | set(centers)
        # Hidden curves too (not drawn, so not picked): their browser rows select them.
        shown = result.shown_points()
        self.entity_names = set(self.seg_owner) | set(shown) | set(self.polyline_names) | set(result.shapes)
        self.point_names = list(shown)
        self.point_pos = np.array([result.world_mm(n) for n in self.point_names]).reshape(-1, 3)
        self.explicit = [n in result.explicit for n in self.point_names]
        self.axes = (result.frame.u, result.frame.v)
        self.lines = scene.visuals.Line(connect="segments", width=2.0, parent=parent)
        self.lines.order = 6
        self.dots = scene.visuals.Markers(parent=parent)
        self.dots.set_gl_state("translucent", depth_test=False)
        self.dots.order = 7
        self.dots.visible = False
        self.crosses = scene.visuals.Line(connect="segments", width=1.5, parent=parent)
        self.crosses.set_gl_state("translucent", depth_test=False)
        self.crosses.order = 7
        self.crosses.visible = any(self.explicit)
        self.cross_segments = np.zeros((0, 2, 3))             # as last drawn, mm (for the checks)
        self._cross_colors = []
        self.dashes = scene.visuals.Line(connect="segments", width=2.0, parent=parent)
        self.dashes.order = 6
        self.dashes.visible = bool(self.construction)
        self.dash_segments = {}     # construction entity -> (K, 2, 3) mm, as last drawn (for the checks)
        self._dash_colors = {}
        self.lines.visible = len(self.segments) > 0
        self.on_top = False         # drawn over the bodies (while it is the sketch edited)
        self.shown_colors = {}      # key -> color last drawn (for the checks)

    def remove(self):
        for visual in (self.lines, self.dots, self.crosses, self.dashes):
            visual.parent = None

    def key(self, entity, editing):
        return ("entity", self.name, entity) if editing else ("feature", self.name)

    def set_style(self, hovered, selected, editing, mm_per_px):
        """selected: the set of selected keys."""
        def color(key, entity=None):
            if entity is not None and key[0] == "feature" and key not in selected and key != hovered:
                key = ("entity", self.name, entity)    # one entity of a sketch not edited (its browser row)
            return HOVER_COLOR if key == hovered else SELECTED_COLOR if key in selected else None

        # The sketch being edited is drawn over the bodies (as Inventor does): one on a face
        # lies under what it extruded from that face (copper on a substrate) and would hide.
        if editing != self.on_top:
            self.on_top = editing
            for visual in (self.lines, self.dashes):
                visual.set_gl_state("translucent", depth_test=not editing)
            self.lines.order = self.dashes.order = 9 if editing else 6
        self.shown_colors = {}
        self._dash_colors = {}
        solid = [i for i, n in enumerate(self.seg_owner) if n not in self.construction]
        colors = []
        for n in self.seg_owner:
            key = self.key(n, editing)
            c = color(key, n) or SKETCH_LINE_COLOR
            self.shown_colors[key] = c
            if n in self.construction:
                self._dash_colors[n] = c
            else:
                colors += [c, c]
        self.lines.visible = bool(solid)
        if solid:
            self.lines.set_data(pos=self.segments[solid].reshape(-1, 3).astype(np.float32),
                                color=np.array(colors, np.float32), width=2.0)
        shown = [i for i, e in enumerate(self.explicit) if e or editing]
        self.dots.visible = bool(shown)
        if shown:
            colors = []
            for i in shown:
                key = self.key(self.point_names[i], editing)
                c = color(key) or SKETCH_DOT_COLOR
                self.shown_colors[key] = c
                colors.append(c)
            self.dots.set_data(self.point_pos[shown].astype(np.float32), size=6 if editing else 5,
                               edge_width=1.0, face_color=np.array(colors), edge_color=(0, 0, 0, 1))
        self._cross_colors = [self.shown_colors.get(self.key(n, editing), SKETCH_LINE_COLOR)
                              for n, e in zip(self.point_names, self.explicit) if e]
        self.draw_screen_sized(mm_per_px)

    def draw_screen_sized(self, mm_per_px):
        self._draw_dashes(mm_per_px)
        self._draw_crosses(mm_per_px)

    def _draw_dashes(self, mm_per_px):
        """Construction lines and circles: dashes of CONSTRUCTION_DASH_PX, gaps of CONSTRUCTION_GAP_PX."""
        if not self.construction:
            return
        owner = np.array(self.seg_owner)
        segments, colors = [], []
        self.dash_segments = {}
        for n in self.construction:
            pieces = self.segments[owner == n]
            polyline = np.vstack([pieces[:1, 0], pieces[:, 1]])
            dashes = _dash_polyline(polyline, CONSTRUCTION_DASH_PX * mm_per_px, CONSTRUCTION_GAP_PX * mm_per_px)
            self.dash_segments[n] = dashes
            segments.append(dashes.reshape(-1, 3))
            colors += [self._dash_colors.get(n, SKETCH_LINE_COLOR)] * (2 * len(dashes))
        self.dashes.set_data(pos=np.concatenate(segments).astype(np.float32),
                             color=np.array(colors, np.float32), width=2.0)

    def _draw_crosses(self, mm_per_px):
        """The crosses of the explicit points, CROSS_GAP_PX..CROSS_ARM_PX from the point along u and v."""
        centers = self.point_pos[np.array(self.explicit, bool)] if len(self.explicit) else np.zeros((0, 3))
        if not len(centers):
            return
        gap, arm = CROSS_GAP_PX * mm_per_px, CROSS_ARM_PX * mm_per_px
        u, v = self.axes
        segments, colors = [], []
        for center, c in zip(centers, self._cross_colors):
            for d in (u, -u, v, -v):
                segments.append([center + gap * d, center + arm * d])
                colors += [c, c]
        self.cross_segments = np.array(segments)
        self.crosses.set_data(pos=self.cross_segments.reshape(-1, 3).astype(np.float32),
                              color=np.array(colors, np.float32), width=1.5)

    def rows(self, editing):
        """Pick rows (see picking.pick_datum): lines as axes; points as points (explicit ones always)."""
        out = []
        for n, (a, b) in zip(self.seg_owner, self.segments):
            length = float(np.linalg.norm(b - a))
            if length > 0:
                out.append((self.key(n, editing), "axis", 0.5 * (a + b), (b - a) / length,
                            np.zeros(3), np.zeros(3), 0.5 * length))
        for n, point, explicit in zip(self.point_names, self.point_pos, self.explicit):
            if editing or explicit:
                out.append((self.key(n, editing), "point", point, np.zeros(3), np.zeros(3), np.zeros(3), 0.0))
        return out


class _BodyVisual:
    """Mesh + edge lines of one body."""

    def __init__(self, body, parent):
        self.body = body
        self.base_color = np.array(body.color or DEFAULT_COLORS[body.kind], dtype=float)

        vertices = body.vertices / SCENE_UNIT
        triangles = body.triangles

        if body.kind == "sheet":
            n = len(vertices)
            vertices = np.concatenate((vertices, vertices))
            triangles = np.concatenate((triangles, triangles[:, ::-1] + n))

        self.mesh = scene.visuals.Mesh(
            vertices=vertices.astype(np.float32),
            faces=triangles,
            color=(*self.base_color, 1.0),
            shading="smooth",
            parent=parent,
        )
        # Edges are drawn exactly on the surface: push the faces back a bit.
        self.mesh.set_gl_state(
            "opaque", depth_test=True, cull_face=False,
            polygon_offset_fill=True, polygon_offset=(1.0, 1.0),
        )

        self.edges = scene.visuals.Line(
            pos=(body.edges / SCENE_UNIT).reshape(-1, 3).astype(np.float32),
            connect="segments", color=EDGE_COLOR, width=1.0, parent=parent,
        )

    def remove(self):
        self.mesh.parent = None
        self.edges.parent = None

    def set_style(self, visible, selected, xray, show_edges, faded=False):
        """faded: a little see-through (SKETCH_MODE_ALPHA), still hiding what is behind it mostly."""
        self.mesh.visible = visible
        self.edges.visible = visible and (show_edges or selected)

        color = self.base_color
        if selected:
            color = 0.5 * color + 0.5 * np.array([1.0, 0.9, 0.4])

        self.mesh.color = (*color, XRAY_ALPHA if xray else SKETCH_MODE_ALPHA if faded else 1.0)

        if xray:
            self.mesh.set_gl_state("translucent", depth_test=False, cull_face=False)
        elif faded:                         # back faces culled: no inner face shows (a copper foil's bottom
            self.mesh.set_gl_state(         # on the board's top would stripe through its top)
                "translucent", depth_test=True, cull_face=self.body.kind == "solid",
                polygon_offset_fill=True, polygon_offset=(1.0, 1.0),
            )
        else:
            self.mesh.set_gl_state(
                "opaque", depth_test=True, cull_face=False,
                polygon_offset_fill=True, polygon_offset=(1.0, 1.0),
            )

        self.edges.set_data(color=SELECTED_EDGE_COLOR if selected else EDGE_COLOR,
                            width=2.0 if selected else 1.0)


# Up axis -> view -> the world direction from the model toward the viewer.
STANDARD_VIEWS = {
    "z": {"Top": (0, 0, 1), "Bottom": (0, 0, -1), "Front": (0, -1, 0), "Back": (0, 1, 0),
          "Right": (1, 0, 0), "Left": (-1, 0, 0)},
    "y": {"Top": (0, 1, 0), "Bottom": (0, -1, 0), "Front": (0, 0, 1), "Back": (0, 0, -1),
          "Right": (1, 0, 0), "Left": (-1, 0, 0)},
    "x": {"Top": (1, 0, 0), "Bottom": (-1, 0, 0), "Front": (0, 0, 1), "Back": (0, 0, -1),
          "Right": (0, -1, 0), "Left": (0, 1, 0)},
}
AXIS_VIEWS = [(e, a) for e in (90.0, -90.0, 0.0) for a in (0.0, 90.0, 180.0, -90.0)]    # elevation, azimuth
PERSPECTIVE_FOV = 30.0


class GeometryView:
    """
    Owns a SceneCanvas. Bodies are addressed by name.
    Rotate: drag; zoom: wheel; pan: Shift + drag.
    """

    def __init__(self, canvas_size=(1000, 750), show=False):
        # keys=None: VisPy's "interactive" keys close the canvas on Esc (the 3D view vanished
        # from the window) and toggle full screen on F11; the keys are ours.
        self.canvas = scene.SceneCanvas(keys=None, size=canvas_size, bgcolor=BACKGROUND, show=show)
        _skip_redundant_qt_swap(self.canvas)

        self.view = self.canvas.central_widget.add_view()
        self.view.camera = TrackballCamera(fov=PERSPECTIVE_FOV, elevation=25, azimuth=-50, up="+z")
        # Near / far planes at distance / sqrt(d) and * sqrt(d). VisPy's
        # default d = 1e6 left too little depth precision: 15 um copper on
        # FR4 flickered through (z-fighting). 200 keeps the whole model.
        self.view.camera.depth_value = 200.0
        self._init_space_pan()

        self.root = scene.Node(parent=self.view.scene)
        # An invisible visual in the scene's coordinates: its transform to the
        # canvas is the camera's (a Node has no get_transform).
        self._probe = scene.visuals.Markers(parent=self.root)
        self._probe.visible = False
        self.box = scene.visuals.Line(connect="segments", color=BOX_COLOR, parent=self.view.scene)
        self.box.visible = False

        self._bodies = {}
        self._visible = {}
        self._datums = {}               # name -> _DatumVisual
        self._sketch_points = {}        # sketch name -> _DatumVisual (a point)
        self._sketch_geometry = {}      # sketch name -> _SketchVisual
        self.editing_sketch = None      # name of the sketch in sketch mode
        self.tool = None                # a drawing tool (view/sketch_tools.py) or None
        self.on_delete = None           # callback(): the Delete key
        self.on_drag = None             # callback(key, (2,) mm moved or None, "start" | "move" | "end" | "cancel")
        self.on_insert = None           # callback(key, (2,) sketch mm): a double click on a sketch entity
        self._drag = None               # a left drag on a sketch entity: {"keys", "start" (2,) mm, "started"}
        self.drag_start = None          # (2,) sketch mm where the current drag began
        self._box = None                # a box being drawn: {"start" (2,) px, "add": Shift}
        self.box_line = scene.visuals.Line(width=1.0, connect="segments", parent=self.canvas.scene)
        self.box_line.visible = False
        self._datum_visible = {}
        self._datum_list = []           # Datum, as given
        self._datum_half = DATUM_HALF_MIN
        self.selection = []             # selected keys ("datum" | "body" | "feature" | "entity", ...)
        self.hovered = None             # ("datum", name) or None
        self.on_select = None           # callback(key or None): a click selected
        self.on_hover = None            # callback(key or None): the mouse moved onto / off a datum
        self.measure = False
        self.on_escape = None           # callback() -> True if the window used the Esc key
        self._pick_kinds = None         # datum kinds a click may pick (None: anything)
        self._forced = set()            # datums drawn and pickable though hidden
        self.sketch_frame = None        # model.sketch.SketchFrame in sketch mode, else None
        self.xray = False
        self.show_edges = True
        self._bounds = (-np.ones(3), np.ones(3))

        self.pick_line = scene.visuals.Line(color=PICK_LINE_COLOR, width=1.5, parent=self.view.scene)
        self.pick_marker = scene.visuals.Markers(parent=self.view.scene)
        for order, visual in enumerate((self.pick_line, self.pick_marker)):
            visual.set_gl_state("translucent", depth_test=False)
            visual.order = 20 + order
            visual.visible = False
        self.sketch_axes = scene.visuals.Line(connect="segments", width=1.5, parent=self.view.scene)
        self.sketch_axes.set_gl_state("translucent", depth_test=True)
        self.sketch_axes.order = 4
        self.sketch_axes.visible = False
        self.guide_lines = scene.visuals.Line(connect="segments", color=GUIDE_COLOR, width=1.0,
                                              parent=self.view.scene)
        self.guide_lines.set_gl_state("translucent", depth_test=False)
        self.guide_lines.order = 29
        self.guide_lines.visible = False
        self.guides = []                # the alignment hints shown: [(from, to) (2,) sketch mm]
        self.snap_marker = scene.visuals.Markers(parent=self.view.scene)
        self.snap_marker.set_gl_state("translucent", depth_test=False)
        self.snap_marker.order = 30
        self.snap_marker.visible = False
        self.snap_spot = None           # (2,) sketch mm where a drag snapped (a radius through a point)
        self._profiles = []             # in profile mode: [(key, frame, region, Mesh)]
        self.profile_mode = False
        self.profile_selection = []     # keys of the selected profiles
        self.on_profiles = None         # callback(keys) after a click changed the selection
        self.shown_profiles = {}        # key -> "hover" | "selected" | None, as drawn
        self.preview_body = None        # the Body shown as a feature's preview, or None
        self.preview_cut = False        # drawn as a cut (red)
        # A hovered body face (green) and the selected ones (blue), over the body's own faces
        # (those are pushed back by their polygon offset, so these coplanar ones win).
        self.face_hover = scene.visuals.Mesh(color=FACE_HOVER_COLOR, parent=self.root)
        self.face_selected = scene.visuals.Mesh(color=FACE_SELECTED_COLOR, parent=self.root)
        for order, visual in enumerate((self.face_selected, self.face_hover)):
            visual.set_gl_state("translucent", depth_test=True, cull_face=False,
                                polygon_offset_fill=True, polygon_offset=(-1.0, -1.0))
            visual.order = 3 + order
            visual.visible = False
        self.preview = scene.visuals.Mesh(parent=self.root, shading="smooth")
        self.preview.set_gl_state("translucent", depth_test=True, cull_face=False)
        self.preview.order = 8
        self.preview.visible = False

        self._picker = None
        self.picks = [None, None]       # points A and B: Pick or None
        self.on_pick = None             # callback([A, B])
        self._press = None

        self._init_headlight()
        self._init_click()
        self._init_gizmo()

    def _init_space_pan(self):
        """
        Space + drag pans (like Shift + drag). The canvas takes keyboard focus
        when the mouse enters it, so Space reaches the 3D view and does not
        toggle a checkbox in the tree that had the focus.
        """
        native = self.canvas.native
        enter = native.enterEvent

        def on_enter(event):
            native.setFocus()
            enter(event)

        native.enterEvent = on_enter
        leave = native.leaveEvent

        def on_leave(event):
            if self.hovered is not None:            # no green left behind off the canvas
                self.set_hover(None)
                if self.on_hover is not None:
                    self.on_hover(None)
            leave(event)

        native.leaveEvent = on_leave
        self.canvas.events.key_press.connect(lambda e: self._space(e, True))
        self.canvas.events.key_release.connect(lambda e: self._space(e, False))

    def _space(self, event, down):
        if event.key != keys.SPACE:
            return
        native = getattr(event, "native", None)
        if native is not None and native.isAutoRepeat():
            return
        self.view.camera.space_down = down
        from PySide6.QtCore import Qt
        if down:
            self.canvas.native.setCursor(Qt.OpenHandCursor)
        else:
            self.canvas.native.unsetCursor()
        event.handled = True

    # =====================================================
    # Clicks: picking points A and B
    # =====================================================

    def _init_click(self):
        self.canvas.events.mouse_press.connect(self._mouse_press)
        self.canvas.events.mouse_move.connect(self._mouse_move)
        self.canvas.events.mouse_release.connect(self._mouse_release)
        self.canvas.events.mouse_double_click.connect(self._double_click)
        self.canvas.events.key_press.connect(self._escape)

    def _escape(self, event):
        if event.key == keys.DELETE and self.on_delete is not None and self.tool is None:
            self.on_delete()
            event.handled = True
            return
        if event.key != keys.ESCAPE:
            return
        if self._drag_end("cancel"):
            event.handled = True
            return
        if self._box is not None:
            self._box = None
            self.box_line.visible = False
            self.canvas.update()
            event.handled = True
            return
        if self.tool is not None and self.tool.escape():
            event.handled = True
            return
        if self.on_escape is not None and self.on_escape():
            event.handled = True
            return
        if any(self.picks):
            self.set_picks([None, None])
        elif self.selected is not None:
            self._click_select(None)
        event.handled = True

    def _mouse_move(self, event):
        if self.profile_mode and event.button is None and not event.is_dragging:
            key = self.profile_at(event.pos)
            if key != self.hovered:
                self.hovered = key
                self._style_profiles()
            return
        if self._drag is not None and event.is_dragging and 1 in event.buttons:
            self._drag_move(event.pos)
            return
        if self._box is not None and event.is_dragging and 1 in event.buttons:
            self._draw_box(event.pos)
            return
        if event.button is not None or event.is_dragging:
            return
        if self.tool is not None:
            self.tool.move(event.pos)
            return
        if self.measure:
            return
        key = self.datum_at(event.pos)
        if key is None:
            key = self.face_key_at(event.pos)
        if key != self.hovered:
            self.set_hover(key)
            if self.on_hover is not None:
                self.on_hover(key)

    def _mouse_press(self, event):
        self._press = np.array(event.pos[:2], float) if event.button == 1 else None
        self._drag = self._box = None
        modifiers = set(event.modifiers)
        if event.button != 1 or self.tool is not None or self.measure or self.view.camera.space_down \
                or self.editing_sketch is None or modifiers - {keys.SHIFT}:
            return
        key = self.datum_at(event.pos)
        start = self.sketch_xy(event.pos)
        if key is not None and key[0] == "entity":
            if not modifiers and start is not None:     # Shift + press on an entity: a toggle on release
                self._drag = {"key": key, "start": start, "started": False}
        else:
            self._box = {"start": self._press.copy(), "add": keys.SHIFT in modifiers}

    # =====================================================
    # Dragging sketch entities
    # =====================================================

    def sketch_xy(self, pos):
        """Where the ray through canvas pixel `pos` meets the sketch's plane: (2,) mm, or None."""
        frame = self.sketch_frame
        if frame is None:
            return None
        origin, direction = _ray(self._transform(), pos)
        facing = float(np.dot(direction, frame.normal))
        if abs(facing) < 1e-9:
            return None                         # the plane is seen edge-on
        t = float(np.dot(frame.origin / SCENE_UNIT - origin, frame.normal)) / facing
        hit = origin + t * direction
        return frame.to_sketch(hit[None] * SCENE_UNIT)[0] / SCENE_UNIT

    def _drag_move(self, pos):
        drag = self._drag
        if not drag["started"]:
            if np.linalg.norm(np.asarray(pos[:2], float) - self._press) < CLICK_PX:
                return                          # still a click
            drag["started"] = True
            if drag["key"] not in self.selection:
                self._click_select(drag["key"])
            drag["keys"] = [k for k in self.selection if k[0] == "entity"]
            self.drag_start = drag["start"]     # where the drag began, sketch mm (a radius drag needs it)
            if self.on_drag is not None:
                self.on_drag(drag["keys"], np.zeros(2), "start")
        xy = self.sketch_xy(pos)
        if xy is not None and self.on_drag is not None:
            self.on_drag(drag["keys"], xy - drag["start"], "move")

    def _drag_end(self, phase):
        """phase: "end" (released: keep) or "cancel" (Esc: put everything back)."""
        drag, self._drag = self._drag, None
        if drag is not None and drag["started"] and self.on_drag is not None:
            self.on_drag(drag["keys"], None, phase)
        return drag is not None and drag["started"]

    def _mouse_release(self, event):
        if event.button == 1 and self._drag_end("end"):
            self._press = None
            return
        if event.button == 1 and self._box is not None:
            box, self._box = self._box, None
            self.box_line.visible = False
            if np.linalg.norm(np.asarray(event.pos[:2], float) - box["start"]) >= CLICK_PX:
                self._press = None
                self._select_box(box["start"], np.asarray(event.pos[:2], float), box["add"])
                return
        press, self._press = self._press, None
        modifiers = set(event.modifiers)
        if press is None or event.button != 1 or self.view.camera.space_down \
                or modifiers - {keys.SHIFT}:
            return
        if np.linalg.norm(np.array(event.pos[:2], float) - press) >= CLICK_PX:
            return                      # a drag: rotated or panned
        if self.profile_mode:
            key = self.profile_at(event.pos)
            if key is not None:
                selection = list(self.profile_selection)
                selection.remove(key) if key in selection else selection.append(key)
                self.set_profile_selection(selection)
                if self.on_profiles is not None:
                    self.on_profiles(list(self.profile_selection))
            return
        if self.tool is not None:
            if not modifiers:
                self.tool.click(event.pos)
            return
        if not self.measure:
            if not modifiers:
                self._click_select(self.key_at(event.pos))
            elif self.editing_sketch is not None:
                self._toggle(self.key_at(event.pos))
            return
        pick = self.pick_at(event.pos)
        if pick is not None:
            picks = list(self.picks)
            picks[1 if keys.SHIFT in modifiers else 0] = pick
            self.set_picks(picks)

    def _click_select(self, key):
        """A click selected `key` (a point with its coincident points), or nothing."""
        self.select_keys(self.coincident(key) if key is not None else [])
        if self.on_select is not None:
            self.on_select(self.selected)

    def _toggle(self, key):
        """Shift + click: adds `key` (with its coincident points) or takes it out."""
        if key is None or key[0] != "entity":
            return
        group = self.coincident(key)
        if key in self.selection:
            self.select_keys([k for k in self.selection if k not in group])
        else:
            self.select_keys(self.selection + [k for k in group if k not in self.selection])
        if self.on_select is not None:
            self.on_select(self.selected)

    def coincident(self, key):
        """A sketch point's key and the keys of the edited sketch's points at the same place."""
        if key is None or key[0] != "entity" or key[1] not in self._sketch_geometry:
            return [key]
        visual = self._sketch_geometry[key[1]]
        if key[2] not in visual.point_names:
            return [key]
        at = visual.point_pos[visual.point_names.index(key[2])]
        same = np.linalg.norm(visual.point_pos - at, axis=1) <= COINCIDENT_MM
        return [key] + [("entity", key[1], n) for n, s in zip(visual.point_names, same) if s and n != key[2]]

    # =====================================================
    # Box selection
    # =====================================================

    def _draw_box(self, pos):
        """The box from the press to `pos` (canvas pixels): solid left to right, dashed right to left."""
        a, b = self._box["start"], np.asarray(pos[:2], float)
        corners = np.array([a, (b[0], a[1]), b, (a[0], b[1]), a])
        crossing = b[0] < a[0]
        segments = []
        for p, q in zip(corners[:-1], corners[1:]):
            if not crossing:
                segments += [p, q]
                continue
            length = float(np.linalg.norm(q - p))
            d = (q - p) / max(length, 1e-9)
            for t in np.arange(0.0, length, GUIDE_DASH_PX + GUIDE_GAP_PX):
                segments += [p + t * d, p + min(t + GUIDE_DASH_PX, length) * d]
        self.box_line.set_data(pos=np.array(segments, np.float32),
                               color=BOX_COLOR_CROSSING if crossing else BOX_COLOR_WINDOW)
        self.box_line.visible = len(segments) > 0
        self.box_crossing = crossing
        self.canvas.update()

    def box_keys(self, a, b):
        """
        The edited sketch's entities in the box from canvas pixel a (the
        press) to b: drawn left to right, what lies wholly inside; right to
        left, what lies inside or crosses it.
        """
        visual = self._sketch_geometry.get(self.editing_sketch)
        if visual is None:
            return []
        crossing = b[0] < a[0]
        lo, hi = np.minimum(a, b), np.maximum(a, b)
        transform = self._transform()

        def inside(xy):
            return np.all((xy >= lo) & (xy <= hi), axis=-1)

        out = []
        if len(visual.point_pos):
            xy, front = _project(transform, visual.point_pos)
            out += [("entity", visual.name, n) for n, i, f in zip(visual.point_names, inside(xy), front) if i and f]
        if len(visual.segments):
            # A line or circle is in when all its pieces are inside (or, crossing, when one touches the box).
            ends, front = _project(transform, visual.segments.reshape(-1, 3))
            ends, front = ends.reshape(-1, 2, 2), front.reshape(-1, 2)
            whole, touch = {}, {}
            for n, (p, q), f in zip(visual.seg_owner, ends, front):
                whole[n] = whole.get(n, True) and bool(f.all() and inside(p) and inside(q))
                touch[n] = touch.get(n, False) or bool(f.all() and _segment_meets_box(p, q, lo, hi))
            out += [("entity", visual.name, n) for n in whole if whole[n] or crossing and touch[n]]
        return out

    def _select_box(self, a, b, add):
        found = self.box_keys(a, b)
        keys = (self.selection if add else []) + found
        self.select_keys(list(dict.fromkeys(k for k in keys if k[0] == "entity")))
        if self.on_select is not None:
            self.on_select(self.selected)

    @property
    def selected(self):
        """The last selected key, or None."""
        return self.selection[-1] if self.selection else None

    @selected.setter
    def selected(self, key):
        self.selection = [] if key is None else [key]

    def select_keys(self, keys):
        self.selection = list(keys)
        self._restyle()

    def _double_click(self, event):
        if self.measure and event.button == 1 and any(self.picks) and self.pick_at(event.pos) is None:
            self.set_picks([None, None])
            return
        # In sketch mode, a double click on a polyline's center segment puts a point there.
        if event.button == 1 and self.editing_sketch is not None and self.tool is None and self.on_insert:
            key = self.datum_at(event.pos)
            xy = self.sketch_xy(event.pos)
            if key is not None and key[0] == "entity" and xy is not None:
                self.on_insert(key, xy)

    def set_tool(self, tool):
        """A drawing tool takes the mouse (None: hover and select again)."""
        if self.tool is not None:
            self.tool.remove()
        self.tool = tool
        self.set_hover(None)

    def set_measure(self, on):
        """Measure mode: clicks pick points A / B instead of selecting; off clears them."""
        self.measure = bool(on)
        self.set_hover(None)
        if not self.measure:
            self.set_picks([None, None])

    def _transform(self):
        """scene (mm) -> canvas pixels."""
        return self._probe.get_transform("visual", "canvas")

    def _visible_bodies(self):
        return {name for name, on in self._visible.items() if on}

    def datum_at(self, pos):
        """
        ("datum", name) of the shown datum under canvas pixel `pos`, or
        ("feature", name) of a sketch's origin point, or None.
        """
        rows = [(("datum", d.name), d.kind, d.origin / SCENE_UNIT, d.direction, d.u, d.v,
                 self._datums[d.name].half)
                for d in self._datum_list if self._datum_visible.get(d.name) or d.name in self._forced]
        if self.editing_sketch is None:
            rows += [(("feature", name), "point", v.datum.origin / SCENE_UNIT, np.zeros(3), np.zeros(3),
                      np.zeros(3), 0.0) for name, v in self._sketch_points.items()]
            for visual in self._sketch_geometry.values():
                rows += visual.rows(editing=False)
        elif self.editing_sketch in self._sketch_geometry:
            rows += self._sketch_geometry[self.editing_sketch].rows(editing=True)
        if self._pick_kinds is not None:
            rows = [row for row in rows if row[1] in self._pick_kinds]
        if not rows:
            return None
        transform = self._transform()
        behind = np.inf
        if self._picker is not None and not self.xray and self.editing_sketch is None:
            # (The sketch being edited is drawn over the bodies, so no body hides it.)
            behind = self._picker.ray_hit(transform, pos, self._visible_bodies())
        hit = pick_datum(transform, pos, rows, behind)
        return hit[0] if hit is not None else None

    def key_at(self, pos):
        """What a click at `pos` selects: a datum, else a body's face, else None."""
        key = self.datum_at(pos)
        return key if key is not None else self.face_key_at(pos)

    def face_key_at(self, pos):
        """
        ("face", body, CAD face index) of the body face under `pos`, or None
        (none in sketch mode; while picking, only if faces are asked for).
        """
        if self._picker is None or self.editing_sketch is not None:
            return None
        if self._pick_kinds is not None and "face" not in self._pick_kinds:
            return None
        hit = self._picker.face_at(self._transform(), pos, self._visible_bodies())
        return None if hit is None else ("face", *hit)

    def face_triangles(self, key):
        """(K, 3, 3) mm: the triangles of a ("face", body, index) key."""
        visual = self._bodies.get(key[1])
        if visual is None:
            return np.zeros((0, 3, 3))
        body = visual.body
        return body.vertices[body.triangles[body.faces == key[2]]] / SCENE_UNIT

    def pick_at(self, pos):
        """The Pick under canvas pixel `pos`, or None."""
        if self._picker is None or not self._bodies:
            return None
        return self._picker.pick(self._transform(), pos, self._visible_bodies(), occlude=not self.xray)

    def set_picks(self, picks):
        """Points A and B (Pick or None each): mark them and call on_pick."""
        self.picks = list(picks)
        shown = [(p.point, PICK_COLORS[i]) for i, p in enumerate(self.picks) if p is not None]
        if shown:
            self.pick_marker.set_data(np.array([p for p, _ in shown], np.float32), size=11, edge_width=2,
                                      face_color=np.array([c for _, c in shown]), edge_color=(0, 0, 0, 1))
        self.pick_marker.visible = bool(shown)
        if len(shown) == 2:
            self.pick_line.set_data(pos=np.array([p for p, _ in shown], np.float32))
        self.pick_line.visible = len(shown) == 2
        self.canvas.update()
        if self.on_pick is not None:
            self.on_pick(self.picks)

    # =====================================================
    # Gizmo and light
    # =====================================================

    def _init_gizmo(self):
        """X, Y, Z in the lower left corner (canvas pixels), turning with the camera."""
        self._gizmo_lines = [scene.visuals.Line(width=2.0, color=color, parent=self.canvas.scene)
                             for color in GIZMO_COLORS]
        self._gizmo_labels = [scene.visuals.Text(name, color=color, font_size=7,
                                                 parent=self.canvas.scene)
                              for name, color in zip("XYZ", GIZMO_COLORS)]
        self.view.camera.on_turn = self._camera_moved
        self.canvas.events.resize.connect(lambda event: self._update_gizmo())
        self._update_gizmo()

    def _camera_moved(self):
        self._update_gizmo()
        mm_per_px = self._mm_per_px()
        for visual in self._sketch_geometry.values():
            visual.draw_screen_sized(mm_per_px)

    def _mm_per_px(self):
        """Scene mm per canvas pixel at the camera's pivot depth (as the pan uses)."""
        camera = self.view.camera
        return float(camera.scale_factor) / max(min(camera._viewbox.size), 1.0)

    def _update_gizmo(self):
        # The world axes as the view draws them: the camera's center and a
        # step along each axis mapped scene -> canvas pixels (perspective
        # included), scaled so that three orthogonal unit axes would project
        # to squared lengths summing to 2 (an axis toward the viewer is short).
        camera = self.view.camera
        center = np.asarray(camera.center[:3], dtype=float)
        step = 0.05 * float(camera.scale_factor)
        points = np.vstack([center, center + step * np.eye(3)])
        mapped = np.asarray(self.view.scene.transform.map(points), dtype=float)
        if np.any(np.abs(mapped[:, 3]) < 1e-12):
            return
        pixels = mapped[:, :2] / mapped[:, 3:4]
        screen = pixels[1:] - pixels[0]                      # canvas y grows downward, as the gizmo's
        scale = np.sqrt(np.sum(screen ** 2) / 2.0)
        if scale < 1e-9:
            return
        origin = np.array([GIZMO_MARGIN, self.canvas.size[1] - GIZMO_MARGIN], dtype=float)
        for d, line, label in zip(screen / scale, self._gizmo_lines, self._gizmo_labels):
            line.set_data(pos=np.array([origin, origin + GIZMO_LENGTH * d], dtype=np.float32))
            label.pos = origin + GIZMO_LENGTH * d + 8 * d / max(np.linalg.norm(d), 0.3)

    def _init_headlight(self):
        """The light follows the camera, so no side of a model stays dark."""
        self._light_eye = np.array([0.3, 0.6, 1.0, 0.0])
        self.view.scene.transform.changed.connect(lambda event: self._update_light())

    def _update_light(self):
        direction = self.view.camera.transform.map(self._light_eye)[:3]
        direction = direction / max(np.linalg.norm(direction), 1e-12)

        for visual in self._bodies.values():
            visual.mesh.shading_filter.light_dir = -direction
        if self.preview.visible:
            self.preview.shading_filter.light_dir = -direction

    # =====================================================
    # Content
    # =====================================================

    def set_geometry(self, geometry, reset=True):
        """Shows the bodies; reset=False keeps the camera (a rebuild of the same model)."""
        for visual in self._bodies.values():
            visual.remove()

        self._bodies = {b.name: _BodyVisual(b, self.root) for b in geometry.bodies}
        self._visible = {name: self._visible.get(name, True) for name in self._bodies}
        self.selection = [k for k in self.selection if k[0] != "body" or k[1] in self._bodies]
        self._picker = Picker(geometry.bodies)
        self.set_picks([None, None])

        if geometry.bodies:
            lo, hi = (np.asarray(v) / SCENE_UNIT for v in geometry.bounds)
            self.box.set_data(pos=box_segments(lo, hi))
            # The origin planes reach a bit past the bodies, as in Inventor.
            self._datum_half = max(DATUM_HALF_MIN, 1.1 * float(np.max(np.abs([lo, hi]))))
        else:
            self._datum_half = DATUM_HALF_MIN
            self.box.visible = False
        self._build_datums()
        self._update_bounds()

        self._restyle()
        self._update_light()
        if reset:
            self.reset_view()

    def set_datums(self, datums):
        """The part's datums (model.part.Datum), shown as their `visible` says."""
        self._datum_list = list(datums)
        self._datum_visible = {d.name: d.visible for d in self._datum_list}
        self.selection = [k for k in self.selection if k[0] != "datum" or k[1] in self._datum_visible]
        self.hovered = None
        self._build_datums()
        self._update_bounds()
        self._restyle()

    def _build_datums(self):
        for visual in self._datums.values():
            visual.remove()
        self._datums = {d.name: _DatumVisual(d, self._datum_half, self.root) for d in self._datum_list}

    def set_sketches(self, sketches):
        """[(name, model.sketch.SketchResult)]: the sketches drawn (origin point and lines)."""
        for visual in list(self._sketch_points.values()) + list(self._sketch_geometry.values()):
            visual.remove()
        self._sketch_points = {
            name: _DatumVisual(Datum(name, "point", result.frame.origin), 0.0, self.root, SKETCH_POINT_COLOR)
            for name, result in sketches}
        self._sketch_geometry = {name: _SketchVisual(name, result, self.root) for name, result in sketches}
        def alive(key):
            if key[0] == "feature":
                return key[1] in self._sketch_points
            if key[0] == "entity":
                visual = self._sketch_geometry.get(key[1])
                return visual is not None and key[2] in visual.entity_names
            return True

        if self.hovered is not None and not alive(self.hovered):
            self.hovered = None
        self.selection = [k for k in self.selection if alive(k)]
        self._update_bounds()
        self._restyle()

    def _update_bounds(self):
        """
        What Reset view frames: the bodies, the sketches and the shown datums
        (hidden ones only while there is no body: an empty part still frames
        its origin).
        """
        points = [v.body.vertices / SCENE_UNIT for v in self._bodies.values() if len(v.body.vertices)]
        datums = [v for name, v in self._datums.items()
                  if not points or self._datum_visible.get(name) or name in self._forced]
        for visual in datums + list(self._sketch_points.values()):
            d = visual.datum
            center = d.origin / SCENE_UNIT
            reach = visual.half * (np.abs(d.u) + np.abs(d.v) + np.abs(d.direction) * (d.kind == "axis"))
            points.append(np.array([center - reach, center + reach]))
        points += [v.segments.reshape(-1, 3) for v in self._sketch_geometry.values() if len(v.segments)]
        if points:
            points = np.concatenate(points)
            self._bounds = (points.min(axis=0), points.max(axis=0))
        else:
            self._bounds = (-np.ones(3), np.ones(3))

    def set_pick_filter(self, kinds=None, forced=()):
        """Only datums of `kinds` hover and click (None: anything); `forced` ones are shown meanwhile."""
        self._pick_kinds = None if kinds is None else set(kinds)
        self._forced = set(forced)
        self.hovered = None
        self._restyle()

    # =====================================================
    # Sketch mode
    # =====================================================

    def set_sketch(self, frame, name=None):
        """
        Sketch mode on the sketch `name`: its axes (frame: a
        model.sketch.SketchFrame), its entities picked one by one; frame=None
        leaves sketch mode.
        """
        self.sketch_frame = frame
        self.editing_sketch = name if frame is not None else None
        self._restyle()
        self.sketch_axes.visible = frame is not None
        if frame is None:
            self.canvas.update()
            return
        # "Endless": the ends lie far beyond the camera's far plane, which cuts the lines.
        lo, hi = self._bounds
        reach = SKETCH_AXIS_REACH * max(float(np.max(hi - lo)), DATUM_HALF_MIN)
        origin = frame.origin / SCENE_UNIT
        axes = [origin - reach * frame.u, origin + reach * frame.u, origin - reach * frame.v, origin + reach * frame.v]
        colors = [SKETCH_AXIS_COLORS[0]] * 2 + [SKETCH_AXIS_COLORS[1]] * 2
        self.sketch_axes.set_data(pos=np.array(axes, np.float32), color=np.array(colors, np.float32))
        self.canvas.update()

    def set_guides(self, frame, guides):
        """
        Alignment hints: dashed lines in the sketch's plane (frame), from a
        point to the one aligned with it; [(from, to) (2,) sketch mm]. Dashes
        and gaps are GUIDE_DASH_PX / GUIDE_GAP_PX long on the screen.
        """
        self.guides = list(guides) if frame is not None else []
        if not self.guides:
            self.guide_lines.visible = False
            self.canvas.update()
            return
        mm_per_px = self._mm_per_px()
        dash, gap = GUIDE_DASH_PX * mm_per_px, GUIDE_GAP_PX * mm_per_px
        segments = []
        for a, b in self.guides:
            start, end = frame.to_world(np.array([a, b]) * SCENE_UNIT) / SCENE_UNIT
            length = float(np.linalg.norm(end - start))
            direction = (end - start) / max(length, 1e-12)
            for t in np.arange(0.0, length, dash + gap)[:2000]:
                segments += [start + t * direction, start + min(t + dash, length) * direction]
        self.guide_lines.set_data(pos=np.array(segments, np.float32))
        self.guide_lines.visible = bool(segments)
        self.canvas.update()

    # =====================================================
    # Profiles
    # =====================================================

    def set_profile_mode(self, items):
        """
        items: [(key, frame, region, vertices (N, 3) mm, triangles (M, 3))]:
        the profiles to pick from, or None to leave profile mode.
        """
        for _, _, _, mesh in self._profiles:
            mesh.parent = None
        self._profiles = []
        self.profile_mode = items is not None
        self.profile_selection = []
        self.hovered = None
        for key, frame, region, vertices, triangles in items or []:
            mesh = scene.visuals.Mesh(vertices=np.asarray(vertices, np.float32), faces=np.asarray(triangles),
                                      color=PROFILE_HOVER_COLOR, parent=self.root)
            # Translucent, pushed back behind the sketch's lines lying on it.
            mesh.set_gl_state("translucent", depth_test=True, cull_face=False,
                              polygon_offset_fill=True, polygon_offset=(1.0, 1.0))
            mesh.order = 5
            self._profiles.append((key, frame, region, mesh))
        self._style_profiles()

    def set_preview(self, body, cut=False):
        """
        What a feature command would add (amber) or cut away (red), before it
        is applied: a see-through body (geometry.body.Body), or None.
        """
        self.preview_body = body
        self.preview_cut = bool(cut)
        if body is not None and len(body.triangles):
            self.preview.set_data(vertices=(body.vertices / SCENE_UNIT).astype(np.float32), faces=body.triangles,
                                  color=PREVIEW_CUT_COLOR if cut else PREVIEW_COLOR)
            self.preview.visible = True
            self._update_light()
        else:
            self.preview.visible = False
        self.canvas.update()

    def set_profile_selection(self, keys):
        self.profile_selection = [k for k in keys if any(k == p[0] for p in self._profiles)]
        self._style_profiles()

    def profile_at(self, pos):
        """The key of the profile under canvas pixel `pos` (the nearest one along the ray), or None."""
        if not self._profiles:
            return None
        origin, direction = _ray(self._transform(), pos)
        best = None
        for key, frame, region, _ in self._profiles:
            facing = float(np.dot(direction, frame.normal))
            if abs(facing) < 1e-9:
                continue
            t = float(np.dot(frame.origin / SCENE_UNIT - origin, frame.normal)) / facing
            if t <= 0 or (best is not None and t >= best[0]):
                continue
            xy = frame.to_sketch((origin + t * direction)[None] * SCENE_UNIT)[0] / SCENE_UNIT
            if region.contains(xy):
                best = (t, key)
        return None if best is None else best[1]

    def _style_profiles(self):
        self.shown_profiles = {}        # key -> "hover" | "selected" | None (for the checks)
        for key, _, _, mesh in self._profiles:
            state = "hover" if key == self.hovered else "selected" if key in self.profile_selection else None
            mesh.visible = state is not None
            if state is not None:
                mesh.color = PROFILE_HOVER_COLOR if state == "hover" else PROFILE_SELECTED_COLOR
            self.shown_profiles[key] = state
        self.canvas.update()

    def set_snap_marker(self, frame, xy):
        """A green dot where a drag snapped, in the sketch's plane (frame); xy None hides it."""
        self.snap_spot = None if frame is None or xy is None else np.asarray(xy, float)
        if self.snap_spot is not None:
            spot = frame.to_world(self.snap_spot[None] * SCENE_UNIT)[0] / SCENE_UNIT
            self.snap_marker.set_data(spot[None].astype(np.float32), size=11, edge_width=1.0,
                                      face_color=SNAP_MARKER_COLOR, edge_color=(0, 0, 0, 1))
        self.snap_marker.visible = self.snap_spot is not None
        self.canvas.update()

    def look_at(self, toward, up):
        """
        The camera looks along -toward (toward points at the viewer) with `up`
        up the screen, framed as Reset view (Inventor's Look At).
        VisPy's chain is cam @ A @ R = world (row vectors, see TrackballCamera):
        the camera's right, up and back axes must land on right, up, toward.
        """
        toward = np.asarray(toward, float) / np.linalg.norm(toward)
        up = np.asarray(up, float) - np.dot(up, toward) * toward
        up /= np.linalg.norm(up)
        right = np.cross(up, toward)
        self.reset_view()
        camera = self.view.camera
        up_cam, forward, right_cam = camera._get_dim_vectors()
        a_map = np.array([right_cam, up_cam, -forward], dtype=float)
        rotation = np.eye(4)
        rotation[:3, :3] = a_map.T @ np.array([right, up, toward])
        camera._rotation = rotation
        camera.view_changed()

    def set_datum_visible(self, name, visible):
        self._datum_visible[name] = bool(visible)
        self._update_bounds()
        self._restyle()

    def set_hover(self, key):
        """Pre-highlight a datum (green); None: none."""
        self.hovered = key
        self._restyle()

    def set_up(self, axis):
        """Vertical axis of the model: 'x', 'y' or 'z'."""
        self.view.camera.up = "+" + axis
        self.reset_view()

    def reset_view(self):
        lo, hi = self._bounds
        center = 0.5 * (lo + hi)
        size = float(np.linalg.norm(hi - lo)) or 1.0

        camera = self.view.camera
        camera.center = tuple(center)
        camera.scale_factor = 1.25 * size
        camera.set_orientation(25.0, -50.0)

    def look_from(self, name):
        """A standard view (STANDARD_VIEWS: Top, Bottom, Front, Back, Left, Right), framed as Reset view."""
        camera = self.view.camera
        views = STANDARD_VIEWS[camera.up[-1]]
        toward = np.asarray(views[name], dtype=float)
        screen_up = {"Top": -np.asarray(views["Front"], dtype=float),
                     "Bottom": np.asarray(views["Front"], dtype=float)}.get(name, np.asarray(views["Top"], dtype=float))
        self.reset_view()
        transform = self.view.scene.transform
        center = np.asarray(camera.center, dtype=float)
        size = 0.25 * camera.scale_factor               # a quarter of the view, in scene mm

        def moved(direction):
            a, b = transform.map(center), transform.map(center + size * direction)
            return b[:3] / b[3] - a[:3] / a[3]          # px on the screen (y down), depth

        for elevation, azimuth in AXIS_VIEWS:
            camera.set_orientation(elevation, azimuth)
            d, u = moved(toward), moved(screen_up)
            # Straight at the viewer (no move on the screen, nearer), screen_up straight up.
            if abs(d[0]) + abs(d[1]) < 1.0 and d[2] < 0 and abs(u[0]) < 1.0 and u[1] < 0:
                return
        raise ValueError(f"no orientation looks from {toward}")

    def set_orthographic(self, on):
        """No perspective (fov 0): flat standard views."""
        self.view.camera.fov = 0.0 if on else PERSPECTIVE_FOV
        self.canvas.update()

    @property
    def orthographic(self):
        return self.view.camera.fov == 0.0

    # =====================================================
    # Style
    # =====================================================

    def set_visible(self, name, visible):
        self._visible[name] = bool(visible)
        self._restyle()

    def select(self, key):
        """Highlight ("datum" | "body", name) as selected; None: nothing."""
        self.selected = key
        self._restyle()

    def set_xray(self, on):
        self.xray = bool(on)
        self._restyle()

    def set_show_edges(self, on):
        self.show_edges = bool(on)
        self._restyle()

    def set_show_box(self, on):
        self.box.visible = bool(on)
        self.canvas.update()

    def _restyle(self):
        selected = set(self.selection)
        for name, visual in self._bodies.items():
            visual.set_style(
                visible=self._visible[name],
                selected=("body", name) in selected,
                xray=self.xray,
                show_edges=self.show_edges,
                faded=self.editing_sketch is not None,
            )
        for name, visual in self._datums.items():
            key = ("datum", name)
            state = "hover" if key == self.hovered else "selected" if key in selected else None
            visual.set_style(self._datum_visible.get(name, True) or name in self._forced, state)
        for name, visual in self._sketch_points.items():
            key = ("feature", name)
            state = "hover" if key == self.hovered else "selected" if key in selected else None
            visual.set_style(True, state)
        for name, visual in self._sketch_geometry.items():
            visual.set_style(self.hovered, selected, name == self.editing_sketch, self._mm_per_px())
        self._draw_faces(selected)
        self.canvas.update()

    def _draw_faces(self, selected):
        for visual, keys in ((self.face_hover, [self.hovered]), (self.face_selected, [k for k in selected])):
            triangles = [self.face_triangles(k) for k in keys if k is not None and k[0] == "face"]
            triangles = np.concatenate(triangles) if triangles else np.zeros((0, 3, 3))
            visual.visible = len(triangles) > 0
            if visual.visible:
                visual.set_data(vertices=triangles.reshape(-1, 3).astype(np.float32),
                                faces=np.arange(3 * len(triangles), dtype=np.uint32).reshape(-1, 3))
