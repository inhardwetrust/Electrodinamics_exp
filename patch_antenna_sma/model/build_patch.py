"""Writes model/patch.part.toml: the inset-fed patch, 50 Ohm line, the SMA launch. Arg: step (0..5)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_launch import ROOT, SHA, SMA_BODIES, box_pts, extrude, lst, rect, sketch   # noqa: E402

PART = os.path.join(ROOT, "model", "patch.part.toml")

PARAMS = [
    ("bw", "50", "the board's side (x and y), centered on the origin"),
    ("edge", "-bw/2", "the board's edge with the connector (y)"),
    ("h", "1.6", "the board's total thickness"),
    ("t", "0.035", "copper (1 oz) on each side"),
    ("hd", "h - 2 * t", "FR4 between the copper layers"),
    ("w50", "2.93", "50 Ohm microstrip width (Hammerstad, eps_r 4.3, h 1.53, t 0.035; Z0 profile 49.9)"),
    ("pin_d", "0.9", "the connector's pin diameter where it lies on the board"),
    ("launch", "4", "how far the pin and the legs reach over the board from its edge"),
    ("pad_w", "1.1", "the trace's width under the pin (launch tuning)"),
    ("pad_len", "launch", "the length of the trace at pad_w from the edge"),
    ("taper", "0.4", "the taper from pad_w to w50"),
    ("gap", "0.5", "the gap from the trace under the pin to the top ground pads"),
    ("gnd_xo", "4.5", "the top ground pads' outer edge (x)"),
    ("gnd_len", "launch", "the top ground pads' length from the edge"),
    ("via_d", "0.5", "the vias' diameter (filled, PEC)"),
    ("via_x", "3.7", "the vias' x (both sides)"),
    ("via_y1", "solder_in + via_d/2", "the first via row from the edge: its edge on the solder's start (no tiny cells)"),
    ("via_dy", "1.6", "the via pitch along y"),
    ("solder_in", "0.3", "the pin's solder: set back from both ends of the pin over the board"),
    ("solder_h", "pin_d / 2", "the pin's solder: from the trace's top up to the pin's axis"),
    ("pl", "11.7", "the patch's resonant length (y): final mesh 30/8/0.025, S11 min at 5.756 GHz (sim/patch_final Run 2)"),
    ("pw", "16", "the patch's width (x): c / (2 f) sqrt(2 / (eps_r + 1))"),
    ("py", "0", "the patch's center (y)"),
    ("inset", "3.9", "the inset depth: cos^2(pi d / L) = 50 / R_edge, R_edge ~250 Ohm"),
    ("inset_gap", "0.7", "the gap on each side of the line in the inset"),
]


def params():
    return "".join(f'[[parameter]]\nname = "{n}"\nformula = "{f}"\ncomment = "{c}"\n\n' for n, f, c in PARAMS)


def build(step, path=PART):
    out = 'format = "mycad-part"\nversion = 1\n\n' + params()
    bodies = []
    if step >= 1:
        out += "# --- the board: FR4 from z 0 up by hd, the ground under it\n"
        s, ls = rect(box_pts("-bw/2", "-bw/2", "bw/2", "bw/2"))
        out += sketch("BoardSketch", "XY Plane") + s + extrude("SubstrateExt", "BoardSketch", [ls], "hd", "Substrate")
        out += sketch("GroundSketch", "SubstrateExt:start") + s + extrude("GroundExt", "GroundSketch", [ls], "t", "Ground")
        bodies += [("Substrate", "#2e7d32"), ("Ground", "#d4a017")]
    if step >= 2:
        out += "# --- the patch with its inset notch\n"
        y0 = "py - pl/2"
        ng = "w50/2 + inset_gap"
        pts = [("-pw/2", y0), (f"-({ng})", y0), (f"-({ng})", f"{y0} + inset"), (ng, f"{y0} + inset"),
               (ng, y0), ("pw/2", y0), ("pw/2", "py + pl/2"), ("-pw/2", "py + pl/2")]
        s, ls = rect(pts, "K")
        out += sketch("PatchSketch", "SubstrateExt:end") + s
        out += extrude("PatchExt", "PatchSketch", [ls], "t", "Patch")
        bodies += [("Patch", "#d4a017")]
    if step >= 3:
        out += "# --- the feed: pad_w under the pin, a taper, w50 into the inset; the ground pads, vias\n"
        out += sketch("TopSketch", "SubstrateExt:end")
        for n, y in [("F1", "edge"), ("F2", "edge + pad_len"), ("F3", "edge + pad_len + taper"),
                     ("F4", "py - pl/2 + inset")]:
            out += f'[[feature.point]]\nname = "{n}"\nx = "0"\ny = "{y}"\n\n'
        out += ('[[feature.polyline]]\nname = "Trace"\npoints = ["F1", "F2", "F3", "F4"]\n'
                'widths = ["pad_w", "pad_w", "w50", "w50"]\nwidth = "w50"\nstart = "flat"\nend = "flat"\n\n')
        sr, lr = rect(box_pts("pad_w/2 + gap", "edge", "gnd_xo", "edge + gnd_len"), "R")
        sl, ll = rect(box_pts("-gnd_xo", "edge", "-pad_w/2 - gap", "edge + gnd_len"), "G")
        out += sr + sl
        out += extrude("TraceExt", "TopSketch", [["Trace"]], "t", "Trace")
        out += extrude("TopGndExt", "TopSketch", [lr, ll], "t", "TopGround")
        out += sketch("ViaSketch", "XY Plane")
        k = 0
        for side in ("via_x", "-via_x"):
            for j in range(3):
                k += 1
                out += (f'[[feature.point]]\nname = "V{k}"\nx = "{side}"\ny = "edge + via_y1 + {j} * via_dy"\n\n'
                        f'[[feature.circle]]\nname = "C{k}"\ncenter = "V{k}"\nradius = "via_d/2"\n\n')
        out += extrude("ViaExt", "ViaSketch", [[f"C{i}"] for i in range(1, k + 1)], "hd", "Vias")
        bodies += [("Trace", "#d4a017"), ("TopGround", "#d4a017"), ("Vias", "#c0c0c0")]
    if step >= 4:
        out += "# --- the SMA end launch, as received\n"
        out += (f'[[feature]]\nname = "SMA"\ntype = "import"\nfile = "patch.files/realconnector.stp"\n'
                f'sha256 = "{SHA}"\nbodies = {lst(SMA_BODIES)}\n\n')
    if step >= 5:
        out += "# --- solder under the round pin: trace top up to the pin's axis\n"
        s, ls = rect(box_pts("-pin_d/2", "edge + solder_in", "pin_d/2", "edge + launch - solder_in"))
        out += sketch("PinSolderSketch", "TraceExt:end") + s
        out += extrude("PinSolderExt", "PinSolderSketch", [ls], "solder_h", "PinSolder")
        bodies += [("PinSolder", "#b0b0b0")]
    for n, c in bodies:
        out += f'[[body]]\nname = "{n}"\ncolor = "{c}"\n\n'
    if step >= 4:
        out += f'[[group]]\nname = "SMA"\nbodies = {lst(SMA_BODIES)}\n\n'
        out += ("# turned 90 deg around Y (one leg pair into the ground, the other beside the pin),\n"
                "# then moved: flush with the edge, the pin on the trace\n")
        out += ('[[transform]]\nname = "PlaceSMA"\ntype = "move"\ntargets = ["group:SMA"]\n'
                'axis = "Y Axis"\nangle = "90"\noffset = ["0.5", "edge + launch", "hd + t + pin_d/2"]\n')
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(out)
    os.replace(tmp, path)


if __name__ == "__main__":
    build(int(sys.argv[1]))
