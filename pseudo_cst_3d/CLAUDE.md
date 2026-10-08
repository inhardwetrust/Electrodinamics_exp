# CLAUDE.md — Pseudo CST

Context for future Claude sessions ("previously on..."). Read this first.

## The project and how we work

- An interactive frontend for building intuition about electrodynamics, inspired by
  CST Studio. The goal is **understanding, not production speed**: waiting for a
  computation is fine. Solvers are **pluggable** (our own or external, e.g. Meep or
  openEMS later).
- The user writes in **Russian**: answer in Russian, keep code and comments in
  English. All code is written by Claude; the user is the interface / UX consultant.
  Discuss architecture before big changes, then implement step by step.
- **Physics is validated numerically, never by eye.** Every solver change gets a
  check script covering: wave speed, charge conservation (div E ≈ 0 away from
  sources, charge ±τ√π after a Gaussian pulse), Poynting balance, PML reflection
  against a large reference domain, the speed in a dielectric (1/√εr), and 1/r
  far-field decay in 3D.
- Things the user cares about:
  - arrows have a **fixed length**: they show direction only, never magnitude;
  - in 3D, fields are read on **slice planes**, not with volume arrows or heatmaps;
  - the slice plane lives **inside the rotatable 3D scene**;
  - geometry will later come from **STEP** (OpenCASCADE or gmsh), not from our
    own CAD.

## Running

- Python venv: `../.venv` (in `VisPy_playground`), backend PySide6 (Qt6).
  Run with `../.venv/Scripts/python.exe main.py [model]`, where `model` is a
  file stem from `models/` or a path to a `.toml` file. `--list` lists the
  models; `--canvas-only` gives a bare VisPy window.
- **Models are files** (`models/*.toml`, the format is documented at the top of
  `model_spec.py`):
  - `model_spec.py` parses and strictly validates them: unknown keys,
    materials or object types raise a `ModelError` naming the file and the
    object;
  - `model_builder.py` builds the SceneModel plus the FieldSource or
    Simulation (solvers: analytic / grid / oscillating / fdtd2d / fdtd3d);
  - `AppController(model=...)` holds only frontend-wide settings (palettes,
    arrows, zoom, playback). There are no scene or solver constants in code
    any more.
  - The UI selector lists the files; **Reload** re-reads the current one.
  - New models need no code change. STEP bodies will become another object
    type.
- `Wire` (scene_model.py, 2D or 3D): the edges along its staircase (same
  rasterizer as ports) are set to PEC. Solvers raise an error if a port edge
  lies on PEC. Half-wave vibrator check (`fdtd3d_halfwave`): E = 0 on the wire,
  current maximal at the feed and falling toward the tips. Input impedance is
  120+100j Ohm at h = λ/20 and 105+80j Ohm at h = λ/40, converging toward the
  thin-dipole 73 Ohm; the gap and the staircase wire radius dominate the
  error. The sign convention of X is not verified yet.
- **Wire current**: optional `Simulation.wire_currents()` returns
  {s, current, axis_label} for all wire and port edges along the antenna axis.
  Current = h * circulation of H around each edge (discrete Ampere law). It is
  exact on PEC edges because E = 0 there; in a port gap it is the total current
  (source plus displacement), so it is continuous. The controller tracks a
  peak-hold envelope every step; `qt_plot.CurvePlot` shows it in the
  Simulation dock. Half-wave check: the profile is symmetric and continuous at
  the feed; the current at the last edge is 0.46 at λ/20 and 0.26 at λ/40,
  converging slowly to cos(kz).
- **Charge editor** (static charge models: analytic / grid): the toolbar
  "Edit" button calls `controller.set_edit_mode`. `edit_tool.ChargeEditTool`
  turns mouse events into controller calls (`charge_at`, `select_charge`,
  `move_charge`); its handlers run first and switch the camera off while a
  charge is dragged. `_scene_edited` rebuilds the field via model_builder
  (throttled to ~30 Hz while dragging). `model_spec.save_objects` rewrites only
  the `[[objects]]` blocks and validates the result before writing. Models
  outside `models/` use their full path as `model_key`.
- Git: the repo root is `VisPy_playground/` (remote
  `inhardwetrust/Electrodinamics_exp`). **Only selected folders are committed**:
  never run `git add .` at the root. It is Unlicense'd.
  `../pseudo_cst_v4_heatmap` is the older 2D-only copy and is not in git.

## Architecture (contracts first)

- `field.py` — **FieldSource**: `quantities()` (name, scalar/vector, signed),
  `sample(name, points)`, plus optional fast paths:
  - `glsl_scalar(name)`: GLSL that evaluates the field per pixel;
  - `grid_array(name)`: a native grid uploaded as a float texture.

  `GridArray` keeps the origin of each quantity (exact Yee staggering).
  `GridFieldSource` supports derived quantities and vectors composed from
  components that live on different grids.
- `simulation.py` — **Simulation**: `step(n)`, `reset()`, `t`, `dt`,
  `step_index`, `source()`, `parameters()` / `get_parameter` / `set_parameter`,
  optional `diagnostics()` and `overlays()`.
- `params.py` — the **Parameter** schema: float / int / bool / choice, dotted
  names, `enabled_when`. The Qt forms (`qt_forms.py`) are generated from these;
  adding a parameter needs no UI code.
