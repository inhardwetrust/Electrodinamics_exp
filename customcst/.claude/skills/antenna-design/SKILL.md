---
name: antenna-design
description: Design, tune, verify and compare with measurements an antenna (a patch, a dipole, a feed network) with customcst - a parametric model (code or a myCad part), quick S11 runs and sweeps on the remote server, the far field (pattern, D, G, efficiency, cuts in 3D), a final run the user opens in the window, a VNA measurement set against the model (eps_r fitted), retuning to a new frequency, and an honest report. Use when the user asks to design, optimize, compare, measure or explain an antenna or its feed in customcst, or to "compute it properly on the server", including a design whose model is a myCad part (a design project folder beside customcst).
---

# Antenna design with customcst

customcst is an FDTD simulator (this folder). Read `REFERENCE.md` first
(the facts: project file, body types, mesh, padding, presets, runs,
results, the window's commands) and follow the `customcst` skill
(`.claude/skills/customcst/`) for each simulation; this skill is the
antenna method on top of them. The user speaks Russian: answer in
Russian, keep code, comments and docs in English.

The way of working: agree on the goal and the structure with the user,
then do the iterations yourself (models, quick runs, sweeps, convergence,
the final run) and come back with numbers, the window showing them, and
an interpretation. Ask only for real decisions (a board stack, a feed
scheme, a trade-off, using the server), not for each run.

## 1. Agree on the goal

Write down before modeling, and confirm in one message:
- frequency or band, the match wanted (S11 below -10 dB over the band?);
- what matters in the pattern: gain, beamwidth, symmetry, cross-pol,
  front/back;
- the board: stack (thickness, layers), material, board size, where the
  connector goes. FR4's eps_r on the datasheet (4.3-4.4) is low for 5 GHz:
  our measured board came out 4.55 at 5.4 GHz (section 7); say which you
  take and that the first board settles it;
- the feed scheme (probe, inset line, edge feed with a lambda/4
  transformer, two probes in antiphase, aperture, behind the ground).
  Explain the trade-offs when asked (`docs/education/ru/patch_feeds.html`
  is the user's notes on this; section 8 has the numbers).

## 2. Build a parametric model

Two ways, both written, not drawn by hand:
- **a myCad part in a design project** (section 9): the usual way now;
- **a generator in `tools/make_test_models.py`**: a stem S with
  `PARAMS = {...}` (every dimension in mm, each with how it was estimated),
  `S()` (the bodies `[(name, OCP shape, color)]`; `_trace(points, width,
  z0, z1)`, `_union`, `BRepAlgoAPI_Cut`) and `S_project(path)` (types,
  material, band, mesh, far field, `save()`). Copy `patch_5g8`,
  `patch_5g8_dual` or `patch_5g8_4layer`. `python tools/make_test_models.py
  STEM` writes only that stem; write into `models/` only stems of your own.

The project (REFERENCE 4-6, 9):
- types: conductors `pec`, the substrate `dielectric` with **f_ref at the
  design frequency** (tan d is exact there only; a preset's 3 GHz under-
  states the loss ~2x at 5.5 GHz), the connector's PTFE, the port: a coax
  connector's end cylinder `waveport`, or a lumped `port` cone (small end
  "+") at a line's start;
- band about +-20-30 % around f0; accuracy -50 (a resonator rings);
- **padding 0 (auto)**: lambda/4 at the band's center without the far
  field, lambda/2 at its lowest frequency with it (set by itself; the
  tree's "padding now" row and the dry run's `padding` line show it);
- mesh and far field from the presets (`Presets` tab, `preset` command):
  mesh Draft 20/4, Sweep 30/4, Fine 40/8, Final 50/12; monitors `f0` or
  `f0 +-2 %` around `[farfield] f0`;
- `[farfield] enabled = false` for S11-only runs (the frequencies stay).

Rules the geometry relies on (REFERENCE 5):
- a waveguide port's plane ends the domain on its side (no air there);
- disjoint solids fused into one body come back as `name#1`, `name#2`;
- conductors may touch or overlap (PEC merges), but they must touch: a
  0.01 mm air gap is a gap on a 0.025 mm min step (section 10);
- two dielectrics in one place: the later body wins cell by cell; avoid.

## 3. Check before running

```bash
../.venv/Scripts/python.exe run.py MODEL.step --dry-run      # grid, cells, padding, memory, dt, port found
../.venv/Scripts/python.exe ../myinventor/tools/probe.py MODEL.step contacts   # what touches what
```
- the port found (`port Port 49.3 Ohm (line)`), no shorts, no unassigned
  body you meant to type, no f_ref warning on the substrate;
- **every contact you rely on**: pin - trace (or its solder / the
  connector's tab) touching or overlapping, legs - ground pads, shell -
  ground; a "gap 0.01 mm" there is an open circuit on the grid;
- the Z0 profile along the line (window: Z0 tools, axis along the line)
  must run from the coax over the whole trace: a section saying "no signal
  conductor: no port ... on the metal here" means the trace is not joined
  to the port's metal (that is how the pin's gap was found);
- the smallest step sets the time step: thin layers and connector details
  (0.0175 mm) make a run long.

## 4. Tune by S11: quick runs and sweeps

**Quick and cheap**: the far field off, padding 0 (lambda/4). Checked on
oldpatch: the same model at lambda/4 (Run 4) and lambda/2 with the far
field (Run 5) put the S11 minimum on the same point (5.4188 GHz), Z 43.3
against 44.2 Ohm. Costs on the server (16 cores), oldpatch (50 x 50 board
with an SMA, 4.5-7 GHz):

| mesh (per lambda / per feature / min step) | cells with PML | dt | time |
|---|---|---|---|
| 20 / 5 / 0.05 mm | 2.3 M | 92 fs | ~6 min |
| 20 / 8 / 0.025 mm | 6.0 M | 42 fs | ~33 min |
| same + far field (lambda/2) | 7.0 M | 42 fs | ~45 min |

A min step of 0.05 mm merges the 35 um copper into a sheet: fine for
trends, compare runs on one mesh only.

With the user's window: `ui.py run_server`, `ui.py wait --server` (in the
background), read the run file. Without it, or for several variants:
```bash
S=.claude/skills/antenna-design/scripts
../.venv/Scripts/python.exe $S/remote_tasks.py status          # run.lock, runs, zz_ folders, disk
../.venv/Scripts/python.exe $S/remote_tasks.py sweep NAME -- --stem STEM --params PARAMS \
    --variants '[{"l": 11.0}, {"l": 11.4}]' --cells 30 --feature 4 --f0 5.8 --farfield 5.8
../.venv/Scripts/python.exe $S/remote_tasks.py wait-sweep NAME PID   # in the background
../.venv/Scripts/python.exe $S/remote_tasks.py clean NAME
```
(sweep.py builds make_test_models stems; myCad variants: section 9.) The
server is the user's: act there only when allowed in this conversation;
it must run the current code, which the user copies by hand (`sync-code`
only when asked; "unknown key" from the server means old code there).

