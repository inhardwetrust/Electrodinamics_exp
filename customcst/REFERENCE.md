# customcst reference

What customcst computes, how it works inside, and how Claude drives it.
For a session with a clean context: read this, then work. The skill
`.claude/skills/customcst/SKILL.md` is the procedure; this file is the
facts it relies on. `antenna-design` (the same folder) is the method for
antennas on top of it. The history, the validations and the reasons are in
ARCHITECTURE.md (named sections below); the module headers are the final
word on every key.

Contents:
1. What customcst is
2. How it works inside
3. The model: STEP in
4. The project file (.toml)
5. Body types: what each needs in the geometry
6. Materials
7. Holes and vias
8. The mesh
9. The simulation: band, pulse, boundaries, stop, far field
10. Running: the window, run.py, Python
11. The server
12. Results: run files and how to read them
13. Driving the window: the control channel
14. The window from the user's side
15. How to work: the method
16. Pitfalls already met
17. Not there yet

All paths are relative to `customcst/`; the Python is
`../.venv/Scripts/python.exe` (Windows; add `-X utf8` when the output has
non-ASCII names). Lengths in the .toml are mm, frequencies GHz.

---

## 1. What customcst is

A 3D electromagnetic simulator in the manner of CST Microwave Studio's
time domain solver, written from scratch (Python, numpy / numba, OCP for
STEP, Qt + VisPy). It reads a STEP model, lets each body be a conductor,
a dielectric, a port or a lumped element, meshes it on a rectilinear
(non-uniform) grid, runs FDTD with a Gaussian pulse and gives:
- the S-matrix and Z_in over a band (VNA-like views, Smith, TDR,
  Touchstone export);
- the far field at chosen frequencies (D, G, realized G, efficiency,
  polar cuts, a 3D pattern);
- Z0 of a line at a plane, Z0 profiles.

The model is defined by the STEP (geometry, body names) plus a project
file beside it (`model.step` → `model.toml`). Runs are saved next to the
model (`model.results/`). The user works in the window; Claude writes the
.toml, runs, reads the results and drives the window (section 13).

## 2. How it works inside

| Level | Folder | Input → output |
|---|---|---|
| 0 CAD | `geometry/` | STEP → `Geometry`: bodies (triangles, name, kind solid / sheet, color, holes). The only OCP code (plus the KiCad import) |
| 1 project | `project/` | Geometry + `.toml` → `Project`: types per body, materials, settings; ports, elements, waveguide ports found from the bodies |
| 2 mesh | `mesh/` | Project → `RectilinearGrid` (x[], y[], z[] lines) |
| 3 discretize | `discretize/` | grid × geometry → `MaterialGrid`: ε, σ on E edges, μ on H edges, PEC edge mask, port columns; `shorts` |
| 4 solver | `solver/` | FDTD (`fdtd.py`, numba kernels), CPML, ports, far-field box → `SimulationResult` |
| view | `view/` | the window: tree, 3D view, Results, Far field, Remote tab, control channel |

- `solver/simulation.py` holds the run's steps (`prepare`, `problem`,
  `parameters`, `simulate`); the window, `run.py` and the scripts call the
  same functions, so a run is the same wherever it was started (to the
  bit).
- Everything is SI inside; the .toml holds the units shown to the user.

## 3. The model: STEP in

- `geometry/step_io.py read_step(path)` → `Geometry` (`.bodies`, `.bounds`
  in m, `.source`). Each body: `name`, `kind` ("solid" or "sheet"),
  `bounds` (m), triangles, `holes`.
- **Body names** come from the CAD: a multi-body part keeps each body's
  name (`MANIFOLD_SOLID_BREP('port')`); a single-body part takes the
  part's name. Solids written under one name (myCad, our writers) are one
  body. Disjoint solids fused into one shape may come back as `name#1`,
  `name#2`. Non-ASCII names must be escaped in the file (`\X\F6`); myCad
  and the connector files do it.
- **Units**: the STEP's own; everything in mm in the .toml.
- **Up axis** (`[view] up`): the model's vertical axis for the view and
  the Z0 tools (Inventor and SolidWorks use Y); it changes no physics.
