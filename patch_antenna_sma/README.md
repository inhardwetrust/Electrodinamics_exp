# patch_antenna_sma

A patch antenna on FR4 1.6 mm fed from an SMA end-launch connector, designed
end to end by Claude with our own tools, the user watching in their windows.
The first project of this kind: it also proves the workflow.

## Three folders

- `../myinventor/`: myCad, the parametric modeler (the 3D model).
- `../customcst/`: customcst, the FDTD simulator (S11, Z, the far field).
- this folder: everything about this design. The tools stay general;
  nothing of this antenna goes into their folders.

## Layout

    README.md         this file: what, how, the status
    CLAUDE.md         how Claude works here (read it first)
    prompt.md         the task as given (the user's prompt, filled in)
    notes/log.md      the discussion and the decisions, dated, newest last
    notes/*.md        estimates, sweep reports, anything worth keeping
    parts/            inputs from outside: the connector STEP (as received)
    model/            the myCad part (*.part.toml) and its folder (*.files:
                      the imported STEPs, copied by myCad), the exported STEP
    sim/              the customcst project(s) (*.step + *.toml), final runs
    sweeps/           sweep folders: variants, their STEPs, projects, results

## The workflow

1. Estimate the starting dimensions (formulas, earlier runs): notes/.
2. Build the part in myCad (`model/`): board, ground, patch, the feed as a
   polyline, the matching, the connector imported and placed. Check it
   with myCad's report (`main.py ... --report`), the user sees it in the
   myCad window (it reloads the file on every change).
3. Export STEP, write the customcst project (materials, the connector's
   coax port, band, mesh) into `sim/`; a dry run finds the port.
4. Tune: sweeps of 1-2 parameters through the bridge (myCad exports each
   variant, customcst runs it): `sweeps/`. Log each round in notes/.
5. The final run with the converged mesh in `sim/`, opened by the user.
6. Report: the figures, what changed and why, what is left.

## Status

- 2026-10-30: the folder made; the connector in `parts/`. The tools get
  their part of the bridge first (customcst: `TASK_mycad_bridge.md`;
  myCad: `--set` in its CLI). Then the prompt (`prompt.md`) and the run.
