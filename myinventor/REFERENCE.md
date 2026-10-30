# myCad reference

What myCad can draw, how it works inside, and how Claude drives it. For a
session with a clean context: read this, then work. The skill
`.claude/skills/mycad/SKILL.md` is the procedure; this file is the facts it
relies on. The design decisions and the roadmap are in DESIGN.md; the
module headers (`model/*.py`) are the final word on every key.

Contents:
1. What myCad is
2. How it works inside
3. Formulas
4. The part file
5. Sketches: entities
6. Sketch planes and their axes
7. Profiles: what an extrusion takes
8. Features: extrude, revolve, work plane, import
9. Bodies, groups, placement
10. The rebuild and the report
11. Without the window: the CLI
12. Driving the window: the control channel
13. The window from the user's side
14. STEP out
15. How to work: the method that worked
16. Measuring: recipes
17. Pitfalls already met
18. Not there yet

---

## 1. What myCad is

A parametric 3D modeler written from scratch (Python, OpenCASCADE through
`OCP`, Qt + VisPy). Its output is STEP with named, colored bodies for
customcst (`../customcst`, our FDTD simulator). A part is a recipe:
parameters with formulas, then a list of features (sketches, extrusions,
revolutions, work planes, STEP imports), then placement transforms. It is
rebuilt from scratch on every change; nothing computed is stored.

The recipe is a text file `name.part.toml`. The user (in the window) and
Claude (writing the file, or driving the window) edit the same document.

## 2. How it works inside

| Layer | Folder | What | Rule |
|---|---|---|---|
| document | `model/` | Part, features, file, undo, rebuild | numpy + stdlib only, no OCP, no Qt |
| kernel | `geometry/` | OCP: solids, booleans, STEP in / out, tessellation | the only OCP code; features get it as `ctx.kernel` |
| window | `view/` | Qt + VisPy: 3D view, browser, panels, control channel | |
| headless | `cli.py`, `main.py` | report, STEP, `--set` | no Qt |

- **Part** (`model/part.py`): `parameters`, `origin` (YZ / XZ / XY Plane,
  X / Y / Z Axis, Center Point; only their visibility is saved),
  `features` (history order), `bodies` (per-body properties: color),
  `groups`, `transforms`.
- **Names are ids.** Parameters have their own namespace (formulas see only
  them). Datums, features and transforms share one (references see them).
  Bodies have theirs. Renaming rewrites every use.
- **Feature types** are registered classes (`model/features.py`: `TYPE`,
  `FIELDS`, `build(ctx)`); transforms have a registry of their own.
- **Rebuild** (`model/rebuild.py`): parameters → each feature in order with
  the results above it → bodies by name in `ctx.shapes` → transforms →
  tessellation. A failing feature is recorded and the features using it
  fail too ("uses X, which failed"); the rest still builds.
- **Document** (`model/document.py`): the file, `modified`, undo / redo
  as snapshots of the plain data (one per committed edit, 200 kept).
- **Window** (`view/main_window.py`): shows the rebuild; while a sketch is
  edited, only up to it is rebuilt and the bodies stay as they were;
  while modeling (sketch, plane pick, Extrude, Revolve, Work Plane) the
  bodies are shown unplaced. It reloads the file when another program
  changes it (unless the user has unsaved edits, or the window is locked).

## 3. Formulas

Every number in the file is a formula (a string; a bare number is read as
its text): `"1.6"`, `"patch_w / 2 + gap"`, `"sqrt(a^2 + b^2)"`.
- Operators `+ - * / ^ ** %`, parentheses.
- Functions: `sqrt abs min max round floor ceil exp log log10 hypot`,
  `sin cos tan` (degrees in), `asin acos atan atan2` (degrees out).
  Constants `pi`, `e`.
- Names: the parameters only. A parameter may use one defined below it;
  cycles, unknown names, non-finite values are errors naming the
  parameter (and every feature using it fails).
- Lengths in mm, angles in degrees. Walked as an `ast`, never `eval`ed.