- **Writing a model from code**: `step_io.write_step(path, [(name, OCP
  shape in mm, (r, g, b) or None), ...])` (`tools/make_test_models.py`).
  A model from myCad: its CLI `--export-step` (../myinventor/REFERENCE.md).

## 4. The project file (.toml)

Beside the STEP, same stem. Written atomically, after every edit in the
window. A file that does not parse is never overwritten (the window goes
read-only). Unknown keys, types or bad values raise `ProjectError` naming
the file and the body.

```toml
# customcst project for model.step     (first line: regenerated on save)

[simulation]
f_min = 4.5            # GHz
f_max = 7.0            # GHz
padding = 26.0         # mm, air around the model; 0 = auto (λ/4 at the center frequency)
trim_pulse = false     # shorter pulse tails (narrow bands)
accuracy = "-50"       # "-30" | "-40" | "-50" | "-60" dB: the stop level
excite = "all"         # "all" or one port's name

[mesh]
cells_per_wavelength = 40.0    # at f_max, in each material (5..200)
cells_per_feature = 8.0        # min cells across a gap between fixpoints (1..50)
max_ratio = 1.3                # max step ratio of neighbor cells
min_step = 0.0                 # mm, fixpoints closer merge; 0 = auto
slanted = true                 # refine the step around slanted metal

[farfield]
frequencies = "5.6, 5.757, 5.9"   # GHz, within f_min..f_max; "" = off
f0 = 5.757             # GHz, what a monitors preset's offsets refer to; 0 = the band's center

[bodies.Substrate]
type = "dielectric"
material = "FR4"       # a preset of [materials] (its values copied in), or "Custom"
eps_r = 4.3
tan_d = 0.02
f_ref = 5.757          # GHz: tan δ is exact here (see 6)
mu_r = 1.0

[bodies.Patch]
type = "pec"

[bodies.feed]
type = "port"
impedance = 50.0       # Ω

[bodies."SMA:Volumenkörper16"]    # quoted: a name with ":" or non-ASCII
type = "waveport"

[holes.diameters]      # through holes by drill (mm): "via" (a PEC cylinder) or "none"
"0.4" = "via"
[holes.single]         # one hole by its center (mm); beats the diameter rule
"-6,0,0.8" = "none"

[materials.FR4]        # the library (built-ins Default, FR4, PTFE + own presets)
eps_r = 4.3
tan_d = 0.02
f_ref = 5.757
mu_r = 1.0

[view]                 # the last display (camera, switches, hidden bodies): never a result input
up = "z"

[z0path]               # the Z0 path (a display tool)
```

- Bodies without a section have no type: they are **not simulated**
  (as if absent). The window and `status` list them as unassigned.
- Sections whose body is missing from the STEP are kept (the body may come
  back after a CAD edit) and reported as orphans.
- **Presets** (ARCHITECTURE "Mesh and monitors presets"): templates beside
  the model, `model.presets/mesh/<Name>.toml` (the [mesh] keys) and
  `model.presets/monitors/<Name>.toml` (`offsets = "-2, 0, 2"`, % from
  [farfield] f0); built-in: mesh Draft 20/4, Sweep 30/4, Fine 40/8,
  Final 50/12; monitors f0, f0 ±2 %. Applying copies the values in
  (`project.presets.apply(project, kind, values)`, `all_presets(step,
  kind)`); a preset edited later changes no project.
- `[view]` and `[z0path]` never outdate a run; `[farfield]` neither (it
  changes no S). Everything else is in the run fingerprint (section 12).

**Writing it from Python** (`project/project_file.py`; validates each
call, then `save()`):

```python
import sys; sys.path.insert(0, ".")
from geometry.step_io import read_step
from project.project_file import Project

step = "path/model.step"
bodies = {b.name: b for b in read_step(step).bodies}
p = Project.load(step)                       # the .toml beside it, or an empty project
p.set_type(bodies["Substrate"], "dielectric")
p.set_material("Substrate", "FR4")           # copies the preset's values in
p.set_value("Substrate", "f_ref", 5.757)     # a changed value turns it "Custom"
p.set_type(bodies["Patch"], "pec")
p.set_type(bodies["feed"], "port"); p.set_value("feed", "impedance", 50.0)
p.set_setting("simulation", "f_min", 4.5)    # checked against the others (f_max > f_min, far field in band)
p.set_setting("mesh", "cells_per_wavelength", 30)
p.set_setting("farfield", "frequencies", "5.757")
p.add_material("Rogers4003", {"eps_r": 3.55, "tan_d": 0.0027, "f_ref": 10.0})
p.orphans(read_step(step))                   # sections without a body
p.save()
```
`set_type` needs the body object (it checks the kind: a sheet cannot be a
dielectric). Set the far-field frequencies after the band.

