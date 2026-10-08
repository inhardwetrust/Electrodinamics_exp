# renderer.py

import numpy as np

from vispy import app, scene

from layer_views import create_layer_view
from layers import ScalarLayer
from scene_model import DiskRegion, PointCharge, Port, RectRegion, Wire


def _skip_redundant_qt_swap(canvas):
    """
    With Qt 6, VisPy draws inside a QOpenGLWidget: it renders into an
    offscreen framebuffer and Qt composites it into the window by itself.
    VisPy still calls context.swapBuffers() after every draw; on the AMD
    driver here that blocks for ~2 display frames (measured: 22 -> 60
    draws/s once skipped). The widget does not need it, so drop it.
    """
    try:
        from PySide6.QtOpenGLWidgets import QOpenGLWidget
    except ImportError:
        return

    backend = canvas._backend

    if isinstance(backend, QOpenGLWidget):
        backend._vispy_swap_buffers = lambda: None


class ClampedPanZoomCamera(scene.PanZoomCamera):
    """PanZoomCamera whose visible height stays within [min, max] world units."""

    def __init__(self, min_view_height=None, max_view_height=None, **kwargs):
        self.min_view_height = min_view_height
        self.max_view_height = max_view_height

        # Called after every view change (zoom, pan, rect assignment, resize).
        # NOTE: camera.events.transform_change does NOT fire for PanZoomCamera:
        # it mutates its STTransform in place instead of replacing it.
        self.on_view_change = None

        super().__init__(**kwargs)

    def _update_transform(self):
        super()._update_transform()

        if self.on_view_change is not None and not self._resetting:
            self.on_view_change()

    def zoom(self, factor, center=None):
        # PanZoomCamera multiplies rect size by factor (>1 zooms out).
        # With aspect=1 only the y component is used anyway.
        factor = float(factor if np.isscalar(factor) else factor[1])
        height = float(self.rect.height)

        if height > 0.0:
            if self.min_view_height is not None:
                factor = max(factor, self.min_view_height / height)
            if self.max_view_height is not None:
                factor = min(factor, self.max_view_height / height)

        if factor == 1.0:
            return

        super().zoom(factor, center)


