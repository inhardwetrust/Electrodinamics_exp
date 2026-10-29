# myCad — design

## Goal

A simple parametric modeler: parameters with formulas, sketches,
extrude / revolve, booleans, a feature list that rebuilds the model.
- The output is STEP with named bodies (and colors).
- It becomes a part of customcst later; then the bodies get types,
  materials and ports there, and parameter sweeps drive the simulation.
- Compatibility with Inventor's .ipt is not a goal. .ipt is closed; its
  feature tree is a snapshot of Inventor's internal state (references to
  its own face and edge ids), so nobody outside Autodesk reproduces it.

## Why general CAD is hard (and how we avoid it)

Besides the UI, parametric CAD has three algorithmic problems:
1. **Sketch constraint solving.** A sketch with constraints is a
   nonlinear system with several solutions. A dimension change can flip
   the sketch into another solution (an arc turns inside out), and the
   solver has to report over- and under-constrained sketches.
2. **Persistent naming.** "The fillet on edge 47": after a rebuild, which
   new edge is edge 47? There is no exact answer; every CAD uses
   heuristics. FreeCAD suffered from this for years (fixed in part in 1.0).
3. **Robust geometric operations.** Near-tangent booleans, fillets that
   collide, thin walls. OpenCASCADE is weaker here than Parasolid
   (SolidWorks, NX) or ShapeManager (Inventor).

Principles that sidestep them:
- **References by meaning, not by topology.** A feature refers to named
  objects and their parameters ("a via fence along trace `feed`, 0.5 mm
  off"; "the port on the end of line `feed`"), never to "face 12". Where
  a face must be chosen, it is chosen by a rule (the +z face of body X,
  the face at x = max), evaluated at every rebuild.
- **Rebuild from scratch.** A parameter change replays the whole feature
  list; there is no incremental update to keep consistent. The kernel is a
  generator of solids, not the store of the history.
- **Expressions before constraints.** Start with a parameter list with
  formulas, evaluated in order, like CST: `w = 0.3`, `g = 0.8*w`. One
  answer, no flips, no solver. Sketches start as parametric primitives
  (rectangle, circle, polyline, slot, arc by points). A 2D constraint
  solver comes later, if ever (references: FreeCAD's PlaneGCS,
  SolveSpace's solver).
- **The model file is text** (TOML or Python, like build123d): the
  parameters plus the feature list. It is readable in a git diff,
  editable by hand, and can be generated for a sweep.
- **Keep the operations few and well tested.** Extrude, revolve, cut,
  fuse, common, pattern, simple fillets and chamfers. Each one gets a
  check: volume, bounds, the faces, a STEP round trip.

## Document (decided)

- **Recipe and result are separate.** The document (`model/part.py`) holds
  only parameters, the origin's visibility and the features; the rebuild
  (`model/rebuild.py`) makes values, OCP shapes and bodies from scratch on
  every change. Nothing computed is saved.
- **Features form a flat list in history order**, each referring by name
  to datums and to features above it. The browser's nesting (a sketch
  under the extrusion that used it) is derived from the references, not
  stored. "Build up to feature N" (Inventor's End of Part) comes free.
- **A name is the id.** Renaming rewrites every formula or reference.
  Parameters have their own namespace; datums and features share one.
- **References by meaning**: a datum or feature name, a sketch entity
  (`Sketch1/Line3`), or a face the producing feature names itself
  (`{feature = "Extrusion1", face = "end"}`; OCP's MakePrism gives
  FirstShape / LastShape / Generated(edge)).
- **Every numeric field is a formula** of the parameters (mm, deg; trig in
  degrees). Parameters evaluate in dependency order; errors name the
  parameter. Formulas are walked as an `ast`, never `eval`ed.
- **A feature type** is a `Feature` subclass registered with
  `@feature_type`: `TYPE`, `FIELDS` (validation, later the property panel),
  `build(ctx)`. A failing feature is marked; the ones that use it fail too,
  the others still build.
- **File**: TOML, `name.part.toml` (format and version header,
  `[origin]`, `[[parameter]]`, `[[feature]]`); strict reading, atomic
  writing checked by reading back. Undo: snapshots of the plain data.
- Bodies get explicit names: customcst assigns materials by body name.
  While the history rebuilds, bodies live by name (`ctx.shapes`): a
  feature makes one (New solid) or changes a named one above it (Join,
  Cut, Intersect); a failed feature leaves it as it was. Editing a feature
  rolls the view back to the bodies just before it (as Inventor does).
- **Body properties** live with the body's name, not in the features that
  make it (`Part.bodies`, `[[body]]` in the file): the color now, the
  customcst material later. A Cut or Join keeps them.
- **Sketches on faces** (done for extrusions): `plane = "Extrusion1:end"`
  (or `start`, `side.L3`). The plane is computed from the feature's own
  values (its sketch frame, distance, profile line), not looked up among
  the OCP faces, so it follows parameter changes; the normal points out of
  the material (into the pocket for a cut). A picked body face is matched
  to the first feature whose named face lies on its plane
  (`MainWindow.face_reference`); curved faces and faces no feature names
  are refused.
- **Profiles** (`model/profiles.py`): the closed regions of a sketch are
  the bounded faces of the planar graph of its lines (points at one place
  join), plus circles; a loop inside another is its hole. A feature names a
  profile by the curves of its outer loop (`["L1", "L2", "L3", "L4"]`), so
  moving points keeps it and breaking the loop fails the feature with that
  name. Not yet: curves crossing without a shared point do not split
  regions.

## Two ways of drawing (decided)

The same geometry can be drawn two ways, and a part may mix them. Both are
the same part file, so the user and an AI (`.claude/skills/mycad`) work on
one document.

**1. Manual.** Primitives as a person draws them: points with plain numbers,
lines, circles, wide polylines; formulas only for the sizes that matter.
Everything drags with the mouse. The interpolating spline is done
(`[[feature.spline]]`: a cubic C2 curve through its points, chord-length
parameter, natural ends or periodic when closed; one exact B-spline in
the kernel and STEP). Missing primitives, to add: an arc, a poly-arc (a chain
of lines and tangent arcs), Bézier curves.

**2. Parametric (generators).** Control parameters → generators → geometry:
the shape is computed from a few numbers (a parabola from its focal length
and diameter, a spiral, a meander), and changes when they change.
- **A generator** is a sketch entity `[[feature.curve]]` with a `kind` from
  a registry in the program. The kind declares its arguments the way a
  Feature declares its `FIELDS` (name, type, meaning); the file stores the
  kind and one formula per argument:

      [[feature.curve]]
      name = "Reflector"
      kind = "parabola"
      focal = "f"
      from = "0"            # the radius range it covers
      to = "D/2"

  The properties panel builds the argument fields from the declaration
  (the same fields an AI fills); the rebuild evaluates them and makes the
  curve. A new kind is one class in the registry, nothing else changes.
- **Exact where possible.** Kinds OCC has exactly (`gp_Parab`; later
  ellipse, hyperbola) are built exactly. The general kind `function`
  (`x = "..."`, `y = "..."` of the variable `t`, a range of `t`) becomes
  one cubic B-spline through its points at evenly spaced t, refined until
  it is within 0.1 µm of the curve (`curves.FIT_TOLERANCE`): in STEP one
  smooth curve, as Inventor's splines are. (OCP 8 binds no point arrays:
  the kernel writes the B-spline as BREP text and reads it back.)
- **No code in files.** Generators live in the program; arguments and
  `function`'s `x(t)`, `y(t)` go through the same `ast` evaluator as every
  formula (`t` is one more name). A file from elsewhere cannot run anything.
- **Joining.** A curve's ends are derived points (`Reflector.start`,
  `Reflector.end`) that lines may use, so curves and lines close loops
  (profiles) like lines do today.
