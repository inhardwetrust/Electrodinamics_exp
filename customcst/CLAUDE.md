# CLAUDE.md — customcst

Context for future Claude sessions. Read this first, then `ARCHITECTURE.md`.

- This is the successor of `../pseudo_cst_3d`; read its CLAUDE.md for the
  working style and its pitfalls (Qt swap on AMD, screenshots only through
  `window.grab()`, and so on).
  - The user writes in Russian: answer in Russian, keep code and comments in
    English. Claude writes all the code.
  - Physics is validated numerically (`checks/`), never by eye.
- Run with `../.venv/Scripts/python.exe main.py [file.step] [--screenshot out.png]`.
  Run checks with `../.venv/Scripts/python.exe checks/check_step_import.py`.
  Regenerate the test models with `tools/make_test_models.py`.
- Imports are package-relative to this folder (`geometry.*`, `view.*`).
  `main.py` and the scripts put this folder on sys.path.
- **OCP 8 (cadquery-ocp 8.0.1)**:
  - static methods end in `_s` (`BRep_Tool.Triangulation_s`), but the TopoDS
    downcasts are plain functions (`TopoDS.Face`);
  - NCollection templates are not bound: there is no `TDF_LabelSequence` and
    no `TopTools_IndexedMapOfShape`, so walk documents with
    `XCAFPrs_DocumentExplorer` and deduplicate in numpy;
  - output float arguments are not returned (`GetLengthUnit_s` is unusable),
    so set the document unit to mm before reading instead;
  - `Bnd_Box.Get()` is broken, so compute bounds from the triangles;
  - temporaries from `explorer.Current()` / `TDF_ChildIterator.Value()` may
    dangle. Keep the node in a variable. Walking XCAF child labels
    (sub-shape names) still crashes (access violation), so body names are
    read with `reader.ChangeReader().WS().TransferReader()
    .EntityFromShapeResult(shape, 1).Name()` on the UNLOCATED shape, which
    is moved afterwards.
- Windows console: the check scripts call
  `sys.stdout.reconfigure(encoding="utf-8")` (they print ε, Ω).
- Never write into the user's project TOML from tests: copy the model into
  the scratchpad first.
- Only OCP code goes in `geometry/step_io.py`; everything above it is numpy.
- Git: never `git add .` at the repo root (see the pseudo_cst_3d notes).
