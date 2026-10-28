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
# Hover and selection: moving the mouse (no button) over a shown datum
# highlights it green (on_hover(key)); a click selects it or a body
# (on_select(key)), a click on nothing clears the selection. Keys are
# ("datum", name) or ("body", name). set_hover / select do the same from
# the browser; a hovered datum is drawn even when hidden (as in Inventor,
# where hovering a hidden plane in the browser shows it).
#
# Picking a plane (set_pick_filter): while a command waits for a plane
# (Start 2D Sketch), only datums of the given kinds hover and click, the
# bodies are not selectable, and the `forced` datums are drawn and pickable
# even when hidden (Inventor shows the origin planes for this).
#
# Sketch mode (set_sketch, look_at): a grid on the sketch's plane (lines
# every `step`, a 1-2-5 number giving about SKETCH_GRID_LINES lines over the
# model's size), its own x (u) and y (v) axes from the sketch origin, and the
# camera looking at the plane: u to the right, v up, the normal toward the
# viewer.
#
# Sketch points (set_sketches): every sketch shows its origin as a point
# (Inventor's projected center point; all an empty sketch shows), keyed
# ("feature", name): it hovers green and a click selects the sketch, like a
# datum point (the origin's datums win where they coincide).
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
from view.picking import Picker, pick_datum

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
PICK_COLORS = ((1.0, 0.85, 0.1, 1.0), (0.2, 0.9, 1.0, 1.0))   # points A, B; black rim
PICK_LINE_COLOR = (1.0, 1.0, 1.0, 0.9)
DATUM_COLOR = (0.95, 0.7, 0.25, 1.0)              # work features: amber
DATUM_FILL_ALPHA = 0.12
HOVER_COLOR = (0.25, 1.0, 0.35, 1.0)              # pre-highlight: green
SELECTED_COLOR = (0.35, 0.65, 1.0, 1.0)           # selection: blue
DATUM_HALF_MIN = 10.0       # mm: half the side of an origin plane of an empty part
AXIS_OVERHANG = 1.2         # an axis reaches this times past the planes
SKETCH_POINT_COLOR = (0.85, 0.88, 0.95, 1.0)      # a sketch's origin point
SKETCH_GRID_COLOR = (0.55, 0.6, 0.7, 0.35)
SKETCH_AXIS_COLORS = ((1.0, 0.45, 0.4, 0.9), (0.45, 0.9, 0.45, 0.9))    # the sketch's x, y
SKETCH_GRID_LINES = 20      # about this many lines across the grid
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


