# customcst — architecture

The second, more serious pass after `../pseudo_cst_3d`. Differences:

- **Two grids.** The *compute grid* is non-uniform rectilinear (tensor product
  of x-, y- and z-lines with variable spacing, as in CST's hexahedral mesh and
  openEMS). The *display grid* is uniform, as in pseudo_cst_3d; fields are
  resampled from one to the other.
- **Real units.** SI everywhere inside (m, s, F/m, H/m, S/m, Ohm). The UI and
  model files use mm / GHz / ns, like CST.
- **Geometry from STEP**, with materials assigned to bodies.

## Levels (built bottom-up)

Each level talks to the next through plain data (numpy arrays + dataclasses).
Only level 0 knows about OpenCASCADE.

| # | Level | Input -> output | Status |
|---|---|---|---|
| 0 | `geometry/` CAD import | STEP -> `Geometry` (bodies: triangles in m, name, kind solid/sheet, color) | **done** |
| 1 | `project/` model | Geometry + object types per body (materials, later ports) + frequency range + boundaries -> `Project` (`model.step` -> `model.toml`) | types PEC / Dielectric done; ports, setup next |
| 2 | `mesh/` compute grid | Project -> `RectilinearGrid` (x[], y[], z[] node lines): fixpoints at body boundaries, max step λ_min/N in each material, grading ratio limit | **done** (first version) |
| 3 | `discretize/` "parser" | Grid x Geometry -> material arrays on the Yee grid (ε, σ on E-edges; μ on H-edges; PEC edge mask; later edge fill fractions for conformal FDTD) | **done** (staircase) |
| 4 | `solver/` FDTD | Material arrays + excitation -> fields in time; non-uniform Yee, CPML, lumped ports with R. Pluggable: openEMS can consume the same grid | **done** (first version) |
| 5 | `monitors/` | Port V/I -> Z(f), S11; DFT field monitors at chosen frequencies; later far field | S-matrix, Z_in done (`solver/sparams.py`); field monitors next |
| 6 | `view/` display | Compute grid -> uniform display grid -> slices, heatmaps, arrows (reuse pseudo_cst_3d layers) | geometry view done |

## Key decisions

**Where the material values live (level 3).** Not "at grid points": each
quantity lives where the Yee scheme uses it.

- ε and σ: on E-edges (an edge-averaged value: the 4 cells around the edge).
- μ: on H-edges (through the face centers of the primary grid).
- PEC: a mask that forces E = 0 on edges.

The solver only sees per-edge update coefficients (Ca, Cb, Da, Db), as in
pseudo_cst_3d, but now with per-edge lengths and dual lengths.

**"Hollow" bodies.** This depends on the material:

- **PEC and good conductors at GHz.** The skin depth in copper at 2.4 GHz is
  1.3 µm, far below any cell, so only the surface matters. We mark the E-edges
  on (or nearest to) the surface as PEC. Filling the inside gives the same
  answer, because no field gets in. Working with the surface is still the
  right model, for two reasons:
  - it handles thin sheets (35 µm copper, zero-thickness STEP faces) that
    fall between grid lines;
  - it leads to a surface-impedance model for finite σ later.
- **Dielectrics are never hollow.** The field fills them, so ε applies over
  the whole volume.
- **Lossy dielectrics** use a volume σ, or a loss tangent tan δ at a reference
  frequency.

**Grid lines vs geometry.** Levels 2–3 work with the triangles only, never
with OCP. The main operation is "where does a grid line cross the surface":
every edge lies on a grid line. That gives inside/outside along each edge
(staircase) now, and the exact crossing point (conformal / PBA-style
fractions) later, from the same intersections.

**Time step.** In a non-uniform grid, the smallest cell limits dt (Courant).
That is why the mesher limits the smallest step and the grading ratio.

**Local refinement.** Here this means "lines are denser in some places", and
those lines run through the whole domain. True subgridding (refining only a
local box) is much harder and is out of scope for now.

## Level 4 (first version)

`solver/fdtd.py` `FdtdSolver(materials, f_min, f_max, active_port)` (the
equations are in the header).

- **Scheme.** SI units, Yee on the non-uniform grid. curl E uses the
  primary steps, curl H the dual steps (half sums). The outer faces are
  PEC.
- **CPML.** 8 uniform cells outside the meshed domain, with the step of the
  outermost cell. Materials are edge-replicated into the CPML, and the
  profile is sigma' = 0.8 (m+1) c0 / step · d^3.
- **Lumped port.** V_s in series with R, spread over the port column:
  sigma_e = L / (R A_e) and J_e = sign V_s / (R A_e). V and the gap current
  I (H circulation, mean over the column) are recorded every step at their
  own times.