Sweep 3-6 variants of one or two knobs, read the trend, narrow down; two
or three rounds. When a knob does nothing to the figure you aim at, find
what dominates instead.

## 5. Converge, then the final run

- Converged when going one preset finer (Sweep -> Fine -> Final) changes
  the S11 minimum by < ~1 % in frequency and D by < 0.1 dB. The minimum
  moved up 20-30 MHz from 40/10 to 50/12 before: tune on one mesh, check
  the final one.
- The final run: the user's project (or a copy they open), the fine mesh,
  accuracy -50, the far field on with frequencies across the band (padding
  goes to lambda/2 by itself). With the window: `ui.py run_server`; else
  `remote_tasks.py launch / wait-run / fetch`. The run lands in
  `model.results/` as Run N.

## 6. Read the results and show them

REFERENCE 12 (`list_results`, `figures`, `cut`, `beamwidth`, `summary`):
- S11: the minimum (GHz, dB, Z), the -10 dB band;
- per far-field frequency: D, G, realized G, efficiency, the beam's
  direction (theta, phi), the beamwidths in the E and H planes,
  front/back, the front asymmetry (`scripts/sweep.py front_asymmetry`).
- **E and H planes follow the feed**: the E plane holds the resonant
  length (the feed's direction). Fed along y (oldpatch, patch_antenna_sma):
  E = cut phi 90 (around x, the yz plane), H = phi 0. Fed along x: the
  other way round. Around z: theta 90.
- **With a coax waveguide port**, part of the connector's coax lies inside
  the far-field box: the run's info gives `excluded cells`; the sphere
  gets 87-95 % of the box flux (`p_sphere / p_box`), so G and realized G
  read 0.2-0.6 dB low; D and efficiency are right. Say so in the report.

Show it in the user's window (locked): `ui.py show "Run N" "Run M"`,
`ui.py farfield frequency=5.42 plane=phi at=90 show3d=true cut3d=true`
(the tab, the 3D pattern and the cut's plane and curve), `ui.py view
name=iso`, `ui.py section axis=y at=-20` for the geometry. After a run or a
fetch read `status.notices`: locked, the window shows no dialog, it notes
(a failed run, the server's model files differing: kept ours unless
`fetch take_server=true`).

Sanity figures (patches on FR4, from our runs):

| antenna | D, dBi | G, dBi | eff. | beam | beamwidth E / H | F/B | -10 dB band |
|---|---|---|---|---|---|---|---|
| probe fed, 1.6 mm, 5.8 GHz, 40 x 40 ground | ~7 | ~5.5 | ~70 % | normal | 85 / 76 | ~19 dB | ~4 % |
| 4-layer, feed behind the ground, 5.76 GHz | 7.1 | 4.7 | 57 % | 1 deg | 86 / 75 | 23 dB | ~4.5 % |
| oldpatch: inset, 1.34 mm, SMA end launch, 50 x 50, 5.42 GHz | 7.8 | 5.0 (+0.5) | 58 % | 12 deg to the connector | 70 / 82 | 19 dB | 3.5 % |

Efficiency far lower means losses in the feed (long thin lines on FR4:
30-40 %); a beam tilted toward the connector means the feed line on the
antenna's side radiates.

## 7. Compare with a measurement (VNA)

What we learned measuring oldpatch with a NanoVNA-F V3 and NanoVNA-Saver
(`vna_data/` beside customcst):
1. **Trust a sweep only if it is clean**: after calibrating, the load
   reads below -25 dB, short and open ~0 dB at 180 / 0 deg, and the
   antenna's |S11| is **nowhere above 0 dB**. A point above 0 dB means an
   error vector of 0.1-0.3: then the minimum's depth (-26 or -35 dB) is
   meaningless; its frequency is usually right. Ask for the load, short
   and open files with the antenna's.
2. **Points**: Saver sets the device's points per sweep; more come from
   "Segments" (5 x 101 = 505), or Manage > Device settings > Datapoints.
   Recalibrate after changing points or range.
3. **Reference plane**: calibrated at a cable's end or the VNA's port, an
   adapter after it (ours 11 mm, ~108 ps round trip) rotates the Smith
   chart: compare frequency and |S11|, and Z only after taking the adapter
   out (measure it open: Gamma_board = Gamma_measured / Gamma_open; or fit
   the delay off resonance).
4. **The setup radiates too**: the antenna screwed onto the VNA had a
   second minimum near 6 GHz (the chassis as more ground); on a cable
   without a ferrite a broad tail after the resonance (the shield's outside
   current; a hand on the cable changed it). The resonance itself (the
   falling edge to the minimum) was the same in all setups and matched the
   model. Measure on a 30-50 cm cable, a ferrite at the board, hanging
   free, averaging on.
5. **Fit eps_r from the frequency**: f is proportional to 1/sqrt(eps_eff):
   a model at 4.3 off by -2.7 % went to 4.55 and landed within 0.5 %.
   The depth then: model -22.5 dB against -16..-18 measured (losses the
   model lacks: smooth PEC copper, tan d at 5 GHz above 0.02, solder).
6. Overlay plots (matplotlib, |S11| and Smith) go next to the user's data,
   not into the project; tell the user the path.

## 8. Retune and feed choices

- **Frequency**: f is proportional to 1 / L_eff, L_eff = L + 2 dL (dL ~0.6 mm
  on 1.34 mm FR4). From f1 to f2: L_eff2 = L_eff1 f1 / f2. Trimming copper
  only raises the frequency: cut the far edge (opposite the feed), not the
  inset.
- **Input resistance** along the length: R(d) = R_edge cos^2(pi d / L)
  (R_edge ~150-300 Ohm on FR4); shortening L with the inset kept lowers R
  (oldpatch to 5.7 GHz: ~44 -> ~33 Ohm); a narrower patch raises R_edge
  (~1 / W) and nudges f up a little: trim the sides to win it back.
- **Edge feed with a lambda/4 transformer** instead of an inset (no slots):
  Z_t = sqrt(R_edge 50) (~100 Ohm, ~0.55 mm on 1.34 mm FR4), length
  lambda_g / 4 (7-8 mm at 5.5 GHz), then 50 Ohm of any length.
- **Line widths**: 50 Ohm on 1.34 mm FR4 (eps_r 4.55) is ~2.55 mm; a 2 mm
  trace is ~56-57 Ohm (~-25 dB mismatch, a background). What sets Z0 is
  the ground and metal around (a pin above, pads beside), not the width
  alone: under an SMA pin a 1.1 mm trace was 48.6 Ohm.
- **Steps between equal Z0 are free; changing Z0 needs length**: a step
  reflects (Z2 - Z1) / (Z2 + Z1) (50 -> 55: -26 dB, fine); a smooth taper
  needs l >~ lambda_g ln(Z2/Z1) / (4 pi Gamma) (50 -> 200 at -20 dB: ~1.1
  lambda_g); a lambda/4 transformer is lambda_g / 4 for any ratio, narrower
  in band (50 -> 200 at -20 dB: ~17 %, plenty for a patch).
- **Cable and ground currents**: a small ground (50 mm ~0.9 lambda) and a
  connector at its edge with the cable in the antenna's plane make the
  cable part of the antenna. A larger ground, the cable leaving behind the
  ground, a feed behind the ground (4 layers) or a ferrite / choke reduce
  it. In a product the same goes for power wires and the enclosure.

## 9. The model from myCad (a design project)

The model is a myCad part (`../myinventor/`; read its skill
`../myinventor/.claude/skills/mycad/SKILL.md` and `REFERENCE.md`). A
design lives in its own folder beside the tools (`../patch_antenna_sma/`,
`../old_patch_calc/`): `model/` the part, `sim/` the customcst project,
`variants/` the part as it was for each run, `notes/log.md`.

One model, changed in place, each change a run (agreed with the user).
Both windows show it; Claude drives them (`ui.py --app mycad ...` and
`ui.py ...`), each locked while Claude acts:
1. Talk over what changes in the geometry and why.
2. `ui.py lock "what I am doing"` (customcst; myCad likewise). Refused
   while the user edits: ask them.
3. Copy the part as it is now to `variants/runNN.part.toml` with its report
   (`main.py PART --report --json > variants/runNN.mycad.json`), then edit
   the part (myCad `set` / a written file + `reload`).
4. Export over the project's STEP, only when the geometry changed (compare
   the report with the last .mycad.json: every export changes the bytes,
   and the run fingerprint hashes them):
   `../.venv/Scripts/python.exe ../myinventor/main.py model/X.part.toml --export-step sim/X.step`.
   Check that the part file and the STEP agree (`probe.py X.step bodies`
   against the report): a stale STEP looked like a broken model once.
5. `ui.py reload`, `ui.py wait`, `ui.py status`: `unassigned` (assign new
   bodies in the .toml, reload again), `orphans`, `problem` null, `shorts`
   empty, `cells`; and section 3's contacts.
6. `ui.py run_server`, `ui.py wait --server` in the background; the window
   fetches the finished run; read `status.notices`. Do not export over the
   STEP while that run goes on.
7. Read the figures (section 6), show them (`show`, `tab`, `farfield ...
   cut3d=true`), `ui.py unlock "look at Run N: ..."`, report.
8. Log it in notes/log.md: Run N = variants/runNN, what changed, the
   figures. Old runs stay in Results, outdated, to compare.
"not in control" in an answer: the user took the control back: stop and
ask. No window (`ui.py` exit 2): the same with run.py and remote_tasks.py.

Several values of one knob at once is a batch: export each with
`--set NAME=FORMULA` into a `zz_` folder with a copy of the .toml renamed
to it, and run them together on the server.

Names and the connector:
- myCad names an imported body `<import>:<its name>` (`SMA:connector_wire`);
  the .toml quotes such a key. Do not rename bodies once the .toml has
  types: the types follow the names. Non-ASCII names (`Volumenkörper16`)
  come through the STEP intact (myCad escapes them).
- A coax connector's port is its end cylinder, type `waveport`; the
  domain ends there (no air on that side: the window's blue box is shorter
  toward the connector), the far-field box leaves the coax's inside out
  (section 6).
- **Place the connector by formulas of the stack** (the pin's bottom on
  the trace's top: `hd + t + pin_d / 2`), never with a hand constant: when
  the board went 1.53 -> 1.34 mm, a "- 0.18" left the pin 0.01 mm above the
  trace. Better also a solder block overlapping pin and trace (as on the
  real board).

## 10. Pitfalls we hit

The general ones are in REFERENCE 16. For antennas:
- too little padding: the far-field box crosses the board (the run's info
  says so); padding 0 with the far field on gives lambda/2 by itself;
- a gap of 0.01-0.05 mm between the pin and the trace (after a stack
  change): S11 nonsense, the Z0 profile stops at the connector, the
  connection only through capacitance; check contacts (section 3);
- feed lines on the antenna's side of 1.6 mm FR4 radiate and lose: the
  beam tilts, the efficiency drops to 40 %; a feed behind the ground
  (4 layers) is shielded;
- an asymmetric feed tilts the beam 1-3 deg and makes the back lobe
  asymmetric: physics, not a mesh error;
- thin layers (0.2 mm prepreg, 35 um copper) and connector details shrink
  the time step: the 4-layer patch ran 28 min against 3 min on 1.6 mm;
  quick runs with min step 0.05;
- a measurement: section 7 (calibration, the adapter, the cable, the
  chassis) before blaming the model.
