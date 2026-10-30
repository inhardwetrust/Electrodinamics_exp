# Task: customcst <-> myCad (the bridge for design projects)

Written by the myCad session (2026-10-30) for the customcst session. The
user agreed on the plan; build it here, step by step, with checks as usual.

## Why

Antenna designs move to a three-folder layout:
- `../myinventor/`: myCad, the parametric modeler. A design is a part file
  (`*.part.toml`: parameters with formulas, sketches, extrusions, an
  imported connector placed by Move / Rotate). It exports STEP (named,
  colored bodies; the names are stable between exports).
- `customcst/` (here): the FDTD simulator.
- a project folder per design (the first: `../patch_antenna_sma/`): its
  part file, the connector STEP, the customcst project, runs, notes,
  instructions for Claude. Read its README.md and CLAUDE.md.

Until now `sweep.py` builds its models from code (`tools/make_test_models.py`
PARAMS + a generator). In a design project the model is a myCad part
instead. Tuning still needs sweeps (3-6 variants of 1-2 parameters, the
table S11 min / at f0, Z_in, D, G, efficiency, beam).

## A. The sweep bridge (the main part)

A mode of `.claude/skills/antenna-design/scripts/sweep.py` (or a sibling
script, your call) that takes its variants from a myCad part:

    sweep.py FOLDER --mycad PROJECT/model/patch.part.toml \
        --project PROJECT/sim/patch.toml \
        --variants '[{"L": "11.0"}, {"L": "11.4", "inset": "3"}]' \
        --cells 30 --feature 4 --f0 5.8 --farfield 5.8

For each variant:
1. Run myCad headless, in a subprocess (both projects have a `geometry`
   package; never import myCad here):

       ../.venv/Scripts/python.exe ../myinventor/main.py PART \
           --set L=11.4 --set inset=3 --export-step FOLDER/<stem>_<tag>.step --report --json

   `--set NAME=FORMULA` overrides a parameter for this run only (the part
   file is not written). Exit codes: 0 ok, 1 the part has errors (the
   report says which feature; the STEP may be partial: treat as a failed
   variant), 2 the part file does not read. The JSON report has the
   bodies (name, volume, bounds) and parameter values: keep it next to the
   STEP (`<stem>_<tag>.mycad.json`) so a result can be traced to its
   geometry.
   NOTE: `--set` does not exist yet; the myCad session adds it (the
   contract above is agreed: build against it, test with a stub or after
   it lands).
2. Put the customcst project next to the STEP: a copy of `--project` (the
   template: materials and object types assigned by body name, the port,
   the band, mesh), its stem renamed. The body names come from the part
   and do not change between variants, so the assignments hold.
   A body the template names but the STEP lacks (or the other way round):
   report it per variant, do not run silently with an unassigned body.
3. Then as now: mesh / cells, run locally or on the server
   (remote_tasks.py sweep), the table per variant, and which parameter
   values gave which row.

Also: a one-shot form for a single variant (the final run): the same
pipeline with one variant and the final mesh, written where the project
folder says (`sim/`), so the user opens it in the window.

## B. Reload the project when it changes on disk

The window notices that the project file changed ("changed on disk: ...
Reload reads it") but does not reload. In a design session Claude writes
the .toml (and the STEP) while the user watches: reload by itself when the
window has no unsaved edits (myCad does this: `MainWindow.poll_disk`, a
QTimer every 700 ms, one undo step, never over unsaved edits, a file that
does not read leaves the window as it was). Same for the STEP the project
points to.

## C. Document it

The antenna-design skill: a section "The model from myCad" (the project
folder layout, the bridge, what goes into the template project, the
connector port below). Keep the code-built path (make_test_models) as it
is.

## What the first project needs from customcst (check it works)

- The connector `../patch_antenna_sma/parts/realconnector.stp` (an SMA end
  launch, 5 bodies): `connector_outer`, `connector_outer_2` (the shell),
  `connector_dielectric` (PTFE, eps_r ~2.1), `connector_wire` (the center
  pin), `Volumenkörper16` (a cylinder r 2.675 mm, 1 mm long, at the
  coax's outer end: the coax waveport). In the file the coax axis is
  along -y, the port at y -14.5 .. -13.5. In myCad they are imported as
  `<import>:<name>` (e.g. `SMA:connector_wire`) and may be moved / turned
  by 90 degree steps only (the waveport cylinder must stay along x, y or z
  and on a domain end: check that `run.py --dry-run` finds it there).
- Conductors may touch or overlap (the pin on the microstrip, "solder"
  extruded on a pad): PEC edges merge, no union needed. Two different
  dielectrics in one place (PTFE into FR4) resolve by body order: report
  such an overlap if you can detect it.
- Body name with a non-ASCII letter (`Volumenkörper16`): make sure the
  project file, the reader and the report keep it intact (the myCad
  report printed it mangled on a cp1251 console).

## Done when

- A check builds variants from a small myCad part (a patch on a board, no
  connector) through the bridge and reads back the STEP and the copied
  project (body names assigned, no unassigned body), with `--set` stubbed
  until myCad has it, then for real.
- The window reloads a project changed on disk (a check).
- The skill section is written; the user is told what changed.
