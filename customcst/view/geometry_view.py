# geometry_view.py
#
# VisPy 3D view of a Geometry: shaded bodies, CAD edges, axes, bounding box.
# The scene is drawn in millimeters (bodies are stored in meters): the usual
# CAD unit, and it keeps the camera numbers human.
#
# Sheets have no inside, so they are drawn two-sided: a reversed copy of the
# triangles with its own vertices (shared vertices would average the two
# normals to zero).
#
# Section view (set_section): every body (and via) is clipped by a plane
# normal to an axis (a VisPy PlanesClipper per visual, in the body's own
# coordinates), and the cut face is drawn: a fill image of the bodies on
# the plane plus their exact outline (view/section.py), the outline a hair
# in front of the fill so it wins the depth test.
# The plane itself is light glass, so it stays visible where it cuts nothing
# or from behind: the fill image's empty pixels over the model's extent, and
# a frame-shaped sheet around it (the two never overlap: coplanar
# translucent layers z-fight into stripes). The fill is drawn after the
# bodies: its pixels write depth, and would hide bodies drawn later behind.
#
# Reference planes (set_reference_planes): a dark blue translucent square on
# each waveguide port's reference plane (the port plane moved by the Results
# tab's shift), drawn through the bodies like the ports.
#
# Picking (for measurements): a left click without a drag (the cursor moved
# less than CLICK_PX) picks a point of the model (view/picking.py: a CAD
# vertex, a point on an edge, or on a face). Two points, A and B:
#   - a click sets or moves A; Shift + click sets or moves B (Shift + drag
#     still pans, a drag rotates); a click on nothing changes nothing;
#   - a double click on nothing, or Esc, removes both;
#   - the markers (A yellow, B cyan) and the line between them are drawn
#     through the bodies; on_pick([A, B]) is called after every change.

import numpy as np

from vispy import scene
from vispy.util import keys, transforms
from vispy.visuals.filters.clipping_planes import PlanesClipper

from view.picking import Picker
from view.section import MAX_PIXELS, section_fill, section_outline

SCENE_UNIT = 1e-3      # one scene unit = 1 mm

DEFAULT_COLORS = {
    "solid": (0.62, 0.66, 0.74),
    "sheet": (0.85, 0.55, 0.25),
}
# Used when the STEP file has no color for the body.
TYPE_COLORS = {
    "pec": (0.85, 0.55, 0.25),
    "dielectric": (0.25, 0.55, 0.3),
    "port": (0.75, 0.75, 0.8),
    "waveport": (0.85, 0.3, 0.8),
}
EDGE_COLOR = (0.08, 0.08, 0.1, 1.0)
SELECTED_EDGE_COLOR = (1.0, 0.85, 0.2, 1.0)
BOX_COLOR = (0.45, 0.45, 0.5, 1.0)
XRAY_ALPHA = 0.3
GHOST_ALPHA = 0.12          # bodies while the solver's materials are shown
GHOST_GRAY = np.array([0.6, 0.6, 0.65])
MESH_COLOR = (0.55, 0.8, 1.0, 0.55)
PEC_EDGE_COLOR = (1.0, 0.95, 0.1, 1.0)            # bright yellow
HOLE_COLOR = (0.2, 1.0, 1.0, 1.0)                  # bright cyan: highlighted holes
VIA_COLOR = (0.95, 0.78, 0.25)                     # gold: vias assigned to holes
PORT_EDGE_COLOR = (1.0, 0.2, 0.9, 1.0)            # magenta
ELEMENT_EDGE_COLOR = (1.0, 1.0, 1.0, 1.0)         # white: no body or edge uses it
ELEMENT_COLOR = (1.0, 1.0, 1.0, 1.0)
DIELECTRIC_LOW = np.array([0.2, 0.45, 0.75])      # eps_r = 1: dim blue
DIELECTRIC_HIGH = np.array([0.3, 0.95, 1.0])      # eps_r >= 10: bright cyan
DOMAIN_COLOR = (0.35, 0.65, 1.0, 1.0)
SECTION_OUTLINE_DARKEN = 0.55     # outline color = body color times this
SECTION_SHEET_COLOR = (0.85, 0.9, 1.0, 0.12)      # the section plane: light glass
QUICK_PIXELS = 300           # section fill resolution while the plane is dragged
PORT_OK_COLOR = (0.2, 1.0, 0.35, 1.0)
PORT_ERROR_COLOR = (1.0, 0.25, 0.2, 1.0)
REFERENCE_FILL = (0.05, 0.1, 0.5, 0.55)           # dark blue, translucent
REFERENCE_EDGE = (0.3, 0.45, 1.0, 0.9)
PICK_COLORS = ((1.0, 0.85, 0.1, 1.0), (0.2, 0.9, 1.0, 1.0))   # points A, B; black rim
PICK_LINE_COLOR = (1.0, 1.0, 1.0, 0.9)
CLICK_PX = 4                # a press and release closer than this is a click, not a drag


