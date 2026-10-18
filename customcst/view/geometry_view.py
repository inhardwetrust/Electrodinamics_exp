# geometry_view.py
#
# VisPy 3D view of a Geometry: shaded bodies, CAD edges, axes, bounding box.
# The scene is drawn in millimeters (bodies are stored in meters): the usual
# CAD unit, and it keeps the camera numbers human.
#
# Sheets have no inside, so they are drawn two-sided: a reversed copy of the
# triangles with its own vertices (shared vertices would average the two
# normals to zero).

import numpy as np

from vispy import scene
from vispy.util import keys, transforms

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
PORT_OK_COLOR = (0.2, 1.0, 0.35, 1.0)
PORT_ERROR_COLOR = (1.0, 0.25, 0.2, 1.0)


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
        self.port_root = scene.Node(parent=self.view.scene)   # drawn last, on top
        # Highlighted holes: their outline plus a halo ring (a 0.3 mm via is
        # invisible at board scale), drawn through the bodies.
        self.hole_lines = scene.visuals.Line(connect="segments", color=HOLE_COLOR, width=2.5,
                                             parent=self.view.scene)
        self.hole_lines.set_gl_state("translucent", depth_test=False)
        self.hole_lines.visible = False
        self.box = scene.visuals.Line(connect="segments", color=BOX_COLOR, parent=self.view.scene)
        self.axes = scene.visuals.XYZAxis(width=3, parent=self.view.scene)

        self._bodies = {}
        self._vias = []                 # _BodyVisual of the assigned vias (not STEP bodies)
        self._visible = {}
        self.selected = None
        self.xray = False
        self.ghost = False
        self.show_edges = True
        self._bounds = (np.zeros(3), np.ones(3))

        self._init_headlight()

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

        lo, hi = (np.asarray(v) / SCENE_UNIT for v in geometry.bounds)
        self._bounds = (lo, hi)
        self.box.set_data(pos=box_segments(lo, hi))

        size = float(np.max(hi - lo)) if geometry.bodies else 1.0
        self.axes.transform = scene.transforms.STTransform(scale=(0.25 * size,) * 3)

        self._restyle()
        self._update_light()
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
