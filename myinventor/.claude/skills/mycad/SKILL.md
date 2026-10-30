---
name: mycad
description: Draw a part (an antenna, a PCB structure, any 3D body built from sketches and extrusions) in myCad, our parametric modeler, by writing its part file (.part.toml), then check it numerically and export STEP for customcst. Use when asked to draw, model, build, change or export geometry in myCad, or to make a STEP of a structure for a simulation.
---

# Drawing in myCad

myCad (this folder) is a parametric modeler: a part is a *recipe* —
parameters, sketches, extrusions — rebuilt from scratch every time. The
recipe is the file `name.part.toml`. It is the one document you and the
user share: whatever you write there opens in the editor exactly as if the
user had drawn it, and they can change any point, width or parameter
afterwards. So never produce geometry any other way (no STEP written by
hand, no OCC scripts): write the part file, check it, export.

## The loop

1. Write (or edit) the part file. Start from `examples/patch_antenna.part.toml`
   (substrate, patch, feed line with an arc bend, colors): it shows every
   piece you usually need.
2. Rebuild it without the window and read the report:

       ../.venv/Scripts/python.exe main.py path/name.part.toml --report

   It lists every parameter with its value, every feature as `ok` or
   `FAILED: why`, each sketch's closed profiles (with the key an extrusion
   uses and their area) and each body (exact volume, size, bounds in mm).
   The last line is `ok` or `NOT OK`; the exit code is 0 / 1 (something
   failed) / 2 (the file did not read: the message names the place).
   Add `--json` for the same as JSON.
3. Check the numbers against what you meant (sizes, positions, volumes:
   compute them by hand). Never call it done by looking only.
4. Export for customcst:

       ../.venv/Scripts/python.exe main.py path/name.part.toml --export-step path/name.step

   The STEP carries the bodies with their names and colors (customcst
   reads them as separate bodies: name them as the simulation needs,
   e.g. Substrate, Copper, Ground).
5. Tell the user the file path: they open it in myCad
   (`../.venv/Scripts/python.exe main.py path/name.part.toml`) to look and
   adjust. If it is already open there, the editor reloads it by itself
   within a second after you write it (one undo step: Ctrl+Z goes back) —
   unless the user has unsaved edits in it: then it only warns, and their
   Save would overwrite your change. So before editing a file the user works
   on, ask them to save; after they adjusted it, re-read the file before you
   edit it again (their changes are in it).

Do not write into the user's existing part files without being asked; a new
design goes into a new file.

## Make it editable: parameters

Put every meaningful size into a `[[parameter]]` (with a `comment`) and use
its name in the coordinates, widths, radii and distances: `x = "-patch_w/2"`,
`distance = "h"`, `radius = "2 * feed_w"`. Then the user changes one number
in the parameter table and the whole structure follows. Formulas: `+ - * / ^`,
parentheses, `sqrt abs min max sin cos tan atan2 ...` (degrees), `pi`. A
parameter may use another one. Numbers alone only for true constants.

In the editor a point set by a formula cannot be dragged with the mouse
(its formula decides where it is; dragging says so); a point given as a
plain number can. So: sizes that matter (resonant lengths, line widths,
gaps) as parameters; where the user wants to place something freely by
hand, a plain number is fine. Say which ones you left free. The user can
also free anything later: the Deparametrize button (properties panel, for
the selection or the whole sketch) replaces its formulas by their values.
So prefer formulas when in doubt: freeing is one click, the reverse is not.
Before editing a file again, re-read it: deparametrized values are numbers
now and must not be turned back into formulas without being asked.

## Two ways of drawing

The user and you share the file, so pick the way that fits the shape
(DESIGN.md "Two ways of drawing"); one part may mix both:

1. **Manual**: primitives as a person would draw them (points, lines,
   circles, wide polylines, splines). Plain numbers where the user may want to move
   things by hand, formulas for the sizes that matter. Use it for layouts:
   patches, feeds, slots, boards, anything made of straight pieces, arcs
   of polyline bends and circles.
