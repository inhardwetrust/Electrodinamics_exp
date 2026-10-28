---
name: antenna-design
description: Design, tune and verify an antenna (a patch, a dipole, a feed network) with customcst - a parametric model, sweeps on the remote server, the far field (pattern, D, G, efficiency), a final run the user opens in the window, and an honest report. Use when the user asks to design, optimize, compare or explain an antenna or its feed in customcst, or to "compute it properly on the server".
---

# Antenna design with customcst

customcst is an FDTD simulator (this folder). Read `CLAUDE.md` first, then the
parts of `ARCHITECTURE.md` named below as you need them. The user speaks
Russian: answer in Russian, keep code, comments and docs in English.

The way of working this skill describes: agree on the goal and the
structure with the user, then do the iterations yourself (models, sweeps,
convergence, the final run) and come back with numbers, pictures and an
interpretation. Ask only for real decisions (a board stack, a feed scheme,
a trade-off), not for each run.

## 1. Agree on the goal

Write down before modeling, and confirm in one message:
- frequency or band, the match wanted (S11 below -10 dB over the band?);
- what matters in the pattern: gain, beamwidth, symmetry, cross-pol,
  front/back;
- the board: stack (thickness, layers), material (FR4: eps_r 4.3-4.4,
  tan d 0.02), board size, where the connector goes;
