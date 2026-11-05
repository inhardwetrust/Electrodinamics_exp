"""Writes a hierarchical parametric PCB: an inset-fed patch with an SMA end launch.

The example of the skill's "hierarchical mode" (.claude/skills/mycad/SKILL.md):
everything follows a few input sizes (the patch, its inset, the feed, the
board, the notch, the pads); the connector's own sizes are measured facts.
Retune the patch or the board: the line, the pads and the connector follow.
It is old_patch_calc/oldpatch.step (a real board) remodeled on 2026-11-05.
Step N: 0 = parameters only, 1 the board, 2 the notch, 3 the patch and the
feed, 4 the connector, 5 the pads (all).
Usage: python build_inset_patch_sma.py [N] [PATH]   (the part beside this script by default)
"""
import os
import sys

STEP = int(sys.argv[1]) if len(sys.argv) > 1 else 99
PATH = sys.argv[2] if len(sys.argv) > 2 else os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                           "inset_patch_sma.part.toml")
FILES = os.path.basename(PATH).removesuffix(".part.toml") + ".files"      # the import's folder beside the part

out = ['format = "mycad-part"', "version = 1", ""]


def q(s):
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


def table(head, **kv):
    out.append(f"[[{head}]]")
    for k, v in kv.items():
        if isinstance(v, bool):
            out.append(f"{k} = {'true' if v else 'false'}")
        elif isinstance(v, list):
            out.append(f"{k} = " + "[" + ", ".join(
                ("[" + ", ".join(q(x) for x in e) + "]") if isinstance(e, list) else q(e) for e in v) + "]")
        else:
            out.append(f"{k} = {q(v)}")
    out.append("")


TUNE = "#ffcc80"        # what is tuned to the frequency: the patch
INPUT = "#fff59d"       # the design's other inputs
FACT = "#b3e5fc"        # measured facts of the connector
# intermediate parameters (fr4) stay white


def param(name, comment, formula=None, color=None, **fields):
    extra = {"color": color} if color else {}
    if formula is not None:
        table("parameter", name=name, formula=formula, comment=comment, **extra)
        return
    table("parameter", name=name, comment=comment, **extra)
    out.pop()
    out.append("[parameter.fields]")
    for k, v in fields.items():
        out.append(f"{k} = {q(v)}")
    out.append("")


# ---- parameters: the patch first, then what follows it ----
param("patch", "the patch: x its width W, y its resonant length L (along the feed); centered on the board",
      color=TUNE, x="15.594", y="12.213")
param("inset", "the inset feed: depth into the patch from its feed edge, gap on each side of the line",
      color=INPUT, depth="4.311", gap="0.73")
param("feed_w", "the feed line's width (2 mm: ~57 Ohm on 1.34 mm FR4)", "2", INPUT)
param("board", "the board: x, y its size (centered on the origin), h its total thickness, cu the copper on each side",
      color=INPUT, x="50", y="50", h="1.41", cu="0.035")
param("fr4", "FR4 between the copper layers", "board.h - 2 * board.cu")
param("notch", "the notch for the connector in the board's -y edge: depth from the edge, width (centered)",
      color=INPUT, depth="3.866", w="8.1")
param("sma", "the SMA end launch as measured (realconnector.stp): axis its coax axis' x in the file, "
             "reach its legs over the board, lift its pin axis above its legs' underside, gap between its "
             "upper and lower legs (the board it is made for), leg_in / leg_out its upper legs' x from the axis",
      color=FACT, axis="0.5", reach="4", lift="0.46", gap="1.56", leg_in="2.11", leg_out="3")
param("pad", "the pads under the connector's upper legs: dy from the notch's bottom, l along y, "
             "w along x from the leg's inner edge outward",
      color=INPUT, dy="1", l="2", w="3.05")

# ---- 1: the board ----
if STEP >= 1:
    table("feature", name="BoardSketch", type="sketch", plane="XY Plane")
    for n, x, y in (("P1", "-board.x/2", "-board.y/2"), ("P2", "board.x/2", "-board.y/2"),
                    ("P3", "board.x/2", "board.y/2"), ("P4", "-board.x/2", "board.y/2")):
        table("feature.point", name=n, x=x, y=y)
    for n, a, b in (("L1", "P1", "P2"), ("L2", "P2", "P3"), ("L3", "P3", "P4"), ("L4", "P4", "P1")):
        table("feature.line", name=n, start=a, end=b)
    table("feature", name="SubstrateExt", type="extrude", sketch="BoardSketch", distance="fr4",
          operation="new", body="Substrate", profiles=[["L1", "L2", "L3", "L4"]])
    table("feature", name="GroundExt", type="extrude", sketch="BoardSketch", distance="-board.cu",
          operation="new", body="Ground", profiles=[["L1", "L2", "L3", "L4"]])

# ---- 2: the notch for the connector, through the board ----
if STEP >= 2:
    table("feature", name="NotchSketch", type="sketch", plane="SubstrateExt:end")
    ny = "-board.y/2 + notch.depth"
    for n, x, y in (("P1", "-notch.w/2", "-board.y/2"), ("P2", "notch.w/2", "-board.y/2"),
                    ("P3", "notch.w/2", ny), ("P4", "-notch.w/2", ny)):
        table("feature.point", name=n, x=x, y=y)
    for n, a, b in (("L1", "P1", "P2"), ("L2", "P2", "P3"), ("L3", "P3", "P4"), ("L4", "P4", "P1")):
        table("feature.line", name=n, start=a, end=b)
    table("feature", name="NotchCut", type="extrude", sketch="NotchSketch", extent="plane", to="GroundExt:end",
          operation="cut", body=["Substrate", "Ground"], profiles=[["L1", "L2", "L3", "L4"]])

