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
- Application settings (`app_settings.py`: recent files, window size and
  maximized state) live in `%APPDATA%\customcst\settings.json`. Any test
  that creates a MainWindow must set `CUSTOMCST_SETTINGS` to a scratch file
  first, or it rewrites the user's recent list and window size.
- `MainWindow.geometry` is the STEP model, and it hides
  `QWidget.geometry()`. Call `QMainWindow.geometry(self)` for the window
  rectangle.
- 3D navigation (`geometry_view.TrackballCamera`, CAD-style like the
  middle-button orbit in SolidWorks or Inventor):
  - left drag rotates as a virtual trackball. The rotation is a free
    matrix: through the middle it tilts about a screen axis, around the
    rim it rolls about the view direction. The pivot is the camera center;
  - Space + left drag (or Shift + left drag) pans; the model at the pivot
    depth follows the cursor exactly;
  - the wheel or a right drag zooms;
  - Reset view (and a change of the Up axis) restores an isometric view
    with the up axis vertical;
  - key autorepeat is ignored. The canvas takes the focus when the mouse
    enters it, so Space never toggles a checkbox in the tree.
- **VisPy drops mouse moves that come < 10 ms after the previous one**
  (`app/base.py _vispy_mouse_move`). The camera is built for that: the
  trackball works from the last DELIVERED position, the pan from the press
  point. Synthetic UI tests must space the moves (`QTest.qWait(15)`
  between them) and leave ≥ 0.5 s between drags. Otherwise only one move
  arrives and the result looks 10x too small.
- Layout: Project dock on the left; the central QTabWidget holds
  "3D View" (the VisPy canvas) and "Results" (ResultsPanel). After a run,
  the window switches to Results. The status bar stays at the bottom.
- Only OCP code goes in `geometry/step_io.py` (and `geometry/kicad_step.py`,
  the KiCad import, which needs booleans); everything above them is numpy.
- The FDTD hot loop runs as numba kernels (`solver/fdtd_kernels.py`) when
  numba is installed. `CUSTOMCST_NUMPY=1` forces the numpy path; after any
  change to the update equations run `checks/check_kernels.py` (both paths
  must stay bit-identical).
- Git: never `git add .` at the repo root (see the pseudo_cst_3d notes).