- **Pulse.** A sine-modulated Gaussian, f0 = band center, 20 dB down at
  f_min and f_max, no DC.
- **Run.** Stops when the electric energy is 40 dB below its peak, or after
  30 pulse lengths.

Post-processing is in `solver/sparams.py`: power waves with reference R,
S_jk = b_j / a_k, Z_in = V / I.

VNA view (`solver/vna.py`, no Qt; `view/results_panel.py`,
`view/smith.py`): all from the S-matrix of one simulation, no re-run.
- Formats: LogMag, Phase, Group delay (-dφ/dω, unwrapped), SWR, LinMag,
  Smith and Z in. SWR, Smith and Z in show reflection traces only.
- Traces S_jk are checkboxes; S11 and S21 are on by default.
- A frequency marker reads out dB, phase, SWR and Z = R(1+Γ)/(1-Γ), with
  the series L or C equivalent.
- "Save .sNp" writes Touchstone v1 (RI, GHz, one reference R).
- `checks/check_vna.py`: hand cases for Γ, SWR, L and C, the group delay of
  a pure delay, and a Touchstone round trip.

UI: "▶ Simulate" runs one solver per port in a QThread
(`view/sim_runner.py`), so S-matrix column k comes from exciting port k.
The Results dock (`view/plot.py`, QPainter) shows |S| in dB or Z_in.
"■ Stop" aborts. Results are not saved yet.

Speed (NumPy, float32): about 3 ms per step for 127 k cells. The test line
takes 2300 steps, about 9 s per port.

Validation:
- `checks/check_fdtd_cavity.py`, a PEC cavity on a graded grid (step
  ratio 1.12), compared with exact values:
  - TE101: +0.30 %;
  - fully filled with εr = 4: +0.6 %;
  - half filled along x, against the transcendental solution: +0.6 %;
  - energy is conserved to 1 %.
- `checks/check_fdtd_line.py`, the 50 Ω microstrip:
  - εeff of the line alone (from two lengths, so the port parasitics
    cancel) is within 1.9 % of Hammerstad-Jensen + Getsinger over
    1–5 GHz, dispersion included;
  - S21 = S12 and S11 = S22;
  - no gain beyond 2 %, and up to 5 % loss at 5 GHz, likely radiation (not
    verified);
  - |S11| < -12 dB.
  - The 50 mm line alone shows about 0.02 ns more delay than the bare
    line: that is the lumped ports' own L and C, as with CST's discrete
    port.

Lessons:
- Do not divide the resonance spectrum by the pulse spectrum near the band
  edges.
- The quasi-static series-capacitor formula is not exact for a layered
  cavity whose layers are not thin.
- Pick the 2π branch explicitly when unwrapping long lines.

## Level 3 (staircase, done)

`discretize/materials.py` `discretize(mesh_result, geometry, project, ports)`
returns a `MaterialGrid`. The rules are in the header; in short:

- **Arrays** are indexed [i, j, k] = (x, y, z). The E-edges Ex, Ey, Ez
  carry eps_r, sigma and a PEC mask; the H-edges carry mu_r.
- **Cells** take the dielectric at the cell center; in an overlap the
  later body in the list wins.
- **eps and sigma** on an edge are the mean of its up to 4 cells, weighted
  by their share of the dual face. **mu** is the harmonic mean along the
  dual edge.
- **PEC**: an edge is PEC when its midpoint is inside or on a conductor,
  or when a segment of ±0.99 · min_step through the midpoint, ACROSS the
  edge, crosses the conductor's surface.
  - So merged thin copper becomes a sheet of PEC edges, and thick metal is
    filled.
  - An isotropic tolerance was a bug: edges pointing at nearby metal (a
    port column) became PEC. The regression test is in
    check_discretize.
- "Materials" view: dielectric edges are found by their body
  (`MaterialGrid.dielectric`), not by eps > 1, and a dielectric that is
  identical to air gets a warning.
- **sigma** = 2π f_ref ε0 εr tan δ. It is exact at f_ref only, so there is
  a warning when f_ref lies outside the band.
- **Ports** become a `DiscretePort`: a column of edges along the port axis
  inside the gap, with the axis, the sign (- to +), R and the length. The
  column must not be PEC, and both of its end nodes must touch PEC edges.

UI: the "Materials" checkbox colors the E-edges on the mesh plane as the
solver sees them: PEC orange, ports magenta, dielectric green. The Mesh node
shows the number of PEC edges, the port columns and any errors or warnings.

Performance: the point tests are brute force over the triangles of each
body, with a bounding-box prefilter. Fine for these models; for big CAD,
switch to ray casting along the grid lines.

`checks/check_discretize.py`.

## Level 2 (first version)