- **The bridge to manual: Deparametrize** (done for formulas): the
  selection's formulas become their values, the geometry stays and drags.
  A generator has its own browser row under its sketch (tick: shown;
  select: its arguments). Convert to Spline replaces it by a spline
  through points on it with plain numbers, as few as keep the spline
  within a tolerance of it (1 µm by default); Copy as Spline makes the
  same spline and hides the curve (`visible = false`: not drawn, not in
  profiles). Either way lines on its ends and extrusion profiles move to
  the spline. Then draw over it by hand.
- **Then features on top**: a curve thickened to one side or both (a closed
  profile, like a polyline's outline), and Revolve around an axis: a
  parabolic reflector is a parabola, thickened, revolved.

## Where to look

- **build123d / CadQuery**: Python APIs over the same OCP. Study how they
  name operations and select faces by rule.
- **SolveSpace**: small, clear, with a good constraint solver and a sane
  UI.
- **FreeCAD**: PlaneGCS, the topological naming work in 1.0, Part Design.
  Study the ideas, not the UX.
- **Plasticity**: a modern modeler UX (commercial, on Parasolid).

## Roadmap

Done: the window and 3D view, parameters, sketches (points, lines,
circles, wide polylines with bends, construction geometry, snapping,
copy / paste), Extrude with join / cut / intersect, sketches on faces,
failed-feature marking, body colors, STEP export (and on Save), the
headless report and STEP (`cli.py`), the AI skill, reload on a change on
disk, Deparametrize, generators (`[[feature.curve]]`: `parabola`,
`function`; argument fields; part of profiles through their end points),
the interpolating spline (Sketch > Spline), true B-splines in the kernel,
generators in the browser (show / hide, Convert / Copy as Spline).

Next, in this order:
1. Thickness for curves (one side or both): a closed profile.
2. Revolve.
3. Manual primitives: arc, poly-arc, Bézier; copy / paste of generators.

Later: patterns, fillets; RF primitives (a board with a layer stack, a
line or CPW, a via fence, an SMA template); measure (picking and the A-B
distance, done in customcst); integration into customcst (a parameter
sweep that rebuilds the STEP and re-runs the simulation).