## 5. Body types: what each needs in the geometry

| Type | Kind | Parameters | Geometry rule |
|---|---|---|---|
| `pec` | solid or sheet | none | any conductor; touching or overlapping PEC bodies are one conductor on the grid (no union needed) |
| `dielectric` | solid | eps_r, tan_d, f_ref (GHz), mu_r, material | the cell center decides; in an overlap the later body in the list wins (avoid overlaps of two dielectrics) |
| `port` (lumped) | solid | impedance R (Ω) | a **truncated cone** between two conductors; its SMALL end is "+"; each end inside or on a PEC body (two bodies, or one body whose gap the axis crosses); the gap (the axis outside metal) is the port; one edge column |
| `lumped` | solid | topology series / parallel, r (Ω), l (nH), c (pF); 0 = absent | a cylinder or cone across a gap between two PEC bodies; no polarity; the radius is its half width (columns in parallel) |
| `waveport` | solid | none (Z0 = the line's own) | a **cylinder** (coax) or a **box** (PCB line) standing on the end face of the line, outside the model, axis along x, y or z; see below |

**Waveguide port** (`project/waveports.py`, ARCHITECTURE "Waveguide
ports"):
- The face touching the model is the port plane; the line is fed from the
  outside face toward it. The body is only a marker (never meshed).
- **The port plane becomes the domain end on that side, with no padding**:
  the line runs on into the absorber as if it went forever.
- Coax: the circle must reach the outer conductor: the conductor touching
  the circle is ground, the one inside the signal.
- Box: its face must cover the line's field (several widths, air above and
  below); the ground must reach the box's border.
- A waveguide port has no gap parasitics (a lumped port adds ~its own L,
  C, like CST's discrete port): for a connector use a waveguide port.

The window and `run.py --dry-run` say which ports were found (`port feed
50 Ω`) or why not. A model needs at least one valid port to run.

## 6. Materials

- Built-in presets (`project/materials.py`): Default (air, 1), FR4 (4.3,
  tan δ 0.02 at 3 GHz), PTFE (2.1, 0.0002). Own presets in `[materials]`;
  a built-in can be edited in the file (the library wins on load).
- **tan δ is exact at f_ref only**: the conductivity is σ = 2π f_ref ε0
  εr tan δ (constant), so the loss tangent scales as f_ref / f. Set f_ref
  to the design frequency (outside the band it is a warning).
- Conductors are perfect (PEC): no copper loss, no skin effect.
- A body that should be air: leave it unassigned (unassigned bodies are
  skipped by the discretization).

## 7. Holes and vias

Through holes found in the bodies (drills through FR4 and both coppers are
one hole) are listed under Model > Holes. A rule per drill diameter (or per
hole) makes them `via` (a PEC cylinder filling the hole) or leaves them
open. A KiCad import (`Import KiCad...`: the board's STEP → one body per
copper layer, pads cut, vias as one body) sets them up. ARCHITECTURE
"Holes and vias", "KiCad import".

## 8. The mesh

A rectilinear grid (tensor product of x, y, z lines), as in CST's
hexahedral mesh:
- **Lines at every body face** normal to an axis (fixpoints), then filled
  so that no step exceeds λ(f_max) / cells_per_wavelength **in each
  material** (λ / sqrt(εr) in FR4), and a gap between two fixpoints gets
  at least cells_per_feature cells; neighbor steps grow at most
  max_ratio. Fixpoints closer than min_step merge (auto: λ/200-ish).
- **Slanted metal** (a 45° trace, a rotated pad, an arc) puts no lines:
  `slanted = true` refines the step around it to (gap or width) /
  cells_per_feature.
- **Cost**: cells = nx·ny·nz; the time step follows the smallest cell
  (thin layers: 35 µm copper, 0.2 mm prepreg make it small and the run
  long). Air padding multiplies the cells (an antenna's λ/2 of air on all
  sides: ×5 in z). The tree's Mesh rows and the dry run show cells, nx ×
  ny × nz, the min / max step.
- **Shorts**: a gap narrower than the grid resolves turns into metal. After
  discretizing, metal pieces are checked: a port whose two ends (or a
  waveguide port whose signal and ground) are one piece is reported as a
  short (`MaterialGrid.shorts`, red under Mesh). The window asks, run.py
  refuses without `--allow-shorts`, the control channel refuses. A short
  can be meant (a PIFA's short pin).
- Typical: 20 / 4 for a first look; 30 / 4-5 for sweeps; 40 / 8 to 50 / 12
  for a final run. Converged when the next finer mesh moves the figure
  you care about by less than you care about (ARCHITECTURE "Z0 accuracy
  of narrow lines": a narrow line needs ≥ 4 cells across).

## 9. The simulation: band, pulse, boundaries, stop, far field

- **Pulse**: a sine-modulated Gaussian, centered in the band, 20 dB down at
  f_min and f_max, no DC. A narrow band means a long pulse; `trim_pulse`
  cuts its tails at 1e-4 instead of 1e-7 (a quarter less time, same
  result).
- **Boundaries**: 8 CPML cells outside the domain on every side (open
  space). The domain is the model's box plus `padding` of air on every
  side (auto: λ/4 at the center frequency), except a waveguide port's
  side, which ends at its plane.
- **Excitation**: `excite = "all"`: one run per port, the full S-matrix.
  One port's name: one run, its column (S11, S21...); the other ports are
  matched loads.
- **Stop**: each port's run stops when the field energy has fallen
  `accuracy` dB below its peak (after the pulse): -40 usual, -50 / -60 for
  resonant structures and narrow bands (an antenna: -50). Also stops after
  30 pulse lengths.
- **Far field** (`[farfield] frequencies`, ARCHITECTURE "Far field"): a
  Huygens box 2 cells inside the meshed domain collects E and H on its
  faces at those frequencies during the run. **It must lie in air**: keep
  the model inside the padding (~λ/2 at the lowest far-field frequency is
  safe). A body crossing the box is reported in the run's info ("a body
  crosses the far-field box"). Per excited port. Quantities: D (over the
  radiated power integrated on the sphere), G (over the accepted power),
  realized G (over the incident power), efficiency = radiated /
  accepted. θ from +z, φ from +x toward +y.
- **Speed**: numba kernels on all cores. The dry run gives the cells, the
  memory, the time step and the pulse length. On the server (16 cores),
  -50 dB, 4.5-7 GHz: a patch on 1.6 mm FR4, 2.2 M cells, smallest step
  0.133 mm: 3 min; the 4-layer patch (0.2 mm prepreg, 35 µm copper),
  3.5 M cells, smallest step 0.0275 mm: 28 min. The smallest step costs
  more than the cell count.

## 10. Running: the window, run.py, Python

**The window** (`main.py [model.step] [--locked]`): ▶ Simulate (local,
off the GUI thread), ■ Stop; the Remote tab's Run on server / Fetch
(section 11).

**run.py** (no Qt, same steps as the window):

    ../.venv/Scripts/python.exe run.py model.step [--excite NAME] [--threads N] [--dry-run] [--allow-shorts] [--every S]

Dry run (mesh, checks, memory; no run, no lock):
```
model      .../patch_5g8_4layer.step
prepared   in 2.5 s
grid       209 × 225 × 75 = 3 526 875 cells, step 0.0275–1.07 mm
port       feed 50 Ω
run        4.5-7 GHz, ports feed, stops at -50 dB; numba, 16 threads
far field  at 5.6, 5.7, 5.757, 5.8, 5.9 GHz
memory     0.57 GB of arrays (4,934,475 cells with PML), time step 56.4 fs, pulse 3.09 ns
dry run: nothing simulated
```
Exit codes: 0 saved (a new run file), 1 failed, 2 not runnable (no port,
shorts, a bad project: the reason printed), 3 another run holds this
machine (`run.lock`), 130 stopped. `model.status.json` beside the model
tells its state while it runs (meshing / running / done / failed /
stopped, port k of n, energy dB).

**Python** (`solver/simulation.py`; what `run.py` does):
```python
from solver.simulation import prepare, problem, simulate
model = prepare(step)                    # read, ports, mesh, discretize
why = problem(model.project, model.mesh, model.materials)   # None = can run
model.materials.shorts                    # [] = none
sim = model.project.settings["simulation"]
result = simulate(model.materials, sim["f_min"], sim["f_max"], end_db=-50.0, farfield=[5.757])
```
(`simulate` does not save; `run.py` saves with `result_file.save_result`.)

## 11. The server

The Remote tab's machine (`remote/remote.toml`: host, user, password,
path, python; **holds the user's password: never print or commit it**).
The user's real server: act there only when the user allowed server work
in the conversation; never stop or delete their runs.
- The window: Run on server = push (STEP + .toml into `<path>/models/`) +
  start `run.py` there under nohup; the window follows it (every 15 s) and
  fetches the finished run into `model.results/` as the next "Run N".
  Fetch takes finished runs; Stop stops it. One run at a time per server
  (`run.lock`).
- **The server keeps nothing it does not need.** After a fetch of a done
  run whose every run file there is taken, the model's files on the server
  go (STEP, .toml, log, status, `model.results/`); the next Run on server
  pushes them again. A failure to delete is only reported (the runs are
  taken). Left behind: failed or stopped runs, runs never fetched, old
  models. **Clean server...** (Remote tab) lists `models/` by model (size,
  run files there, state) and deletes the ticked; a model whose run goes
  is never deleted. From Python: `sync.server_models(config)` →
  [ServerModel name, files, size, runs, state, active],
  `sync.clean(config, [names])` → {deleted, skipped}. Deleting is the
  user's call: ask before cleaning models that still have run files.
- **The code there.** The user copies the code to the server by hand
  (the server has no git). An older code there fails on a newer project
  file: "cannot read the model: ... [farfield]: unknown key 'f0'", or an
  ImportError, or worse, computes the old physics. Then tell the user the
  server needs the current code; `remote_tasks.py sync-code` (it backs the
  old code up first, keeps the 3 newest backups) only when they ask.
- Scripts (`.claude/skills/antenna-design/scripts/remote_tasks.py`):
  `status` (run.lock, runs, zz_ folders, disk), `sync-code` (backup +
  upload of the git-tracked code), `sweep NAME --
  ARGS` (sweep.py there in `zz_NAME/`), `wait-sweep`, `clean`, `launch
  STEP` / `wait-run STEP` / `fetch STEP` (as the window, recorded so the
  window shows the pending run).
- The server has no git and little disk (~1 GB free): clean `zz_` folders.
- Over SSH start with `cd ROOT || exit 1; nohup ... & echo $!`, never
  `cd && nohup ... &` (the channel hangs). A timed-out call may still have
  started the process: check `status` before starting again.

## 12. Results: run files and how to read them

- Each finished run: `model.results/<save time>.npz` (numpy, no pickle):
  frequencies (Hz), port names, reference R, S[j, k, f], Z_in, info
  lines, name ("Run N", renamable in the window), save time, elapsed,
  the fingerprint, a snapshot of the parameters, the far field per
  excited port.
- **Fingerprint**: SHA-256 of the STEP's bytes + body types and values,
  hole rules, [simulation] and [mesh]. When the current model differs,
  the run is still listed but **outdated** (compare old against new: that
  is how variants are compared). Every re-export of a STEP changes its
  bytes (a time stamp): export only when the geometry changed.

```python
import numpy as np
from solver.result_file import list_results, fingerprint
from solver.farfield import figures, cut, beamwidth, summary
runs, errors = list_results(step)        # oldest first
run = runs[-1]
run.name, run.saved, run.ports, run.info  # info: text lines (grid, time, far-field summary)
f = run.frequencies                      # Hz
s11 = run.s[(0, 0)]                      # complex array; (j, k) = S_{j+1, k+1}
z = run.z_in[run.ports[0]]               # complex Ω
current = run.fingerprint == fingerprint(Project.load(step))
ff = run.farfield[run.ports[0]]          # {} without far field; per excited port
figures(ff)   # per frequency: frequency, directivity, gain, realized, efficiency, theta, phi
summary(ff)   # the same as text, plus the box-crossing warning
angles, db = cut(ff, k, "phi", 0.0)      # plane φ = 0 (E-plane of an x-polarized patch), dBi
cut(ff, k, "phi", 90.0)                  # H-plane;  cut(ff, k, "theta", 90.0): the horizon
cut(ff, k, "phi", 0.0, quantity="gain", component="ludwig3_x")   # co / cross polarization
beamwidth(angles, db)                    # (peak angle, -3 dB width or None)
```
Components: total, theta, phi, ludwig3_x, ludwig3_y; quantities:
directivity, gain, realized. dB of an exact zero is floored (−100 dBi);
cast complex64 arrays to complex128 before squaring small values.
Typical figures: S11 minimum (dB, GHz) via `np.abs(s11).argmin()`, the
−10 dB band, Z at f0, then D / G / efficiency / beam direction / F/B.

## 13. Driving the window: the control channel

The open window listens on 127.0.0.1 (a free port; port and a random
token in `control.json` beside the app settings: `%APPDATA%\customcst\`;
`CUSTOMCST_CONTROL` / `CUSTOMCST_SETTINGS` move it). The client is shared
with myCad:

    ../.venv/Scripts/python.exe -X utf8 tools/ui.py [--app customcst] COMMAND [ARGS]

Exit 0 (the result as JSON on stdout), 1 (the window's error on stderr),
2 (no window). Arguments `key=value` (JSON when they parse); `open`,
`lock`, `unlock`, `tab`, `screenshot` take their first word
positionally; `show` takes the run names. From Python:
`from tools.ui import send, wait; send("tab", name="results")`.

| Command | Needs the lock | Arguments | Does / answers |
|---|---|---|---|
| `describe` | no | | app, protocol 1, each command's help |
| `status` | no | | `model`, `locked`, `busy` {mesh, local_run, transfer}, `server_run` (the recorded launch: state...), `tab`, `project_error`, `bodies` {name: type}, `unassigned`, `orphans`, `problem` (null = can run), `shorts`, `cells`, `runs` [{name, saved, current, shown}], `message` |
| `screenshot` | no | `path` (absolute) | `window.grab()` to a PNG, for Claude's own eyes |
| `lock` | no | `message` | Claude in control: the banner; refused while the user edits a tree field or a dialog is open |
| `unlock` | yes | `message` | control back; the message in the status bar |
| `open` | yes | `path` | read a model (no dialog: a file that does not read is the error) |
| `reload` | yes | | the open model again (STEP + .toml), the camera kept; meshing follows (`wait`) |
| `tab` | yes | `name` | `3d`, `results`, `farfield` |
| `show` | yes | run names | tick these runs (the others off) |
| `run_server` | yes | | Run on server (refused: no model, cannot run, shorts, a run going, a transfer, Remote not set) |
| `fetch` | yes | | Fetch |
| `preset` | yes | `kind` (mesh / monitors), `name` | apply a preset (built-in or the model's); answer: the status |
| `wait` (client) | | `--server`, `--timeout S` | polls `status` until nothing is busy; `--server` also until the server's run has ended |

- **Two modes.** Normal: the user works; Claude may read; every change is
  refused ("not in control"). Locked (`lock`, or `main.py MODEL
  --locked`): a yellow banner "Claude is in control: <message>" with
  "Take control back"; the toolbars and the side panel (tree, Materials,
  Remote) are off; the 3D view, the view buttons, the tabs, Results and
  Far field work for looking.
- **Locked, the window does not reload from disk by itself**: write the
  files, then `reload`. Unlocked, it reloads a changed STEP or .toml by
  itself once both have settled (not during a run, a transfer, a mesh
  job, a tree edit or a dialog); the camera stays.
- **"not in control"** after a lock: the user took control back. Stop and
  ask; never re-lock on your own.
- Commands never open dialogs; long work goes on after the answer
  (`ui.py wait`).
- The window only gets the channel when started by a version that has it:
  a window open from before needs a restart.

## 14. The window from the user's side

- **Toolbar**: Open STEP, Import KiCad, Recent, Reload, Reset view,
  ▶ Simulate, ■ Stop, Edges, X-ray, Ref. planes, Up; second row: Mesh,
  Materials (the plane as the solver sees it), Section, the plane's axis
  and position, Z0 here, Z0 path.
- **Left panel**: Project tree (Simulation: settings, Monitors with Ports
  and Far field, Results with one row per run (current / outdated, tick
  to show, rename, delete); Mesh: settings and the grid's figures; Model:
  Z0 path, Holes, Bodies with their types, right click to set a type,
  double click a value to edit; right click Mesh or Far field: presets),
  Materials (the library), Presets (mesh and monitors templates), Remote (the
  server, Push, Fetch, Run on server, Stop).
- **Tabs**: 3D View (left drag rotates, Space / Shift + left pans, wheel or
  right drag zooms, view buttons top right, axes gizmo bottom left; the
  far-field pattern in 3D when "Show in 3D" is on), Results (S in dB,
  phase, group delay, SWR, Smith, Z in; a marker; reference plane shift;
  normalize; .sNp), Far field (polar cuts per frequency, port, quantity,
  component, plane; beamwidth, front/back).
- TDR and Z0 profile windows from the Results / Z0 tools.

## 15. How to work: the method

1. **The model and its project.** Read the STEP's bodies (names, kinds,
   bounds). Assign every body a type (section 5): conductors `pec`,
   substrates `dielectric` with a material and f_ref at the design
   frequency, the port. Band around the design frequency (±20-30 %),
   accuracy, padding, far field. Leave nothing unassigned unless it is
   meant to be air.
2. **Check before running**: `run.py MODEL --dry-run` (or the window's
   `status`): the port found, `problem` null, no shorts, the cells and the
   time step reasonable. A short or no port: fix the geometry or the mesh,
   do not run.
3. **Coarse first** (20-30 cells/λ, 4 per feature) to see the resonance;
   tune (one or two knobs at a time); converge the mesh; then a final run
   with the fine mesh and the far field. The user opens it in the window.
4. **Show it** (the user watches): `lock`, act, `reload` / `run_server` /
   `show` / `tab`, `unlock "what to look at"`. Without a window: the same
   with run.py and the scripts.
5. **Read the numbers from the run file** (section 12), never from a
   picture. Compare runs (outdated ones too).
6. **Report**: a table, what changed and why (the physics), what the user
   can open, what is not committed.

A design whose model is a myCad part (one model changed in place, each
change a run): `.claude/skills/antenna-design/SKILL.md` section 8, and the
design project's own README (`../patch_antenna_sma/`).

## 16. Pitfalls already met

- A STEP copied without its .toml runs with the defaults (1-5 GHz, no
  types): copy both.
- A lumped port is a cone (small end +), not a cylinder; its ends must sit
  in two different PEC bodies.
- A body left unassigned is not simulated: a forgotten substrate gives a
  board in air.
- Far-field frequencies outside f_min..f_max are rejected; changing them
  does not outdate a run.
- The far-field box must lie in air: too little padding, or a waveguide
  port (its side has no padding: the box then runs through what stands at
  the port, a connector's coax), gives the "body crosses the box" warning.
  Not validated yet for a connector at the board edge: for a pattern run
  of such a design, compare with a lumped-port feed or check how much
  the warning matters.
- tan δ is exact only at f_ref.
- 45° traces need `slanted = true`; square 90° bends are exact on the grid.
- Thin layers make the time step small: runs 3-5× longer; sweep coarse.
- The resonance moves up ~1-2 % from a 40/8 to a 50/12 mesh: tune on one
  mesh, check the final one.
- Lines on 1.6 mm FR4 radiate and lose: long feed lines on the antenna's
  side tilt the beam and cut the efficiency.
- Every STEP export changes its bytes: the last run turns outdated even
  for the same geometry.
- On the server: old code there fails on a newer .toml ("unknown key"),
  at import, or computes old physics: it needs the current code (the user
  copies it, or `sync-code` when they ask; section 11).

## 17. Not there yet

- Copper loss and surface roughness (conductors are PEC).
- Field monitors (E / H / current plots), only the far field and the port
  quantities.
- Symmetry planes, conformal (subcell) metal: staircase only, slanted
  refinement instead.
- An optimizer: tuning is sweeps by hand (`antenna-design` scripts).
- Frequency-dependent ε (Debye / Lorentz).
- Several runs at once on one server.