- Settings live in `project/settings.py` (schemas like the object types)
  and are stored as `[simulation]` and `[mesh]` in the project file.
  - Simulation: f_min, f_max (GHz) and padding (mm; 0 = auto = λ0/4 at
    the center frequency).
  - Mesh: cells per λ, cells per feature, max step ratio, min step (0 =
    auto = the smallest λ/N divided by 10).
- `mesh/rectilinear.py` `build_grid(geometry, project, ports)`
  (algorithm in the header):
  1. Fixpoints: the planar faces and extents of typed bodies, the port
     axes and the domain ends.
  2. Fixpoints closer than min_step are merged, with priority domain >
     dielectric > conductor > port. So thin copper lands on the substrate
     face and the substrate thickness stays exact.
  3. Target step per interval: min(λ/N of the densest dielectric spanning
     it, interval / cells_per_feature).
  4. Grading h(x) = min_j (h_j + (r-1) dist).
  5. Lines are placed by inverting ∫dx/h.

  The result also reports the cells, the steps, dt (Courant), the merges
  and warnings.
- The UI shows the Simulation and Mesh nodes in the tree, plus mesh
  statistics and merges. In 3D, "Mesh" draws the grid lines on a plane
  normal to X, Y or Z, chosen with a slider that moves line by line, plus
  the domain box.
- Not yet: the "thirds rule" at metal edges, refinement for curved
  surfaces, and a cell count or memory limit.
- `checks/check_mesh.py`.

## Level 1 (done)

Everything is defined **in the STEP**: each body gets an object type in the
UI, and the type is stored in `model.toml` next to the file.

- `project/object_types.py`:
  - the schemas: `ObjectType` (params, a note, the allowed body kinds) and
    `Param` (default, limits, unit);
  - the UI menu and the editors are generated from them;
  - types so far: `pec` (no parameters) and `dielectric` (εr, tan δ, f_ref
    in GHz, μr; solid bodies only).
- `project/project_file.py`:
  - `Project.load/save` with atomic writes; it is saved after every edit;
  - strict validation (unknown type or key, bad values → `ProjectError`
    naming the body);
  - orphan sections are kept;
  - a file that cannot be parsed is never overwritten: the UI goes read-only.
- UI:
  - right click a body → Set/Change type, Remove type;
  - the type and its parameters appear as a subtree;
  - double click a value to edit it; an invalid edit is reverted and the
    reason is shown in the status bar.
- **Ports are STEP bodies too**: a truncated cone between two conductors.
  - The SMALL end is "+": the polarity comes from the CAD.
  - Each end must lie inside or on a PEC body, the two ends on different
    bodies.
  - The active part is the gap: the part of the axis outside all
    conductors. Ends drawn on the outer copper faces are fine, and so is an
    axis lying on the board's end face.
  - `project/ports.py` turns this into a `PortGeometry` (the ends, the
    conductors, the gap). The UI shows − end / + end / gap or the error,
    and draws a green or red arrow.
  - Port parameter: R (Ω). The port body is a marker and is never meshed.
- `[view] up = "y"` in the project: the vertical axis of the CAD model
  (Inventor and SolidWorks use Y).
- Body names: in a multi-body part each body keeps its CAD name (for
  example `MANIFOLD_SOLID_BREP('port')`); a single-body part takes the part
  name.
- `geometry/queries.py` (numpy, from the triangles):
  - `points_inside` (ray parity);
  - `surface_distances` (exact);
  - `points_in_or_on` (the robust "belongs to the body" test, needed for
    points on faces).

  The voxelizer will reuse the same ray/triangle code.
- Checks: `checks/check_project_file.py` and `checks/check_ports.py`.
- First real model: `models/01_linie50x30_3.stp` (from Inventor). A 50 Ω
  microstrip on FR4: w = 3.0 mm, h = 1.6 mm, 35 µm copper, 50 x 30 mm,
  cone ports on both ends. Expected: Z0 ≈ 50.7 Ω, εeff ≈ 3.23, delay
  0.30 ns (Hammerstad-Jensen).

## Level 0 (done)

- `geometry/body.py`: the `Body` / `Geometry` contract.
- `geometry/step_io.py`: `read_step` / `write_step` through XCAF (names,
  colors and assembly placements are kept). Solids become kind `solid`;
  shells and faces outside solids become `sheet`. Free edges (wire
  antennas) are not imported yet.
- `tools/make_test_models.py`: the 2.4 GHz dipole and the patch on FR4.
- `checks/check_step_import.py`: names, kinds, colors and units survive the
  round trip. The tessellated volume matches OCCT's exact volume within
  area × deflection, and solid surfaces are closed.
- `view/`: GeometryView (VisPy) + MainWindow (Qt): the body list, visibility,
  selection, edges and X-ray.