2. **Parametric (generators)**: a few control parameters → a computed
   shape (a parabola from f and D, a horn's profile, any x(t), y(t)),
   rebuilt when they change. In the file a generator is `[[feature.curve]]`
   with a `kind` and one formula per argument (see "Curves" below); its
   ends are points (`Name.start`, `Name.end`) that lines use to close a
   profile. Prefer a generator to many points with formulas: it is exact
   (a parabola is OCC's parabola, a function a B-spline within 0.1 µm)
   and the user edits its arguments in the properties. Never draw a curve
   as a polyline of points and call it exact.

Either way the user can free formulas later (Deparametrize, below) and keep
working by hand.

## The file

All lengths are mm. Names are unique: parameters and features in the part,
points / lines / circles / polylines within their sketch. TOML arrays of
tables, in order:

    format = "mycad-part"
    version = 1
    [[parameter]]   name, formula, comment (optional)
    [[feature]]     in history order; a feature may use only features above it
    [[body]]        name, color = "#rrggbb" (optional, per body)

Reading is strict: an unknown key, a wrong type or a missing reference is an
error naming the place (exit 2). The full format with every key is in the
module headers: `model/part_file.py`, `model/part.py`, `model/sketch.py`,
`model/extrude.py`, `model/traces.py` (polyline outlines and bends). Read
them when you need more than the example shows.

### Sketch (`type = "sketch"`)

`plane` = an origin plane or a planar face of an extrusion above it:

| plane | sketch x | sketch y | an extrusion goes (+distance) |
|---|---|---|---|
| `"XY Plane"` | +X | +Y | +Z |
| `"XZ Plane"` | +X | −Z | +Y |
| `"YZ Plane"` | +Y | +Z | +X |
| `"Ext:end"` (the cap at the distance) | as Ext's sketch | as Ext's sketch | out of the body |
| `"Ext:start"` (the cap on Ext's plane) | as Ext's sketch | **mirrored** | out of the body |
| `"Ext:side.L3"` (the wall line L3 swept) | along L3 from its start point | out of the wall × x | out of the body |

A sketch on `Ext:end` keeps Ext's origin and axes, moved to the cap: draw a
patch on a substrate's top with the substrate's own x, y. Check positions in
the report's bounds anyway.

Geometry (each is a `[[feature.<kind>]]` table under the sketch):
- `point`: `name`, `x`, `y` (formulas). Every line, circle and polyline
  refers to points by name. Name them meaningfully (P1.., or F1.. for a feed).
- `line`: `name`, `start`, `end`. A closed chain of lines sharing points is a
  profile; its key is the sorted line names (`["L1", "L2", "L3", "L4"]`).
  A rectangle is four lines through four points.
- `circle`: `name`, `center` (a point), `radius`. Its key: `["C1"]`.
- `polyline`: a wide line (a microstrip, a trace): `points` (its center
  line), `widths` (one formula per point; it tapers linearly between),
  `width` (for points added later), `start` / `end` = `"flat"` | `"round"`,
  optionally `bends` and `radii` (one per point, the ends' unused):
  - `"normal"`: a sharp (mitered) corner;
  - `"arc"`: the center line turns along an arc of radius R (`radii`, default
    the width), both edges concentric, constant width: a curved microstrip;
  - `"chamfer"`: the center line cut straight, `radii` = the setback from the
    point (default the width), constant width;
  - `"miter"`: only the outer corner cut (the classic microstrip miter),
    `radii` = the cutting circle's radius around the point (default w/2).
  Its outline is a profile on its own, key `["<its name>"]`. A bend's radius
  or setback must fit between its neighbours or the sketch fails, naming it.
- `curve`: a generator, `name`, `kind`, then its arguments (formulas;
  the optional ones may be left out). Kinds (the registry is
  `model/curves.py`; its `ARGS` say what each argument means):
  - `parabola`: `focal` (f > 0), `from`, `to` (its distance from the axis
    where it starts and ends, signed), `x0`, `y0` (the vertex, default 0):
    y = y0 + (x − x0)² / (4f), the axis along the sketch's y. Exact.
  - `function`: `x`, `y` (formulas of `t` and the parameters), `from`,
    `to` (the range of t, default 0..1). Inside x and y, `t` is the
    curve's own variable (it hides a parameter named t there). Trig in
    degrees: `sin(360 * t)`. One B-spline within 0.1 µm.
  Its ends are `Name.start` and `Name.end`: use them as line ends to close
  a profile; a profile's key then names the curve too (`["L1", "Parabola1"]`,
  from the report). A curve cannot be dragged; Deparametrize does not
  touch it. `visible = false` hides it (not drawn, not in profiles; its
  ends stay). The user turns one into a spline by hand (browser row or
  properties: Convert to Spline, or Copy as Spline, which hides the curve;
  50 µm by default): lines on its ends and extrusion profiles move to the
  spline. The report lists each curve with its ends' coordinates.
- `spline`: an interpolating spline (cubic, smooth, through every point):
  `name`, `points` (two or more, each once; the first again at the end
  closes it, smooth all around, then three or more). Its key on its own
  when closed (`["Spline1"]`); open, it joins lines at its first and last
  points like a line. Its points drag; in the kernel and STEP it is one
  exact B-spline. Use it for a free smooth shape the user will shape
  by hand; for a shape with a formula use a generator.
- `offset`: the Offset modifier of a line, circle, spline or curve: `of`
  (its name), `left`, `right` (mm to each side along it from its start to
  its end; a circle runs counter-clockwise, its left is inside), `start`,
  `end` = `"flat"` | `"round"` (caps of an open one). The entity becomes a
  spine (in no profile itself); the band is a profile, key
  `["<name>.offset"]`; a closed one's inner loop `["<name>.offset.inner"]`
  (the band's hole, and a profile itself). A side of 0 is the entity
  exactly (a reflector's parabola stays exact); a side past the radius of a
  bend fails, naming it. Use it for a thick curve (a reflector's shell, a
  curved strip, a ring).
- `construction = true` on a line, circle, curve or spline: a helper, never a profile.

A loop inside another one is a hole of the outer profile (a rectangle with
a circle inside: the plate with a hole, and the disc, two profiles). Curves
crossing without a shared point do NOT split profiles: share points.

### Extrusion (`type = "extrude"`)

`sketch`, `profiles` (a list of keys, from the report), `distance` (a formula;
negative: the other way), `operation` = `"new"` (a new body named `body`) |
`"join"` | `"cut"` | `"intersect"` (changes the existing body `body`).
Several profiles in one extrusion become one body (touching ones fuse).
A cut that removes nothing or everything fails on purpose: check the report.

## Conventions for RF structures

- The board on the XY plane from z = 0 up by `h`; copper on its top as a
  sketch on `Substrate:end` extruded by `t`; a ground plane as a sketch on
  `Substrate:start` extruded by `t` (it goes down, out of the substrate;
  its y is mirrored, see the table).
- Separate bodies per material: `Substrate`, `Copper` (or `Top`, `Ground`,
  `Via`...), each with a color. customcst assigns materials by body.
- A port or feed reaching the board edge: end the line exactly on the edge
  (`x = "sub_w/2"`), so the report shows the copper's bound equal to the
  substrate's.
- Keep the structure centered on the origin unless asked otherwise.

## When it fails

Read the `FAILED:` reason; it names the feature and the curve. Typical:
a profile key that does not exist (`profile ... is not a closed region`: the
chain is open, or the key names the wrong lines: take it from the report),
a polyline bend that does not fit, a parameter with an error (every feature
using it fails too), a sketch on a face that is not there (`no face`).
A failed feature leaves its body out of the STEP; the export says which.
