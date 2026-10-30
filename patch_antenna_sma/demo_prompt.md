# Demo prompt: a two-layer PCB with the SMA connector

A test of the workflow (myCad driven through its control channel, the user
watching). Done once by hand on 2026-10-30 (`../myinventor/tests/pcb_sma.part.toml`);
this prompt has what that run had to find out, so it works the first time.

---

Draw in myCad, in its window, showing me each step (read
`../myinventor/.claude/skills/mycad/SKILL.md` first, and the
`../myinventor/REFERENCE.md` it points to): a two-layer PCB with the SMA end-launch connector from `parts/`.

**Where.** A new part `demo/pcb_sma.part.toml` (the folder `demo/` here;
the connector's STEP copied to `demo/pcb_sma.files/realconnector.stp`, the
import's `sha256` of it). Open it with `main.py demo/pcb_sma.part.toml
--locked`, then build it step by step: write the file, `reload`, show
(`view`, `fit`, `select` the new feature), check the report's numbers
against your own figures before the next step. At the end `save` and
`unlock` with what to look at. Everything as parameters.

**The board.** 50 x 50 mm, 1.6 mm in total: FR4 1.53 mm (`Substrate`, on
XY from z 0, centered on the origin) and 35 µm copper on each side.
- Bottom: copper over the whole board (`Ground`: a sketch on
  `SubstrateExt:start`, extruded by t: it goes down).
- Top: only the trace (`Trace`, a sketch on `SubstrateExt:end`).

**The trace.** A polyline from the board's center (0, 0) to the middle of
the edge y = -25:
- 4 mm wide;
- 2 mm before the tip of the connector's pin it tapers to the pin's
  diameter where it lies on the board (0.9 mm);
- then 0.9 mm wide under the pin to the board's edge.

**The connector** (`parts/realconnector.stp`, an import named `SMA`, its
five bodies `SMA:...` in a group `SMA`). In its file:
- the coax axis is along y, at x 0, z -0.5;
- the body runs to -y; the pin (0.9 mm where it leaves the body, 1.27 inside)
  and the four legs reach 4 mm from y -4 to the pin's tip at y 0;
- `Volumenkörper16` is the coax port cylinder at the outer end.

Place it with one Move of the group: the body outside the board, flush
with the edge y = -25 at its middle, the legs and the pin over the board,
the pin's bottom on the trace's top (z 1.565). Turn it 90° around its
own axis first, the way that puts one pair of legs into the bottom copper
and the other pair beside the pin at its level (as received, one pair is
above the pin and the other far out to one side). In myCad that is
`axis = "Y Axis"`, `angle = "90"` (around the origin's Y, which moves the
coax axis to x -0.5, z 0), then
`offset = ["0.5", "-bw/2 + launch", "hd + t + pin_d/2"]`. Check it:
slice the placed connector over the board
(`../myinventor/tools/probe.py demo/pcb_sma.part.toml slice y=-23 "SMA:*"`). The
legs must be at x ±(2.1..3.0): the lower pair at z -0.985..-0.005 (0.03 mm
into the ground copper), the other pair at the pin's level.

**Solder.** Where a conductor of the connector does not touch the copper,
add a block (a rectangle extruded) that touches the copper and reaches
into the connector part:
- the round pin touches the trace only along a line: a block 0.9 mm wide,
  y 0.3 mm in from both ends of the pin over the board, from the trace's
  top up to the pin's axis (`PinSolder`);
- a leg that ends up in the copper needs none.
Measure every contact and overlap with `../myinventor/tools/probe.py
demo/pcb_sma.part.toml contacts` (and `slice` where it matters); do not
guess them. (The connector's small `connector_outer` plate already
overlaps the trace under the pin: say so, keep `PinSolder` anyway.)

**Names and colors.** `Substrate` (green), `Ground`, `Trace` (copper
color), `PinSolder` (grey); the connector keeps its own.

**Done when:**
- the report says ok;
- the volumes match your own figures (the trace: (19·4 + 2·(4+0.9)/2 +
  4·0.9)·0.035 = 2.9575 mm³);
- the board spans z -0.035..1.565;
- the saved STEP read with customcst's `read_step` gives all the bodies
  with their names intact (`SMA:Volumenkörper16`).

Then tell me in a few lines what you placed where, what touches what
(with the overlaps measured), and anything in the connector model that
looks off.
