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
  Run with `../.venv/Scripts/python.exe main.py [mode]`.
- Modes: `analytic`, `grid`, `oscillating`, `fdtd`, `fdtd_slab`,
  `fdtd_reflector`, `fdtd3d`, `fdtd3d_reflector`.
  `--canvas-only` gives a bare VisPy window.
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
- `app_controller.py` — UI-agnostic logic: modes, presets, slices
  (`set_slice`), the simulation loop, and listeners (`status`, `playback`,
  `layers`, `simulation`, `slice`). `main_window.py` is the thin Qt shell, with
  a Slice dock in 3D modes.
- Arrows have three levels: strong (full), weak (short and faded), and ~0 (a
  dot). Through-plane ⊙ / ⊗ markers are hollow circles with a dot or a cross.
  The reference is the 98th percentile of the FULL magnitude (`|name|`). In
  static modes the levels are off (fractions = 0).

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