## 4. The part file

    format = "mycad-part"
    version = 1

    [origin]                       # optional: which origin datums are shown
    visible = ["XY Plane"]

    [[parameter]]                  # in table order
    name = "h"
    formula = "1.6"
    comment = "substrate"          # optional

    [[feature]]                    # in history order: a feature uses only what is above it
    name = "Sketch1"
    type = "sketch"                # sketch | extrude | revolve | workplane | import
    ...
    [[feature.point]]              # a sketch's entities: sub-tables of its [[feature]]

    [[body]]                       # per body: its color
    name = "Substrate"
    color = "#2e7d32"

    [[group]]                      # flat sets of bodies (a body in one group at most)
    name = "SMA"
    bodies = ["SMA:Pin", "SMA:Shell"]

    [[transform]]                  # placement, after all the features, in order
    name = "PlaceSMA"
    type = "move"                  # move | mirror
    ...

- **Reading is strict:** an unknown key, a wrong type, a missing reference
  or a duplicate name is an error naming the place (the CLI exits 2; the
  window refuses the file and keeps what it had).
- **Writing** (Save) goes through a temp file, is read back and compared,
  then replaces the file. **Save rewrites the file from the data: comments
  and the layout are lost**, keys come in the program's order.
- Body and group names: no quotes, no control characters (STEP). Feature
  names are free text; keep them identifiers (`SubstrateExt`).
- An import's files live in `<part name>.files/` beside the part file.

## 5. Sketches: entities

A sketch (`type = "sketch"`, `plane = ...`, section 6) holds 2D geometry
in its own x, y (mm). Entities are sub-tables; names are unique within
the sketch. Everything refers to points by name.

Two ways of drawing (DESIGN.md), mixed freely in one part:
- **manual**: points, lines, circles, arcs, polylines, splines, as a person
  draws them; formulas for the sizes that matter, plain numbers where the
  user may want to drag things. Layouts: boards, patches, feeds, slots.
- **generators** (`curve`): a few arguments → an exact computed shape (a
  parabola from f and D, any x(t), y(t)). Never draw a curve as many
  points and call it exact.

A point given by a formula cannot be dragged in the window; the user frees
formulas with Deparametrize (they become numbers). Prefer formulas when in
doubt: freeing is one click, the reverse is not.

| Entity | Keys | Notes |
|---|---|---|
| `point` | `name`, `x`, `y`; `explicit = true` (optional) | an explicit point is drawn and stays when its users go |
| `line` | `name`, `start`, `end`; `construction` | lines sharing points chain; a closed chain is a profile |
| `circle` | `name`, `center`, `radius`; `construction` | profile key `["C1"]` |
| `arc` (center) | `name`, `kind = "center"`, `center`, `radius`, `from`, `to` | degrees from the sketch's x, counter-clockwise from `from` to `to`; its ends are points `A1.start`, `A1.end`; exact |
| `arc` (three points) | `name`, `kind = "three"`, `start`, `end`, `through` | from start through `through` to end; exact |
| `polyline` | `name`, `points`, `widths` (one per point), `width` (for new points), `start` / `end` = `flat` \| `round`; optional `bends`, `radii` (per point) | a wide line (a trace): its outline is a profile `["W1"]`; width tapers linearly between points |
| `spline` | `name`, `points` (first again at the end: closed); `construction` | interpolating cubic, one exact B-spline; closed: profile `["Spline1"]`, open: joins lines at its ends |
| `curve` | `name`, `kind`, its arguments; `construction`, `visible` | a generator (below); ends `Name.start`, `Name.end` |
| `offset` | `of`, `left`, `right`, `start`, `end` | a band around a line / circle / spline / curve; that entity becomes its spine |

**Polyline bends** (per inner point; the ends' entries unused):
- `normal`: a sharp mitered corner (beveled past 4 half-widths);
- `arc`: the center line turns along an arc of radius R (default the
  width), both sides concentric, constant width (a curved microstrip);
- `chamfer`: the center line cut straight, R = the setback (default the
  width);
- `miter`: only the outer corner cut, R = the cutting circle's radius
  (default w/2): the classic microstrip miter.

A bend that does not fit between its neighbours fails the sketch, naming it.

**Generators** (`model/curves.py`):
- `parabola`: `focal` (> 0), `from`, `to` (signed distance from its axis
  where it starts and ends), `x0`, `y0` (vertex, default 0). It is
  y = y0 + (x − x0)² / 4f, its axis along the sketch's y. Exact (OCC's
  parabola).
