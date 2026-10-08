# renderer3d.py
#
# One 3D view: the slice plane is drawn INSIDE the rotatable 3D scene,
# together with the domain, PML and scene objects (later: STEP bodies).
#
# The plane's content is produced by the same 2D layer views as in the 2D
# renderer (heatmap, three-level arrows, through-plane markers). They are
# children of a plane node whose transform maps plane coordinates (u, v, 0)
# into 3D, and they see a fixed "virtual view" of the whole plane:
# view_bounds = plane extent, PLANE_VIRTUAL_PX virtual pixels along its
# height. So arrows keep their fixed length (now in plane units) and their
# spacing, independent of the field value - and the 3D camera can rotate
# and zoom without touching the layer contents at all.

import numpy as np

from vispy import app, scene
from vispy.visuals.transforms import MatrixTransform

from layer_views import create_layer_view
from layers import ScalarLayer
from renderer import _skip_redundant_qt_swap
from scene3d import BoxRegion, Port3D, SphereRegion
from scene_model import DiskRegion, Port, RectRegion

BOX_EDGES = [
    (0, 1), (1, 3), (3, 2), (2, 0),
    (4, 5), (5, 7), (7, 6), (6, 4),
    (0, 4), (1, 5), (2, 6), (3, 7),
]


def box_segments(lo, hi):
    lo, hi = np.asarray(lo, float), np.asarray(hi, float)
    corners = np.array([
        [(hi if (n >> a) & 1 else lo)[a] for a in range(3)]
        for n in range(8)
    ])
    return np.array([corners[i] for edge in BOX_EDGES for i in edge], dtype=np.float32)


def rect_outline(x0, y0, x1, y1):
    return np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]], dtype=np.float32)