def _skip_redundant_qt_swap(canvas):
    """
    Qt 6 + this AMD driver: VisPy's explicit swapBuffers() after each draw
    blocks for ~2 frames inside a QOpenGLWidget, which composites by itself.
    (Same workaround as pseudo_cst_3d/renderer.py.)
    """
    try:
        from PySide6.QtOpenGLWidgets import QOpenGLWidget
    except ImportError:
        return

    if isinstance(canvas._backend, QOpenGLWidget):
        canvas._backend._vispy_swap_buffers = lambda: None


def _perpendicular_basis(axis):
    axis = np.asarray(axis, dtype=np.float64)
    helper = np.array([1.0, 0.0, 0.0]) if abs(axis[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    e1 = np.cross(axis, helper)
    e1 /= np.linalg.norm(e1)
    return e1, np.cross(axis, e1)


def _circle_segments(center, e1, e2, radius, n=48):
    t = np.linspace(0, 2 * np.pi, n + 1)
    points = center + radius * (np.cos(t)[:, None] * e1 + np.sin(t)[:, None] * e2)
    return np.repeat(points, 2, axis=0)[1:-1]          # consecutive pairs


def box_segments(lo, hi):
    corners = np.array([[(hi if (n >> a) & 1 else lo)[a] for a in range(3)] for n in range(8)])
    pairs = [(0, 1), (1, 3), (3, 2), (2, 0), (4, 5), (5, 7), (7, 6), (6, 4),
             (0, 4), (1, 5), (2, 6), (3, 7)]
    return np.array([corners[i] for pair in pairs for i in pair], dtype=np.float32)


def cone_mesh(base, tip, radius, segments=24):
    """Triangles of a closed cone (arrow head) from base center to tip."""
    base, tip = np.asarray(base, float), np.asarray(tip, float)
    axis = tip - base
    axis /= np.linalg.norm(axis)
    helper = np.eye(3)[np.argmin(np.abs(axis))]
    u = np.cross(axis, helper)
    u /= np.linalg.norm(u)
    v = np.cross(axis, u)

    angles = np.linspace(0.0, 2.0 * np.pi, segments, endpoint=False)
    ring = base + radius * (np.cos(angles)[:, None] * u + np.sin(angles)[:, None] * v)
    vertices = np.vstack((ring, tip, base))
    n, apex, center = segments, segments, segments + 1
    faces = [(i, (i + 1) % n, apex) for i in range(n)]
    faces += [((i + 1) % n, i, center) for i in range(n)]
    return vertices.astype(np.float32), np.array(faces, dtype=np.uint32)


class TrackballCamera(scene.TurntableCamera):
    """
    CAD-style free rotation (as the middle-button orbit in SolidWorks or
    Inventor), on top of VisPy's TurntableCamera so the "up" axis mapping,
    zoom and projection stay the same.

    Orientation: a free 4x4 rotation matrix (`_rotation`), not azimuth /
    elevation. Left drag = virtual trackball (Shoemake): the press and the
    current cursor are lifted onto a sphere over the view center, the model
    turns about the axis perpendicular to both. Dragging through the middle
    tilts about a screen axis; dragging around the rim rolls about the view
    direction. The pivot is the camera center (moved by panning).

    Pan: Space + left drag or Shift + left drag (`space_down` is set by the
    view's key handlers); the motion follows the screen axes in any
    orientation.

    Matrices are VisPy's row-vector convention: camera coords
    v_cam @ A @ T(pos) @ R @ S @ T(center) = world (see
    Base3DRotationCamera._update_camera_pos); a rotation about a screen
    axis is Q in "A-space", so R_new = Q @ R.
    """

    space_down = False
    TRACKBALL_RADIUS = 0.8          # fraction of the half view size

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._rotation = None
        self._last = None
        self._pan_start = None

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
                and 1 in event.buttons and 2 not in event.buttons:
            modifiers = event.mouse_event.modifiers
            if self.space_down or keys.SHIFT in modifiers:
                self._pan(event)
            elif not modifiers:
                self._trackball(event)
            else:
                super().viewbox_mouse_event(event)
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


class _BodyVisual:
    """Mesh + edge lines of one body."""

    def __init__(self, body, parent):
        self.body = body
        self.base_color = np.array(body.color or DEFAULT_COLORS[body.kind], dtype=float)
        self.cad_color = body.color is not None

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
        # Section view: one clipper per visual (a filter belongs to one visual).
        self.clippers = [PlanesClipper(coord_system="visual") for _ in range(2)]
        self.mesh.attach(self.clippers[0])
        self.edges.attach(self.clippers[1])

    def set_clip(self, planes):
        """planes: (n, 2, 3) float32 [position, normal] in mm; (0, 2, 3) = none."""
        for clipper in self.clippers:
            clipper.clipping_planes = planes

    def set_style(self, visible, selected, xray, show_edges, ghost=False):
        self.mesh.visible = visible
        self.edges.visible = visible and (show_edges or selected) and not ghost

        color = self.base_color
        if selected:
            color = 0.5 * color + 0.5 * np.array([1.0, 0.9, 0.4])

        alpha = XRAY_ALPHA if xray else 1.0
        if ghost:                       # pale gray, so colored edges stand out
            color = 0.5 * color + 0.5 * GHOST_GRAY
            alpha = GHOST_ALPHA
        self.mesh.color = (*color, alpha)

        if xray or ghost:
            self.mesh.set_gl_state("translucent", depth_test=False, cull_face=False)
        else:
            self.mesh.set_gl_state(
                "opaque", depth_test=True, cull_face=False,
                polygon_offset_fill=True, polygon_offset=(1.0, 1.0),
            )

        self.edges.set_data(color=SELECTED_EDGE_COLOR if selected else EDGE_COLOR,
                            width=2.0 if selected else 1.0)


class GeometryView:
    """
    Owns a SceneCanvas. Bodies are addressed by name.
    Rotate: drag; zoom: wheel; pan: Shift + drag.
    """

    def __init__(self, canvas_size=(1000, 750), show=False):
        self.canvas = scene.SceneCanvas(keys="interactive", size=canvas_size,
                                        bgcolor=(0.13, 0.14, 0.17, 1.0), show=show)
        _skip_redundant_qt_swap(self.canvas)

        self.view = self.canvas.central_widget.add_view()
        self.view.camera = TrackballCamera(fov=30, elevation=25, azimuth=-50)
        # Near / far planes at distance / sqrt(d) and * sqrt(d). VisPy's
        # default d = 1e6 left too little depth precision: 15 um copper on
        # FR4 flickered through (z-fighting). 200 keeps the whole model.
        self.view.camera.depth_value = 200.0
        self._init_space_pan()

        self.root = scene.Node(parent=self.view.scene)
        self.mesh_lines = scene.visuals.Line(connect="segments", color=MESH_COLOR,
                                             width=1.0, parent=self.view.scene)
        self.mesh_lines.set_gl_state("translucent", depth_test=False)
        self.mesh_lines.visible = False
        # Solver's view of the materials on the mesh plane (thick colored edges).
        self.material_thin = scene.visuals.Line(connect="segments", width=2.5, parent=self.view.scene)
        self.material_thick = scene.visuals.Line(connect="segments", width=4.0, parent=self.view.scene)
        for visual in (self.material_thin, self.material_thick):
            visual.set_gl_state("translucent", depth_test=False)
            visual.visible = False
        self.domain_box = scene.visuals.Line(connect="segments", color=DOMAIN_COLOR,
                                             width=1.5, parent=self.view.scene)
        self.domain_box.visible = False
        self.reference_root = scene.Node(parent=self.view.scene)
        self.port_root = scene.Node(parent=self.view.scene)   # drawn last, on top
        # Highlighted holes: their outline plus a halo ring (a 0.3 mm via is
        # invisible at board scale), drawn through the bodies.
        self.hole_lines = scene.visuals.Line(connect="segments", color=HOLE_COLOR, width=2.5,
                                             parent=self.view.scene)
        self.hole_lines.set_gl_state("translucent", depth_test=False)
        self.hole_lines.visible = False
        self.box = scene.visuals.Line(connect="segments", color=BOX_COLOR, parent=self.view.scene)
        self.axes = scene.visuals.XYZAxis(width=3, parent=self.view.scene)
        # Section view: the cut face (fill image) and its outline.
        self.section_image = scene.visuals.Image(np.zeros((1, 1, 4), np.float32), interpolation="nearest",
                                                 parent=self.root)
        self.section_image.set_gl_state("translucent", depth_test=True, cull_face=False)
        self.section_image.order = 10           # after the bodies (see the header)
        self.section_image.visible = False
        self.section_lines = scene.visuals.Line(connect="segments", width=2.0, parent=self.root)
        self.section_lines.visible = False
        self.section_sheet = scene.visuals.Mesh(color=SECTION_SHEET_COLOR, parent=self.root)
        self.section_sheet.set_gl_state("translucent", depth_test=True, cull_face=False)
        self.section_sheet.order = 11
        self.section_sheet.visible = False
        self._section = None            # (axis, coordinate m, keep_below) or None
        self._types = {}

        self._bodies = {}
        self._vias = []                 # _BodyVisual of the assigned vias (not STEP bodies)
        self._visible = {}
        self.selected = None
        self.xray = False
        self.ghost = False
        self.show_edges = True
        self._bounds = (np.zeros(3), np.ones(3))

        self.pick_line = scene.visuals.Line(color=PICK_LINE_COLOR, width=1.5, parent=self.view.scene)
        self.pick_marker = scene.visuals.Markers(parent=self.view.scene)
        for order, visual in enumerate((self.pick_line, self.pick_marker)):
            visual.set_gl_state("translucent", depth_test=False)
            visual.order = 20 + order
            visual.visible = False
        self._picker = None
        self.picks = [None, None]       # points A and B: Pick or None
        self.on_pick = None             # callback([A, B])
        self._press = None

        self._init_headlight()
        self._init_click()

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

    def _init_click(self):
        self.canvas.events.mouse_press.connect(self._mouse_press)
        self.canvas.events.mouse_release.connect(self._mouse_release)
        self.canvas.events.mouse_double_click.connect(self._double_click)
        self.canvas.events.key_press.connect(self._escape)

    def _escape(self, event):
        if event.key == keys.ESCAPE and any(self.picks):
            self.set_picks([None, None])
            event.handled = True

    def _mouse_press(self, event):
        self._press = np.array(event.pos[:2], float) if event.button == 1 else None

    def _mouse_release(self, event):
        press, self._press = self._press, None
        modifiers = set(event.modifiers)
        if press is None or event.button != 1 or self.view.camera.space_down                 or modifiers - {keys.SHIFT}:
            return
        if np.linalg.norm(np.array(event.pos[:2], float) - press) >= CLICK_PX:
            return                      # a drag: rotated or panned
        pick = self.pick_at(event.pos)
        if pick is not None:
            picks = list(self.picks)
            picks[1 if keys.SHIFT in modifiers else 0] = pick
            self.set_picks(picks)

    def _double_click(self, event):
        if event.button == 1 and any(self.picks) and self.pick_at(event.pos) is None:
            self.set_picks([None, None])

    def pick_at(self, pos):
        """The Pick under canvas pixel `pos`, or None."""
        if self._picker is None or not self._bodies:
            return None
        visual = next(iter(self._bodies.values())).mesh
        transform = visual.get_transform("visual", "canvas")
        section = None
        if self._section is not None:
            axis, coordinate, keep_below = self._section
            section = (axis, coordinate / SCENE_UNIT, keep_below)
        visible = {name for name, on in self._visible.items() if on}
        return self._picker.pick(transform, pos, visible, section, occlude=not (self.xray or self.ghost))

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

    def _init_headlight(self):
        """The light follows the camera, so no side of a model stays dark."""
        self._light_eye = np.array([0.3, 0.6, 1.0, 0.0])
        self.view.scene.transform.changed.connect(lambda event: self._update_light())

    def _update_light(self):
        direction = self.view.camera.transform.map(self._light_eye)[:3]
        direction = direction / max(np.linalg.norm(direction), 1e-12)

        for visual in list(self._bodies.values()) + self._vias:
            visual.mesh.shading_filter.light_dir = -direction

    # =====================================================
    # Content
    # =====================================================

    def set_geometry(self, geometry):
        for visual in self._bodies.values():
            visual.mesh.parent = None
            visual.edges.parent = None

        self._bodies = {b.name: _BodyVisual(b, self.root) for b in geometry.bodies}
        self._visible = {name: True for name in self._bodies}
        self.selected = None
        self._picker = Picker(geometry.bodies)
        self.set_picks([None, None])

        lo, hi = (np.asarray(v) / SCENE_UNIT for v in geometry.bounds)
        self._bounds = (lo, hi)
        self.box.set_data(pos=box_segments(lo, hi))

        size = float(np.max(hi - lo)) if geometry.bodies else 1.0
        self.axes.transform = scene.transforms.STTransform(scale=(0.25 * size,) * 3)

        self._restyle()
        self._update_light()
        self._apply_section()
        self.reset_view()

    def set_up(self, axis):
        """Vertical axis of the model: 'x', 'y' or 'z' (CAD tools differ)."""
        self.view.camera.up = "+" + axis
        self.reset_view()

    def set_mesh_plane(self, grid, axis=None, coordinate=None):
        """
        Grid lines on the plane normal to `axis` at `coordinate` (meters), over
        the whole domain; grid=None hides them. Drawn on top of the bodies.
        """
        if grid is None:
            self.mesh_lines.visible = False
            self.canvas.update()
            return

        u_axis, v_axis = [a for a in range(3) if a != axis]
        us, vs = grid.lines[u_axis], grid.lines[v_axis]
        segments = []
        for u in us:
            segments += [(u, vs[0]), (u, vs[-1])]
        for v in vs:
            segments += [(us[0], v), (us[-1], v)]

        points = np.zeros((len(segments), 3))
        points[:, axis] = coordinate
        points[:, [u_axis, v_axis]] = segments
        self.mesh_lines.set_data(pos=(points / SCENE_UNIT).astype(np.float32))
        self.mesh_lines.visible = True
        self.canvas.update()

    def set_material_plane(self, materials, axis=None, index=None):
        """
        E-edges lying in the mesh plane normal to `axis` at line `index`,
        colored as the solver sees them: PEC (orange, thick), port columns
        (magenta, thick), dielectric (green, brighter = higher eps_r).
        Air edges are left to the plain mesh lines. materials=None hides.
        """
        if materials is None:
            self.material_thin.visible = self.material_thick.visible = False
            self.canvas.update()
            return

        lines = materials.grid.lines
        thin_pos, thin_col, thick_pos, thick_col = [], [], [], []

        for b in range(3):
            if b == axis:
                continue
            take = [slice(None)] * 3
            take[axis] = index
            take = tuple(take)
            pec = materials.pec[b][take]
            eps = materials.eps_r[b][take]
            in_dielectric = materials.dielectric[b][take]
            idx = [f[take] for f in np.indices(materials.pec[b].shape)]

            is_port = np.zeros(pec.shape, dtype=bool)
            is_element = np.zeros(pec.shape, dtype=bool)
            for items, mask in ((materials.ports, is_port), (materials.elements, is_element)):
                for item in items:
                    if item.axis != b:
                        continue
                    for e in item.edges:
                        if e[axis] == index:
                            mask[tuple(np.delete(e, axis))] = True

            def endpoints(mask):
                n = [i[mask] for i in idx]
                start = np.stack([lines[a][n[a]] for a in range(3)], axis=-1)
                end = start.copy()
                end[:, b] = lines[b][n[b] + 1]
                return np.stack((start, end), axis=1).reshape(-1, 3)

            for mask, color in ((is_port, PORT_EDGE_COLOR), (is_element, ELEMENT_EDGE_COLOR),
                                (pec & ~is_port, PEC_EDGE_COLOR)):
                if mask.any():
                    p = endpoints(mask)
                    thick_pos.append(p)
                    thick_col.append(np.tile(color, (len(p), 1)))

            diel = (~pec) & (~is_port) & (~is_element) & in_dielectric
            if diel.any():
                p = endpoints(diel)
                t = np.clip(np.log(np.maximum(eps[diel], 1.0)) / np.log(10.0), 0, 1)
                rgb = DIELECTRIC_LOW + t[:, None] * (DIELECTRIC_HIGH - DIELECTRIC_LOW)
                thin_pos.append(p)
                thin_col.append(np.repeat(np.c_[rgb, np.ones(len(rgb))], 2, axis=0))

        for visual, pos, col in ((self.material_thin, thin_pos, thin_col),
                                 (self.material_thick, thick_pos, thick_col)):
            if pos:
                visual.set_data(pos=(np.concatenate(pos) / SCENE_UNIT).astype(np.float32),
                                color=np.concatenate(col).astype(np.float32))
                visual.visible = True
            else:
                visual.visible = False
        self.canvas.update()

    def set_domain(self, bounds):
        """Outline of the computational domain ((lo, hi) in meters) or None."""
        if bounds is None:
            self.domain_box.visible = False
        else:
            lo, hi = (np.asarray(b) / SCENE_UNIT for b in bounds)
            self.domain_box.set_data(pos=box_segments(lo, hi))
            self.domain_box.visible = True
        self.canvas.update()

    def set_ports(self, ports, elements=()):
        """
        ports: PortGeometry list. Each valid port is an arrow - -> + along its
        axis; the active gap is the thick part. Invalid ports: a red axis.
        Drawn without depth test: the arrow lives inside the port's cone.
        """
        for child in list(self.port_root.children):
            child.parent = None

        for port in ports:
            if port.minus is None:
                continue

            a, b = port.minus / SCENE_UNIT, port.plus / SCENE_UNIT
            color = PORT_OK_COLOR if port.ok else PORT_ERROR_COLOR
            visuals = [scene.visuals.Line(
                pos=np.array([a, b], dtype=np.float32), color=color, width=2.0,
                parent=self.port_root,
            )]

            if port.ok:
                g0, g1 = port.gap_minus / SCENE_UNIT, port.gap_plus / SCENE_UNIT
                length = float(np.linalg.norm(g1 - g0))
                head = 0.3 * length
                base = g1 - head * (g1 - g0) / length
                vertices, faces = cone_mesh(base, g1, 0.25 * head)
                visuals.append(scene.visuals.Line(
                    pos=np.array([g0, base], dtype=np.float32), color=color, width=5.0,
                    parent=self.port_root,
                ))
                visuals.append(scene.visuals.Mesh(
                    vertices=vertices, faces=faces, color=color, parent=self.port_root,
                ))

            for visual in visuals:
                visual.set_gl_state("translucent", depth_test=False)

        # Lumped elements: a thick white bar across the gap (no polarity).
        for element in elements:
            if element.minus is None:
                continue
            if element.ok:
                a, b = element.gap_minus / SCENE_UNIT, element.gap_plus / SCENE_UNIT
                color, width = ELEMENT_COLOR, 6.0
            else:
                a, b = element.minus / SCENE_UNIT, element.plus / SCENE_UNIT
                color, width = PORT_ERROR_COLOR, 2.0
            bar = scene.visuals.Line(pos=np.array([a, b], dtype=np.float32), color=color,
                                     width=width, parent=self.port_root)
            bar.set_gl_state("translucent", depth_test=False)

        self.canvas.update()

    def set_reference_planes(self, planes):
        """planes: [(axis, center (m), (half size along u, along v) m)], u < v the other axes."""
        for child in list(self.reference_root.children):
            child.parent = None
        for axis, center, (hu, hv) in planes:
            u, v = [a for a in range(3) if a != axis]
            corners = np.zeros((4, 3))
            for n, (su, sv) in enumerate(((-1, -1), (1, -1), (1, 1), (-1, 1))):
                corners[n] = center
                corners[n, u] += su * hu
                corners[n, v] += sv * hv
            corners = (corners / SCENE_UNIT).astype(np.float32)
            fill = scene.visuals.Mesh(vertices=corners, faces=np.array([[0, 1, 2], [0, 2, 3]], np.uint32),
                                      color=REFERENCE_FILL, parent=self.reference_root)
            edge = scene.visuals.Line(pos=corners[[0, 1, 1, 2, 2, 3, 3, 0]], connect="segments",
                                      color=REFERENCE_EDGE, width=2.0, parent=self.reference_root)
            for visual in (fill, edge):
                visual.set_gl_state("translucent", depth_test=False, cull_face=False)
        self.canvas.update()

    def set_show_reference_planes(self, on):
        self.reference_root.visible = on
        self.canvas.update()

    def reset_view(self):
        lo, hi = self._bounds
        center = 0.5 * (lo + hi)
        size = float(np.linalg.norm(hi - lo)) or 1.0

        camera = self.view.camera
        camera.center = tuple(center)
        camera.scale_factor = 1.25 * size
        camera.set_orientation(25.0, -50.0)

    # =====================================================
    # Style
    # =====================================================

    def set_visible(self, name, visible):
        self._visible[name] = bool(visible)
        self._restyle()
        self._apply_section()

    # =====================================================
    # Section view
    # =====================================================

    def set_section(self, axis=None, coordinate=None, keep_below=True, quick=False):
        """
        Cut the model by the plane normal to `axis` at `coordinate` (m) and
        keep the half below it (keep_below=False: above); axis=None: whole.
        quick: a coarser fill image (while a slider is dragged).
        """
        self._section = None if axis is None else (int(axis), float(coordinate), bool(keep_below))
        self._apply_section(quick)

    def _apply_section(self, quick=False):
        visuals = list(self._bodies.values()) + self._vias
        if self._section is None:
            for visual in visuals:
                visual.set_clip(np.zeros((0, 2, 3), np.float32))
            self.section_image.visible = self.section_lines.visible = False
            self.section_sheet.visible = False
            self.canvas.update()
            return

        axis, coordinate, keep_below = self._section
        position = np.zeros(3)
        position[axis] = coordinate / SCENE_UNIT
        normal = np.zeros(3)
        normal[axis] = -1.0 if keep_below else 1.0
        planes = np.array([[position, normal]], dtype=np.float32)
        for visual in visuals:
            visual.set_clip(planes)

        # Dielectrics first, conductors (and the rest) over them, vias last.
        shown = [(v.body, v.base_color) for name, v in self._bodies.items() if self._visible.get(name)]
        shown.sort(key=lambda item: 0 if self._types.get(item[0].name) == "dielectric" else 1)
        shown += [(v.body, v.base_color) for v in self._vias]

        lo, hi = (np.asarray(b) * SCENE_UNIT for b in self._bounds)
        image, (u0, u1), (v0, v1) = section_fill(shown, axis, coordinate, lo, hi, keep_below,
                                                    QUICK_PIXELS if quick else MAX_PIXELS,
                                                    SECTION_SHEET_COLOR)
        u, v = [a for a in range(3) if a != axis]
        nv, nu = image.shape[:2]
        matrix = np.zeros((4, 4))
        matrix[0, u] = (u1 - u0) / nu / SCENE_UNIT
        matrix[1, v] = (v1 - v0) / nv / SCENE_UNIT
        matrix[2, axis] = 1.0
        matrix[3, u], matrix[3, v], matrix[3, axis] = u0 / SCENE_UNIT, v0 / SCENE_UNIT, coordinate / SCENE_UNIT
        matrix[3, 3] = 1.0
        self._section_sheet(axis, coordinate)
        self.section_image.set_data(image)
        self.section_image.transform = scene.transforms.MatrixTransform(matrix)
        self.section_image.visible = True

        segments, colors = [], []
        for body, color in shown:
            s = section_outline(body, axis, coordinate)
            if len(s):
                segments.append(s.reshape(-1, 3))
                rgba = (*(SECTION_OUTLINE_DARKEN * np.asarray(color)), 1.0)
                colors.append(np.tile(rgba, (len(s) * 2, 1)))
        if segments:
            pos = np.concatenate(segments) / SCENE_UNIT
            # A hair toward the removed half: in front of the fill for the depth test.
            size = float(np.max(self._bounds[1] - self._bounds[0])) or 1.0
            pos[:, axis] += (1e-4 * size) * (1.0 if keep_below else -1.0)
            self.section_lines.set_data(pos=pos.astype(np.float32),
                                        color=np.concatenate(colors).astype(np.float32))
            self.section_lines.visible = True
        else:
            self.section_lines.visible = False
        self.canvas.update()

    def _section_sheet(self, axis, coordinate):
        """
        The glass frame around the fill image: from the model's extent (the
        image's edge) out to a margin around it.
        """
        lo, hi = (np.asarray(b, dtype=float) for b in self._bounds)      # scene units
        margin = 0.05 * (float(np.max(hi - lo)) or 1.0)
        u, v = [a for a in range(3) if a != axis]
        corners = np.zeros((8, 3))
        corners[:, axis] = coordinate / SCENE_UNIT
        for start, d in ((0, margin), (4, 0.0)):                 # outer, inner rectangle
            corners[start:start + 4, u] = [lo[u] - d, hi[u] + d, hi[u] + d, lo[u] - d]
            corners[start:start + 4, v] = [lo[v] - d, lo[v] - d, hi[v] + d, hi[v] + d]
        faces = [[k, (k + 1) % 4, 4 + k] for k in range(4)] + [[(k + 1) % 4, 4 + (k + 1) % 4, 4 + k]
                                                                for k in range(4)]
        self.section_sheet.set_data(vertices=corners.astype(np.float32), faces=np.array(faces, np.uint32),
                                    color=SECTION_SHEET_COLOR)
        self.section_sheet.visible = True

    def set_vias(self, bodies):
        """The PEC cylinders of the assigned vias (geometry.holes.via_body), as gold pins."""
        for visual in self._vias:
            visual.mesh.parent = None
            visual.edges.parent = None
        self._vias = []
        for body in bodies:
            visual = _BodyVisual(body, self.root)
            visual.base_color = np.array(VIA_COLOR)
            self._vias.append(visual)
        self._restyle()
        self._update_light()
        self._apply_section()

    def highlight_holes(self, holes):
        """holes: geometry.holes.Hole list (empty: none)."""
        if not holes:
            self.hole_lines.visible = False
            self.canvas.update()
            return
        size = float(np.max(self._bounds[1] - self._bounds[0])) or 1.0
        segments = []
        for hole in holes:
            start, end = hole.start / SCENE_UNIT, hole.end / SCENE_UNIT
            r = hole.radius / SCENE_UNIT
            e1, e2 = _perpendicular_basis(hole.axis)
            for center, radius in ((start, r), (end, r), (0.5 * (start + end), max(3 * r, 0.012 * size))):
                segments.append(_circle_segments(center, e1, e2, radius))
            for angle in np.linspace(0, 2 * np.pi, 4, endpoint=False):
                offset = r * (np.cos(angle) * e1 + np.sin(angle) * e2)
                segments.append(np.array([start + offset, end + offset]))
        self.hole_lines.set_data(pos=np.concatenate(segments).astype(np.float32))
        self.hole_lines.visible = True
        self.canvas.update()

    def select(self, name):
        self.selected = name
        self._restyle()

    def set_type_colors(self, types):
        """types: {body name: object type name}; only bodies without a CAD color."""
        self._types = dict(types)
        for name, visual in self._bodies.items():
            if not visual.cad_color:
                color = TYPE_COLORS.get(types.get(name)) or DEFAULT_COLORS[visual.body.kind]
                visual.base_color = np.array(color, dtype=float)
        self._restyle()

    def set_xray(self, on):
        self.xray = bool(on)
        self._restyle()

    def set_ghost(self, on):
        """Pale see-through bodies (used while the solver's materials are shown)."""
        if self.ghost != bool(on):
            self.ghost = bool(on)
            self._restyle()

    def set_show_edges(self, on):
        self.show_edges = bool(on)
        self._restyle()

    def _restyle(self):
        for name, visual in self._bodies.items():
            visual.set_style(
                visible=self._visible[name],
                selected=(name == self.selected),
                xray=self.xray,
                show_edges=self.show_edges,
                ghost=self.ghost,
            )
        for visual in self._vias:
            visual.set_style(visible=True, selected=False, xray=self.xray,
                             show_edges=self.show_edges, ghost=self.ghost)
        self.canvas.update()