- `function`: `x`, `y` (formulas of `t` and the parameters; `t` hides a
  parameter named t), `from`, `to` (t range, default 0..1). One B-spline
  within 0.1 µm. Trig in degrees: `sin(360 * t)`.
- `visible = false`: not drawn, not in profiles; its ends stay usable.

**Offset** (`model/offsets.py`): `left` / `right` (mm, ≥ 0, not both 0)
along the entity from its start to its end; a circle runs
counter-clockwise, its left is inside. Caps `flat` | `round` for an open
one. A side of 0 is the entity exactly. Profiles: `["X.offset"]` (the
band), for a closed one also `["X.offset.inner"]` (the hole / the
enclosed region). A side past the radius of curvature fails, naming it.

**Construction** geometry is never part of a profile (helpers, axes for
Revolve).

## 6. Sketch planes and their axes

`plane =`:

| plane | sketch x | sketch y | an extrusion goes (+distance) |
|---|---|---|---|
| `"XY Plane"` | +X | +Y | +Z |
| `"XZ Plane"` | +X | −Z | +Y |
| `"YZ Plane"` | +Y | +Z | +X |
| `"Ext:end"` (the cap at the distance) | as Ext's sketch | as Ext's sketch | out of the body |
| `"Ext:start"` (the cap on Ext's plane) | as Ext's sketch | **mirrored** | out of the body |
| `"Ext:side.L3"` (the wall line L3 swept) | along L3 from its start point | out of the wall × x | out of the body |
| `"Rev:start"` (a revolution under 360°: its sketch's plane) | as Rev's sketch | as Rev's sketch, or mirrored | out of the body |
| `"Rev:end"` (that plane turned by the angle) | Rev's sketch x turned | its y turned, **mirrored** | out of the body |
| `"Rev:disc.L3"` (the flat ring a profile line across the axis sweeps) | along L3 from its start | out of the ring × x | out along the axis; the origin on the axis |
| `"WorkPlane1"` (a work plane) | its base's x (turned with it) | its base's y | its normal |
| `"SMA:face.2.12"` (an import's planar face: body 2 of its list, face 12) | the face's plane x | normal × x | out of the body; the origin at the face's center |

(`checks/check_cli.py` checks the origin rows and the Ext rows of this table.)

- A face plane is computed from the feature's own values, so it follows
  parameter changes. The normal points out of the material (into the
  pocket for a cut).
- `Ext:end` keeps Ext's origin and axes, moved to the cap: draw on a
  substrate's top with the substrate's x, y.
- `Ext:start` mirrors y: on a ground plane under the board, a world y of
  −25..−21 is sketch y 21..25. Check positions in the report's bounds.
- An import's faces are as the file has them, before placement.
- Curved faces and faces no feature names cannot carry a sketch.

## 7. Profiles: what an extrusion takes

All bounding curves of a sketch (lines, circles, arcs, curves, splines,
polyline and offset outlines; not construction, not offset spines) go into
one planar graph (`model/profiles.py`):
- ends within 1 µm are one node (an end on another curve's middle cuts it
  there);
- curves are cut where they cross or touch; the pieces are exact;
- regions are the bounded faces; a loop inside another is its hole (a
  rectangle with a circle inside: the plate with a hole, and the disc).

Two kinds of keys, both valid in `profiles`:
- **whole loops**, as drawn: the sorted names of the loop
  (`["L1", "L2", "L3", "L4"]`, `["Feed"]`, `["A1", "L5"]`), nothing cut
  where loops overlap. Prefer these. Overlapping copper (a feed over a
  patch) fuses when both whole loops are extruded together.
- **pieces** where curves cross: an entity cut by others is named per
  piece from its start, `"L1:0"`, `"L1:1"`... (a circle cut by a line:
  `["C1:0", "L1:1"]` and `["C1:1", "L1:1"]`). They change when what
  crosses what changes. Take them from the report (`pieces`, with areas):
  "the larger part" is the larger area.

The report lists each sketch's `profiles` (whole) and `pieces` with key,
area (from polygons: ~1e-3 relative off the exact) and holes. A key that
is no region fails the extrusion, naming it.

## 8. Features: extrude, revolve, work plane, import

**Extrude** (`type = "extrude"`): `sketch`, `profiles` (a list of keys),
`distance` (negative: the other way), `operation` = `new` | `join` |
`cut` | `intersect`, `body`.
- `new` makes the body named `body` (the name must be free). The others
  change that existing body.
- Several profiles in one extrusion make one body (touching ones fuse;
  apart ones: one body of several solids).
- A cut that removes nothing or everything fails on purpose.
- Faces for sketches: `Ext:end`, `Ext:start`, `Ext:side.<line>`.

**Revolve** (`type = "revolve"`): `sketch`, `profiles`, `axis` (a line of
that sketch, usually construction; `"Sketch2:L1"`; or `"X Axis"`,
`"Y Axis"`, `"Z Axis"`), `angle` (default 360; negative: the other way,
right hand around the axis's direction from its start to its end),
`operation`, `body`. The axis lies in the sketch's plane and no profile
crosses it (touching is fine). Faces: `Rev:start`, `Rev:end` (under 360°),
`Rev:disc.<line>`. A parabolic reflector: the parabola from the axis to
D/2, an `offset` for its thickness (the parabola side exact), a
construction line on the axis:

    [[feature]]
    name = "Dish"
    type = "revolve"
    sketch = "Profile"
    profiles = [["Reflector.offset"]]
    axis = "L1"
    angle = "360"
    operation = "new"
    body = "Reflector"

**Work plane** (`type = "workplane"`), three ways:
- `base` + `offset`: the base moved along its normal;
- `base` + `axis` + `angle`: turned around an origin axis or a sketch line
  in the base (the right hand way); `offset` must be 0;
- `points = [p1, p2, p3]` (no base): each a sketch point `"Sketch1:P3"`,
  `"Center Point"`, or `["x", "y", "z"]` formulas. Origin at p1, x toward
  p2, the normal the right hand way round 1 → 2 → 3.

`visible = false` hides it. A sketch names it as its plane.

**Import** (`type = "import"`): `file` (relative to the part's folder:
`<part>.files/x.stp`), `sha256` (the file's content; `""` skips the check),
`bodies` (one name per body of the file, in its order; customcst-style
`"SMA:Pin"`).
- Each solid is a body; a sheet (a shell or face outside a solid) too;
  solids named like their part are one body. Colors come from the file
  unless `[[body]]` sets one.
- A different number of bodies or no file: it fails. A changed file
  (another sha256): built, but marked failed until Reload accepts it.
- Its bodies are like any other: join / cut them, color them, rename
  them. Faces for sketches: `SMA:face.<i>.<k>` (unplaced).
- By hand (`File > Import STEP`), the window copies the file into
  `<part>.files/`, fills the sha256, names the feature after the file
  (`realconnector`), its bodies `<feature>:<name in the file>`, and makes
  a group of them named like the feature. Writing the file yourself, do
  all of that (any feature name: `SMA`, bodies `SMA:...`, group `SMA`).

## 9. Bodies, groups, placement

- **Bodies** come from features. `[[body]]` holds a body's own properties
  (`color = "#rrggbb"`); a join or cut keeps them. customcst assigns
  materials by body name: name by meaning (`Substrate`, `Ground`, `Trace`,
  `Patch`), separate bodies per material. Renaming in a file: every
  feature's `body` and the `[[body]]` row (the window's Rename does it).
- **Groups** (`[[group]]`): flat named sets (a body in one group at most),
  shown, hidden and moved together. A transform targets one as
  `"group:SMA"`.
- **Transforms** (`[[transform]]`), after all the features, in order, on
  the bodies as built. Nothing of the features sees them (sketch
  coordinates, face planes, booleans are all unplaced).
  - `move`: `targets`, `offset = ["x", "y", "z"]` (mm), optional `axis`
    (an origin axis through the origin, or a sketch line `"Sketch1:L3"`)
    and `angle` (degrees, the right hand way around the axis):
    **turned first, then moved**. Turning around `"Y Axis"` by +90 maps
    (x, z) → (z, −x): a point off the axis moves, so compensate in
    `offset` (section 15 has the worked case).
  - `mirror`: `targets`, `plane` = `YZ` | `XZ` | `XY`: each target in
    place, in the plane parallel to that one through its own center (a
    group's: all its bodies as one). No outside planes, no copy.
  - A body of a group is placed before its group (local, then global); a
    transform of a body after one of its group fails, naming it. No
    targets: it fails.
- No booleans between bodies after placement (no Combine yet): conductors
  that only touch or overlap are fine for customcst (PEC merges on the
  grid). Avoid two dielectrics in one place (the later one wins per cell).

## 10. The rebuild and the report

`cli.report(part, result)` (the CLI's `--json`, the window's `status`,
the answers of `set` / `undo` / `redo`):

    {"parameters": [{"name", "formula", "value" | "error"}],
     "features": [{"name", "type", "error"?,
                   sketch: "plane", "points" (count), "lines", "circles", "polylines", "arcs",
                           "curves" [{name, kind, args, visible, start, end}],
                           "splines" [{name, points, construction, spans}],
                           "offsets", "profiles" [{key, area, holes}], "pieces" [{key, area, holes}],
                   extrude: "sketch", "profiles", "distance", "operation", "body",
                   revolve: ... "axis", "angle", import: "file", "bodies",
                   workplane: "base", "offset", "axis", "angle", "points"}],
     "bodies": [{"name", "volume" (mm³, exact), "solids", "bounds" [[lo], [hi]], "size", "color"}],
     "groups": [{"name", "bodies"}],
     "transforms": [{"name", "type", "targets", ..., "what", "error"?}],
     "ok": true | false}

Bodies are reported placed (after the transforms). Volumes are exact
(B-spline faces split at their knots first). The text form (`--report`
without `--json`) has one fact per line and ends with `ok` or `NOT OK`.

## 11. Without the window: the CLI

    ../.venv/Scripts/python.exe main.py PART --report [--json] [--export-step out.step] [--set NAME=FORMULA ...]

- `--report`: the report (text, or `--json`).
- `--export-step X`: the bodies built (a failed feature's are left out;
  the output says which).
- `--set NAME=FORMULA` (repeatable): a parameter's formula for this run
  only, the file untouched: sweep variants.
- Exit code 0 all built, 1 something failed (the report says what; the
  STEP is still written), 2 the file does not read, `--set` names no
  parameter, or the STEP was not written.
- No Qt is loaded. Run from `myinventor/` (or give the full path to
  `main.py`).

## 12. Driving the window: the control channel

The open window listens on 127.0.0.1 (a free port, a random token in
`control.json` beside the app settings: `%APPDATA%\myCad\`;
`MYCAD_CONTROL` / `MYCAD_SETTINGS` move it). The client is customcst's,
shared by both apps:

    ../.venv/Scripts/python.exe ../customcst/tools/ui.py --app mycad COMMAND [ARGS]

Exit 0 (the result as JSON on stdout), 1 (the window's error on stderr),
2 (no window). Arguments are `key=value`; values are read as JSON when
they parse (`h=1.6` is a number, `"x=a / 2"` a string). `lock`, `unlock`,
`open`, `screenshot` take their first word positionally. From Python:
load `ui.py` by path and call `send("set", app="mycad", h="1.6")`.

| Command | Needs the lock | Arguments | Does / answers |
|---|---|---|---|
| `describe` | no | | the app, protocol 1, each command's help |
| `status` | no | | `file`, `modified`, `changed_on_disk`, `locked`, `busy` ({}), `editing` (sketch or null), `panel` (open command panel or null), `can_undo`, `can_redo`, `message`, `report` |
| `screenshot` | no | `path` | `window.grab()` to a PNG (for Claude's own eyes) |
| `lock` | no | `message` | Claude in control: banner with the message |
| `unlock` | yes | `message` | control back; the message in the status bar |
| `open` | yes | `path` | open a part (refused with unsaved edits; a bad file: error, nothing changes) |
| `reload` | yes | | the file again, one undo step, the camera kept |
| `set` | yes | `NAME=FORMULA ...` | parameters, one undo step; answer: the report. Unknown name or a formula that does not evaluate: error, nothing changed |
| `save` | yes | | write the part (and `name.step` beside it if the user's STEP on Save is on). Refused if the file changed on disk since read |
| `export_step` | yes | `path` | the bodies to STEP; answer `{path, note}` (note: failed features left out) |
| `view` | yes | `name` | `iso`, `top`, `bottom`, `front`, `back`, `left`, `right` (as the view buttons) |
| `fit` | yes | | frame the model, direction kept |
| `select` | yes | `kind`, `name` | a browser row as a click: `body`, `feature`, `group`, `transform`, `datum`; a feature lights what it made |
| `undo` / `redo` | yes | | answer: the report |

- **Two modes.** Normal: the user works; Claude may read, every change is
  refused ("not in control"). Locked (`lock`, or start with `main.py PART
  --locked`): a yellow banner "Claude is in control: <message>" with
  "Take control back"; the ribbon, the browser and the panels are off,
  all editing shortcuts dead; the user can only turn, pan, zoom and use
  the view buttons.
- **`lock` is refused** while the user is in a sketch, has a command
  panel open, drags, has a dialog or a field open, or has unsaved edits
  (the answer says which: ask them).
- **Locked, the window does not reload from disk by itself:** write the
  file, then `reload`. A file that does not read is an error answer and
  the window keeps what it had.
- **"not in control"** after a lock: the user took control back. Stop,
  ask; never re-lock on your own.
- Paths: give absolute ones (the window resolves relative paths against
  its own working folder).
- Commands never open dialogs; every problem is the error text.
- A parameter named `app` or `timeout` cannot go through `ui.py set`
  (they are the client's own arguments).

## 13. The window from the user's side

What the user can do by hand (to talk about it, and to know what they see):
- **Ribbon** File (New, Open, Recent, Save, Save As, Import STEP, Export,
  STEP on Save), 3D Model (Start 2D Sketch, Extrude, Revolve, Work Plane,
  Move, Rotate, Mirror, Measure, view switches), Sketch (Line, Point,
  Circle, Arc, Polyline, Spline, Curve, Construction, Copy / Cut / Paste,
  Look At, Finish Sketch).
- **Browser** (left): the origin, the features (a sketch under the
  feature using it), the bodies (groups, colors, eyes), the placement.
  Selecting a feature lights what it made (blue; a cut's removed volume
  red). Right click: rename, color, group, delete, edit.
  **Parameters** tab: the table of formulas.
- **3D**: middle drag rotates, Ctrl + middle (or Space + left) pans, the
  wheel or a right drag zooms, a right click opens a menu (New Sketch on
  a face), left click selects. View buttons (Iso, Top, Front...) top
  right.
- **Sketch mode**: snapping to points and curves, alignment hints, drags
  (a point given by a formula does not drag: Deparametrize frees it).
- The window reloads the file when it changes on disk (one undo step),
  unless the user has unsaved edits (it only warns then) or it is locked.

## 14. STEP out

`geometry/step_io.py export_part`: each body a compound with its name and
color; names written into `MANIFOLD_SOLID_BREP('...')` too; characters
beyond ASCII escaped as ISO 10303-21 wants (`\X\F6`, `\X2\041F\X0\`), so
`Volumenkörper16` reads back intact; the file read back and compared
before it replaces the old one. Every export differs in bytes (a time
stamp): customcst's run fingerprint hashes the bytes, so export only when
the geometry changed. customcst's `read_step` is the reader that counts:

    cd ../customcst && ../.venv/Scripts/python.exe -X utf8 -c "import sys; sys.path.insert(0, '.'); from geometry.step_io import read_step; print([b.name for b in read_step(r'PATH').bodies])"

## 15. How to work: the method that worked

The runs of 2026-10-30 (a cube with a rod and a triangle plate; a PCB with
the SMA connector, `tests/pcb_sma.part.toml`) settled this:

1. **Understand the inputs first, numerically.** For an imported STEP,
   list its bodies with bounds and volumes, then slice them (section 16)
   to find axes, pins, legs, gaps. Write down the frame (for the SMA: the
   coax axis along y at x 0, z −0.5; the pin 0.9 mm where it lies on the
   board; legs ±(2.1..3.0) after the turn).
2. **Plan the parameters and the steps.** Every size that matters a
   parameter with a comment; positions as formulas of them.
3. **Write a builder script** (in the scratchpad) that writes the whole
   part file for step N (0: parameters only, then one meaningful step
   each), atomically (temp file + `os.replace`). Keep it: the file's
   comments do not survive a Save, the script does.
4. **Dry run headless**: build the last step, `--report`, compare every
   volume and bound with your own figures, measure contacts. Fix until
   right. Then write step 0.
5. **Show it**: open the window locked (`main.py PART --locked`, in the
   background; wait until `status` answers with the file) or `lock` the
   user's window. Per step: write N, `reload` (its answer is the status
   with the report: check the new numbers), `view` + `fit`, `select` the
   new feature. One step per meaningful thing (the board, the trace, the
   connector, the solder), not 30 tiny ones.
6. **Finish**: `save`, read the STEP back with customcst's reader if it
   goes there, `unlock "what to look at"`. Report what is where, what
   touches what (with the measured overlaps), and what looks off.
7. **Later edits** to a file the window has saved: edit the saved file by
   its tables (split on `\n[[`, drop or change whole tables; a feature's
   `[[feature.*]]` sub-tables follow it), or change the builder script and
   regenerate. Parameter changes: `set`.

**Placing a part by turning it** (the SMA, worked): the turn of a Move
goes around an origin axis through the origin, before the offset.
1. Work out where the part's own axis lands: turning around +Y by +90°
   maps (x, z) → (z, −x), so the coax axis at (x 0, z −0.5) lands at
   (−0.5, 0).
2. Choose the sign by what must end where (+90 put one leg pair into the
   bottom copper, the other beside the pin; −90 the other way round).
3. Compensate in the offset: `offset = ["0.5", "-bw/2 + launch",
   "hd + t + pin_d/2"]`.
4. Verify by slicing the placed bodies.

**Contacts** (customcst merges touching conductors, but a round pin on a
flat trace touches along a line only): where a conductor does not touch,
add a block (a rectangle extruded from the copper's face) that overlaps
it; measure every overlap as a `Common` volume, never guess.

## 16. Measuring: recipes

Rebuild a part and look at its shapes (run with the venv's Python, the
folder `myinventor/` on `sys.path`):

    from model.part_file import load
    from model.rebuild import rebuild
    from geometry import kernel
    part = load(path)
    result = rebuild(part, folder=os.path.dirname(path))      # placement=False: unplaced
    shape = result.shapes["Trace"]
    kernel.volume(shape), kernel.bounds(shape), kernel.solid_count(shape)

A slice or an overlap (OCP):

    from OCP.BRepAlgoAPI import BRepAlgoAPI_Common
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.gp import gp_Pnt
    box = BRepPrimAPI_MakeBox(gp_Pnt(-50, -24, -50), gp_Pnt(50, -22, 50)).Shape()
    piece = BRepAlgoAPI_Common(shape, box).Shape()        # then walk its solids (TopExp_Explorer, TopAbs_SOLID)
    overlap = kernel.volume(BRepAlgoAPI_Common(a, b).Shape())

A STEP file's bodies: `geometry.step_io.read_bodies(path)` →
`[(name, kind, shape, ...)]` (unpack with `*_`).

## 17. Pitfalls already met

- **Save drops comments.** Do not find blocks in a saved file by your
  comments; parse the tables, or regenerate from the builder script.
- **Piping `ui.py`'s JSON into Python on Windows:** read it with
  `python -X utf8`, or `Volumenkörper16` shows as `VolumenkÃ¶rper16` (the
  data is right, the pipe's decoding is not).
- **Heredocs with backslashes or quotes break** in the Bash tool: write
  scripts with the Write tool into the scratchpad and run them.
- **A sketch on `Ext:start` mirrors y** (a ground plane under the board).
- **A transform's axis goes through the origin**, not through the part:
  compensate the offset (section 15).
- **Connector models from vendors are odd:** legs not where expected,
  small extra bodies overlapping the pin or reaching into the board.
  Slice and report them; do not fix someone else's part silently.
- **Polygon areas in the report** are ~1e-3 relative off the exact; volumes
  are exact.
- **Two exports of the same geometry differ in bytes**: export only when
  the geometry changed (customcst's fingerprint).

## 18. Not there yet

No Combine (a body with a body after the fact), no fillets, chamfers or
patterns, no constraint solver (formulas instead), no sketch on curved
faces, no poly-arc or Bézier, no copy / paste of arcs and generators, no
"jump in time" (features never see the placement). The window cannot add
features by command: Claude adds them by writing the file and `reload`.