class VisPyRenderer3D:
    """
    Same context interface as the 2D renderer for layer views
    (source, base_step, prefer_gpu, heatmap_*, view_bounds(),
    units_per_pixel(), view_size()), plus the plane and the 3D scene.

    Draw order (scene graph order):
        plane heatmaps      depth tested: hide what is behind the plane
        3D geometry         depth tested
        plane overlays      on top of the plane: mesh, PML frame, cuts
        plane arrows        on top of the plane (no z-fighting with it)
    """

    PLANE_VIRTUAL_PX = 700.0

    PORT_COLOR = (1.0, 0.62, 0.1, 1.0)
    DIELECTRIC_EDGE = (0.6, 0.85, 1.0, 0.9)
    PEC_EDGE = (0.85, 0.85, 0.92, 1.0)
    PLANE_EDGE = (0.45, 0.85, 1.0, 1.0)

    def __init__(
        self,
        scene3d,
        bounds,
        plane,
        source,
        layers,
        base_step,
        pml_inner=None,
        plane_scene=None,
        plane_mesh=None,
        overlays=(),
        show_mesh=True,
        heatmap_sample_px=3,
        settle_delay_s=0.08,
        canvas_size=(1000, 700),
        show=True,
    ):
        self.scene3d = scene3d
        self.source = source
        self.base_step = float(base_step)

        # Layer-view context (see layer_views.py).
        self.prefer_gpu = True
        self.smooth_texture = True
        self.heatmap_sample_px = float(heatmap_sample_px)
        self.heatmap_margin_fraction = 0.0      # the virtual view never moves

        self.mesh_enabled = bool(show_mesh)
        self.solver_mesh = None
        self.grid_visual = None
        self.status_text = ""

        self.canvas = scene.SceneCanvas(
            keys="interactive", size=canvas_size, bgcolor="#202020", show=show,
        )
        _skip_redundant_qt_swap(self.canvas)

        self.view = self.canvas.central_widget.add_view()
        self._bounds = tuple(np.asarray(b, float) for b in bounds)
        self.view.camera = scene.TurntableCamera(fov=35)
        self.reset_view()

        root = self.view.scene

        # Plane content lives in plane coordinates (u, v, 0).
        self.plane_node = scene.Node(parent=root)
        self.scalar_root = scene.Node(parent=self.plane_node)

        self.geometry_root = scene.Node(parent=root)
        self._render_geometry(pml_inner)

        self.plane_overlay_node = scene.Node(parent=root)
        self.mesh_root = scene.Node(parent=self.plane_overlay_node)
        self.overlay_root = scene.Node(parent=self.plane_overlay_node)
        self.cut_root = scene.Node(parent=self.plane_overlay_node)

        self.plane_vector_node = scene.Node(parent=root)
        self.vector_root = scene.Node(parent=self.plane_vector_node)

        self.plane_edge = scene.visuals.Line(color=self.PLANE_EDGE, width=2, parent=self.plane_overlay_node)

        self._settle_timer = app.Timer(
            interval=float(settle_delay_s), connect=self._on_settled,
            iterations=1, start=False,
        )

        self.layer_views = []
        self.layers = []
        self.set_plane(plane, plane_scene, plane_mesh, overlays)
        self.set_layers(layers)

    # =====================================================
    # Layer-view context: a fixed virtual view of the whole plane
    # =====================================================

    def view_bounds(self):
        return self._plane_extent

    def units_per_pixel(self):
        x0, x1, y0, y1 = self._plane_extent
        upp = (y1 - y0) / self.PLANE_VIRTUAL_PX
        return upp, upp

    def view_size(self):
        x0, x1, y0, y1 = self._plane_extent
        upp = self.units_per_pixel()[1]
        return (x1 - x0) / upp, (y1 - y0) / upp

    # =====================================================
    # Camera
    # =====================================================

    def reset_view(self):
        lo, hi = self._bounds
        size = float(np.max(hi - lo))
        cam = self.view.camera
        cam.center = tuple(0.5 * (lo + hi))
        cam.elevation = 22.0
        cam.azimuth = -55.0
        cam.distance = 3.0 * size

    # =====================================================
    # Plane
    # =====================================================

    def set_plane(self, plane, plane_scene=None, plane_mesh=None, overlays=()):
        """Move the slice plane; plane_scene / mesh / overlays are its 2D content."""
        self.plane = plane
        u0, u1 = plane.axis_range(plane.u_axis)
        v0, v1 = plane.axis_range(plane.v_axis)
        self._plane_extent = (u0, u1, v0, v1)

        # (u, v, w) in plane coordinates -> world, row-vector convention.
        ui, vi, ni = ("xyz".index(a) for a in (plane.u_axis, plane.v_axis, plane.normal))
        m = np.zeros((4, 4))
        m[0, ui] = 1.0
        m[1, vi] = 1.0
        m[2, ni] = 1.0
        m[3, ni] = plane.position
        m[3, 3] = 1.0

        for node in (self.plane_node, self.plane_overlay_node, self.plane_vector_node):
            transform = MatrixTransform()
            transform.matrix = m
            node.transform = transform

        self.plane_edge.set_data(pos=rect_outline(u0, v0, u1, v1))

        if plane_mesh is not None:
            self.set_solver_mesh(plane_mesh)
        if plane_scene is not None:
            self.set_scene(plane_scene)
        self.set_overlays(overlays)
        self.canvas.update()

    # =====================================================
    # Layers (same views as in 2D)
    # =====================================================

    def set_layers(self, layers):
        for layer_view in self.layer_views:
            layer_view.remove()

        self.layers = list(layers)
        self.layer_views = []

        for layer in self.layers:
            parent = self.scalar_root if isinstance(layer, ScalarLayer) else self.vector_root
            self.layer_views.append(create_layer_view(layer, self, parent))

        for layer_view in self.layer_views:
            layer_view.on_view_changed()
            layer_view.on_view_settled()

        self._apply_depth_rules()
        self._update_title()
        self.canvas.update()

    def refresh_data(self, source=None):
        if source is not None:
            self.source = source

        for layer_view in self.layer_views:
            layer_view.on_data_changed()

        self._apply_depth_rules()
        self.canvas.update()

    def _on_settled(self, event=None):
        for layer_view in self.layer_views:
            layer_view.on_view_settled()
        self.canvas.update()

    def _apply_depth_rules(self):
        def walk(node, depth_test):
            if hasattr(node, "update_gl_state"):
                node.update_gl_state(depth_test=depth_test)
            for child in node.children:
                walk(child, depth_test)

        walk(self.scalar_root, True)            # the plane occludes / is occluded
        walk(self.plane_overlay_node, False)    # drawn on the plane
        walk(self.plane_vector_node, False)     # arrows always readable

    # =====================================================
    # 3D geometry
    # =====================================================

    def _render_geometry(self, pml_inner):
        lo, hi = self._bounds
        root = self.geometry_root

        scene.visuals.Line(box_segments(lo, hi), connect="segments",
                           color=(0.75, 0.75, 0.75, 1.0), parent=root)
        if pml_inner is not None:
            scene.visuals.Line(box_segments(*pml_inner), connect="segments",
                               color=(0.5, 0.5, 0.5, 0.6), parent=root)

        for obj in self.scene3d.objects:
            if isinstance(obj, Port3D):
                scene.visuals.Line(np.array([obj.a, obj.b], np.float32), color=self.PORT_COLOR,
                                   width=4, parent=root)
            elif isinstance(obj, BoxRegion):
                color = self.PEC_EDGE if obj.material.pec else self.DIELECTRIC_EDGE
                scene.visuals.Line(box_segments(obj.lo, obj.hi), connect="segments",
                                   color=color, width=2, parent=root)
            elif isinstance(obj, SphereRegion):
                sphere = scene.visuals.Sphere(
                    radius=obj.radius, rows=12, cols=24, method="latitude",
                    color=(0.55, 0.8, 1.0, 0.15), edge_color=self.DIELECTRIC_EDGE, parent=root,
                )
                sphere.transform = scene.transforms.STTransform(translate=obj.center)

        size = float(np.max(hi - lo))
        colors = ((1.0, 0.35, 0.35, 1.0), (0.4, 1.0, 0.4, 1.0), (0.45, 0.6, 1.0, 1.0))
        for a, name in enumerate("xyz"):
            tip = lo.copy()
            tip[a] += 0.25 * size
            scene.visuals.Line(np.array([lo, tip], np.float32), color=colors[a], width=3, parent=root)
            scene.visuals.Text(name, pos=tip, color=colors[a], font_size=12, parent=root)

    # =====================================================
    # Plane overlays: solver mesh, PML frame, cross-sections
    # =====================================================

    def set_solver_mesh(self, mesh):
        for child in list(self.mesh_root.children):
            child.parent = None

        self.solver_mesh = mesh
        if mesh is not None:
            self.grid_visual = scene.visuals.Line(
                pos=mesh.grid_segments(), connect="segments",
                color=(0.15, 0.15, 0.15, 0.6), width=1, parent=self.mesh_root,
            )
            self.grid_visual.visible = self.mesh_enabled
        self._apply_depth_rules()

    def set_mesh_visible(self, visible):
        self.mesh_enabled = bool(visible)
        if self.solver_mesh is not None:
            self.grid_visual.visible = self.mesh_enabled
        self.canvas.update()

    def set_overlays(self, overlays):
        for child in list(self.overlay_root.children):
            child.parent = None

        for overlay in overlays:
            if overlay.get("kind") != "frame":
                continue
            ox0, oy0, ox1, oy1 = overlay["outer"]
            ix0, iy0, ix1, iy1 = overlay["inner"]

            for x0, y0, x1, y1 in (
                (ox0, oy0, ox1, iy0), (ox0, iy1, ox1, oy1),
                (ox0, iy0, ix0, iy1), (ix1, iy0, ox1, iy1),
            ):
                if x1 > x0 and y1 > y0:
                    scene.visuals.Rectangle(
                        center=(0.5 * (x0 + x1), 0.5 * (y0 + y1)),
                        width=x1 - x0, height=y1 - y0,
                        color=(0.0, 0.0, 0.0, 0.45), parent=self.overlay_root,
                    )
            if ix1 > ix0 and iy1 > iy0:
                scene.visuals.Line(rect_outline(ix0, iy0, ix1, iy1), color=(1, 1, 1, 0.35),
                                   parent=self.overlay_root)
        self._apply_depth_rules()

    def set_scene(self, plane_scene):
        """Where the plane cuts the scene objects (outlines on the plane)."""
        for child in list(self.cut_root.children):
            child.parent = None

        for obj in plane_scene.objects:
            if isinstance(obj, RectRegion):
                color = self.PEC_EDGE if obj.material.pec else self.DIELECTRIC_EDGE
                scene.visuals.Line(rect_outline(obj.x_min, obj.y_min, obj.x_max, obj.y_max),
                                   color=color, width=2, parent=self.cut_root)
            elif isinstance(obj, DiskRegion):
                t = np.linspace(0.0, 2.0 * np.pi, 64)
                pts = np.column_stack((obj.center[0] + obj.radius * np.cos(t),
                                       obj.center[1] + obj.radius * np.sin(t))).astype(np.float32)
                scene.visuals.Line(pts, color=self.DIELECTRIC_EDGE, width=2, parent=self.cut_root)
            elif isinstance(obj, Port):
                scene.visuals.Markers(pos=np.array([obj.a, obj.b], np.float32), size=9,
                                      face_color=(0, 0, 0, 0), edge_color=self.PORT_COLOR,
                                      edge_width=2, parent=self.cut_root)
        self._apply_depth_rules()

    # =====================================================
    # Misc (same API as the 2D renderer where the controller uses it)
    # =====================================================

    @property
    def show_mesh(self):
        return self.solver_mesh is not None

    def _update_title(self):
        parts = [
            f"{layer.quantity}" + (" arrows" if not isinstance(layer, ScalarLayer) else "")
            for layer in self.layers if layer.visible
        ]
        title = "Pseudo CST 3D - " + " + ".join(parts)
        if self.status_text:
            title += "   |   " + self.status_text
        self.canvas.title = title

    def set_status(self, text):
        self.status_text = text
        self._update_title()

    def shutdown(self):
        self._settle_timer.stop()

    def run(self):
        app.run()