- `layers.py` — layer configs without VisPy (ScalarLayer, VectorLayer,
  ClimPolicy) and their schemas. `layer_views.py` contains the VisPy
  implementations:
  - scalar backends in priority order: `gpu` (shader), `grid` (texture + LUT in
    the shader), `texture` (CPU);
  - hooks: `on_view_changed` (cheap), `on_view_settled` (debounced, expensive),
    `on_data_changed` (a new time step).
- `color_mapping.py` — scales (linear / log / symlog) and 256-entry LUT
  palettes, shared by the GPU and CPU paths so their colors are identical.
  `running_clim` is a peak-hold that decays the field *amplitude*; values below
  1e-6 of the peak are ignored as float32 noise.
- `fdtd.py` — 2D TE; `fdtd3d.py` — 3D Yee. Both use float32 with in-place
  updates, CPML applied only in the boundary strips, per-edge material
  coefficients Ca/Cb (PEC → 0), and soft-source ports rasterized as edge
  staircases. H is synchronized to E's time level for S and u. Signals come
  from `waveforms.py`.
- `field3d.py` (`GridArray3D`, `Field3D`) and `slicing.py` — **SlicePlane**
  maps a 3D field to a 2D FieldSource (**SliceSource**):
  - vectors are *projected* onto the plane;
  - `<vec> normal` is the through-plane component, and `normal_toward_viewer`
    gives the sign that points out of the screen;
  - `slice_scene` produces 2D cross-sections of the 3D objects, and
    `slice_frame` cuts the PML frame.

  Plane coordinates are (u, v): z→(x, y), y→(x, z), x→(y, z).
- `renderer.py` — the 2D view. `renderer3d.py` — the **3D view**: the plane is a
  node with a MatrixTransform (u, v, 0) → world, and the layer views see a
  *virtual view* of the whole plane (700 virtual px along its height), so the
  2D layers are reused unchanged. Depth rules:
  - the plane heatmap is depth tested;
  - plane overlays and arrows are drawn without depth testing;
  - the 3D geometry is depth tested.
- 3D models have **two views of the same slice**: the 3D scene (the default)
  and a flat 2D view. The "Full viewport" checkbox calls
  `controller.set_flat_view`; the flat view is a regular `VisPyRenderer` on the
  SliceSource, created on first use. Only the active renderer is updated, and
  `_sync_renderer(full=True)` catches the other one up on switch. Both
  canvases are pages of a QStackedWidget in MainWindow.
- `app_controller.py` — UI-agnostic logic: layer presets, slices
  (`set_slice`), the simulation loop, and listeners (`status`, `playback`,
  `layers`, `simulation`, `slice`). `main_window.py` is the thin Qt shell, with
  a Slice dock for 3D models.
- Arrows have three levels: strong (full), weak (short and faded), and ~0 (a
  dot). Through-plane ⊙ / ⊗ markers are hollow circles with a dot or a cross.
  The reference is the 98th percentile of the FULL magnitude (`|name|`). In
  static models the levels are off (fractions = 0).

## Pitfalls found (do not rediscover)

- **2D layers at z = 0 must not depth test**, or the heatmap hides the lines
  and the mesh cuts the arrows into dashes. See `_disable_depth_test`.
- **`camera.events.transform_change` never fires for PanZoomCamera**: hook
  `_update_transform` instead (`ClampedPanZoomCamera.on_view_change`).
- **`camera.rect` is the requested rectangle, not the visible one**: use
  `_real_rect` (`renderer.visible_rect()`), especially when the canvas is
  docked.
- **Image row 0 is drawn at the bottom** with PanZoomCamera, so no `flipud`.
- **Qt6 QOpenGLWidget plus VisPy's explicit swapBuffers blocks for about two
  frames** on this AMD GPU (22 instead of 60 fps). `_skip_redundant_qt_swap`
  makes it a no-op. Use `PreciseTimer` for the frame timer on Windows.
- Test screenshots: **only `window.grab()` or `canvas.render()`, never screen
  grabs**. A screen grab once captured the user's browser.
- Throttle panel status refreshes to about 15 Hz.
- Probe points in tests should sit at cell centers: `int()` of a value like
  111.999 picks the neighboring cell.
- A Gaussian current pulse leaves static charge behind; test absorption with
  `modulated_gaussian`.
- Known open issue: a test script that creates and closes several standalone
  canvases in one process fails with a GL context error in this folder. It
  does not happen in the app (switching models through MainWindow is fine), so
  regression tests drive MainWindow.

## Status and next steps

Done:
- 2D statics;
- 2D and 3D FDTD with PML and materials;
- derived quantities (div E / div D, curl E, S, u);
- the Qt shell;
- slice planes inside the 3D view.

NumPy speed: 60³ cells ≈ 11 ms/step.

Proposed order:
1. Record results and add DFT monitors (amplitude and phase at chosen
   frequencies), with a timeline.
2. Linked tri-planar slices (XY / XZ / YZ); rotated planes later.
3. STEP import and voxelization; materials and ports assigned in the UI.
4. Port impedance and S11, far field.
5. GPU / numba, non-uniform grids.
