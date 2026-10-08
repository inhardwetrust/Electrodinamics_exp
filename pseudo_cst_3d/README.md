# Pseudo CST

> **Note:** All the code in this project was written by Claude (Anthropic's AI).
> I acted as the meatbag consultant on the interface: ideas, feedback,
> "this looks wrong", and taste.

An interactive playground for building intuition about electrodynamics:
you set up sources and materials, watch the fields develop in real time,
and look at them from any angle. It is inspired by CST Studio, but the goal
is understanding, not production simulation.

The project is a **frontend with pluggable solvers**. Field data comes in
through one small interface, so built-in solvers, analytic models and
(later) external solvers like Meep or openEMS all plug in the same way.

## What it can do

- **2D models:** static point charges (exact analytic field, evaluated per
  pixel on the GPU, or on a solver grid), plus a quasi-static oscillating
  dipole.
- **2D FDTD (TE: Ex, Ey, Hz):** a Yee grid, port excitation (sine, Gaussian,
  modulated Gaussian), dielectrics, lossy materials and PEC, and a CPML
  absorbing boundary (about -94 dB reflection), with Mur as an option for
  comparison.
- **3D FDTD:** the full 3D Yee scheme with CPML, materials and ports. You
  look at it through **slice planes placed inside a rotatable 3D scene**,
  not as volume clutter.
- **Derived quantities, computed exactly on the Yee grid:** div E (total
  charge), div D (free charge), curl E, the Poynting vector S, and the
  energy density u.
- **Fixed-length arrows:** they show direction only, never magnitude. There
  are three strength levels (full, short and faded, ~zero). Where the field
  pierces a 3D slice, ⊙ means toward you and ⊗ means away from you.
- **Heatmaps:** linear, log or symlog scale, viridis or diverging palette,
  and several color-limit policies (fixed, initial, per view, or running
  peak-hold for waves).
- **Qt interface:** the layer and simulation panels are generated from
  declarative parameter schemas. There are play/pause/step controls, live
  diagnostics (energy, port current and power), and slice controls.

The solvers are validated numerically: wave speed, charge conservation
(div E ≈ 0 to round-off), Poynting's theorem, PML reflection, the wave speed
in a dielectric, and 1/r far-field decay in 3D.

## Running

```bash
python -m venv .venv
.venv/Scripts/activate        # Windows; on Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt

python main.py                       # Qt window, default model
python main.py fdtd3d_dipole         # a model from models/ (file name)
python main.py path/to/my_model.toml # any model file
python main.py --list                # list the models
python main.py --canvas-only         # bare VisPy window, keyboard only
```

## Models are files

Each model is a TOML file in `models/`. The file describes the solver,
domain, boundary, excitation, materials, objects and the initial view. The
model selector lists every file in that folder, and **Reload** re-reads the
current one after you edit it. Invalid files are rejected with a message
that says what is wrong and where.

```toml
name = "Dipole + glass slab"
solver = "fdtd2d"                    # analytic | grid | oscillating | fdtd2d | fdtd3d

[domain]
bounds = [[-5.71, -4.0], [5.71, 4.0]]
cells_per_wavelength = 20

[boundary]
kind = "pml"
cells = 12

[excitation]
frequency = 1.0
waveform = "sine"

[materials]
glass = { eps_r = 4.0 }

[[objects]]
type = "port"
a = [-0.25, 0.0]
b = [0.25, 0.0]

[[objects]]
type = "rect"
material = "glass"
lo = [-50, 0.75]
hi = [50, 1.5]

[view]
heatmap = "Hz"
```

Object types: `charge`, `port`, `rect`, `disk`, `wire` (2D); `port`, `box`,
`sphere`, `wire` (3D). A `wire` is a thin PEC conductor along a segment; see
`models/fdtd3d_halfwave.toml` for a half-wave vibrator fed in its gap. The full key reference is at the top of `model_spec.py`.

**Canvas keys:**

| Key | Action |
|---|---|
| `Q` | Next heatmap quantity |
| `V` | Next arrow quantity |
| `Space` | Play / pause |
| `N` | One step |
| `R` | Reset |
| `+` / `-` | Steps per frame |

In 3D: drag to rotate, use the wheel to zoom, and Shift + drag to pan.

## Architecture (short)

| File | Role |
|---|---|
| `field.py` | `FieldSource` contract: named quantities, sampling, optional GPU / native-grid paths |
| `simulation.py`, `params.py` | `Simulation` contract (step, reset, parameters, diagnostics) and parameter schemas |
| `fdtd.py`, `fdtd3d.py` | 2D TE and 3D FDTD solvers (NumPy, float32, in-place updates) |
| `field3d.py`, `slicing.py` | 3D field data and slice planes, which turn 3D data into 2D sources |
| `layers.py`, `layer_views.py` | What to show (config) and how to draw it (VisPy) |
| `renderer.py`, `renderer3d.py` | The 2D view, and the 3D view with the slice plane inside the scene |
| `model_spec.py`, `model_builder.py`, `models/` | Model files: parsing and validation, then building the scene plus the source or simulation |
| `main_window.py`, `qt_forms.py` | Qt shell; forms are generated from the schemas |

## Status and ideas

This is a learning tool. It uses staircased geometry, uniform grids and
NumPy speed (60³ cells ≈ 11 ms/step).

Possible next steps:

- recording results, with a timeline and DFT monitors (amplitude and phase
  at given frequencies);
- several linked slices;
- STEP import (OpenCASCADE or gmsh) with voxelization;
- port impedance and S11, far-field patterns;
- GPU acceleration.

## License

Public domain: [The Unlicense](../LICENSE). Do whatever you want with it.
