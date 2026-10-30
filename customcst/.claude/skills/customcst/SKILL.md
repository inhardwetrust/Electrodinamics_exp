---
name: customcst
description: Simulate a structure in customcst, our FDTD simulator (a PCB line, a connector, a filter, an antenna) from a STEP model - assign body types and materials in its project file, check the mesh and the ports, run locally or on the user's server, read S11 / Z / the far field from the run files, and, when the user watches, drive their open customcst window step by step through its control channel. Use when asked to simulate, compute, check or compare a model in customcst, to set up its project (.toml), or to show results in its window.
---

# Simulating in customcst

customcst (`customcst/`) is an FDTD simulator: a STEP model plus its
project file `model.toml` (body types, materials, band, mesh, far field)
give runs saved beside the model (`model.results/`). You set up the
project, check, run, and read the numbers; the user sees it all in the
window when it is open.

**Read `customcst/REFERENCE.md` first.** This skill is the procedure;
REFERENCE is the facts: the project file and its Python API (section 4),
the body types and their geometry rules (5), materials and f_ref (6), the
mesh (8), the simulation settings (9), run.py and the dry run (10), the
server (11), reading run files (12), the window's commands (13), the
method (15), the pitfalls (16). For antennas (tuning, convergence, the
far field, a model from myCad) also read
`.claude/skills/antenna-design/SKILL.md`.

All paths below are relative to `customcst/`; the Python is
`../.venv/Scripts/python.exe` (`-X utf8` for non-ASCII names).

## 1. Understand before simulating

- What is asked: S11 / match over a band, Z at a frequency, transmission
  (S21), the pattern (D, G, efficiency, beam), a comparison of variants.
  Write down the band and the figures you will report.
- Read the model's bodies (names, kinds, bounds): REFERENCE 3. Decide what
  each is: conductor, dielectric (which material), port, lumped element,
  air. A body you do not understand: ask.
- Which port: a coax connector → its end cylinder as `waveport`; a PCB
  edge → a box `waveport`; a probe or a gap → a lumped `port` cone (small
  end +). The geometry must follow REFERENCE 5; a model without one cannot
  run: say what to add (in myCad, or the user's CAD).
- Ask only what changes the result and has no sensible default; say the
  defaults you chose (FR4 4.3 / 0.02, f_ref at the design frequency,
  accuracy -40, or -50 for antennas and resonators).

## 2. Set up the project

Write the .toml through `Project` (REFERENCE 4), not by hand, unless you
only change a value:
- every body a type; dielectrics a material, f_ref = the design frequency;
- the band ±20-30 % around it; `excite` all, or the one port you need;
- the mesh: 20-30 cells/λ, 4 per feature for a first look;
- an antenna: padding ~λ/2 at the lowest far-field frequency, the far-field
  frequencies inside the band, accuracy -50;
- `save()`. Never write into the user's own models or their .toml from a
  test: copy them to the scratchpad. A new model of yours goes where the
  task says (a design project's `sim/`, or `models/` for examples).

## 3. Check without running

    ../.venv/Scripts/python.exe -X utf8 run.py MODEL.step --dry-run

- The ports found (`port feed 50 Ω`), no "cannot run", no shorts.
- The grid: cells and the smallest step (it sets the time step: a thin
  layer makes runs long); the memory; far field at the frequencies asked.
- Warnings: an unassigned body you meant to type, f_ref outside the band,
  a dielectric equal to air.
Fix and dry-run again until clean. Never run with a short you do not mean.

## 4. Show it in the window (when the user watches)

    UI="../.venv/Scripts/python.exe -X utf8 tools/ui.py"

1. **Get a window.** `$UI status`:
   - exit 2: no window. Start `../.venv/Scripts/python.exe main.py
     MODEL.step --locked` in the background and wait until `$UI status`
     answers with that model.
   - the user's window: ask before opening another model in it, then
     `$UI lock "what I am doing"` (refused while they edit: ask them).
2. **Per step**: write the files, `$UI reload`, `$UI wait`, `$UI status`:
   check `unassigned` (empty, or meant), `orphans`, `problem` (null),
   `shorts` (empty), `cells`. Show what matters: `$UI tab 3d|results|farfield`,
   `$UI show "Run N" "Run M"`.
3. **Run**: `$UI run_server`, then `$UI wait --server` in the background
   (a run takes minutes to an hour); the window fetches the finished run
   (else `$UI fetch`).
4. **Finish**: tick the runs to compare, the right tab, then
   `$UI unlock "look at Run N: ..."`.

- An answer "not in control": the user took control back. Stop and ask;
  never re-lock on your own.
- Screenshots (`$UI screenshot path=ABS.png`) are for your own eyes; the
  user watches the live window: do not hand them paths.
- No window watching: skip this section; run with run.py (local) or
  `remote_tasks.py launch / wait-run / fetch` (the server), REFERENCE 10-11.

## 5. Run

- Local (`run.py MODEL.step`, or ▶ Simulate): small models, a first look.
- The server (REFERENCE 11): anything of millions of cells or a final
  run, only when the user allowed server work in this conversation.
  `remote_tasks.py status` first (another run holds the machine?);
  `sync-code` after code changes. One run at a time there.
- Do not change the model's STEP or .toml while its run goes: the result
  comes back outdated.

## 6. Read the results

From the run file (REFERENCE 12), never from a picture: `list_results`,
`run.s`, `run.z_in`, `run.farfield` with `figures`, `cut`, `beamwidth`.
- S11: the minimum (dB, GHz), the value and Z at f0, the -10 dB band.
- Transmission: |S21| in dB over the band, the phase or delay if asked.
- Far field: D, G, realized G, efficiency, the beam direction, the
  beamwidths in the main cuts, front/back; `summary(ff)` warns when a body
  crosses the far-field box.
- Compare with the previous run (outdated ones too) and with what physics
  expects (REFERENCE 15, antenna-design's sanity figures).

## 7. Report

In Russian, short:
- a table of the figures (per run or per frequency);
- what changed since the last run and why (the physics, not only the
  numbers); correct earlier statements plainly when a result contradicts
  them;
- what the user can open now (model, Run N, tab, cut);
- what is not committed. Commit and push only when the user asks; never
  commit the user's own models or the [view] their window saved.

## When it fails

- "no valid port": the port body's geometry (REFERENCE 5): a lumped port
  is a cone with its ends in metal; a waveguide port stands on the line's
  end face, outside the model, along x, y or z, reaching the ground.
- A short on the mesh: a gap narrower than the grid resolves; refine
  (`cells_per_feature`, `min_step`) or check the geometry (a solder block
  bridging the pin and the shell?).
- S11 ≈ 0 dB everywhere: a short, a port in the wrong place, a body
  unassigned (the line's substrate missing), the wrong polarity is not it
  (polarity only flips the phase).
- "a body crosses the far-field box": more padding; at a waveguide port's
  side it cannot be avoided (REFERENCE 16).
- The window refuses a command: read the error text; it names the reason
  (not locked, cannot run, a transfer going on...).