class VisPyRenderer:
    """
    Interactive field frontend.

    Draw order (bottom to top):
        scalar layers  -> heatmaps, in list order
        solver mesh    -> optional debug overlay
        materials      -> material regions, PML frame (simulation overlays)
        vector layers  -> arrows, in list order
        geometry       -> point-like scene objects (charges, ports)

    Field data comes only from a FieldSource; what is shown comes only from
    the layer list (layers.py). The renderer owns the camera and tells each
    layer view when the view changed (cheap) and when it settled (expensive
    work, debounced like the 100 ms delay in dipole_sim).
    """

    def __init__(
        self,
        scene_model,
        source,
        layers,
        base_step,
        initial_view_rect,
        canvas_size=(1000, 700),
        solver_mesh=None,
        show_mesh=True,
        prefer_gpu=True,
        smooth_texture=True,
        heatmap_sample_px=3,
        heatmap_margin_fraction=0.25,
        settle_delay_s=0.08,
        min_mesh_cell_px=6,
        min_view_height=None,
        max_view_height=None,
        charge_icon_diameter=0.24,
        charge_icon_min_px=12,
        overlays=(),
        show=True,
    ):
        self.scene_model = scene_model
        self.source = source
        self.base_step = float(base_step)
        self.solver_mesh = solver_mesh

        # Mesh visuals exist whenever there is a solver mesh; show_mesh is
        # only the initial state of the user switch.
        self.show_mesh = solver_mesh is not None
        self.mesh_enabled = bool(show_mesh)
        self.prefer_gpu = bool(prefer_gpu)
        self.smooth_texture = bool(smooth_texture)
        self.heatmap_sample_px = float(heatmap_sample_px)
        self.heatmap_margin_fraction = float(heatmap_margin_fraction)
        self.min_mesh_cell_px = float(min_mesh_cell_px)
        self.charge_icon_diameter = float(charge_icon_diameter)
        self.charge_icon_min_px = float(charge_icon_min_px)

        self.canvas = scene.SceneCanvas(
            keys="interactive",
            size=canvas_size,
            bgcolor="black",
            show=show,
        )

        _skip_redundant_qt_swap(self.canvas)

        self.view = self.canvas.central_widget.add_view()
        self.view.camera = ClampedPanZoomCamera(
            min_view_height=min_view_height,
            max_view_height=max_view_height,
            rect=initial_view_rect,
            aspect=1.0,
        )

        self._settle_timer = app.Timer(
            interval=float(settle_delay_s),
            connect=self._on_view_settled,
            iterations=1,
            start=False,
        )

        self.scalar_root = scene.Node(parent=self.view.scene)
        self.mesh_root = scene.Node(parent=self.view.scene)
        self.material_root = scene.Node(parent=self.view.scene)
        self.overlay_root = scene.Node(parent=self.material_root)
        self.vector_root = scene.Node(parent=self.view.scene)
        self.geometry_root = scene.Node(parent=self.view.scene)

        # Editor selection ring, drawn above everything (survives set_scene).
        self.highlight_root = scene.Node(parent=self.view.scene)
        self._highlight = None
        self._highlight_position = None

        if self.show_mesh:
            self._render_solver_mesh()

        self._render_materials()
        self._render_geometry()
        self.set_overlays(overlays)

        self.status_text = ""
        self.layer_views = []
        self.set_layers(layers)

        self.view.camera.on_view_change = self._on_view_change

    # =====================================================
    # Context for layer views
    # =====================================================

    def visible_rect(self):
        """
        World rectangle actually on screen.

        camera.rect is the REQUESTED rectangle; with aspect=1 and a widget
        whose proportions differ (e.g. docked between panels), PanZoomCamera
        shows more than that. _real_rect (set in _update_transform) is what
        is really visible.
        """
        camera = self.view.camera
        real = getattr(camera, "_real_rect", None)
        return real if real is not None else camera.rect

    def view_bounds(self):
        rect = self.visible_rect()
        return (
            float(rect.left),
            float(rect.right),
            float(rect.bottom),
            float(rect.top),
        )

    def units_per_pixel(self):
        width_px, height_px = self.view.size
        rect = self.visible_rect()

        return (
            rect.width / float(max(width_px, 1)),
            rect.height / float(max(height_px, 1)),
        )

    def view_size(self):
        return self.view.size

    # =====================================================
    # Layers
    # =====================================================

    def set_layers(self, layers):
        """Replace the whole layer list (cheap: views are lightweight)."""
        for layer_view in self.layer_views:
            layer_view.remove()

        self.layers = list(layers)
        self.layer_views = []

        for layer in self.layers:
            parent = (
                self.scalar_root
                if isinstance(layer, ScalarLayer)
                else self.vector_root
            )
            self.layer_views.append(create_layer_view(layer, self, parent))

        self._disable_depth_test(self.view.scene)
        self._update_title()

        for layer_view in self.layer_views:
            layer_view.on_view_changed()
            layer_view.on_view_settled()

        self.canvas.update()

    def _update_title(self):
        parts = []

        for layer in self.layers:
            if not layer.visible:
                continue

            if isinstance(layer, ScalarLayer):
                parts.append(
                    f"{layer.quantity} [{layer.scale}, {layer.colormap}]"
                )
            else:
                parts.append(f"{layer.quantity} arrows")

        title = "Pseudo CST - " + " + ".join(parts)

        if self.status_text:
            title += "   |   " + self.status_text

        self.canvas.title = title

    def set_status(self, text):
        """Extra text in the window title (time, play state, ...)."""
        self.status_text = text
        self._update_title()

    # =====================================================
    # Data changes
    # =====================================================

    def refresh_data(self, source=None):
        """
        The field changed (e.g. a simulation step). Layers re-read the
        source; the camera and layer configuration stay as they are.
        """
        if source is not None:
            self.source = source

        for layer_view in self.layer_views:
            layer_view.on_data_changed()

        self._update_geometry()
        self.canvas.update()

    # =====================================================
    # View changes
    # =====================================================

    def _on_view_change(self):
        for layer_view in self.layer_views:
            layer_view.on_view_changed()

        if self.show_mesh:
            self._update_mesh_visibility()

        self._update_geometry()

        # Restart: expensive work runs once the view stops moving.
        self._settle_timer.stop()
        self._settle_timer.start()

    def _on_view_settled(self, event=None):
        for layer_view in self.layer_views:
            layer_view.on_view_settled()

        self.canvas.update()

    # =====================================================
    # Solver mesh (debug overlay)
    # =====================================================

    def _render_solver_mesh(self):
        self.grid_visual = scene.visuals.Line(
            pos=self.solver_mesh.grid_segments(),
            connect="segments",
            color=(0.18, 0.18, 0.18, 1.0),
            width=1,
            parent=self.mesh_root,
        )

        d = self.solver_mesh.domain

        self.domain_border = scene.visuals.Line(
            pos=np.array([
                [d.x_min, d.y_min], [d.x_max, d.y_min],
                [d.x_max, d.y_min], [d.x_max, d.y_max],
                [d.x_max, d.y_max], [d.x_min, d.y_max],
                [d.x_min, d.y_max], [d.x_min, d.y_min],
            ], dtype=np.float32),
            connect="segments",
            color=(0.55, 0.55, 0.55, 1.0),
            width=1,
            parent=self.mesh_root,
        )

        self.domain_border.visible = self.mesh_enabled
        self._update_mesh_visibility()

    def _update_mesh_visibility(self):
        """Hide the mesh lines when cells become too small on screen."""
        cell_px = self.solver_mesh.dy / self.units_per_pixel()[1]
        self.grid_visual.visible = (
            self.mesh_enabled and cell_px >= self.min_mesh_cell_px
        )

    def set_mesh_visible(self, visible):
        """User switch for the solver mesh overlay (lines and border)."""
        if self.solver_mesh is None:
            return

        self.mesh_enabled = bool(visible)
        self.domain_border.visible = self.mesh_enabled
        self._update_mesh_visibility()
        self.canvas.update()

    def shutdown(self):
        self._settle_timer.stop()

    # =====================================================
    # Geometry
    # =====================================================

    # Material regions: dielectrics stay see-through (the field inside is
    # the interesting part), conductors are filled.
    DIELECTRIC_FILL = (0.55, 0.8, 1.0, 0.10)
    DIELECTRIC_EDGE = (0.6, 0.85, 1.0, 0.9)
    PEC_FILL = (0.62, 0.63, 0.68, 1.0)
    PEC_EDGE = (0.9, 0.9, 0.95, 1.0)

    def _render_materials(self):
        for wire in self.scene_model.get_objects(Wire):
            pts = np.array([wire.a, wire.b], np.float32)
            if np.allclose(pts[0], pts[1]):
                # A wire crossing a 3D slice plane.
                scene.visuals.Markers(pos=pts[:1], size=7, face_color=self.PEC_EDGE,
                                      edge_color=self.PEC_EDGE, parent=self.material_root)
            else:
                scene.visuals.Line(pts, color=self.PEC_EDGE, width=4, parent=self.material_root)

        for region in self.scene_model.objects:
            if not isinstance(region, (RectRegion, DiskRegion)):
                continue

            m = region.material

            if m.pec:
                fill, edge, label = self.PEC_FILL, self.PEC_EDGE, f"{m.name} (PEC)"
            else:
                fill, edge = self.DIELECTRIC_FILL, self.DIELECTRIC_EDGE
                label = f"{m.name}  \u03b5r = {m.eps_r:g}"
                if m.sigma:
                    label += f", \u03c3 = {m.sigma:g}"

            if isinstance(region, RectRegion):
                center = (
                    0.5 * (region.x_min + region.x_max),
                    0.5 * (region.y_min + region.y_max),
                )
                scene.visuals.Rectangle(
                    center=center,
                    width=region.x_max - region.x_min,
                    height=region.y_max - region.y_min,
                    color=fill,
                    border_color=edge,
                    border_width=1.5,
                    parent=self.material_root,
                )
                # Label just above the top edge, near its left end (regions
                # can be much wider than the view).
                label_pos = (max(region.x_min, -1.5) + 0.05, region.y_max + 0.08)
            else:
                center = region.center
                scene.visuals.Ellipse(
                    center=center,
                    radius=(region.radius, region.radius),
                    color=fill,
                    border_color=edge,
                    border_width=1.5,
                    parent=self.material_root,
                )
                label_pos = (center[0], center[1] + region.radius + 0.08)

            scene.visuals.Text(
                label,
                pos=label_pos,
                color=edge,
                font_size=9,
                anchor_x="left" if isinstance(region, RectRegion) else "center",
                anchor_y="bottom",
                parent=self.material_root,
            )

    def set_scene(self, scene_model):
        """Replace the drawn scene objects (e.g. a new 3D slice cross-section)."""
        self.scene_model = scene_model

        for child in list(self.material_root.children):
            if child is not self.overlay_root:
                child.parent = None
        for child in list(self.geometry_root.children):
            child.parent = None

        self._render_materials()
        self._render_geometry()
        self._disable_depth_test(self.material_root)
        self._disable_depth_test(self.geometry_root)
        self.canvas.update()

    def set_solver_mesh(self, mesh):
        """Replace the solver mesh overlay (keeps the user's on/off switch)."""
        for child in list(self.mesh_root.children):
            child.parent = None

        self.solver_mesh = mesh
        self.show_mesh = mesh is not None

        if self.show_mesh:
            self._render_solver_mesh()
            self._disable_depth_test(self.mesh_root)

        self.canvas.update()

    def set_overlays(self, overlays):
        """Replace simulation overlays (e.g. the PML frame)."""
        for child in list(self.overlay_root.children):
            child.parent = None

        for overlay in overlays:
            if overlay.get("kind") != "frame":
                continue

            ox0, oy0, ox1, oy1 = overlay["outer"]
            ix0, iy0, ix1, iy1 = overlay["inner"]
            shade = (0.0, 0.0, 0.0, 0.45)

            # Four bands between the outer and inner rectangles.
            for x0, y0, x1, y1 in (
                (ox0, oy0, ox1, iy0),     # bottom
                (ox0, iy1, ox1, oy1),     # top
                (ox0, iy0, ix0, iy1),     # left
                (ix1, iy0, ox1, iy1),     # right
            ):
                if x1 > x0 and y1 > y0:
                    scene.visuals.Rectangle(
                        center=(0.5 * (x0 + x1), 0.5 * (y0 + y1)),
                        width=x1 - x0,
                        height=y1 - y0,
                        color=shade,
                        parent=self.overlay_root,
                    )

            scene.visuals.Line(
                pos=np.array([
                    [ix0, iy0], [ix1, iy0], [ix1, iy1], [ix0, iy1], [ix0, iy0],
                ], dtype=np.float32),
                color=(1.0, 1.0, 1.0, 0.35),
                width=1,
                parent=self.overlay_root,
            )

            scene.visuals.Text(
                overlay.get("label", ""),
                pos=(ix0 + 0.05, iy1 + 0.5 * (oy1 - iy1)),
                color=(1.0, 1.0, 1.0, 0.6),
                font_size=8,
                anchor_x="left",
                anchor_y="center",
                parent=self.overlay_root,
            )

        self._disable_depth_test(self.overlay_root)
        self.canvas.update()

    # Sign glyph size relative to the marker diameter (was 20 pt on 42 px).
    CHARGE_FONT_PER_MARKER_PX = 20.0 / 42.0

    PORT_COLOR = (1.0, 0.62, 0.1, 1.0)

    def _render_geometry(self):
        """
        Charge icons are sized in WORLD units, so they scale with zoom like
        the rest of the scene; charge_icon_min_px keeps them visible when
        zoomed far out. Position, size and sign are refreshed in
        _update_geometry() (a simulation may change charges over time).
        """
        self._charge_icons = []

        for charge in self.scene_model.get_objects(PointCharge):
            marker = scene.visuals.Markers(parent=self.geometry_root)

            text = scene.visuals.Text(
                "+",
                pos=charge.position,
                anchor_x="center",
                anchor_y="center",
                parent=self.geometry_root,
            )

            self._charge_icons.append((charge, marker, text))

        # Ports: static segment with end rings (fixed screen size).
        for port in self.scene_model.get_objects(Port):
            ends = np.asarray([port.a, port.b], dtype=np.float32)

            scene.visuals.Line(
                pos=ends,
                color=self.PORT_COLOR,
                width=3,
                parent=self.geometry_root,
            )

            # Hollow rings: charge piling up at the port ends (div E) must
            # stay visible underneath.
            scene.visuals.Markers(
                pos=ends,
                size=11,
                face_color=(0.0, 0.0, 0.0, 0.0),
                edge_color=self.PORT_COLOR,
                edge_width=2,
                parent=self.geometry_root,
            )

        self._update_geometry()

    def _update_geometry(self):
        size_px = max(
            self.charge_icon_diameter / self.units_per_pixel()[1],
            self.charge_icon_min_px,
        )

        # Outline grows with the icon, but never below 1 px.
        edge_px = max(1.0, size_px * 2.0 / 42.0)

        for charge, marker, text in self._charge_icons:
            is_positive = charge.charge >= 0.0

            marker.set_data(
                pos=np.asarray([charge.position], dtype=np.float32),
                size=size_px,
                face_color="white" if is_positive else (0.22, 0.22, 0.22, 1.0),
                edge_color="black",
                edge_width=edge_px,
            )

            text.text = "+" if is_positive else "−"
            text.color = "black" if is_positive else "white"
            text.pos = charge.position
            text.font_size = size_px * self.CHARGE_FONT_PER_MARKER_PX

        self._update_highlight(size_px)

    # =====================================================
    # Editing support
    # =====================================================

    HIGHLIGHT_COLOR = (1.0, 0.85, 0.1, 1.0)

    def screen_to_world(self, pos):
        """Canvas pixel position -> world (x, y)."""
        transform = self.canvas.scene.node_transform(self.view.scene)
        x, y = transform.map(np.asarray(pos[:2], dtype=np.float64))[:2]
        return float(x), float(y)

    def charge_icon_radius_world(self):
        """Radius of the drawn charge icon in world units (for hit tests)."""
        upp = self.units_per_pixel()[1]
        size_px = max(self.charge_icon_diameter / upp, self.charge_icon_min_px)
        return 0.5 * size_px * upp

    def set_highlight(self, position):
        """Ring around the selected object; None hides it."""
        self._highlight_position = None if position is None else tuple(position)

        if self._highlight is None:
            self._highlight = scene.visuals.Markers(parent=self.highlight_root)
            self._highlight.update_gl_state(depth_test=False)

        upp = self.units_per_pixel()[1]
        self._update_highlight(max(self.charge_icon_diameter / upp, self.charge_icon_min_px))
        self.canvas.update()

    def _update_highlight(self, size_px):
        if self._highlight is None:
            return

        if self._highlight_position is None:
            self._highlight.visible = False
            return

        self._highlight.visible = True
        self._highlight.set_data(
            pos=np.asarray([self._highlight_position], dtype=np.float32),
            size=size_px + 12,
            face_color=(0.0, 0.0, 0.0, 0.0),
            edge_color=self.HIGHLIGHT_COLOR,
            edge_width=2.5,
        )

    # =====================================================
    # Helpers
    # =====================================================

    @classmethod
    def _disable_depth_test(cls, node):
        """
        Everything here is a flat 2D layer at z=0, ordered by draw order.

        With depth testing, the first visual drawn at a pixel writes depth=0
        and every later one there fails GL_LESS: heatmap hid mesh/arrow shafts,
        mesh lines cut arrow shafts into dashes and covered charge markers.
        """
        if hasattr(node, "update_gl_state"):
            node.update_gl_state(depth_test=False)

        for child in node.children:
            cls._disable_depth_test(child)

    def run(self):
        app.run()
