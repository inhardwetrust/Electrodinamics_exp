# probe.py
#
# Measures the bodies of a part file (rebuilt) or of a STEP file, without
# the window: for Claude checking a design, and for understanding an
# imported part (a connector: its axis, pin, legs, gaps). The geometry is
# geometry/probe.py. All in mm.
#
#   python tools/probe.py FILE bodies [BODY ...]
#       each body: volume, solids, bounds, size (each solid when several)
#   python tools/probe.py FILE slice x|y|z=VALUE [BODY ...]
#       the cut by that plane: per body its pieces, each with its area and
#       its extent in the other two coordinates (where a pin, a leg, a gap is)
#   python tools/probe.py FILE contacts [BODY ...] [--near MM]
#       each pair of bodies closer than --near (default 1): the gap
#       (0: touching or overlapping) and the volume they share
#   options: --unplaced (a part's bodies as the features built them, before
#   the transforms), --json (the same as JSON)
# BODY: a name or a pattern ("SMA:*"); none: all.
# Exit code 0, 2 (the file does not read or build, a name matches nothing).

import json
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)


def fmt(v):
    return f"{v:.6g}" if abs(v) >= 1e-9 else "0"          # OCC's 1e-15 noise reads as 0


def span(lo, hi, axes):
    return "  ".join(f"{'xyz'[k]} {fmt(lo[k])}..{fmt(hi[k])}" for k in axes)


def main(argv):
    as_json = "--json" in argv
    placed = "--unplaced" not in argv
    near = 1.0
    if "--near" in argv:
        i = argv.index("--near")
        near = float(argv[i + 1])
        argv = argv[:i] + argv[i + 2:]
    args = [a for a in argv if not a.startswith("--")]
    if len(args) < 2 or args[1] not in ("bodies", "slice", "contacts"):
        print(open(__file__, encoding="utf-8").read().split("\n\n")[0])
        return 2
    path, command, rest = args[0], args[1], args[2:]
    from geometry import probe

    plane = None
    if command == "slice":
        if not rest or "=" not in rest[0] or rest[0].split("=")[0] not in probe.AXES:
            print("slice needs x=VALUE, y=VALUE or z=VALUE")
            return 2
        axis, value = rest[0].split("=", 1)
        plane, rest = (axis, float(value)), rest[1:]
    try:
        bodies = probe.pick(probe.load(path, placed=placed), rest)
    except (KeyError, ValueError, OSError) as error:
        print(f"{path}: {error}")
        return 2
    except Exception as error:          # noqa: BLE001 (a part file that does not read: PartFileError)
        print(f"{path}: {error}")
        return 2

    if command == "bodies":
        data = [{"name": n, **probe.body_info(s)} for n, s in bodies]
        if as_json:
            print(json.dumps(data, indent=2, ensure_ascii=False))
            return 0
        for b in data:
            (lo, hi) = b["bounds"]
            print(f"{b['name']}: volume {fmt(b['volume'])} mm3, {b['solids']} solid(s), "
                  f"size {' x '.join(fmt(v) for v in b['size'])}, {span(lo, hi, (0, 1, 2))}")
            for p in b.get("parts", []):
                print(f"    solid: volume {fmt(p['volume'])}, {span(*p['bounds'], (0, 1, 2))}")
        return 0

    if command == "slice":
        axis, at = plane
        others = [k for k in range(3) if k != probe.AXES[axis]]
        data = []
        for n, s in bodies:
            pieces = probe.section(s, axis, at)
            if pieces:
                data.append({"name": n, "pieces": pieces})
        if as_json:
            print(json.dumps({"plane": {axis: at}, "bodies": data}, indent=2, ensure_ascii=False))
            return 0
        print(f"the cut at {axis} = {fmt(at)}" + ("" if data else ": it meets no body"))
        for b in data:
            print(f"{b['name']}:")
            for p in b["pieces"]:
                print(f"    {span(*p['bounds'], others)}  area {fmt(p['area'])} mm2")
        return 0

    pairs = []
    for i, (na, a) in enumerate(bodies):
        for nb, b in bodies[i + 1:]:
            g = probe.gap(a, b)
            if g <= near:
                pairs.append({"a": na, "b": nb, "gap": g, "overlap": probe.overlap(a, b) if g < 1e-9 else 0.0})
    if as_json:
        print(json.dumps(pairs, indent=2, ensure_ascii=False))
        return 0
    print(f"pairs closer than {fmt(near)} mm" + ("" if pairs else ": none"))
    for p in pairs:
        what = (f"overlap {fmt(p['overlap'])} mm3" if p["overlap"] > 1e-12 else "touching") if p["gap"] < 1e-9 \
            else f"gap {fmt(p['gap'])} mm"
        print(f"  {p['a']} - {p['b']}: {what}")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main(sys.argv[1:]))
