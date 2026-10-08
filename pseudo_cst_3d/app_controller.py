# app_controller.py

import dataclasses

import numpy as np

from vispy import app

import model_builder
import model_spec
from layers import ClimPolicy, ScalarLayer, VectorLayer
from model_spec import ModelError, ModelSpec
from params import with_value
from scene_model import PointCharge
from edit_tool import ChargeEditTool
from renderer import VisPyRenderer
from renderer3d import VisPyRenderer3D
from slicing import SlicePlane, SliceSource, slice_frame, slice_scene


class AppController:
    """
    Pseudo-CST frontend logic (no Qt here).

    What to compute comes from a MODEL FILE (models/*.toml, see
    model_spec.py): solver, domain, materials, objects, excitation and the
    initial view. model_builder.py turns it into a scene plus a FieldSource
    or Simulation. This class only holds frontend-wide settings: palettes,
    arrows, zoom, playback.

        FieldSource (field.py): the only interface between field data and
            the frontend, with named quantities ("E", "|E|", "Hz", ...).
        Simulation (simulation.py): step / reset / parameters / diagnostics.
        Layers (layers.py): what to show and how (quantity, scale, palette,
            clim policy, arrow style).

    Keys on the canvas:
        Q     -> cycle the heatmap quantity
        V     -> cycle the arrow quantity
        Space -> play / pause            (simulations)
        N     -> one step while paused
        R     -> reset to t = 0
        + / - -> steps per frame x2 / /2
    """

    DEFAULT_MODEL = "fdtd_dipole"

    CANVAS_SIZE = (1000, 700)

    # Initially visible height (world units) if the model's [view] has none.
    DEFAULT_VIEW_HEIGHT = 4.0

    # Arrow strength levels (fraction of the peak field in view):
    # >= weak -> full arrow, >= zero -> short faded arrow, below -> ring
    # (or dot / cross when the field crosses a 3D slice plane).
    ARROW_WEAK_FRACTION = 0.25
    ARROW_ZERO_FRACTION = 0.05

    # Arrow layout (screen px in 2D / flat views, virtual plane px in 3D).
    ARROW_SPACING_PX = 20
    ARROW_LENGTH_PX = 10

    # Plane mesh lines in the 3D view (off: they clutter a rotated plane).
    SHOW_MESH_3D = False

    # Simulation playback: frames per second; solver steps per frame come
    # from the model ([view] steps_per_frame, default 1).
    SIM_FPS = 60
    SIM_START_PLAYING = False          # start paused; Space / Play to run

    # Color limits of time-varying fields: peak-hold with this decay/frame.
    RUNNING_CLIM_DECAY = 0.995

    # Zoom limits relative to the initial view. Display cost does not grow
    # with zoom-out, so these are just sane bounds, not performance limits.
    MIN_ZOOM = 0.1
    MAX_ZOOM = 8.0

    SHOW_MESH = True  # solver mesh overlay (models with a solver mesh)

    # Evaluate heatmaps per pixel in a shader when the source supports it.
    PREFER_GPU = True

    # CPU heatmap texture (when neither the shader nor the grid path applies).
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

    # Default palettes (all have black = no field, for the black background):
    #   signed quantities (Hz, V, Ex, div E...): purple - black - yellow
    #   magnitudes (|E|, u, |S|...): black - green - yellow - red ("heat")
    # Also available: "green_red", "diverging" (blue-gray-red), "viridis".
    SIGNED_PALETTE = "purple_yellow"
    MAGNITUDE_PALETTE = "heat"

    # symlog linear range for signed quantities (field units, k = 1).
    SIGNED_LINTHRESH = 0.05

    # symlog linear range for charge densities (div E, div D).
    CHARGE_LINTHRESH = 0.05

    @staticmethod
    def available_models():
        """[(key, name, path)] of the model files in models/."""
        return model_spec.list_models()

    def __init__(self, model=None, embedded=False):
        """
        model:    a ModelSpec, a model key ("fdtd_dipole") or a path to a
                  .toml file (default: DEFAULT_MODEL). Raises ModelError for
                  unknown or invalid models.
        embedded: True -> the canvas is not shown as its own window; the
                  caller places renderer.canvas.native into its UI.
        """
        spec = model if isinstance(model, ModelSpec) else model_spec.load(model or self.DEFAULT_MODEL)
        self.spec = spec
        # Models in models/ are addressed by file stem, others by full path.
        in_models_dir = spec.path.resolve().parent == model_spec.MODELS_DIR
        self.model_key = spec.key if in_models_dir else str(spec.path.resolve())

        self.is_3d = spec.solver == "fdtd3d"
        self.is_fdtd = spec.solver == "fdtd2d"

        # Callbacks fn(kind), kind in {"status", "playback", "layers",
        # "simulation", "slice", "view", "edit", "selection", "scene"}; "status" fires every frame, the
        # others on change; lets a UI follow changes made elsewhere (keys on
        # the canvas, ...).
        self._listeners = []

        canvas_width, canvas_height = self.CANVAS_SIZE
        self.view_height = float(spec.view.get("height", self.DEFAULT_VIEW_HEIGHT))
        view_width = self.view_height * canvas_width / canvas_height

        self.initial_view_rect = (
            -view_width / 2.0,
            -self.view_height / 2.0,
            view_width,
            self.view_height,
        )

        # -------------------------------------------------
        # Scene and field source / simulation (from the model file)
        # -------------------------------------------------

        built = model_builder.build(spec)

        self.scene_model = built.scene_model
        self.simulation = built.simulation
        self.mesh = built.mesh
        self.cell_size = built.cell_size
        self.source = built.source
        self.slice_plane = None

        if self.is_3d:
            slice_spec = spec.view.get("slice", {"normal": "y", "position": 0.0})
            self.slice_plane = self._make_plane(slice_spec["normal"], slice_spec.get("position", 0.0))
            self.mesh = self.slice_plane.mesh()
            self.source = self._current_source()

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

        self.heatmap_layer = self.default_scalar_layer(self._initial_heatmap_quantity())

        dynamic = self.simulation is not None
        arrows = spec.view.get("arrows", "E")
        if arrows not in self.vector_quantities:
            raise ModelError(
                f"{spec.path.name}: [view] arrows = {arrows!r} is not available; "
                f"use one of {self.vector_quantities}"
            )

        self.vector_layer = VectorLayer(
            quantity=arrows,
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

        # Charge editor state (see "Charge editing" below).
        self.edit_mode = False
        self.selected_charge = None
        self.dirty = False
        self._edit_tool = None

        # -------------------------------------------------
        # Simulation loop
        # -------------------------------------------------

        self._start_loop()

    def _initial_heatmap_quantity(self):
        requested = self.spec.view.get("heatmap")

        if requested is not None:
            if requested not in self.scalar_quantities:
                raise ModelError(
                    f"{self.spec.path.name}: [view] heatmap = {requested!r} is not available; "
                    f"use one of {self.scalar_quantities}"
                )
            return requested

        # Waves: the H component through the view (Hz in 2D TE, "H normal"
        # on a 3D slice) shows wavefronts with their sign; statics: |E|.
        return next(
            name for name in ("H normal", "Hz", "|E|")
            if name in self.scalar_quantities
        )

    def _create_renderer_2d(self, embedded):
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

            min_view_height=self.view_height / self.MAX_ZOOM,
            max_view_height=self.view_height / self.MIN_ZOOM,

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

        # Wire-current envelope (None: the model has no wires / ports).
        self.wire_envelope = None
        if self.simulation is not None and self.simulation.wire_currents() is not None:
            self.wire_envelope = np.zeros(len(self.simulation.wire_currents()["current"]))
        self.steps_per_frame = max(1, int(self.spec.view.get("steps_per_frame", 1)))

        if self.simulation is not None:
            self._frame_timer = app.Timer(
                interval=1.0 / self.SIM_FPS,
                connect=self._on_frame,
                start=False,
            )
            _use_precise_qt_timer(self._frame_timer)
            self.set_playing(self.SIM_START_PLAYING)

    # =====================================================
    # 3D slices
    # =====================================================

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
    # Layer presets
    # =====================================================

    def default_scalar_layer(self, name):
        """
        Sensible style for a quantity:
            magnitude -> MAGNITUDE_PALETTE;  signed -> SIGNED_PALETTE, zero in the middle

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
                colormap=self.SIGNED_PALETTE,
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
            colormap=self.MAGNITUDE_PALETTE,
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
    # Charge editing (UI-agnostic; the mouse tool and the panel call these)
    # =====================================================

    # Static charge models only: in "oscillating" the charges are scaled in
    # time, so an edit would freeze whatever phase they happen to be in.
    EDITABLE_SOLVERS = ("analytic", "grid")

    @property
    def can_edit(self):
        return self.spec.solver in self.EDITABLE_SOLVERS

    def set_edit_mode(self, on):
        on = bool(on) and self.can_edit
        if on == self.edit_mode:
            return

        self.edit_mode = on

        if on:
            self._edit_tool = ChargeEditTool(self, self.renderer)
        else:
            self._edit_tool.detach()
            self._edit_tool = None
            self.select_charge(None)

        self._notify("edit")

    def charges(self):
        return self.scene_model.get_objects(PointCharge)

    def charge_at(self, position, radius):
        """Index of the charge whose icon covers position (nearest), or None."""
        best, best_d2 = None, radius * radius
        for i, charge in enumerate(self.charges()):
            dx = charge.position[0] - position[0]
            dy = charge.position[1] - position[1]
            d2 = dx * dx + dy * dy
            if d2 <= best_d2:
                best, best_d2 = i, d2
        return best

    def select_charge(self, index):
        self.selected_charge = index
        self._update_highlight()
        self._notify("selection")

    def _update_highlight(self):
        index = self.selected_charge
        position = None if index is None else self.charges()[index].position
        self.renderer.set_highlight(position)

    def move_charge(self, index, position):
        self.update_charge(index, position=position)

    def update_charge(self, index, position=None, charge=None):
        target = self.charges()[index]
        if position is not None:
            target.position = (float(position[0]), float(position[1]))
        if charge is not None:
            target.charge = float(charge)
        self._scene_edited()

    def add_charge(self, position=None, charge=1.0):
        if position is None:
            # Middle of what is on screen.
            x0, x1, y0, y1 = self.renderer.view_bounds()
            position = (0.5 * (x0 + x1), 0.5 * (y0 + y1))

        self.scene_model.add(PointCharge(position=tuple(position), charge=float(charge)))
        self.selected_charge = len(self.charges()) - 1
        self._scene_edited()

    def remove_charge(self, index):
        if len(self.charges()) <= 1:
            raise ModelError("a charge model needs at least one charge")

        self.scene_model.objects.remove(self.charges()[index])
        self.selected_charge = None
        self._scene_edited()

    def _scene_edited(self):
        """Rebuild the field from the edited charges and redraw."""
        self.dirty = True
        self.spec = dataclasses.replace(self.spec, objects=list(self.scene_model.objects))

        built = model_builder.build(self.spec)
        self.scene_model = built.scene_model
        self.source = built.source

        r = self.renderer
        r.source = self.source
        r.set_scene(self.scene_model)
        r.refresh_data(self.source)
        self._update_highlight()
        self._notify("scene")

    def save_model(self):
        """Write the charges back into the model file (other content kept)."""
        model_spec.save_objects(self.spec)
        self.dirty = False
        self._notify("scene")

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
        if self._edit_tool is not None:
            self._edit_tool.detach()
            self._edit_tool = None

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

    # Envelope of the wire current: peak hold, decaying per solver step.
    WIRE_ENVELOPE_DECAY = 0.99

    def advance(self, steps):
        """Step the simulation and push the new state to the renderer."""
        if self.wire_envelope is not None:
            # Step one at a time so the envelope sees every peak.
            for _ in range(int(steps)):
                self.simulation.step(1)
                self._update_wire_envelope()
        else:
            self.simulation.step(steps)
        self.source = self._current_source()
        self.renderer.refresh_data(self.source)
        self._update_status()

    def _update_wire_envelope(self):
        current = self.simulation.wire_currents()["current"]
        env = self.wire_envelope
        if env is None or env.shape != current.shape:
            self.wire_envelope = abs(current)
        else:
            self.wire_envelope = np.maximum(env * self.WIRE_ENVELOPE_DECAY, abs(current))

    def wire_plot_data(self):
        """(s, current, envelope, axis_label) or None (no wires / ports)."""
        if self.simulation is None:
            return None
        wc = self.simulation.wire_currents()
        if wc is None:
            return None
        return wc["s"], wc["current"], self.wire_envelope, wc["axis_label"]

    def reset_simulation(self):
        self.simulation.reset()
        if self.wire_envelope is not None:
            self.wire_envelope = np.zeros_like(self.wire_envelope)
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