def _nice_step(raw):
    """The 1-2-5 number at or above `raw` (> 0)."""
    power = 10.0 ** np.floor(np.log10(raw))
    for m in (1.0, 2.0, 5.0, 10.0):
        if m * power >= raw * (1 - 1e-9):
            return m * power
    return 10.0 * power


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

    def set_state(self, center, distance, rotation):
        matrix = np.eye(4)
        matrix[:3, :3] = np.asarray(rotation, dtype=float).reshape(3, 3)
        self._rotation = matrix
        self.center = tuple(float(v) for v in center)
        self.scale_factor = float(distance)
        self.view_changed()

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

    def set_style(self, visible, selected, xray, show_edges):
        self.mesh.visible = visible
        self.edges.visible = visible and (show_edges or selected)

        color = self.base_color
        if selected:
            color = 0.5 * color + 0.5 * np.array([1.0, 0.9, 0.4])

        self.mesh.color = (*color, XRAY_ALPHA if xray else 1.0)

        if xray:
            self.mesh.set_gl_state("translucent", depth_test=False, cull_face=False)
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
        self.canvas = scene.SceneCanvas(keys="interactive", size=canvas_size, bgcolor=BACKGROUND, show=show)
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
        self._datum_visible = {}
        self._datum_list = []           # Datum, as given
        self._datum_half = DATUM_HALF_MIN
        self.selected = None            # ("datum" | "body", name) or None
        self.hovered = None             # ("datum", name) or None
        self.on_select = None           # callback(key or None): a click selected
        self.on_hover = None            # callback(key or None): the mouse moved onto / off a datum
        self.measure = False
        self.on_escape = None           # callback() -> True if the window used the Esc key
        self._pick_kinds = None         # datum kinds a click may pick (None: anything)
        self._forced = set()            # datums drawn and pickable though hidden
        self.sketch_frame = None        # model.sketch.SketchFrame in sketch mode, else None
        self.sketch_step = None         # grid step (mm) of the shown sketch
        self.xray = False
        self.show_edges = True
        self._bounds = (-np.ones(3), np.ones(3))

        self.pick_line = scene.visuals.Line(color=PICK_LINE_COLOR, width=1.5, parent=self.view.scene)
        self.pick_marker = scene.visuals.Markers(parent=self.view.scene)
        for order, visual in enumerate((self.pick_line, self.pick_marker)):
            visual.set_gl_state("translucent", depth_test=False)
            visual.order = 20 + order
            visual.visible = False
        self.sketch_grid = scene.visuals.Line(connect="segments", color=SKETCH_GRID_COLOR, width=1.0,
                                              parent=self.view.scene)
        self.sketch_grid.set_gl_state("translucent", depth_test=True)
        self.sketch_axes = scene.visuals.Line(connect="segments", width=2.0, parent=self.view.scene)
        self.sketch_axes.set_gl_state("translucent", depth_test=True)
        self.sketch_axes.order = self.sketch_grid.order = 4
        self.sketch_grid.visible = self.sketch_axes.visible = False

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
        if event.key != keys.ESCAPE:
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
        if event.button is not None or event.is_dragging or self.measure:
            return
        key = self.datum_at(event.pos)
        if key != self.hovered:
            self.set_hover(key)
            if self.on_hover is not None:
                self.on_hover(key)

    def _mouse_press(self, event):
        self._press = np.array(event.pos[:2], float) if event.button == 1 else None

    def _mouse_release(self, event):
        press, self._press = self._press, None
        modifiers = set(event.modifiers)
        if press is None or event.button != 1 or self.view.camera.space_down \
                or modifiers - {keys.SHIFT}:
            return
        if np.linalg.norm(np.array(event.pos[:2], float) - press) >= CLICK_PX:
            return                      # a drag: rotated or panned
        if not self.measure:
            if not modifiers:
                self._click_select(self.key_at(event.pos))
            return
        pick = self.pick_at(event.pos)
        if pick is not None:
            picks = list(self.picks)
            picks[1 if keys.SHIFT in modifiers else 0] = pick
            self.set_picks(picks)

    def _click_select(self, key):
        self.select(key)
        if self.on_select is not None:
            self.on_select(key)

    def _double_click(self, event):
        if self.measure and event.button == 1 and any(self.picks) and self.pick_at(event.pos) is None:
            self.set_picks([None, None])

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
        shown = [(("datum", d.name), d) for d in self._datum_list
                 if self._datum_visible.get(d.name) or d.name in self._forced]
        shown += [(("feature", name), visual.datum) for name, visual in self._sketch_points.items()]
        if self._pick_kinds is not None:
            shown = [(key, d) for key, d in shown if d.kind in self._pick_kinds]
        if not shown:
            return None
        transform = self._transform()
        behind = np.inf
        if self._picker is not None and not self.xray:
            behind = self._picker.ray_hit(transform, pos, self._visible_bodies())
        half = {("datum", n): v.half for n, v in self._datums.items()}
        rows = [(key, d.kind, d.origin / SCENE_UNIT, d.direction, d.u, d.v, half.get(key, 0.0))
                for key, d in shown]
        hit = pick_datum(transform, pos, rows, behind)
        return hit[0] if hit is not None else None

    def key_at(self, pos):
        """What a click at `pos` selects: a datum, else a body, else None."""
        key = self.datum_at(pos)
        if key is None and self._picker is not None and self._pick_kinds is None:
            pick = self._picker.pick(self._transform(), pos, self._visible_bodies(), occlude=not self.xray)
            key = ("body", pick.body) if pick is not None else None
        return key

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
        self.view.camera.on_turn = self._update_gizmo
        self.canvas.events.resize.connect(lambda event: self._update_gizmo())
        self._update_gizmo()

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

    # =====================================================
    # Content
    # =====================================================

    def set_geometry(self, geometry, reset=True):
        """Shows the bodies; reset=False keeps the camera (a rebuild of the same model)."""
        for visual in self._bodies.values():
            visual.remove()

        self._bodies = {b.name: _BodyVisual(b, self.root) for b in geometry.bodies}
        self._visible = {name: self._visible.get(name, True) for name in self._bodies}
        if self.selected is not None and self.selected[0] == "body" and self.selected[1] not in self._bodies:
            self.selected = None
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
        if self.selected is not None and self.selected[0] == "datum" and self.selected[1] not in self._datum_visible:
            self.selected = None
        self.hovered = None
        self._build_datums()
        self._update_bounds()
        self._restyle()

    def _build_datums(self):
        for visual in self._datums.values():
            visual.remove()
        self._datums = {d.name: _DatumVisual(d, self._datum_half, self.root) for d in self._datum_list}

    def set_sketches(self, sketches):
        """[(name, model.sketch.SketchFrame)]: the sketches whose origin points are shown."""
        for visual in self._sketch_points.values():
            visual.remove()
        self._sketch_points = {
            name: _DatumVisual(Datum(name, "point", frame.origin), 0.0, self.root, SKETCH_POINT_COLOR)
            for name, frame in sketches}
        for key in ("hovered", "selected"):
            value = getattr(self, key)
            if value is not None and value[0] == "feature" and value[1] not in self._sketch_points:
                setattr(self, key, None)
        self._update_bounds()
        self._restyle()

    def _update_bounds(self):
        """What Reset view frames: the bodies and the datums."""
        points = [v.body.vertices / SCENE_UNIT for v in self._bodies.values() if len(v.body.vertices)]
        for visual in list(self._datums.values()) + list(self._sketch_points.values()):
            d = visual.datum
            center = d.origin / SCENE_UNIT
            reach = visual.half * (np.abs(d.u) + np.abs(d.v) + np.abs(d.direction) * (d.kind == "axis"))
            points.append(np.array([center - reach, center + reach]))
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

    def set_sketch(self, frame):
        """Shows the grid and axes of a sketch (model.sketch.SketchFrame), or hides them (None)."""
        self.sketch_frame = frame
        self.sketch_grid.visible = self.sketch_axes.visible = frame is not None
        if frame is None:
            self.sketch_step = None
            self.canvas.update()
            return
        lo, hi = self._bounds
        half = 0.5 * float(np.max(hi - lo)) if np.all(np.isfinite([lo, hi])) else DATUM_HALF_MIN
        half = max(half, DATUM_HALF_MIN)
        step = _nice_step(2 * half / SKETCH_GRID_LINES)
        n = int(np.ceil(half / step))
        reach = n * step
        origin = frame.origin / SCENE_UNIT
        segments = []
        for k in range(-n, n + 1):
            for along, across in ((frame.u, frame.v), (frame.v, frame.u)):
                base = origin + k * step * across
                segments += [base - reach * along, base + reach * along]
        self.sketch_grid.set_data(pos=np.array(segments, np.float32))
        axes = [origin, origin + reach * frame.u, origin, origin + reach * frame.v]
        colors = [SKETCH_AXIS_COLORS[0]] * 2 + [SKETCH_AXIS_COLORS[1]] * 2
        self.sketch_axes.set_data(pos=np.array(axes, np.float32), color=np.array(colors, np.float32))
        self.sketch_step = step
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
        for name, visual in self._bodies.items():
            visual.set_style(
                visible=self._visible[name],
                selected=(("body", name) == self.selected),
                xray=self.xray,
                show_edges=self.show_edges,
            )
        for name, visual in self._datums.items():
            key = ("datum", name)
            state = "hover" if key == self.hovered else "selected" if key == self.selected else None
            visual.set_style(self._datum_visible.get(name, True) or name in self._forced, state)
        for name, visual in self._sketch_points.items():
            key = ("feature", name)
            state = "hover" if key == self.hovered else "selected" if key == self.selected else None
            visual.set_style(True, state)
        self.canvas.update()
