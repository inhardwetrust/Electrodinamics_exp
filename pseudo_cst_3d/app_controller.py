# app_controller.py

import dataclasses

from vispy import app

from domain import SimulationDomain2D
from fdtd import FdtdTE2D
from fdtd3d import FdtdYee3D
from field import AnalyticPointChargeField, grid_source_from_point_charge_result
from layers import ClimPolicy, ScalarLayer, VectorLayer
from params import with_value
from mesh import RectangularMesh2D
from renderer import VisPyRenderer
from renderer3d import VisPyRenderer3D
from scene3d import BoxRegion, Port3D
from scene_model import Material, PointCharge, Port, RectRegion, SceneModel
from simulation import OscillatingDipoleSimulation
from slicing import SlicePlane, SliceSource, slice_frame, slice_scene
from solver import PointChargeFieldSolver


class AppController:
    """
    Pseudo-CST demo.

    Separation of concerns:

        Solver mesh / domain:
            defined by the MODEL, never by the camera.

        FieldSource (field.py):
            the only interface between field data and the frontend:
            named quantities ("E", "|E|", "Ex", "Ey", "V", ...).
              "analytic" -> exact field anywhere, heatmap on the GPU
              "grid"     -> solver result on its fixed mesh, interpolated;
                            undefined (transparent) outside the domain
              "oscillating" -> time-stepping demo (Simulation contract):
                            quasi-static dipole q(t) = q0 cos(wt) on the
                            "grid" mesh; heatmap via native-grid texture
              "fdtd"     -> 2D TE FDTD (Ex, Ey, Hz) on a Yee grid, driven
                            by a port; real wave propagation

        Layers (layers.py):
            what to show and how: quantity + scale + palette + clim policy.

    Keys (until the Qt panel exists):
        Q     -> cycle the heatmap quantity
        V     -> cycle the arrow quantity (E, S = Poynting in "fdtd")
        Space -> play / pause            (simulations)
        N     -> one step while paused
        R     -> reset to t = 0
        + / - -> steps per frame x2 / /2
    """

    CANVAS_SIZE = (1000, 700)

    INITIAL_VIEW_HEIGHT = 4.0

    # "analytic", "grid", "oscillating" or "fdtd" (see class docstring).
    FIELD_MODE = "fdtd"

    # "fdtd" mode (normalized units: c = 1, wavelength = 1 / frequency).
    FDTD_FREQUENCY = 1.0
    FDTD_CELLS_PER_WAVELENGTH = 20      # >= ~15 keeps numerical dispersion low
    FDTD_WAVEFORM = "sine"              # "sine" | "gaussian" | "modulated_gaussian"
    FDTD_COURANT = 0.5
    FDTD_DOMAIN_SCALE = 2.0             # domain size relative to the initial view
    FDTD_PORT = ((-0.25, 0.0), (0.25, 0.0))   # half-wave dipole at f = 1
    FDTD_STEPS_PER_FRAME = 2

    # Arrow strength levels (fraction of the peak field in view):
    # >= weak -> full arrow, >= zero -> short faded arrow, below -> ring
    # (or dot / cross when the field crosses a 3D slice plane).
    ARROW_WEAK_FRACTION = 0.25

    # Arrow layout (screen px in 2D / flat views, virtual plane px in 3D).
    ARROW_SPACING_PX = 20
    ARROW_LENGTH_PX = 10
    ARROW_ZERO_FRACTION = 0.05
    FDTD_BOUNDARY = "pml"               # "pml" | "mur"
    FDTD_PML_CELLS = 12                 # ~ -94 dB reflection at 20 cells/wavelength

    # "fdtd3d*" modes: cube [-half, half]^3 (PML included). Cost ~ N^3:
    # 60^3 cells ~ 11 ms/step in NumPy.
    FDTD3D_HALF_SIZE = 2.0
    FDTD3D_CELLS_PER_WAVELENGTH = 15
    FDTD3D_COURANT = 0.5                # 3D limit: 1/sqrt(3) = 0.577
    FDTD3D_PML_CELLS = 10
    FDTD3D_PORT = ((0.0, 0.0, -0.25), (0.0, 0.0, 0.25))   # half-wave z-dipole
    FDTD3D_STEPS_PER_FRAME = 1

    # Initial slice: normal axis and position. y = 0 contains the dipole
    # (E in the plane, H through it); z = 0 is the equatorial plane.
    SLICE_NORMAL = "y"
    SLICE_POSITION = 0.0

    # Plane mesh lines in the 3D view (off: they clutter a rotated plane).
    SHOW_MESH_3D = False

    # Simulation playback: frames per second and solver steps per frame.
    SIM_FPS = 60
    SIM_STEPS_PER_FRAME = 1
    SIM_START_PLAYING = False          # start paused; Space / Play to run

    # "oscillating" mode: period in simulation time units.
    OSCILLATION_PERIOD = 4.0

    # Color limits of time-varying fields: peak-hold with this decay/frame.
    RUNNING_CLIM_DECAY = 0.995

    # Solver resolution; also the base step of all display lattices.
    SOLVER_CELLS_PER_INITIAL_VIEW_HEIGHT = 40

    # "grid" mode only: fixed solver domain relative to the initial view.
    SOLVER_DOMAIN_SCALE = 4.0

    # Zoom limits relative to the initial view. Display cost does not grow
    # with zoom-out, so these are just sane bounds, not performance limits.
    MIN_ZOOM = 0.1
    MAX_ZOOM = 8.0

    SHOW_MESH = True  # solver mesh overlay, "grid" mode only

    # Evaluate heatmaps per pixel in a shader when the source supports it.
    PREFER_GPU = True

    # CPU heatmap texture ("grid" mode, or PREFER_GPU = False).
    SMOOTH_TEXTURE = True
    HEATMAP_SAMPLE_PX = 3

    # Expensive per-layer work runs this long after zoom/pan stops.
    SETTLE_DELAY_S = 0.08

    # Hide the solver mesh overlay once cells get smaller than this on screen.
    MIN_MESH_CELL_PX = 12

    # Charge icon diameter in WORLD units: scales with zoom like the scene.
    # 0.24 = the old fixed 42 px at the initial zoom (4.0 units / 700 px).
    CHARGE_ICON_DIAMETER = 0.24

    # ...but never smaller than this on screen when zoomed far out.
    CHARGE_ICON_MIN_PX = 12

    # symlog linear range for signed quantities (field units, k = 1).
    SIGNED_LINTHRESH = 0.05

    # symlog linear range for charge densities (div E, div D).
    CHARGE_LINTHRESH = 0.05

    FIELD_MODES = (
        "analytic", "grid", "oscillating",
        "fdtd", "fdtd_slab", "fdtd_reflector",
        "fdtd3d", "fdtd3d_reflector",
    )

    def __init__(self, field_mode=None, embedded=False):
        """
        field_mode: one of FIELD_MODES (default: FIELD_MODE).
        embedded:   True -> the canvas is not shown as its own window; the
                    caller places renderer.canvas.native into its UI.
        """
        self.field_mode = field_mode or self.FIELD_MODE

        if self.field_mode not in self.FIELD_MODES:
            raise ValueError(f"Unknown field mode: {self.field_mode!r}")

        self.is_3d = self.field_mode.startswith("fdtd3d")
        self.is_fdtd = self.field_mode.startswith("fdtd") and not self.is_3d

        # Callbacks fn(kind), kind in {"status", "playback", "layers",
        # "simulation", "slice", "view"}; "status" fires every frame, the
        # others on change;
        # lets a UI follow changes made elsewhere (keys on the canvas, ...).
        self._listeners = []

        canvas_width, canvas_height = self.CANVAS_SIZE
        viewport_aspect = canvas_width / canvas_height

        initial_view_height = self.INITIAL_VIEW_HEIGHT
        initial_view_width = initial_view_height * viewport_aspect

        self.initial_view_rect = (
            -initial_view_width / 2.0,
            -initial_view_height / 2.0,
            initial_view_width,
            initial_view_height,
        )

        self.cell_size = (
            initial_view_height
            / self.SOLVER_CELLS_PER_INITIAL_VIEW_HEIGHT
        )

        softening_radius = 0.20 * self.cell_size

        # -------------------------------------------------
        # Physical scene
        # -------------------------------------------------

        self.scene_model = SceneModel()

        if self.is_3d:
            a, b = self.FDTD3D_PORT
            self.scene_model.add(Port3D(a=a, b=b, name="port 1"))

            for region in self._fdtd3d_scene_regions():
                self.scene_model.add(region)

        elif self.is_fdtd:
            a, b = self.FDTD_PORT
            self.scene_model.add(Port(a=a, b=b, name="port 1"))

            for region in self._fdtd_scene_regions():
                self.scene_model.add(region)
        else:
            self.scene_model.add(
                PointCharge(
                    position=(-1.0, 0.0),
                    charge=+1.0,
                )
            )

            self.scene_model.add(
                PointCharge(
                    position=(1.0, 0.0),
                    charge=-1.0,
                )
            )

        # -------------------------------------------------
        # Field source
        # -------------------------------------------------

        self.mesh = None
        self.simulation = None
        self.slice_plane = None

        if self.field_mode == "analytic":
            self.source = AnalyticPointChargeField(
                scene_model=self.scene_model,
                k=1.0,
                softening_radius=softening_radius,
            )

        elif self.field_mode in ("grid", "oscillating"):
            domain = SimulationDomain2D.centered(
                width=initial_view_width * self.SOLVER_DOMAIN_SCALE,
                height=initial_view_height * self.SOLVER_DOMAIN_SCALE,
                center=(0.0, 0.0),
            )

            self.mesh = RectangularMesh2D(
                domain=domain,
                nx=round(domain.width / self.cell_size),
                ny=round(domain.height / self.cell_size),
            )

            solver = PointChargeFieldSolver(
                k=1.0,
                softening_radius=softening_radius,
            )

            self.result = solver.solve(
                scene_model=self.scene_model,
                mesh=self.mesh,
            )

            self.source = grid_source_from_point_charge_result(
                self.mesh,
                self.result,
            )

            if self.field_mode == "oscillating":
                self.simulation = OscillatingDipoleSimulation(
                    scene_model=self.scene_model,
                    base_source=self.source,
                    period=self.OSCILLATION_PERIOD,
                )
                self.source = self.simulation.source()

        elif self.is_fdtd:
            self.simulation = FdtdTE2D(
                scene_model=self.scene_model,
                domain=SimulationDomain2D.centered(
                    width=initial_view_width * self.FDTD_DOMAIN_SCALE,
                    height=initial_view_height * self.FDTD_DOMAIN_SCALE,
                    center=(0.0, 0.0),
                ),
                cell_size=(
                    1.0 / self.FDTD_FREQUENCY
                    / self.FDTD_CELLS_PER_WAVELENGTH
                ),
                courant=self.FDTD_COURANT,
                waveform=self.FDTD_WAVEFORM,
                frequency=self.FDTD_FREQUENCY,
                boundary=self.FDTD_BOUNDARY,
                pml_cells=self.FDTD_PML_CELLS,
            )

            self.mesh = self.simulation.mesh
            self.cell_size = self.simulation.h
            self.source = self.simulation.source()

        elif self.is_3d:
            half = self.FDTD3D_HALF_SIZE
            self.simulation = FdtdYee3D(
                scene_model=self.scene_model,
                bounds=((-half,) * 3, (half,) * 3),
                cell_size=1.0 / self.FDTD_FREQUENCY / self.FDTD3D_CELLS_PER_WAVELENGTH,
                courant=self.FDTD3D_COURANT,
                waveform=self.FDTD_WAVEFORM,
                frequency=self.FDTD_FREQUENCY,
                pml_cells=self.FDTD3D_PML_CELLS,
            )

            self.cell_size = self.simulation.h
            self.slice_plane = self._make_plane(self.SLICE_NORMAL, self.SLICE_POSITION)
            self.mesh = self.slice_plane.mesh()
            self.source = self._current_source()

        else:
            raise ValueError(f"Unknown FIELD_MODE: {self.field_mode!r}")

        # -------------------------------------------------
        # Layers
        # -------------------------------------------------

        self.scalar_quantities = [
            q.name
            for q in self.source.quantities()
            if q.kind == "scalar"
        ]

        self.vector_quantities = [
            q.name
            for q in self.source.quantities()
            if q.kind == "vector"
        ]

        # Waves: the H component through the view (Hz in 2D TE, "H normal"
        # on a 3D slice) shows wavefronts with their sign; statics: |E|.
        self.heatmap_layer = self.default_scalar_layer(next(
            name for name in ("H normal", "Hz", "|E|")
            if name in self.scalar_quantities
        ))

        dynamic = self.simulation is not None

        self.vector_layer = VectorLayer(
            quantity="E",
            spacing_px=self.ARROW_SPACING_PX,
            length_px=self.ARROW_LENGTH_PX,
            color="white",
            line_width=2,
            head_type="stealth",
            head_size=8,
            # Statics (1/r^2 near charges): one level, every arrow full.
            zero_fraction=self.ARROW_ZERO_FRACTION if dynamic else 0.0,
            weak_fraction=self.ARROW_WEAK_FRACTION if dynamic else 0.0,
        )

        # -------------------------------------------------
        # Renderer
        # -------------------------------------------------

        self._embedded = embedded
        self.mesh_visible = self.SHOW_MESH_3D if self.is_3d else self.SHOW_MESH

        # 3D models: the rotatable 3D scene, plus (created on demand) the flat
        # 2D view of the same slice ("full viewport"). Only the active one is
        # updated; the other catches up when it becomes active.
        self.flat_view = False
        self.renderer_3d = None
        self.renderer_2d = None

        if self.is_3d:
            self.renderer_3d = self._create_renderer_3d(embedded)
            self.renderer = self.renderer_3d
        else:
            self.renderer = self.renderer_2d = self._create_renderer_2d(embedded)

        self.renderer.canvas.events.key_press.connect(self._on_key_press)

        # -------------------------------------------------
        # Simulation loop
        # -------------------------------------------------

        self._start_loop()

    def _create_renderer_2d(self, embedded):
        initial_view_height = self.INITIAL_VIEW_HEIGHT

        return VisPyRenderer(
            scene_model=self._display_scene(),
            source=self.source,
            layers=[self.heatmap_layer, self.vector_layer],
            base_step=self.cell_size,
            initial_view_rect=self.initial_view_rect,
            canvas_size=self.CANVAS_SIZE,
            solver_mesh=self.mesh,
            show_mesh=self.mesh_visible,

            prefer_gpu=self.PREFER_GPU,
            smooth_texture=self.SMOOTH_TEXTURE,
            heatmap_sample_px=self.HEATMAP_SAMPLE_PX,
            settle_delay_s=self.SETTLE_DELAY_S,
            min_mesh_cell_px=self.MIN_MESH_CELL_PX,

            min_view_height=initial_view_height / self.MAX_ZOOM,
            max_view_height=initial_view_height / self.MIN_ZOOM,

            charge_icon_diameter=self.CHARGE_ICON_DIAMETER,
            charge_icon_min_px=self.CHARGE_ICON_MIN_PX,

            overlays=self._overlays(),

            show=not embedded,
        )

    def _create_renderer_3d(self, embedded):
        sim = self.simulation
        frames = sim.overlays_3d()

        return VisPyRenderer3D(
            scene3d=self.scene_model,
            bounds=sim.bounds,
            plane=self.slice_plane,
            source=self.source,
            layers=[self.heatmap_layer, self.vector_layer],
            base_step=self.cell_size,
            pml_inner=frames[0]["inner"] if frames else None,
            plane_scene=self._display_scene(),
            plane_mesh=self.mesh,
            overlays=self._overlays(),
            show_mesh=self.mesh_visible,
            heatmap_sample_px=self.HEATMAP_SAMPLE_PX,
            settle_delay_s=self.SETTLE_DELAY_S,
            canvas_size=self.CANVAS_SIZE,
            show=not embedded,
        )

    def _start_loop(self):
        self.playing = False
        self.steps_per_frame = int(
            self.FDTD3D_STEPS_PER_FRAME if self.is_3d
            else self.FDTD_STEPS_PER_FRAME if self.is_fdtd
            else self.SIM_STEPS_PER_FRAME
        )

        if self.simulation is not None:
            self._frame_timer = app.Timer(
                interval=1.0 / self.SIM_FPS,
                connect=self._on_frame,
                start=False,
            )
            _use_precise_qt_timer(self._frame_timer)
            self.set_playing(self.SIM_START_PLAYING)

    # =====================================================
    # 3D: scene presets and slices
    # =====================================================

    def _fdtd3d_scene_regions(self):
        if self.field_mode == "fdtd3d_reflector":
            # Metal plate a quarter wavelength behind the z-dipole (yz plane).
            return [BoxRegion(
                (-0.30, -0.8, -0.8), (-0.25, 0.8, 0.8),
                Material("metal", pec=True),
                name="reflector",
            )]
        return []

    def _make_plane(self, normal, position):
        return SlicePlane(normal, float(position), self.simulation.bounds, self.simulation.h)

    def _current_source(self):
        """FieldSource for the frontend: the simulation's, or a 3D slice."""
        if self.is_3d:
            return SliceSource(self.simulation.field(), self.slice_plane)
        return self.simulation.source()

    def _display_scene(self):
        if self.is_3d:
            return slice_scene(self.scene_model, self.slice_plane)
        return self.scene_model

    def _overlays(self):
        if self.simulation is None:
            return []
        if self.is_3d:
            return [slice_frame(f, self.slice_plane) for f in self.simulation.overlays_3d()]
        return self.simulation.overlays()

    def slice_axis_nodes(self, normal):
        """(first, step, count) of the grid-node positions along an axis."""
        a = "xyz".index(normal)
        n = (self.simulation.nx, self.simulation.ny, self.simulation.nz)[a]
        return float(self.simulation.lo[a]), float(self.simulation.h), n + 1

    def set_slice(self, normal, position):
        """Move the slice plane (normal axis "x"/"y"/"z", world position)."""
        new_axis = normal != self.slice_plane.normal
        self.slice_plane = self._make_plane(normal, position)

        self.source = self._current_source()

        if new_axis:
            self.mesh = self.slice_plane.mesh()

        self._sync_renderer(new_axis=new_axis)
        self._notify("slice")

    def _sync_renderer(self, new_axis, full=False):
        """
        Bring the active renderer up to date with the slice.

        new_axis: the plane changed orientation (new extents and mesh).
        full:     the renderer was inactive and may be stale in every respect.
        """
        r = self.renderer
        r.source = self.source
        rebuild = new_axis or full

        if r is self.renderer_3d:
            # The plane moves inside the 3D scene; the camera stays put.
            r.set_plane(
                self.slice_plane,
                plane_scene=self._display_scene(),
                plane_mesh=self.mesh if rebuild else None,
                overlays=self._overlays(),
            )
        else:
            r.set_scene(self._display_scene())
            r.set_overlays(self._overlays())
            if rebuild:
                r.set_solver_mesh(self.mesh)
                r.set_mesh_visible(self.mesh_visible)
            if new_axis:
                r.view.camera.rect = self.initial_view_rect

        if rebuild:
            # Other plane extents and axes: fresh layer views.
            r.set_layers([self.heatmap_layer, self.vector_layer])
        else:
            r.refresh_data(self.source)

    def set_flat_view(self, flat):
        """3D models: show the slice flat over the whole view, or in 3D."""
        flat = bool(flat) and self.is_3d
        if flat == self.flat_view:
            return

        self.flat_view = flat

        if flat and self.renderer_2d is None:
            self.renderer_2d = self._create_renderer_2d(self._embedded)
            self.renderer_2d.canvas.events.key_press.connect(self._on_key_press)

        self.renderer = self.renderer_2d if flat else self.renderer_3d
        self._sync_renderer(new_axis=False, full=True)
        self._update_status()
        self._notify("view")

    # =====================================================
    # FDTD scene presets
    # =====================================================

    def _fdtd_scene_regions(self):
        """Material regions of the FDTD model variants (dipole at the origin)."""
        if self.field_mode == "fdtd_slab":
            # Glass slab above the dipole, running through the PML (CPML
            # stretches coordinates, so it absorbs inside dielectrics too).
            # Inside: wavelength / 2, partial reflection, refraction.
            return [RectRegion(
                -50.0, 0.75, 50.0, 1.5,
                Material("glass", eps_r=4.0),
                name="slab",
            )]

        if self.field_mode == "fdtd_reflector":
            # Metal plate a quarter wavelength behind the dipole: the
            # reflected wave adds in phase in front -> directional antenna.
            return [RectRegion(
                -1.0, -0.30, 1.0, -0.25,
                Material("metal", pec=True),
                name="reflector",
            )]

        return []

    # =====================================================
    # Layer presets
    # =====================================================

    def default_scalar_layer(self, name):
        """
        Sensible style for a quantity:
            magnitude -> viridis;  signed -> diverging palette, zero in the middle

            static fields  -> log / symlog scale (huge dynamic range near
                              charges), limits frozen from the initial view
            time-varying   -> linear scale (wave amplitude and phase read
                              directly), peak-hold "running" limits
        """
        quantity = self.source.quantity(name)

        dynamic = self.simulation is not None
        mode = "running" if dynamic else "initial"

        # "running" starts tiny, so the first non-zero frame sets the limits
        # (a wave that has not arrived yet has no meaningful range).
        start = 1e-12 if dynamic else 1.0

        # Waves: most of the view is quiet background, so the upper limit
        # must come from (nearly) the strongest part of the field.
        percentiles = (0.5, 99.5) if dynamic else (2.0, 98.0)

        # Charge densities are point-like: a port end can be ~100x stronger
        # than the bound charge on a dielectric surface. symlog shows both.
        charge_density = name in ("div E", "div D")

        if quantity.signed:
            return ScalarLayer(
                quantity=name,
                colormap="diverging",
                scale="symlog" if (charge_density or not dynamic) else "linear",
                linthresh=(
                    self.CHARGE_LINTHRESH
                    if charge_density
                    else self.SIGNED_LINTHRESH
                ),
                clim=ClimPolicy(
                    mode=mode,
                    lo=-start,
                    hi=start,
                    percentiles=percentiles,
                    symmetric=True,
                    decay=self.RUNNING_CLIM_DECAY,
                ),
            )

        return ScalarLayer(
            quantity=name,
            colormap="viridis",
            scale="linear" if dynamic else "log",
            clim=ClimPolicy(
                mode=mode,
                lo=0.0 if dynamic else -1.0,
                hi=start,
                percentiles=percentiles,
                decay=self.RUNNING_CLIM_DECAY,
            ),
        )

    def cycle_heatmap_quantity(self):
        names = self.scalar_quantities
        current = names.index(self.heatmap_layer.quantity)
        self.update_heatmap("quantity", names[(current + 1) % len(names)])

    # =====================================================
    # Layer editing (UI-agnostic; the Qt panel calls these)
    # =====================================================

    def add_listener(self, callback):
        self._listeners.append(callback)

    def _notify(self, kind):
        for callback in list(self._listeners):
            callback(kind)

    def _apply_layers(self):
        self.renderer.set_layers([self.heatmap_layer, self.vector_layer])
        self._notify("layers")

    def update_heatmap(self, name, value):
        """Change one (dotted) field of the heatmap layer."""
        if name == "quantity":
            # A new quantity gets its own sensible style (palette, scale...).
            layer = dataclasses.replace(
                self.default_scalar_layer(value),
                visible=self.heatmap_layer.visible,
            )

        elif name == "clim.mode" and value == "fixed":
            # Freeze what is on screen right now instead of arbitrary numbers.
            lo, hi = self.current_heatmap_clim()
            layer = with_value(self.heatmap_layer, "clim", dataclasses.replace(
                self.heatmap_layer.clim, mode="fixed", lo=lo, hi=hi,
            ))

        else:
            layer = with_value(self.heatmap_layer, name, value)

        self.heatmap_layer = layer
        self._apply_layers()

    def update_vector(self, name, value):
        """Change one field of the arrow layer."""
        self.vector_layer = with_value(self.vector_layer, name, value)
        self._apply_layers()

    def current_heatmap_clim(self):
        """Color limits actually in use (after the clim policy)."""
        return tuple(self.renderer.layer_views[0].clim)

    def set_mesh_visible(self, visible):
        self.mesh_visible = bool(visible)
        self.renderer.set_mesh_visible(visible)

    def reset_view(self):
        if self.renderer is self.renderer_3d:
            self.renderer.reset_view()
        else:
            self.renderer.view.camera.rect = self.initial_view_rect

    # =====================================================
    # Simulation control (UI-agnostic)
    # =====================================================

    def set_steps_per_frame(self, steps):
        self.steps_per_frame = max(1, min(int(steps), 4096))
        self._update_status()
        self._notify("playback")

    def set_simulation_parameter(self, name, value):
        """May raise ValueError (e.g. unstable Courant number)."""
        old_step = self.simulation.step_index

        self.simulation.set_parameter(name, value)
        self.renderer.set_overlays(self._overlays())

        if self.simulation.step_index != old_step:
            # The simulation restarted (e.g. dt changed): fresh layer state.
            self.reset_simulation()
        else:
            self.source = self._current_source()
            self.renderer.refresh_data(self.source)

        self._notify("simulation")

    def shutdown(self):
        """Stop timers before the canvas is thrown away."""
        if self.simulation is not None:
            self._frame_timer.stop()

        for r in {self.renderer_3d, self.renderer_2d} - {None}:
            r.shutdown()

    # =====================================================
    # Simulation loop
    # =====================================================

    def set_playing(self, playing):
        self.playing = bool(playing)

        if self.playing:
            self._frame_timer.start()
        else:
            self._frame_timer.stop()

        self._update_status()
        self._notify("playback")

    def advance(self, steps):
        """Step the simulation and push the new state to the renderer."""
        self.simulation.step(steps)
        self.source = self._current_source()
        self.renderer.refresh_data(self.source)
        self._update_status()

    def reset_simulation(self):
        self.simulation.reset()
        self.source = self._current_source()

        # Fresh layer views: "running" limits must not remember the
        # previous run.
        self.renderer.source = self.source
        self.renderer.set_layers(self.renderer.layers)

        self.renderer.refresh_data(self.source)
        self._update_status()

    def _on_frame(self, event=None):
        self.advance(self.steps_per_frame)

    def _update_status(self):
        if self.simulation is None:
            return

        state = "playing" if self.playing else "paused"

        readouts = "  ".join(
            f"{label} = {value:+.4g}"
            for label, value in self.simulation.diagnostics().items()
        )

        self.renderer.set_status(
            f"t = {self.simulation.t:.3f}  "
            f"step {self.simulation.step_index}  "
            f"[{state}, {self.steps_per_frame} steps/frame]"
            + (f"   {readouts}" if readouts else "")
        )

        self._notify("status")

    # =====================================================
    # Keys
    # =====================================================

    def cycle_vector_quantity(self):
        names = self.vector_quantities
        current = names.index(self.vector_layer.quantity)

        self.update_vector("quantity", names[(current + 1) % len(names)])

    def _on_key_press(self, event):
        key = event.key.name.upper() if event.key is not None else ""
        text = event.text or ""

        if key == "Q":
            self.cycle_heatmap_quantity()

        elif key == "V":
            self.cycle_vector_quantity()

        if self.simulation is None:
            return

        if key == "SPACE":
            self.set_playing(not self.playing)

        elif key == "N" and not self.playing:
            self.advance(1)

        elif key == "R":
            self.reset_simulation()

        elif text in ("+", "="):
            self.set_steps_per_frame(self.steps_per_frame * 2)

        elif text in ("-", "_"):
            self.set_steps_per_frame(self.steps_per_frame // 2)

    def run(self):
        self.renderer.run()


def _use_precise_qt_timer(timer):
    """
    VisPy's Qt timer is a default (coarse) QTimer. On Windows coarse timers
    snap to the 15.6 ms system tick, so a 16 ms interval fires every ~31 ms
    (~30 instead of 60 frames/s). PreciseTimer keeps millisecond accuracy.
    No-op on non-Qt backends.
    """
    backend = getattr(timer, "_backend", None)

    if hasattr(backend, "setTimerType"):
        from PySide6.QtCore import Qt

        backend.setTimerType(Qt.TimerType.PreciseTimer)
