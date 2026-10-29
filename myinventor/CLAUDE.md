# CLAUDE.md — myCad

Context for Claude sessions. Read this first, then `DESIGN.md`.

myCad is a simple parametric 3D modeler, written from scratch. (It was
"myinventor" until 2026-10-28; renamed because Inventor is Autodesk's
trademark. Old part files and settings still load. The folder itself may
still be `myinventor/` until it is renamed; nothing depends on its name.) Its
output is STEP. Later it becomes a part of `../customcst` (our CST-like
FDTD simulator, which reads STEP), so STEP stays the base format.
Compatibility with Inventor or any other CAD is not a goal.

## How we work

- The user writes in Russian: answer in Russian, keep code, comments and
  docs in English. Claude writes all the code. The user is the
  UX consultant and the domain expert (RF hardware, Inventor, KiCad).
- Discuss the architecture before big changes, then build step by step.
- Validate numerically, never by eye. Every feature gets a check script
  in `checks/` (volumes, bounds, face counts, a STEP round trip), like
  `../customcst/checks/`.
- Commit and push only when the user asks. Add files explicitly: never
  `git add .` (the repo root holds unrelated work). End commit messages
  with the attribution line from the session instructions.
- Never write into the user's model files from tests: copy them to the
  scratchpad first.
- Screenshots only through `window.grab()` or `canvas.render()`, never a
  screen grab: one once captured the user's browser.
- Any app settings file (recent files, window size) must be redirectable
  by an environment variable, and tests must redirect it. customcst uses
  `CUSTOMCST_SETTINGS`.
- Never use `git stash`: it touches the user's uncommitted files.

## Environment

- Windows 11, AMD GPU. The Python venv is shared: `../.venv`, run with
  `../.venv/Scripts/python.exe`.
- Stack (same as customcst): numpy, PySide6 (Qt6), VisPy, cadquery-ocp 8
  (OpenCASCADE 7.x bound as `OCP`). The geometry kernel is OpenCASCADE,
  the same one FreeCAD, CadQuery and build123d use.

## Layout and running

- Run with `../.venv/Scripts/python.exe main.py [name.part.toml] [--screenshot out.png]`.
  Checks (run all after a change): `checks/check_document.py` (model, no
  window), `checks/check_view.py`, `checks/check_window_files.py`,
  `checks/check_sketch.py`, `checks/check_sketch_line.py`,
  `checks/check_sketch_drag.py`, `checks/check_sketch_point.py`,
  `checks/check_sketch_align.py`, `checks/check_sketch_select.py`,
  `checks/check_sketch_circle.py`, `checks/check_sketch_snap.py`,
  `checks/check_sketch_construction.py`, `checks/check_sketch_clipboard.py`,
  `checks/check_extrude.py`, `checks/check_extrude_edit.py`,
  `checks/check_extrude_ops.py`, `checks/check_sketch_on_face.py`,
  `checks/check_feature_status.py`, `checks/check_body_color.py`,
  `checks/check_step_export.py` (it reads the STEP with customcst's own
  reader, in a subprocess: both projects have a `geometry` package),
  `checks/check_sketch_polyline.py`, `checks/check_cli.py`,
  `checks/check_deparametrize.py`.
