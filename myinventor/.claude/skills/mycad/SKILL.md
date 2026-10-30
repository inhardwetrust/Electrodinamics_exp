---
name: mycad
description: Draw, change or export a part in myCad, our parametric modeler (an antenna, a PCB with a connector, any 3D body from sketches, extrusions, revolutions, imports), by writing its part file (.part.toml), checking it numerically, and, when the user watches, driving their open myCad window step by step through its control channel. Use when asked to draw, model, build, place, change or export geometry in myCad, or to make a STEP of a structure for customcst.
---

# Drawing in myCad

myCad (`myinventor/`) is a parametric modeler: a part is a recipe
(parameters, sketches, features, placement) in a text file
`name.part.toml`, rebuilt from scratch on every change. You draw by
writing that file; the user sees and edits the same document in the
window. Never make geometry any other way (no hand-written STEP, no OCC
scripts as the result).

**Read `myinventor/REFERENCE.md` first.** It has everything this skill
relies on: the file format (section 4), every sketch entity (5), the
sketch planes and their axes (6), profile keys (7), the features (8),
bodies, groups and placement (9), the report (10), the CLI (11), the
window's commands (12), the method (15), measuring (16), the pitfalls
(17). Start from `examples/patch_antenna.part.toml` (substrate, patch,
feed with an arc bend, colors) for the shape of a file.

All paths below are relative to `myinventor/`; the Python is
`../.venv/Scripts/python.exe`.

## 1. Understand before drawing

- Clear up what is ambiguous in the request and decide what has a
  convention (say which you chose). Examples:
  - a "1.6 mm board" is 1.6 in total: FR4 1.53 + 35 µm copper on each side;
  - "a trace to the connector": say where it starts.
- An imported part (a connector): measure it first, numerically (its
  bodies, bounds, slices: REFERENCE 16). Find its axis, pin, legs and
  gaps, and how it must be turned and moved (REFERENCE 15, "Placing a
  part by turning it").
- Ask only what changes the result and has no sensible default.

## 2. Plan

- Every size that matters is a `[[parameter]]` with a `comment`;
  positions are formulas of them. Plain numbers only for true constants.
- Bodies named by meaning, one per material (`Substrate`, `Ground`,
  `Trace`, `Patch`, `PinSolder`), with colors. customcst assigns materials
  by these names: do not rename bodies once a simulation project uses them.
- Conventions for RF structures:
  - the board on XY from z 0 up by its FR4 thickness, centered on the
    origin;
  - top copper on `SubstrateExt:end`, ground on `SubstrateExt:start`
    (it goes down, y mirrored);
  - a line reaching the edge ends exactly on it;
  - conductors may touch or overlap (no union needed); a round pin on a
    flat trace touches along a line only, so add a solder block that
    overlaps it.
- Steps for showing: one meaningful thing each (the board, the trace,
  the connector, the solder), not one per line.

## 3. Write and check without the window

Write a builder script in the scratchpad that writes the whole part for
step N (0 = parameters only), atomically (temp file + `os.replace`). Keep
it: a Save in the window drops the file's comments. Build the last step
and read the report:

    ../.venv/Scripts/python.exe main.py PART --report [--json]

- Every feature `ok`, the last line `ok`; exit 0 / 1 (failed: the reason
  names the feature and the curve) / 2 (does not read: the place).
- **Compare every number with your own:** volumes (exact), bounds, sizes;
  profile and piece areas (from polygons, ~1e-3 relative). Pieces: take
  the keys from the report (`pieces`), the larger part is the larger area.
- Contacts and overlaps: measure them as `Common` volumes of the rebuilt
  shapes (REFERENCE 16). Never call it done by looking only.

## 4. Show it in the window (when the user watches)

    UI="../.venv/Scripts/python.exe ../customcst/tools/ui.py --app mycad"

1. **Get a window.**
   - None open (`$UI status` exits 2): write step 0, then start
     `../.venv/Scripts/python.exe main.py PART --locked` in the background
     and wait until `$UI status` shows the file.
   - The user's window is open: ask before opening another part in it.
     Then `$UI lock "what I am doing"` (refused while they edit, or with
     unsaved edits: ask them to finish or save).
2. **Per step:** write step N, `$UI reload`. Its answer is the status with
   the report: check the numbers in it. Then `$UI view name=iso|top|front...`,
   `$UI fit`, `$UI select kind=feature name=<new feature>`.
   A parameter change needs no file write: `$UI set name=formula ...`
   (one undo step, the report back).
3. **Finish:** `$UI save`. If it goes to customcst, read the STEP back with
   customcst's reader (REFERENCE 14). Then
   `$UI unlock "what to look at"`.

- Read `ui.py`'s JSON with `python -X utf8` (non-ASCII names).
- An answer "not in control": the user took control back. Stop and ask;
  never lock again on your own.
- Without a window watching, skip this section: write the file, check,
  export (`--export-step`).

## 5. Report

In a few lines:
- what is where (in mm), and the numbers that prove it;
- what touches what, with the measured overlaps;
- what you decided on your own;
- what looks off (in an imported model too).

Do not hand the user screenshot paths: they watch the live window.
Screenshots (`$UI screenshot path=ABS.png`) are for your own eyes.

## 6. Changing an existing part

- Re-read the file first: the user may have changed it, and
  Deparametrize turns formulas into numbers (do not turn them back
  without being asked).
- Do not write into the user's part files without being asked; a new
  design goes into a new file.
- Unlocked, the open window reloads the file on every write: never write
  while it has unsaved edits of the user's (it then only warns, and their
  Save would overwrite you). Locked, it waits for `reload`.
- Edit a saved file by its tables (REFERENCE 15, step 7), or regenerate
  from your builder script.
- Variants of a sweep without touching the file:
  `main.py PART --set NAME=FORMULA [--set ...] --export-step out.step --report --json`.

## When it fails

Read the `FAILED:` reason; it names the feature and the curve. Typical
causes:
- a profile key that is not a region: take it from the report;
- a polyline bend that does not fit;
- a parameter with an error: every user of it fails;
- a sketch on a face that is not there;
- a cut that removes nothing;
- a transform of a body after its group's.

A failed feature's body is left out of the STEP; the export says which.
A file that does not read leaves the window as it was: fix it and
`reload` again.