- the feed scheme (probe, inset line, two probes in antiphase, aperture,
  lines on one or both sides). Explain the trade-offs when the user asks
  (`docs/education/ru/patch_feeds.html` is the user's notes on this).

## 2. Build a parametric model

Models are written by code, not drawn: `tools/make_test_models.py`. A new
design is a new stem S with three parts (copy `patch_5g8`, `patch_5g8_dual`
or `patch_5g8_4layer`):
- `PARAMS = {...}`: every dimension in mm, with a comment how each starting
  value was estimated (transmission-line formulas, earlier runs);
- `S()`: the bodies `[(name, OCP shape, color)]` built from boxes, cylinders,
  cones; `_trace(points, width, z0, z1)` for axis-parallel lines,
  `_union` / `BRepAlgoAPI_Cut` for insets and antipads;
- `S_project(path)`: body types, material (`set_material("FR4")` and
  `f_ref` at the design frequency: the loss is exact at f_ref only), band
  (f min .. f max around it), mesh, far-field frequencies, `save()`.

Rules the code relies on (ARCHITECTURE "Lumped elements", "Waveguide
ports", project/ports.py):
- a lumped port is a truncated cone between two conductors, its SMALL end
  "+"; a lumped element is a cylinder; a waveguide port sits on a domain end;
- disjoint solids fused into one body come back as `name#1`, `name#2`: map
  types by `name.split("#")[0]`;
- `_trace` adds half the width at the ends: end a line at the board edge
  with `edge + width / 2`;
- keep the antenna inside the air: the padding (Simulation, auto lambda/4)
  holds the far-field box; a body crossing the box is reported;
- write into `models/` only new stems of your own. Never write into the
  user's models or their .toml (copy into the scratchpad to experiment);
  `python tools/make_test_models.py STEM` writes only that stem.

## 3. Check it fast, locally

```bash
../.venv/Scripts/python.exe tools/make_test_models.py STEM      # or write it into the scratchpad
../.venv/Scripts/python.exe run.py models/STEM.step --dry-run   # grid, cells, memory, time step, port found
```
Look for: the port found (`port feed 50 Ω`), no shorts, the cell count and
the time step (thin layers make dt small and runs long). One coarse local
run (cells per wavelength 20-30, per feature 4-5) shows whether the
resonance is anywhere near.

## 4. Tune with sweeps on the server

The server is the Remote tab's machine (README "Running on another
machine"). It is the user's real server: act there only when the user has
asked for server work in this conversation; never stop or delete their runs.

```bash
S=.claude/skills/antenna-design/scripts
../.venv/Scripts/python.exe $S/remote_tasks.py status          # run.lock, runs, zz_ folders, disk
../.venv/Scripts/python.exe $S/remote_tasks.py sync-code       # backup + upload: the server runs THIS code
../.venv/Scripts/python.exe $S/remote_tasks.py sweep NAME -- --stem STEM --params PARAMS \
    --variants '[{"l": 11.0}, {"l": 11.4}]' --cells 30 --feature 4 --f0 5.8 --farfield 5.8
../.venv/Scripts/python.exe $S/remote_tasks.py wait-sweep NAME PID   # run in the background; you are notified
../.venv/Scripts/python.exe $S/remote_tasks.py clean NAME
```
`sweep.py` prints per variant: S11 minimum and at f0, Z_in at f0, D, G,
realized G, efficiency, the beam direction, the front asymmetry of the E
and H planes. The same script runs locally (`scripts/sweep.py FOLDER ...`).
Sync the code after any change to solver/, project/, tools/ that the run
needs; a server with old code fails at import or, worse, computes old
physics.

Knobs and what they do (patch on FR4, from our runs):
- the resonance scales as 1 / L (the resonant length); formulas land 5-10 %
  off: on 1.6 mm FR4 L = 11.9 mm gave 5.33 GHz, 11.1 mm gave 5.85;
- a probe or via: R grows from the center to the edge as cos^2(pi d / L)
  (d from the edge); the probe adds +j30..50 Ohm on 1.6 mm, so the S11
  minimum sits above the R maximum; fed at two points, each sees twice the
  single-feed R;
- arms in antiphase: lambda_g / 2 longer (lambda_g = c / (f sqrt(eps_eff)));
  bends and couplings shift the phase: sweep the length;
- the S11 minimum moves up 20-30 MHz from a 40/10 mesh to 50/12: tune on a
  mesh, then check the final one.

Sweep 3-6 variants at a time, coarse (30 / 4-5), change one or two knobs,
read the trend, narrow down. Two or three rounds are usually enough. When
a knob does nothing to the figure you aim at, stop and find out what
dominates instead (our dual-inset sweep: the meander length changed
nothing because the lines' own radiation set the pattern).

## 5. Converge, then the final run

- Converged when going 30/4 -> 40/8 -> 50/12 changes D by < 0.1 dB and the
  S11 minimum by < ~1 % in frequency. Say what moved.
- The final run is a project of its own that the user opens: a new stem,
  or a copy (`STEM_final.step` + `.toml`, the .toml's first comment line
  renamed), with the fine mesh, accuracy -50 dB, padding ~lambda/2, far
  field at several frequencies across the band. Copy the .toml along with
  the STEP: without it the defaults (1-5 GHz) apply.
- Launch it like the window does, so the pending row shows in the app:
```bash
../.venv/Scripts/python.exe $S/remote_tasks.py launch models/STEM_final.step
../.venv/Scripts/python.exe $S/remote_tasks.py wait-run models/STEM_final.step   # in the background
../.venv/Scripts/python.exe $S/remote_tasks.py fetch models/STEM_final.step
```
  The run lands in `models/STEM_final.results/` as Run N (not in git);
  the user opens the model and sees it in Results and the Far field tab.

## 6. Read the results (solver/farfield.py)

```python
from solver.result_file import list_results
from solver.farfield import cut, beamwidth, figures, surface
run = list_results(step)[0][-1]; ff = run.farfield["feed"]      # per excited port
figures(ff)          # per frequency: D, G, realized, efficiency, beam direction
cut(ff, k, "phi", 0) # E-plane of an x-polarized patch; "phi", 90: H-plane; "theta", 90: horizon
beamwidth(angles, values)
```
ARCHITECTURE "Far field (antenna pattern)" has the definitions (D over the
sphere integral, G over the accepted power, efficiency = radiated /
accepted, Ludwig-3 co / cross).

Sanity figures (a patch on FR4 1.6 mm, 5.8 GHz, probe fed, 40 x 40 mm
ground): D ~7 dBi, efficiency ~70 %, G ~5.5 dBi, 85 / 76 degree beamwidth
(E / H), front/back ~19 dB, -10 dB bandwidth ~4 %. Efficiency far lower
means losses in the feed (long thin lines on FR4: 30-40 %); a beam tilted
off the normal means an asymmetric feed or radiating feed lines.

## 7. Report

To the user, in Russian, short:
- a table: the variants or the final figures per frequency;
- what each number means, what changed and why (the physics, not only the
  numbers); call results that contradict what you said before, and correct
  yourself plainly;
- what the user can open now (model, Run N, which tab, which cut);
- what is not committed, and the next options. Commit and push only when
  the user asks; never commit the user's own files (their models, the
  [view] the window saved into a .toml).

## Pitfalls we hit

- `cd ROOT && nohup ... &` over SSH hangs the channel: use
  `cd ROOT || exit 1; nohup ... & echo $!` (remote_tasks.py does).
- An SSH call that timed out may still have started the process: check
  with `status` before starting it again.
- Far-field frequencies must lie within f min .. f max; they are not in the
  run's fingerprint, so changing them does not outdate a run.
- dB of exact zeros (a symmetry plane's cross-pol, an axis null): floor the
  value before log10; complex64 arrays underflow in float32 - cast to
  complex128 first.
- Square 90 degree bends are exact on the grid; 45 degree traces need the
  slanted-metal refinement (on by default; ARCHITECTURE "Slanted metal").
- Thin layers (0.2 mm prepreg, 15 um planes) shrink the time step: a run
  takes 3-5x longer; sweep coarse.
