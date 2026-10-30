"""Writes model/launch.part.toml: SMA -> launch -> 50 Ohm line -> box waveport. Arg: step (0..5)."""
import os
import sys

ROOT = r"C:\Users\v.ivko\Documents\ivko_files\work_files\68_fields_research\VisPy_playground\patch_antenna_sma"
PART = os.path.join(ROOT, "model", "launch.part.toml")
SHA = "b3ca3a28cd2ffd839aac35b41e640c4d4876db66c2ab465d82391ee13584e0fd"
SMA_BODIES = ["SMA:connector_outer", "SMA:connector_outer_2", "SMA:connector_dielectric",
              "SMA:connector_wire", "SMA:Volumenkörper16"]

PARAMS = [
    ("bx", "30", "the board's width (x)"),
    ("edge", "-25", "the board's edge with the connector (y)"),
    ("line_end", "-5", "where the line and the board end, the box port beyond (y)"),
    ("h", "1.6", "the board's total thickness"),
    ("t", "0.035", "copper (1 oz) on each side"),
    ("hd", "h - 2 * t", "FR4 between the copper layers"),
    ("w50", "2.93", "50 Ohm microstrip width (Hammerstad, eps_r 4.3, h 1.53, t 0.035)"),
    ("pin_d", "0.9", "the connector's pin diameter where it lies on the board"),
    ("launch", "4", "how far the pin and the legs reach over the board from its edge"),
    ("pad_w", "w50", "the trace's width under the pin"),
    ("pad_len", "launch", "the length of the trace at pad_w from the edge"),
    ("taper", "1", "the taper from pad_w to w50"),
    ("gap", "0.5", "the gap from the trace under the pin to the top ground pads"),
    ("gnd_xo", "4.5", "the top ground pads' outer edge (x)"),
    ("gnd_len", "launch + 1", "the top ground pads' length from the edge"),
    ("via_d", "0.5", "the vias' diameter (filled, PEC)"),
    ("via_x", "3.7", "the vias' x (both sides)"),
    ("via_y1", "0.6", "the first via row from the edge"),
    ("via_dy", "1.6", "the via pitch along y"),
    ("solder_in", "0.3", "the pin's solder: set back from both ends of the pin over the board"),
    ("solder_h", "pin_d / 2", "the pin's solder: from the trace's top up to the pin's axis"),
    ("port_h", "8", "the line port box's height above the ground"),
]


def q(s):
    return '"' + s + '"'


def lst(items):
    return "[" + ", ".join(q(i) for i in items) + "]"


def params():
    return "".join(f'[[parameter]]\nname = "{n}"\nformula = "{f}"\ncomment = "{c}"\n\n' for n, f, c in PARAMS)


def sketch(name, plane):
    return f'[[feature]]\nname = "{name}"\ntype = "sketch"\nplane = "{plane}"\n\n'


def rect(pts, prefix="", first=1):
    """Points and lines of a closed rectangle; returns (text, line names)."""
    s, names = "", []
    n = len(pts)
    for i, (x, y) in enumerate(pts):
        s += f'[[feature.point]]\nname = "{prefix}P{first + i}"\nx = "{x}"\ny = "{y}"\n\n'
    for i in range(n):
        a, b = first + i, first + (i + 1) % n
        s += f'[[feature.line]]\nname = "{prefix}L{a}"\nstart = "{prefix}P{a}"\nend = "{prefix}P{b}"\n\n'
        names.append(f"{prefix}L{a}")
    return s, names