- Without the window: `main.py name.part.toml --report [--json]
  [--export-step out.step]` (`cli.py`: rebuild, report, STEP; no Qt).
  The skill `.claude/skills/mycad/SKILL.md` teaches an AI to draw by
  writing part files and checking them this way; its example is
  `examples/patch_antenna.part.toml` (check_cli.py checks both, including
  the skill's table of sketch-plane axes). The window reloads its open file
  when another program changes it (`MainWindow.poll_disk`).
- A check that imports helpers from another check runs that module's top
  too, which points `MYCAD_SETTINGS` to its own temp file (still never the
  user's): read the path with `app_settings.settings_path()`.
- Features that need OCP get it as `ctx.kernel` (`geometry/kernel.py`,
  imported lazily by `model/rebuild.py`), so `model/` stays OCP-free.
- VisPy swallows an exception raised in an event callback (it only logs
  "ERROR: Invoking ..."): every window check installs
  `checks/vispy_errors.py` and fails on any such log.
- Synthetic mouse moves in checks wait ~20 ms first: VisPy drops a move
  that comes < 10 ms after the previous one (a flaky hover otherwise).
- A check's window opens on the user's screen, maybe under their cursor:
  real mouse moves then mix with the synthetic ones (a flaky preview).
  Checks that send mouse events set `WA_TransparentForMouseEvents` on the
  canvas (`check_sketch_line.shield`); `sendEvent` still delivers.
- Feature types live in their own modules (`model/sketch.py`) and are
  registered by importing them in `model/part.py`.
- `model/`: the document, numpy + stdlib only (no OCP, no Qt); see
  DESIGN.md "Document". `expressions.py` (safe formulas), `features.py`
  (Feature base + registry), `part.py`, `part_file.py` (TOML),
  `document.py` (file, modified, snapshot undo), `rebuild.py`.
  Browser keys are `("datum" | "body", name)`.
- Window dialogs go through `MainWindow.ask_*` / `show_error`: checks
  replace them, never let a modal dialog open in a check.
- `geometry/`: the only OCP code (`tessellate.py`: shape -> `Body`, with
  the CAD face index of each triangle). `view/`: numpy + VisPy + Qt
  (`geometry_view.py` and `picking.py` are trimmed copies of customcst's).
  Imports are package-relative to this folder; `main.py` puts it on
  sys.path.
- Settings: `app_settings.py`, `%APPDATA%\myCad\settings.json`, or
  `MYCAD_SETTINGS` (tests must set it).
- QTest.mouseMove carries no pressed button in Qt 6 (and a plain hover
  does not reach the canvas): synthetic moves send `QMouseEvent`s
  themselves (`checks/check_view.py`).
- Navigation differs from customcst: middle drag rotates, Ctrl + middle
  pans (Space + left too), the wheel / right drag zooms; the left button
  only selects (and will draw in sketches).

## Reuse from customcst (read the code, copy what fits)

| What | Where |
|---|---|
| STEP read (XCAF: names, colors, placements), tessellation, CAD edges and vertices | `../customcst/geometry/step_io.py` |
| STEP write with names and colors; one part of several named solids | `step_io.write_step`, `../customcst/geometry/kicad_step.py _write` |
| Booleans (fuse per layer, cut by a box, UnifySameDomain) | `../customcst/geometry/kicad_step.py` |
| The numpy body contract (triangles, edges, corners in m) | `../customcst/geometry/body.py` |
| 3D view: CAD trackball camera, edges, X-ray, section plane, headlight | `../customcst/view/geometry_view.py` |
| Picking points (vertex / edge / face snap, visibility, perspective-exact), A-B distance | `../customcst/view/picking.py`, `checks/check_picking.py` |
| Section fill and outline | `../customcst/view/section.py` |
| Project file as TOML: strict validation, never overwrite a file that failed to parse | `../customcst/project/project_file.py` |
| Settings: recent files, window state | `../customcst/app_settings.py` |
| Long work in a background thread with progress | `../customcst/view/main_window.py` (`_start_discretize`, `_poll_discretize`) |

## Pitfalls already found (do not rediscover)

**OCP 8 (cadquery-ocp 8.0.1):**
- Static methods end in `_s` (`BRep_Tool.Triangulation_s`). The TopoDS
  downcasts are plain functions (`TopoDS.Face`).
- NCollection templates are not bound: there is no `TDF_LabelSequence`,
  no `TopTools_IndexedMapOfShape`, and no `TopTools_ListOfShape` for
  multi-argument booleans. Walk documents with `XCAFPrs_DocumentExplorer`,
  deduplicate in numpy, and fuse pairwise.
- Output float arguments are not returned (`GetLengthUnit_s` is unusable):
  set the document unit to mm before reading.
- `Bnd_Box.Get()` is broken. Compute bounds from the triangles, or use
  `BRepBndLib.AddOptimal_s` and read the corners with `CornerMin/Max`.
- Temporaries from `explorer.Current()` may dangle: keep the node in a
  variable.
- Walking XCAF child labels crashes (access violation). Read body names
  through `reader.ChangeReader().WS().TransferReader()
  .EntityFromShapeResult(shape, 1).Name()` on the unlocated shape.
- OCCT prints transfer statistics to stdout. Silence the messenger
  (`step_io._silence_occt`).

**STEP writing:**
- OCC's writer leaves solid names empty. To keep several solids as one
  named body, write a compound and fill the names into the
  `MANIFOLD_SOLID_BREP('…'` entities of the text afterwards, then read the
  file back and compare.
- Always write a compound: a located solid written alone becomes an
  instance of an unnamed part ('SOLID').
- Inventor names a part after its file and may repeat body names inside
  it. A reader must not merge same-named solids blindly.

**VisPy / Qt:**
- Qt6 QOpenGLWidget plus VisPy's explicit swapBuffers blocks for about two
  frames on this AMD GPU. Make it a no-op: `_skip_redundant_qt_swap` in
  geometry_view.py.
- VisPy drops mouse moves that come < 10 ms after the previous one. The
  trackball works from the last delivered position, the pan from the press
  point. Synthetic UI tests must space the moves (`QTest.qWait(15)`).
- The default camera `depth_value` (1e6) leaves too little depth
  precision: a 35 µm copper layer z-fights with the board. 200 works.
- Draw edges exactly on the surface and push the faces back with
  `polygon_offset_fill=True, polygon_offset=(1, 1)`.
- A sheet (zero thickness) needs a reversed copy of its triangles with its
  own vertices: shared vertices average the normals to zero.
- `set_gl_state` does not accept `depth_mask`; passing it broke all
  drawing.
- `SceneCanvas(keys="interactive")` closes the canvas on Esc (the 3D view
  vanishes from the window) whatever our handlers do: use `keys=None`.
  customcst still has "interactive".
- VisPy's camera resets on Backspace through `BaseCamera.set_state(state)`:
  a camera subclass must not define its own `set_state` (ours is
  `set_view_state`), and ours ignores camera keys (`viewbox_key_event`).
  customcst's TrackballCamera still overrides `set_state`.
- An overlay drawn on a face (the section glass) needs a small offset
  toward the viewer (~2e-4 of the model size), or it stripes (z-fighting).
- Translucent bodies look hollow: keep the fill opaque and make only
  overlays translucent.
- A sketch on a face lies under what was extruded from it (copper 35 µm
  above): the sketch being edited is drawn without depth test and its
  picking ignores the bodies (`_SketchVisual.on_top`, `datum_at`), or it
  can be neither seen nor picked.
- Click versus drag: a press and release within 4 px is a click (it
  picks), anything longer rotates or pans.
- In perspective, a screen fraction along an edge is not its 3D fraction:
  take the edge point closest to the click ray.
- Background threads must not touch Qt objects. Poll the job with a QTimer
  and take the result in the GUI thread. A nested event loop
  (processEvents during work) crashed the app.
- `MainWindow.geometry` as an attribute hides `QWidget.geometry()`. Pick
  another name for the model.
