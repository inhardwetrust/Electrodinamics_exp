---
name: antenna-design
description: Design, tune and verify an antenna (a patch, a dipole, a feed network) with customcst - a parametric model, sweeps on the remote server, the far field (pattern, D, G, efficiency), a final run the user opens in the window, and an honest report. Use when the user asks to design, optimize, compare or explain an antenna or its feed in customcst, or to "compute it properly on the server", including a design whose model is a myCad part (a design project folder beside customcst).
---

# Antenna design with customcst

customcst is an FDTD simulator (this folder). Read `REFERENCE.md` first
(the facts: project file, body types, mesh, runs, results, the window's
commands) and follow the `customcst` skill (`.claude/skills/customcst/`)
for each simulation; this skill is the antenna method on top of them.
The user speaks Russian: answer in Russian, keep code, comments and docs
in English.

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

Models are written by code, not drawn: `tools/make_test_models.py`, or a
myCad part in a design project (section 8). In make_test_models a new
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

Rules the code relies on (REFERENCE 5):
- a lumped port is a truncated cone between two conductors, its SMALL end
  "+"; a lumped element is a cylinder; a waveguide port's plane ends the
  domain on its side;
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
The server must run the current code (solver/, project/, tools/): the
user copies it by hand; `sync-code` only when they ask. A server with old
code fails on a newer .toml ("unknown key"), at import or, worse,
computes old physics. A sweep uploads its own sweep.py and
make_test_models.py, not the rest.

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

## 6. Read the results

REFERENCE 12 (`list_results`, `figures`, `cut`, `beamwidth`, `summary`).
For a patch: S11 minimum and the -10 dB band; per far-field frequency D,
G, realized G, efficiency, the beam direction, the E (φ 0) and H (φ 90)
beamwidths, front/back, and the front asymmetry (U(θ) against U(-θ) in
each plane, θ < 80°: `scripts/sweep.py front_asymmetry`).

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

## 8. The model from myCad (a design project)

Instead of a generator in `tools/make_test_models.py`, the model can be a
myCad part (`../myinventor/`, the parametric modeler; read its skill
`../myinventor/.claude/skills/mycad/SKILL.md`). Such a design lives in its
own folder beside the tools (the first: `../patch_antenna_sma/`; read its
README.md and CLAUDE.md): `model/` the part, `sim/` the customcst project,
`variants/` the part as it was for each run, `notes/log.md`.

One model, changed in place, each change a run (agreed with the user):
The user's customcst window shows each step; Claude drives it through
`tools/ui.py` (ARCHITECTURE "Control by Claude"):
1. Talk over what changes in the geometry and why.
2. `ui.py lock "what I am doing"`: the user only looks now. Refused while
   they edit something: ask them.
3. Copy the part as it is now to `variants/runNN.part.toml` (NN: the run
   it will give) with its report beside it
   (`main.py PART --report --json > variants/runNN.mycad.json`), then edit
   the open part (the myCad window reloads it).
4. Export over the project's STEP:
   `../.venv/Scripts/python.exe ../myinventor/main.py model/X.part.toml --export-step sim/X.step`.
   Only when the geometry changed (compare the report's parameter values
   and bodies with the last run's .mycad.json): every export changes the
   file's bytes (a time stamp, OCC's entity order), and the run
   fingerprint hashes the bytes, so an export of the same geometry would
   mark the last run outdated for nothing.
5. `ui.py reload`, `ui.py wait`, `ui.py status`: `unassigned` (new bodies:
   assign them in the .toml, reload again), `orphans`, `problem` (must be
   null), `shorts` (must be empty), `cells`. The .toml keeps its types by
   body name.
6. `ui.py run_server`, then `ui.py wait --server` (in the background: a
   run takes long); the window fetches the finished run itself (else
   `ui.py fetch`). Do not export over the STEP while that run goes on: its
   result would come back already outdated.
7. Read the figures from the run file (section 6), `ui.py show "Run N"
   "Run N-1"`, `ui.py tab results` (or farfield), then
   `ui.py unlock "look at Run N: ..."` and report.
8. Log the round in notes/log.md: Run N = variants/runNN, what changed,
   the figures. Old runs stay in Results, marked outdated: they still show
   and compare.
If an answer says "not in control", the user took the control back: stop
and ask. No window open (`ui.py` exit code 2): the same steps work with
scripts (`run.py --dry-run`, `remote_tasks.py launch / wait-run / fetch`);
an open window without the lock reloads changed files by itself.

The first time: export the STEP into `sim/`, write the .toml beside it
(`Project.load(step)`, `set_type(body, ...)`, `set_material`, band, mesh,
far field, `save()`), `run.py sim/X.step --dry-run` (the port found, no
shorts). Several values of one knob at once (L at four values) is a batch:
export each with `--set NAME=FORMULA` into a `zz_` folder with a copy of
the .toml renamed to it, and run them together on the server.

Names and the connector:
- myCad names an imported body `<import>:<its name>` (`SMA:connector_wire`);
  the .toml quotes such a key. Do not rename bodies once the .toml has
  types: the types follow the names.
- A non-ASCII name (`Volumenkörper16`) must come through the STEP intact:
  read the exported STEP's body names back (`read_step`) before assigning.
- A coax connector's port is its end cylinder, type `waveport`
  (REFERENCE 5); the dry run says whether it is found. Its plane ends the
  domain on that side with no padding, so the coax runs through the
  far-field box: the box leaves the coax's inside out (REFERENCE 16), the
  efficiency is right, the pattern's level ~0.2 dB low. Still check the
  run's "body crosses the far-field box" line: other metal (the
  connector's body, the board) reaching the box is not left out.
- Conductors may touch or overlap (a pin on the line, solder on a pad):
  PEC merges. Two dielectrics in one place: the later body wins cell by
  cell; avoid it.

## Pitfalls we hit

The general ones are in REFERENCE 16. For antennas:
- too little padding: the far-field box crosses the board (the run's info
  says so): ~λ/2 of air at the lowest far-field frequency;
- feed lines on the antenna's side of 1.6 mm FR4 radiate and lose: the
  beam tilts, the efficiency drops to 40 %; a feed behind the ground
  (4 layers) is shielded;
- an asymmetric feed tilts the beam 1-3° and makes the back lobe
  asymmetric: that is physics, not a mesh error;
- thin layers (0.2 mm prepreg, 35 µm copper) shrink the time step: the
  4-layer patch ran 28 min against 3 min on 1.6 mm: sweep coarse.