def box_pts(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def extrude(name, sketch_name, profiles, distance, body, op="new"):
    profs = "[" + ", ".join(lst(p) for p in profiles) + "]"
    return (f'[[feature]]\nname = "{name}"\ntype = "extrude"\nsketch = "{sketch_name}"\n'
            f'profiles = {profs}\ndistance = "{distance}"\noperation = "{op}"\nbody = "{body}"\n\n')


def build(step, path=PART):
    out = 'format = "mycad-part"\nversion = 1\n\n' + params()
    bodies = []
    board = box_pts("-bx/2", "edge", "bx/2", "line_end")
    if step >= 1:
        out += "# --- the board: FR4 from z 0 up by hd, the ground under it\n"
        s, ls = rect(board)
        out += sketch("BoardSketch", "XY Plane") + s + extrude("SubstrateExt", "BoardSketch", [ls], "hd", "Substrate")
        # Ext:start mirrors y
        s, ls = rect(box_pts("-bx/2", "-line_end", "bx/2", "-edge"))
        out += sketch("GroundSketch", "SubstrateExt:start") + s + extrude("GroundExt", "GroundSketch", [ls], "t", "Ground")
        bodies += [("Substrate", "#2e7d32"), ("Ground", "#d4a017")]
    if step >= 2:
        out += "# --- top copper: the trace (pad_w under the pin, a taper, w50), the ground pads\n"
        out += sketch("TopSketch", "SubstrateExt:end")
        for n, y in [("F1", "edge"), ("F2", "edge + pad_len"), ("F3", "edge + pad_len + taper"), ("F4", "line_end")]:
            out += f'[[feature.point]]\nname = "{n}"\nx = "0"\ny = "{y}"\n\n'
        out += ('[[feature.polyline]]\nname = "Trace"\npoints = ["F1", "F2", "F3", "F4"]\n'
                'widths = ["pad_w", "pad_w", "w50", "w50"]\nwidth = "w50"\nstart = "flat"\nend = "flat"\n\n')
        sr, lr = rect(box_pts("pad_w/2 + gap", "edge", "gnd_xo", "edge + gnd_len"), "R")
        sl, ll = rect(box_pts("-gnd_xo", "edge", "-pad_w/2 - gap", "edge + gnd_len"), "G")
        out += sr + sl
        out += extrude("TraceExt", "TopSketch", [["Trace"]], "t", "Trace")
        out += extrude("TopGndExt", "TopSketch", [lr, ll], "t", "TopGround")
        out += "# --- vias: filled cylinders through the FR4 under the ground pads\n"
        out += sketch("ViaSketch", "XY Plane")
        k = 0
        for side in ("via_x", "-via_x"):
            for j in range(3):
                k += 1
                out += (f'[[feature.point]]\nname = "V{k}"\nx = "{side}"\ny = "edge + via_y1 + {j} * via_dy"\n\n'
                        f'[[feature.circle]]\nname = "C{k}"\ncenter = "V{k}"\nradius = "via_d/2"\n\n')
        out += extrude("ViaExt", "ViaSketch", [[f"C{i}"] for i in range(1, k + 1)], "hd", "Vias")
        bodies += [("Trace", "#d4a017"), ("TopGround", "#d4a017"), ("Vias", "#c0c0c0")]
    if step >= 3:
        out += "# --- the SMA end launch, as received\n"
        out += (f'[[feature]]\nname = "SMA"\ntype = "import"\nfile = "launch.files/realconnector.stp"\n'
                f'sha256 = "{SHA}"\nbodies = {lst(SMA_BODIES)}\n\n')
    if step >= 4:
        out += "# --- solder under the round pin: trace top up to the pin's axis\n"
        s, ls = rect(box_pts("-pin_d/2", "edge + solder_in", "pin_d/2", "edge + launch - solder_in"))
        out += sketch("PinSolderSketch", "TraceExt:end") + s
        out += extrude("PinSolderExt", "PinSolderSketch", [ls], "solder_h", "PinSolder")
        bodies += [("PinSolder", "#b0b0b0")]
    if step >= 5:
        out += "# --- the line's port: a box beyond the board's end, ground bottom to port_h\n"
        out += ('[[feature]]\nname = "PortPlane"\ntype = "workplane"\nbase = "XZ Plane"\noffset = "line_end"\n'
                'visible = false\n\n')
        s, ls = rect(box_pts("-bx/2", "t", "bx/2", "-port_h"))      # XZ: sketch y = -z
        out += sketch("PortSketch", "PortPlane") + s + extrude("PortExt", "PortSketch", [ls], "1", "LinePort")
        bodies += [("LinePort", "#6a5acd")]
    for n, c in bodies:
        out += f'[[body]]\nname = "{n}"\ncolor = "{c}"\n\n'
    if step >= 3:
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
