# CLAUDE.md — atomic FDTD

A learning sandbox next to `../pseudo_cst_3d` (read its CLAUDE.md for the
working style: the user writes in Russian; answer in Russian, keep code in
English; validate numerics with numbers; screenshots only via `window.grab()`
or `canvas.render()`, never screen grabs).

- Goal: build understanding of FDTD from the smallest pieces. Step 1 is 1D
  local diffusion: new array from the old one, then a swap. Likely next
  steps: 2D diffusion, the 1D wave equation (two fields, leapfrog), then a
  1D Yee FDTD.
- Structure (keep it, so models scale):
  - a `Simulation` model (`simulation.py`);
  - a `Controller` without Qt;
  - an `ArrayRenderer` (VisPy);
  - `MainWindow` (PySide6).

  Model parameters are declared as `params.Param`; `restart=True` means the
  value is pending until Reset (shown as "applies on Reset").
- The dif is the initial perturbation: Reset = zeros + dif;
  `controller.apply_dif()` adds it at any time.
- The renderer handles 1D (rows = 1) and 2D arrays. Long 1D strips get taller
  cells; values are written into cells up to 60 cells; the grid is hidden
  above 100 cells.
- Carried over from pseudo_cst: `_skip_redundant_qt_swap` (AMD, Qt6) and a
  `PreciseTimer` for playback.
- Checked: one step from a center peak gives [0, 0, .25, .5, .25, 0, 0] at
  D = .25; reflect and periodic conserve the sum; fixed leaks; D = .5 equals
  the neighbour average; D = .6 blows up with alternating signs.
- **2D** (`Diffusion2D`): nx x ny grid, stencils 5-point / 9-point isotropic
  (diagonal weight 1/4) / 9-point equal. All are normalized to 2a + 4b = 2,
  so D means the same in every stencil and in 1D. Boundaries use `np.pad`
  modes (constant / edge / wrap). Checks:
  - stability limits 0.25 / 0.375 / 0.5 match the computed `d_max` (just
    below: bounded; just above: blows up);
  - reflect and periodic conserve the sum for every stencil;
  - after normalization the anisotropy of a smooth spot is below 0.3% for
    all stencils.
- The Controller holds `MODELS` and `set_model()`; the window rebuilds its
  panel on the "model" event, deferred with `QTimer.singleShot`. Each model
  has `DEFAULT_COLOR_LIMITS`: 1D "initial", 2D "auto" (a 2D peak decays like
  1/t and would go black).
- **Feed (set)**, in the `Simulation` base: `make_set(name)` returns NaN
  where a cell is free; `set_source()` selects it; `apply_set()` does
  state[mask] = set[mask]. Models call it after every step and at the end of
  reset (zeros, then + dif, then set). Checks: 1D left = 1 / right = 0
  converges exactly to linspace(1, 0); center = 1 with fixed ends gives a
  tent; 2D left / right columns give equal linear rows. The renderer outlines
  masked cells (`set_marks`).
- **Feed modulation** (`Simulation` base): `feed_wave` (constant / sine),
  `feed_period` (in steps), `feed_amplitude`, `feed_offset`.
  forced = A * set * w(n) + offset, where n is the step index. The models
  increment n before `apply_set()`, so the forced value belongs to the new
  time level and n = 0 at reset gives sin = 0. The params have
  `group="feed"` and appear in the Feed box; feed edits apply at once.
  Check: in 1D, D = .25, P = 40, the amplitude and phase versus distance
  match exp(-x/delta) and x/delta, delta = sqrt(2D/omega) = 1.78, to 1-3%.
- Palettes: heat / signed (purple - black - yellow, symmetric limits).
  Labels are shown only if a cell is >= 42 px wide.
- Colors and labels: `round_values()` / `format_value()` in renderer.py. With
  "match numbers" the color uses the rounded value, so equal labels have equal
  colors (checked: the old mode had two colors for "0.018", the new one none).
- Run: `../.venv/Scripts/python.exe main.py`. Git: the repo root is
  `VisPy_playground/`, commit only selected folders.