# ---- 3: the top copper: the patch with its inset, the feed line; 5: the pads ----
if STEP >= 3:
    table("feature", name="TopCopper", type="sketch", plane="SubstrateExt:end")
    s = "feed_w/2 + inset.gap"                  # the inset slot's half width
    yb = "-patch.y/2"                           # the feed edge
    yi = "-patch.y/2 + inset.depth"             # the inset's bottom
    for n, x, y in (("K1", "-patch.x/2", yb), ("K2", f"-({s})", yb), ("K3", f"-({s})", yi), ("K4", s, yi),
                    ("K5", s, yb), ("K6", "patch.x/2", yb), ("K7", "patch.x/2", "patch.y/2"),
                    ("K8", "-patch.x/2", "patch.y/2")):
        table("feature.point", name=n, x=x, y=y)
    for i in range(8):
        table("feature.line", name=f"K{i + 11}", start=f"K{i + 1}", end=f"K{(i + 1) % 8 + 1}")
    table("feature.point", name="F1", x="0", y="-board.y/2 + notch.depth")
    table("feature.point", name="F2", x="0", y=yi)
    table("feature.polyline", name="Feed", points=["F1", "F2"], widths=["feed_w", "feed_w"], width="feed_w",
          start="flat", end="flat")
    if STEP >= 5:
        py = "-board.y/2 + notch.depth + pad.dy + pad.l/2"
        table("feature.point", name="A1", x="sma.leg_in", y=py)          # each pad's reference: the leg's inner edge
        table("feature.point", name="A2", x="pad.w", y="0")
        table("feature.point", name="B1", x="-sma.leg_in", y=py)
        table("feature.point", name="B2", x="-pad.w", y="0")
        table("feature.polyline", name="PadR", points=["A1", "A2"], widths=["pad.l", "pad.l"], width="pad.l",
              start="flat", end="flat", reference="A1", relative=True)
        table("feature.polyline", name="PadL", points=["B1", "B2"], widths=["pad.l", "pad.l"], width="pad.l",
              start="flat", end="flat", reference="B1", relative=True)
    table("feature", name="PatchExt", type="extrude", sketch="TopCopper", distance="board.cu",
          operation="new", body="Patch", profiles=[sorted(f"K{i + 11}" for i in range(8))])
    table("feature", name="FeedExt", type="extrude", sketch="TopCopper", distance="board.cu",
          operation="new", body="Trace", profiles=[["Feed"]])

# ---- 4: the connector: imported, its lower legs lengthened to this board, placed by meaning ----
if STEP >= 4:
    table("feature", name="SMA", type="import", file=f"{FILES}/realconnector.stp",
          sha256="b3ca3a28cd2ffd839aac35b41e640c4d4876db66c2ab465d82391ee13584e0fd",
          bodies=["SMA:connector_outer", "SMA:connector_outer_2", "SMA:connector_dielectric",
                  "SMA:connector_wire", "Port"])
    # the lower legs' inner face (as the file has it): their footprints, 0.9 x 4 each
    table("feature", name="LegSketch", type="sketch", plane="SMA:face.2.0")
    for n, x, y in (("P1", "0.45", "2"), ("P2", "-0.45", "2"), ("P3", "-0.45", "-2"), ("P4", "0.45", "-2"),
                    ("P5", "-4.65", "2"), ("P6", "-5.55", "2"), ("P7", "-5.55", "-2"), ("P8", "-4.65", "-2")):
        table("feature.point", name=n, x=x, y=y)
    for n, a, b in (("L1", "P1", "P2"), ("L2", "P2", "P3"), ("L3", "P4", "P3"), ("L4", "P4", "P1"),
                    ("L5", "P5", "P6"), ("L6", "P6", "P7"), ("L7", "P8", "P7"), ("L8", "P8", "P5")):
        table("feature.line", name=n, start=a, end=b)
    table("feature", name="LegExt", type="extrude", sketch="LegSketch", distance="sma.gap - board.h",
          operation="join", body="SMA:connector_outer_2",
          profiles=[["L1", "L2", "L3", "L4"], ["L5", "L6", "L7", "L8"]])

# ---- 5: the pads' copper ----
if STEP >= 5:
    table("feature", name="PadExt", type="extrude", sketch="TopCopper", distance="board.cu",
          operation="new", body="Conn_Pads", profiles=[["PadL"], ["PadR"]])

# ---- bodies, the group, the placement ----
for name, color in (("Substrate", "#2e7d32"), ("Ground", "#d4a017"), ("Patch", "#d4a017"),
                    ("Trace", "#d4a017"), ("Conn_Pads", "#bd9a1d")):
    table("body", name=name, color=color)
if STEP >= 4:
    table("group", name="SMA", bodies=["SMA:connector_outer", "SMA:connector_outer_2",
                                       "SMA:connector_dielectric", "SMA:connector_wire", "Port"])
    # turned +90 around Y: the coax axis (x 0, z -sma.axis in the file) lands at x -sma.axis;
    # its upper legs stand on the top copper, its flange on the notch's bottom
    table("transform", name="PlaceSMA", type="move",
          offset=["sma.axis", "-board.y/2 + notch.depth + sma.reach", "fr4 + board.cu + sma.lift"],
          axis="Y Axis", angle="90", targets=["group:SMA"])

tmp = PATH + ".tmp"
with open(tmp, "w", encoding="utf-8", newline="\n") as f:
    f.write("\n".join(out))
os.replace(tmp, PATH)
print("step", STEP, "->", PATH)
