# Log

## 2026-10-30: the project set up (in the myCad session)

- Goal (the user): a patch antenna on FR4 1.6 mm with the SMA connector in
  `parts/`, designed end to end by Claude with myCad and customcst, shown
  in their windows: start from estimates, model in myCad, export STEP,
  simulate in customcst (materials, the connector's port cylinder), tune
  for the best match at the design frequency.
- Decided:
  - three folders: the two tools and this project folder; everything of
    the design here (README.md, CLAUDE.md).
  - the feed is a myCad polyline (a microstrip, widths per point); the
    matching to the connector is part of the design.
  - no union of conductors is needed: customcst marks PEC edges inside or
    on any conductor, so touching / overlapping conductors merge on the
    grid; PEC wins over a dielectric; two dielectrics overlapping resolve
    by body order (avoid it).
  - before the run, the tools get their part of the bridge:
    - myCad: `--set NAME=FORMULA` in its CLI (a variant without editing
      the part file);
    - customcst: the sweep bridge (variants from a myCad part), reloading
      a project changed on disk, the skill section
      (`../customcst/TASK_mycad_bridge.md`).
- The connector (read with myCad): 5 solids; the coax axis along -y; the
  cylinder `Volumenkörper16` at y -14.5 .. -13.5 (r 2.675) is the port.
- Next: the tools' parts, then `prompt.md` filled in and run.

## 2026-10-30: the demo (demo_prompt.md): a two-layer PCB with the SMA

- `demo/pcb_sma.part.toml` (+ `pcb_sma.files/realconnector.stp`, `pcb_sma.step`),
  built in the locked myCad window in 4 steps (board, trace, connector, solder).
- Report ok; volumes as computed: Substrate 3825, Ground 87.5, Trace 2.9575,
  PinSolder 1.377 mm3; the board z -0.035..1.565.
- The SMA: Move of the group, Y Axis +90, offset (0.5, -bw/2 + launch,
  hd + t + pin_d/2). Slice y=-23: lower legs x +-(2.1..3), z -0.985..-0.005
  (Ground overlap 0.216 mm3); upper legs x +-(2.11..3), z 1.555..2.515, in air
  (0.025 above the FR4, touching no copper); the pin x +-0.45, z 1.565..2.465.
- Contacts: pin - Trace touching (a line); PinSolder overlaps the pin 1.081 mm3.
  `connector_outer` (a 1 x 0.5 mm plate, y -25.38..-21) overlaps the pin
  1.602 mm3, the Trace 0.036 mm3 (0.01 mm into it), all of PinSolder, and the
  PTFE 0.0015 mm3 (it sticks 0.38 mm past the body's face into the dielectric).
- customcst read_step: all 9 bodies, `SMA:Volumenkörper16` intact.

## 2026-10-30: the task — a 5.75 GHz patch matched to the SMA

- Goal (the user): a patch at 5.75 GHz on the demo's board, matched to the
  connector; work from the middle out: first the 50 Ohm line and its launch
  from the SMA (pin pad, the legs' pads, gaps), then the patch size and the
  feed point. Agreed: inset feed; the goal is the S11 minimum at 5.75 GHz
  (no band figure required); top ground pads under the connector's upper
  legs, vias to the bottom ground; board 50 x 50, FR4 eps_r 4.3 / tan d
  0.02 (f_ref 5.75), copper 35 um. The server allowed.
- Method: one parametric myCad part per stage; variants by `--set` exports
  (no hand-edited STEPs, so no redrawing at the end). Z0 along y with
  customcst's line_profile (impedance_profile on the 3D grid at 8 cells
  per feature: ~10 s a model), then FDTD on the server.
- 50 Ohm line: w50 = 2.93 mm (Hammerstad, h 1.53, t 0.035); Z0 profile 49.9.
- The launch test bench `model/launch.part.toml` -> `sim/launch.step`: the
  connector, the line 20 mm (y -25..-5), a box waveport at its end
  (`LinePort`), excited from the coax port. Ports: coax 48.7, line 49.6 Ohm.
- Z0 profile of the launch (gap to the pads 0.5):
  - trace at w50 under the pin (first try): 36.4 Ohm over the 4 mm under
    the pin (the pin + solder + the vendor plate on a wide trace, the legs
    and pads beside), 40 past it: too capacitive (~45 deg at 5.75 GHz);
  - pad_w 0.9: 48.5 under the pin, but a 1 mm taper beyond the pin's tip
    peaked at 83 Ohm (no pin, no side ground there): taper 0.4 mm;
  - pad_w 1.1 / gap 0.5: 48.6; pad_w 1.1 / gap 0.6: 51.2; pad_w 1.2 /
    gap 0.45: 48.4 (all with taper 0.4, pads ending with the pin).
  - The taper's slope passes 0.2 mm from the pads' corners.
- Round 1 in 3D (`sweeps/launch_r1`, 30 cells/lambda, 4 per feature):
  v0 the first try, v1 pad_w 0.9, v2 pad_w 1.1, v3 pad_w 1.1 / gap 0.6.
- Tools:
  - customcst `remote_tasks.py sync-code` split `git ls-files` on spaces
    (`docs/RFSW8000 Application Note.pdf`) and stopped half way, leaving the
    server's code mixed: fixed (`splitlines()`), synced again (backups in
    ~/customcst-code-*.tgz on the server).
  - No batch command for myCad variant STEPs (sweep.py only takes
    make_test_models stems): a scratch script runs them one by one with
    `launch / wait-run / fetch`. Worth a `remote_tasks.py batch` in customcst.
- Round 1 results (coarse 30/4, min_step auto; S11 at 5.75 / worst 5-6.5 GHz
  / Z / |S21|): v0 -13.0 / -12.1 / 71+15j / -0.70; v1 -25.2 / -24.1 /
  44.3+2.3j / -0.47; v2 -26.3 / -25.3 / 44.8+2.0j / -0.47; v3 -23.2 /
  -22.8 / 42.5+0j / -0.48. Narrowing the trace under the pin: +13 dB.
- The user: mesh min_step 0.025 mm and 8 cells per feature (coarser
  drifts a lot). Set in sim/launch.toml and sim/patch.toml. Cost: launch
  0.65 M -> 3.0 M cells, dt 92.7 -> 33.8 fs; the antenna 7.1 M cells, dt
  33 fs. Port Z0 on the finer grid: coax 48.7 -> 49.1, line 49.6 -> 50.2.
- Round 2 (`sweeps/launch_r2`): v1, v2, v3 again on the fine mesh.
- Tool wish (customcst): the window shows one model's server run; a batch
  of variants needs a watcher opening each one. A batch view in the window
  (and `remote_tasks.py batch`) would do it.
- The patch part `model/patch.part.toml` (-> `sim/patch.step`): board 50 x 50,
  the launch of v2 (pad_w 1.1, gap 0.5, taper 0.4), the 50 Ohm line into the
  inset, the patch pw 16 x pl 11.3 centered at the origin, inset 3.9 with
  0.7 mm gaps. Band 4.5-7, padding 15, -50 dB, far field 5.75.
  Coarse 1.9 M cells (30/4); fine 7.1 M (8 / 0.025).
- Plan (agreed): tune the patch on the coarse mesh locally while the server
  runs the fine launch; then confirm on the fine mesh on the server.
- Patch round 1 (`sweeps/patch_r1`, coarse, local): pl 11.0 / 11.3 / 11.6.
- The user: the window should show the work going on (each run as it
  finishes). Done with a scratch watcher (ui.py open / show / tab per
  event). Tool wish (customcst): runs started outside the window (run.py
  locally, another model's server run) are invisible until the model is
  opened; a "batch" or "activity" view listing every pending and finished
  run of a folder would make this need no watcher.
- The user asked whether the far-field monitor costs time. By the code
  (solver/farfield.py): a Huygens box sampled ~8 times per period of the
  pulse's top frequency (every few hundred steps at dt 33 fs), a surface
  against the volume update; the stop is by energy either way: about free
  (not timed yet). What costs is the air it needs. Agreed: no program
  change; tuning runs with ~lambda/4 padding (13-15 mm) and the monitor on
  (D, efficiency, beam tilt from the feed line), the final run lambda/2
  (~26 mm). For the antenna-design skill (later): say this split plainly
  (section 4/5: padding per stage, the monitor is cheap).
- Mesh density: the user's own models (customcst/models: justswitch,
  test_prep...) use 20 / 8 / 0.025. 30 -> 20 cells per lambda at 8 / 0.025:
  launch 3.0 -> 2.7 M cells, patch 7.1 -> 5.7 M, dt unchanged (33-34 fs).
  dt is set by the smallest cells: z 0.0175 (35 um copper in 2 cells) and
  y 0.0169 (the vias' edge at -24.65 is 0.05 from the solder's start at
  -24.70: an accident of my parameters; align them). Decided: tune at
  20 / 8 / 0.025, the final run at 30 to see the drift. sim/*.toml set to 20.
  (Dispersion at 20 cells/lambda: ~0.3-0.4 % phase velocity, ~20 MHz.)
- The user asked whether 1-10 GHz would run faster. Dry run (patch, 20/8/0.025):
  4.5-7: 5.7 M cells, pulse 3.09 ns; 1-10: 6.7 M (+18 %, f_max sets the
  step), pulse 0.86 ns; dt 33.0 fs both. The stop (-50 dB) is dominated by
  the patch's ring-down (Q ~18: ~6 ns), equal for both: ~25 % fewer steps,
  18 % more cells: about the same time; and 15 mm of air is lambda/20 at
  1 GHz. Kept 4.5-7 (patch), 4-7.5 (launch). Not timed in practice yet.
- Round 2 (fine 30 / 8 / 0.025, server ~985 s each): S11 at 5.75 / worst
  5-6.5 / Z / |S21|: v1 -23.4 / -22.7 / 43.1+1.5j / -0.47; v2 -23.7 / -23.2
  / 43.2+0.9j / -0.47; v3 -21.1 / -20.7 / 41.2-0.7j / -0.49. The fine mesh
  reads 2-2.5 dB worse than the coarse, same order. Chosen: v2 (pad_w 1.1,
  gap 0.5, taper 0.4): the launch looks like ~43 Ohm against the coax's 49;
  good enough under a patch. (A wider gap, v3, read worse in 3D though the
  Z0 profile said higher Z: the profile is quasi-static, 3D decides.)
- Tool note (customcst server): the server keeps models by file name in
  <path>/models/: launch_r1/v1.step and launch_r2/v1.step are the same file
  there, and the fetch of r2 also took r1's run (Run 1, outdated in r2).
  Use unique stems per batch (p2L116...); a tool fix would key by folder.
- Patch round 1 (coarse 30/4, local, ~20 min each: the local machine is
  ~6x slower than the server): pl 11.0 -> S11 min -18.1 dB at 6.019 GHz;
  11.3 -> -23.0 at 5.894; 11.6 running. Slope ~-0.42 GHz/mm: 5.75 at ~11.65.
  The inset 3.9 is near right (-23 dB at the minimum).
- Vias aligned with the solder's start (via_y1 = solder_in + via_d/2): the
  smallest step 0.0169 -> 0.0175 (copper), dt 33.0 -> 39.0 fs (~18 % faster).
- Patch round 2 (`sweeps/patch_r2`, fine 20 / 8 / 0.025, server): p2L116
  (pl 11.6): where the fine mesh puts the resonance.
- Patch round 1 done (coarse 30/4, local; run 5.1 ns, 1200 s): pl 11.6 ->
  S11 min -32.3 dB at 5.775 GHz (Z 48.4+2.3j), -10 dB 5.669-5.894; at 5.75
  -21.6. Far field 5.75: D 7.4 / 7.8 / 8.0 dBi, G 4.4 / 5.1 / 5.6 (pl 11.0 /
  11.3 / 11.6), the maximum tilted 17 / 14 / 11 deg toward -y (the feed and
  connector side).
- The run's efficiency reads -39 % (nonsense): "a body crosses the
  far-field box (3823 edges in material)": the coax waveport's side has no
  padding, so the Huygens box runs through the connector (REFERENCE 16:
  not validated). D - G = 2.4 dB (~57 %) is plausible but not trusted.
  Plan: a check run with a lumped port at the board edge, no connector,
  air all around, to compare D, G and the tilt. Tool note (customcst):
  flag or refuse an efficiency outside 0..100 %; think about a far-field
  box that excludes the coax beyond the port plane.
- Tool note (customcst window): opening a model whose run goes on the
  server (launched by remote_tasks.py, or from another window) shows the
  recorded row ("on the server ... meshing") but does not poll: the
  progress bar starts only after Run on server or Fetch
  (`_remote_from_record` only reads the record). The watcher now sends
  `fetch` after `open`. Better: start following a pending launch on open.
- Tool bug (customcst window): `ui.py fetch` can open a modal question
  ("The server's ... differ from yours. Take the server's?",
  main_window.py `_pulled`) though control commands should never open
  dialogs (REFERENCE 13); Claude cannot answer it, and the window blocks.
  Cause here: the window writes [view] (and a default [z0path]) into the
  .toml after open, so the pushed copy on the server "differs". Fixes to
  consider: in control mode never ask (keep local, say so in the answer);
  ignore [view] / [z0path] when comparing; a separate `follow` command that
  only starts polling. Until then the watcher does not send fetch (the
  user answers "No" once).
- Patch round 2 result (fine 20 / 8 / 0.025, server, 2671 s, 7.2 M cells,
  5.05 ns): pl 11.6 -> S11 min -42.2 dB at 5.750 GHz, Z 49.5+0.7j;
  -10 dB 5.644-5.869 (225 MHz, 3.9 %). D 8.06 dBi, G 5.64 (efficiency
  printout -39.9 %: the box crosses the connector, see above), beam
  theta 11 deg toward -y. The coarse mesh had put 11.6 at 5.775: the fine
  one lands 25 MHz lower. The goal (minimum at 5.75) is met with inset 3.9.
- Decided: pl = 11.6 into model/patch.part.toml (shown in the myCad window);
  sim/patch.step re-exported (the working project, 20 / 8 / 0.025).
- Final run: sim/patch_final.step + .toml (30 / 8 / 0.025, padding 26 mm
  = lambda/2, -50 dB, far field 5.6 / 5.75 / 5.9), 8.2 M cells, dt 39 fs;
  started from the customcst window (Run on server: the window follows it,
  no fetch question).
- The user: the beam tilt toward the feed matches a similar real patch of
  theirs (physics: the line and the connector).
- The -40 % "efficiency" explained (a hypothesis that fits): efficiency =
  p_box / p_acc, and the box crosses the coax beyond the waveport; the feed
  wave flows into the box there, counted as outward flux with a minus:
  p_box ~ P_rad - P_acc => p_box / p_acc ~ eta - 1 => eta ~ 0.60. Check:
  D - G = 2.42 dB => eta ~ 0.57. So eta ~57-60 % (feed line on FR4).
  Tool wish (customcst): with a waveport inside the far-field box, subtract
  the port's own power flow (or cut the box around the coax) so the
  efficiency reads right; check whether the coax aperture's currents bias
  D toward the port side (the lumped-port check run will tell).
- Final Run 1 (sim/patch_final, = variants/run01: pl 11.6; 30 / 8 / 0.025,
  padding 26, 3773 s, 8.2 M cells): S11 min -32.7 dB at 5.794 GHz
  (Z 48.5+2.2j); at 5.75 -17.7 dB (Z 58.4+10.6j); -10 dB 5.688-5.912
  (225 MHz). 20 -> 30 cells/lambda moved the minimum +44 MHz (0.8 %).
  Far field (D / G / realized dBi, eta from D-G, printed "efficiency",
  tilt toward -y): 5.6: 7.56 / 4.66 / 3.39, 51 %, -46 %, 16 deg;
  5.75: 7.99 / 5.52 / 5.44, 57 %, -40 %, 12 deg; 5.9: 8.25 / 6.00 / 5.63,
  60 %, -38 %, 9 deg. Beamwidth at 5.75: E (yz) 66, H (xz) 78 deg.
  The "eta - 1" reading of the printed efficiency holds at all three.
- Run 2 = variants/run02 (to be written after): pl 11.7 (+44 MHz at
  -0.42 GHz/mm -> +0.1 mm), set in the myCad window and saved, exported
  over sim/patch_final.step, run from the customcst window.
- 21:3x: Claude Code closed both windows (myCad, customcst: they ran as
  background processes of this session) and the wait, on low system
  memory while idle. The server's Run 2 goes on (33 % of the pulse at
  771 s). Not restarted without the user. Note: the customcst window
  meshes the 8 M-cell model locally (~1 GB+) just to show it.
- Final Run 2 (= variants/run02: pl 11.7; 30 / 8 / 0.025, padding 26,
  3869 s): S11 min -41.1 dB at 5.756 GHz (Z 48.7+0.7j); at 5.75 -34.3 dB
  (Z 50.2+1.6j); -10 dB 5.650-5.869 (219 MHz, 3.8 %).
  Far field (D / G / realized dBi, eta from D-G, tilt toward -y, E / H
  beamwidth): 5.6: 7.65 / 4.86 / 4.00, 53 %, 15 deg, 68 / 75;
  5.75: 8.05 / 5.63 / 5.63, 57 %, 11 deg, 66 / 78; 5.9: 8.27 / 6.01 /
  5.36, 59 %, 9 deg, 64 / 81. The goal (the minimum at 5.75) is met.
- The builder scripts (they write the part files with comments; a Save in
  myCad drops them) copied to model/ (build_patch.py, build_launch.py).
- Open: the far field's back and connector side with a waveport (the box
  crosses the coax): a lumped-port check run is still to do if the
  pattern figures matter beyond D / G at the beam.
