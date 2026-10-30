# Task: customcst <-> myCad (the bridge for design projects)

Written by the myCad session (2026-10-30), revised with the user in the
customcst session the same day. The state of each part is at its end.

## Why

Antenna designs move to a three-folder layout:
- `../myinventor/`: myCad, the parametric modeler. A design is a part file
  (`*.part.toml`); it exports STEP (named, colored bodies; the names are
  stable between exports).
- `customcst/` (here): the FDTD simulator.
- a project folder per design (the first: `../patch_antenna_sma/`).

## The workflow (agreed)

One model, changed in place; each change a run. Everything is local but
the computing, which customcst does on the server on its own (Run on
server / Fetch: no myCad there, none needed).
1. Build the part in myCad, export the STEP into `sim/`, write the
   customcst .toml beside it (types, materials, the port, band, mesh).
2. A change: talk it over; copy the part as it is to
   `variants/runNN.part.toml` (+ its `--report --json`), edit the open
   part, export over the STEP (only if the geometry changed: every export
   changes the bytes, and the run fingerprint hashes them).
3. customcst reloads the STEP and the .toml by itself; the .toml keeps the
   types by body name; new bodies get theirs, gone ones show as orphans.
4. Run on the server, fetch, look at it together; log it in
   notes/log.md (Run N = variants/runNN, what changed, the figures).
Old runs stay, marked outdated, and compare. No per-variant STEPs are
kept. A batch of one knob's values (`--set` exports into a `zz_` folder,
run together on the server) only when a sweep is really wanted.

## customcst's part

A. No sweep bridge as a mode of sweep.py (dropped: the loop above needs
   none). The batch form is described in the skill.
B. Reload the model when it changes on disk: DONE.
   `MainWindow.poll_disk` (700 ms); after the files settle; not during a
   run, a transfer, a mesh job, a tree edit or a dialog; the camera stays;
   a file that does not read leaves the window as it was, without a
   dialog. `checks/check_disk_reload.py`.
C. The skill: DONE. `.claude/skills/antenna-design/SKILL.md` section 8
   "The model from myCad".

## myCad's part

- `--set NAME=FORMULA` in the CLI (for the batch form).
- Non-ASCII body names in the exported STEP: myCad writes them raw
  (UTF-8) into `MANIFOLD_SOLID_BREP('...')`, customcst reads
  `Volumenkörper16` back as `VolumenkÃ¶rper16`. ISO 10303-21 wants them
  encoded (`\X\F6`, or `\X2\00F6\X0\`); the connector's own file has
  `\X\F6` and reads right. To fix in myCad's `geometry/step_io.py`, with
  a check that reads the name back through customcst.

## Checked here

- The connector imported in myCad and exported: the five bodies arrive in
  customcst, the non-ASCII name mangled (above).
- Two exports of the same part differ in bytes (the time stamp in
  FILE_NAME, the order of STYLED_ITEM references).
