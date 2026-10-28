# customcst

> **Note:** All the code in this project was written by Claude (Anthropic's AI).
> I acted as the meatbag consultant: ideas, models, feedback, "this looks
> wrong", and taste.

## Overview

customcst is an attempt to reproduce a part of CST Studio's workflow from
scratch: open a STEP model, assign materials and ports to its bodies, mesh
it, run a 3D FDTD simulation and look at S11, the way CST's time-domain
solver does it.

It grew out of [`pseudo_cst_3d`](../pseudo_cst_3d), a playground for building
intuition about fields on uniform grids with toy models. customcst is the
second, more serious pass:
- real CAD geometry;
- real units;
- a non-uniform rectilinear grid like CST's hexahedral mesh;
- results that can be compared with a calculator and a VNA.

The target so far has been feed lines: an SMA connector on a 4-layer PCB, a
grounded coplanar line, a 50 Ω load at its end. The question is how well it
all is matched.

**What is not there yet:**
- far field and the other antenna quantities (pattern, gain, efficiency);
- field visualization (DFT field monitors on slices);
- wideband dispersive dielectrics.

**What is there** is the foundation these need. It works and is checked
numerically:
- the STEP import;
- the project model;
- the mesher;
- the discretization into the Yee grid;
- the FDTD solver with CPML, ports and lumped elements;
- the S-parameter extraction.

The fields already live on the grid. Far field and field views are the next
levels on top, not a rewrite.

## Models we worked through

**A microstrip line**: the first model, with two discrete ports.

![Microstrip line](docs/images/line.png)

**A coaxial line** with a waveguide port at each end (blue: the reference
planes). Z0 from the 2D port solver: 49.5 Ω.

![Coaxial line](docs/images/coax.png)

**An SMA connector on a 4-layer board**: drawn in Inventor; a coplanar line
with vias to a 50 Ω lumped load. The waveguide port sits on the connector's
coax.

![Connector on a 4-layer PCB](docs/images/connector_pcb.png)

**The same model in section**, with the compute grid shown on the plane.

![Section with the mesh](docs/images/section_mesh.png)

**Its S11**: about −30 dB over 4–6 GHz, with the marker readout (SWR, Z, the
equivalent L or C).

![Results](docs/images/results.png)

**The same board drawn in KiCad** and imported from its STEP export:
- the copper is merged per layer and the pads are cut to the copper;
- the vias become one body;
- the connector is a separate STEP model.

![KiCad board](docs/images/kicad_board.png)

**The KiCad board cut at the top copper**: the trace, the pour, the via
fence and the lumped load in the clearance slot.

![KiCad board in section](docs/images/kicad_section.png)

**The KiCad board's S11**: about −27 dB over 4–6 GHz on the same mesh
settings, within a couple of dB of the Inventor model above.

![KiCad board results](docs/images/kicad_results.png)

**The Z0 profile along the line**: the line impedance at every grid plane.
It shows which part of the feed is off 50 Ω.

![Z0 profile](docs/images/z0_profile.png)

**The Z0 profile along a drawn path**: click the path along the trace in
the 3D view (the plane in blue), and every cross-section is cut square to
it, through the bends and diagonals. Here a KiCad board from the
connector to the switch; the green window is what the section under the
profile's marker takes in.

![Z0 path](docs/images/z0_path.png)

![Z0 profile along the path](docs/images/z0_path_profile.png)

**TDR on the Z0 profile**: the time domain transform of the runs' S11,
like a VNA's, laid on the profile's own axis (the time from each
section's eps_eff). Where each run reflects, next to the profile and to
what a TDR would see of the profile alone. Here the justswitch input:
the switch pin's reflection at the path's end shrinks from run to run.

![TDR](docs/images/tdr.png)

## Features

- **STEP import** (OpenCASCADE). Bodies keep their CAD names and colors.
  Through holes are found and can be filled as vias.
- **KiCad import**: a KiCad board STEP becomes a clean model. There is one
  body per copper layer, with the pads merged in, plus the vias and the
  board. Every via, and every plated hole in a footprint, becomes a solid
  through cylinder of its drill: blind and buried vias are not modeled
  yet. The pads and vias of footprints left outside the board are
  dropped. Re-importing an updated board keeps the assignments of the
  surviving bodies.
- **Project file next to the model** (`model.step` → `model.toml`):
  - body types: PEC, dielectric, discrete port, waveguide port, lumped
    element;
  - a material library (FR4, PTFE, …, with εr and tan δ);
  - frequency range and mesh settings.
- **Non-uniform rectilinear mesh**:
  - lines at the body faces;
  - a step of at most λ/N in each material;
  - a number of cells per feature;
  - a finer step around slanted metal (a 45° trace), sized by its gap;
  - a grading limit.
- **Background discretization** with progress: staircase materials on the
  Yee grid, with PEC edges and a closure of thin metal.
- **3D FDTD solver**:
  - CPML;
  - numba kernels on all cores, with a numpy fallback;
  - stops at a given energy level.
- **Ports**:
  - discrete ports, with a resistance;
  - waveguide ports, which use the line's own quasi-TEM mode from a 2D
    solver. Cylinders work for coax and boxes for PCB lines.
- **Lumped R, L, C**, series or parallel, drawn as a cylinder across a gap.
- **Results**:
  - S11 as LogMag, phase, SWR, Smith chart or Z;
  - a marker readout;
  - reference-plane shift;
  - renormalization to any impedance;
  - Touchstone `.sNp` export;
  - every run is saved, can be compared with the others, and is marked
    outdated when the model changes;
  - far field of an antenna (Simulation > Monitors > Far field: the
    frequencies): the "Far field" tab draws polar cuts (vertical planes
    phi = const or cones theta = const) of the directivity, gain or
    realized gain in dBi, total or one polarization (E_theta, E_phi,
    Ludwig-3 x / y: co / cross), with the beamwidth, front / back and the
    radiation efficiency; "Show in 3D" puts the pattern as a colored
    surface around the model in the 3D view. Example: `models/patch_5g8` (a 5.8 GHz patch on
    FR4: D 7.0 dBi, G 5.4 dBi, efficiency 69 %).
- **TDR** of the shown runs: low pass (step as Z, impulse) with a causal
  continuation to DC, or band pass; on a Z0 profile's axis, with the
  profile as a TDR would see it.
- **Line impedance tools**:
  - Z0 at any grid plane;
  - a Z0 profile along an axis;
  - a Z0 profile along a path drawn in the model (one per project, kept in
    its file): cross-sections square to it, on a fine grid of their own;
  - computed profiles are saved with the runs: a model reopened unchanged
    shows them without computing again;
  - Z0 convergence at a plane: the plane alone on finer and finer grids,
    the converged value and how far the current 3D grid is from it;
  - an accuracy note for narrow gaps.
- **3D view**:
  - CAD-style trackball navigation;
  - standard views (top, bottom, front, back, left, right, iso) and an
    orthographic projection, from buttons over the view;
  - edges and X-ray modes;
  - a section plane;
  - the mesh plane;
  - material colors;
  - reference planes.
- **Remote runs**: a model set up here runs on a faster machine over SSH
  (the Remote tab; Run on server, Stop, Fetch): one run at a time there,
  followed while the window is open, the finished run taken back. The
  server runs the same program folder; `run.py` also runs a model from a
  console.
- **Numerical checks** (`checks/`) for every level: the mesh, discretization,
  port modes, the FDTD against analytic lines and a cavity, lumped elements,
  vias, the KiCad import.

## Exporting a board from KiCad

1. **Cut the board to what is simulated.** Draw an Edge.Cuts outline
   around the part of the board the run needs: the connector, the line,
   the chip's pin and the ground around them. Remove the copper outside it
   (tracks, zones beyond the outline) and refill the zones (B), so the
   pours end at the outline. The import drops footprints' pads and vias
   left outside the outline, but only reports other copper outside it,
   it does not cut it.
2. **Export the STEP**: File > Export > STEP, with these settings:

   ![KiCad STEP export settings](docs/images/kicad_export_settings.png)

   - Board options: Export board body on; Cut vias in board body off (the
     import makes every via a solid copper cylinder); silkscreen and
     solder mask off (not modeled).
   - Export components: only the ones that belong in the model (Components
     matching filter): the connector, whose 3D model gives the coax, and
     the parts whose 3D models are the lumped elements' cylinders. On the
     justswitch board: `J4, R6, C6`.
   - Conductor options: tracks and vias, pads, zones, inner conductor
     layers on; Fill all vias on; Fuse shapes off (the import fuses each
     layer itself, faster).
   - Coordinates: Drill/place file origin, so every re-export lands at the
     same coordinates (the project's Z0 path, view and planes stay valid).
   - Other options: Substitute similarly named models, Overwrite old file
     and Don't write P-curves on; board outline chaining tolerance Tight
     (0.001 mm).
3. **Import it**: Toolbar > Import KiCad turns `board.step` into
   `board_prep.stp` with its project file. A re-export imported again keeps
   the types of the bodies that are still there and reports what changed.

## Running

```bash
pip install -r requirements.txt
python main.py                    # the last model opened
python main.py models/03_coax_line.stp
python checks/check_fdtd_line.py  # any check
```

The architecture, the design decisions and the plan are in
[ARCHITECTURE.md](ARCHITECTURE.md).

## Running on another machine (remote runs)

A model set up in the app can run on a faster machine, for example a
Linux server with more cores: the app sends the model there, starts the
run, follows it and takes the result back. The run there is `run.py`, the
console runner: it does exactly what the Simulate button does
(`solver/simulation.py`, shared by both), so the S-parameters are the same
as from a local run.

### Once: the same program on the server

Copy **the very same `customcst` folder** the app runs from here to the
server: the same version of the code, nothing changed, nothing left out
(`models/` is not needed: models go there with Push). The server must run
exactly the code the app runs, or its results will not match a local run.
After every update of the app here (a `git pull`, a fix), copy the folder
to the server again.

Then, in that folder on the server:
```bash
python3.12 -m venv .venv
.venv/bin/pip install numpy scipy numba cadquery-ocp
```
The runner needs neither PySide6 nor VisPy. If importing OCP fails on a
headless server with a missing `libGL.so.1`, install the system library
(`sudo apt install libgl1` on Debian/Ubuntu).

### The Remote tab

The Remote tab, next to Project and Materials, says where the server is:
- **Host, Port, User, Password**: the SSH login. The password is kept as
  plain text, so an SSH key is better: leave the password empty;
- **Path**: the `customcst` folder on the server, the one with `run.py`
  (`~/...` is the home folder there);
- **Python**: the Python that runs `run.py`, relative to Path
  (`.venv/bin/python` by default, the venv made above);
- **Check** connects and looks for `run.py` there.

The settings are saved in `remote/remote.toml`, which is not in git;
`remote/remote.example.toml` shows the format. The first connection to a
server asks to trust its host key: compare the fingerprint with
`ssh-keygen -lf /etc/ssh/ssh_host_*_key.pub` on the server. A trusted key
is kept in `remote/known_hosts`.

### Run on server, Fetch, Stop, Push

The buttons under the Project tree:
- **▶ Run on server** sends the open model (its STEP and `.toml`) to
  `models/` on the server and starts `run.py` on it there, in the
  background (`nohup`, its output in `models/model.log`). One run at a
  time on a server: while another one goes, it says which. While the
  window is open it follows the run every 15 s: the status bar shows the
  port, the pulse (first half of the bar) or the energy falling to the
  stop level (second half), and the time. A finished run is taken back by
  itself; a failed one shows the end of its log;
- **Fetch** asks the server how the model's run goes and takes its
  finished runs into the model's results, ticked and shown like a local
  run. If the run still goes, it follows it again. If the server's
  `.toml` or STEP differ from yours (changed here since the run was sent),
  it asks whether to take the server's: yes replaces yours and reloads
  the model;
- **■ Stop** ends the model's run on the server (nothing is saved);
- **Push** only sends the model, without starting it.

Run on server and Push are off until the model can run here: the mesh
built, nothing wrong with it (shorts are asked about, like Simulate).

**Nothing connects by itself**: opening a model asks the server nothing.
Once a run is started, the window can be closed, the network dropped, the
computer turned off: the run goes on on the server. A run started from
here stays a pending row in Project > Simulation > Results ("⏳ on the
server", with its last known state) until it is taken; Fetch asks again
when you want. Right click on the row: fetch, or forget it.

A run taken back is named by this machine: its time is when you started
it here (the time of taking for a run started on the server by hand), its
name the model's next "Run N", and its info says where it was computed.
Runs are downloaded into a temp folder first and moved in only when all
arrived, so a lost connection leaves the local runs as they were.

### By hand, from a console

The same runner without the app, in the `customcst` folder on the server:
```bash
.venv/bin/python run.py models/model.stp --dry-run   # grid, ports, memory; no run
nohup .venv/bin/python run.py models/model.stp > run.log 2>&1 &
tail -f run.log                                      # progress every 30 s
```
Copy `model.stp` and `model.toml` there first, both, set up in the app
(types, elements, ports, mesh and simulation settings). Options:
`--threads N` (default: all cores), `--excite "J2 5"` (one port; like
changing the excite setting, the app then shows the run as outdated
unless the project has the same one), `--allow-shorts`, and `--every S`
(seconds between progress lines). Ctrl+C stops (nothing is saved). Fetch
takes such a run back too; by hand, copy `models/model.results/<date>.npz`
into the `model.results/` folder next to the model here: the app lists it
as a current run if the STEP and its `.toml` are the ones it ran with.

## License

Public domain: [The Unlicense](../LICENSE).
