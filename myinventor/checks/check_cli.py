# check_cli.py
#
# The part without the window (cli.py, main.py --report / --export-step),
# on the example the AI skill points to (examples/patch_antenna.part.toml),
# copied to a temp folder first:
#   - the example builds: exit 0, two bodies with exact volumes and bounds
#     (the substrate 40 x 40 x 1.6; the copper: the patch plus the feed, its
#     arc bend exact: (192 + 3 (16 + 3 pi)) x 0.035), the profiles named as
#     the extrusion names them;
#   - --json is valid JSON with the same numbers;
#   - --export-step writes a STEP that reads back (two solids);
#   - a parameter change in the file moves everything (the substrate wider);
#   - a failing feature: exit 1, the report names it, the STEP still has
#     the bodies built; an unreadable file: exit 2, nothing written;
#   - --set NAME=FORMULA (as a process): the variant built and exported
#     (the substrate 50 wide, the feed to its edge), the file unchanged; an
#     unknown name or no '=': exit 2, nothing written;
#   - main.py runs it as a process, without Qt.
#
#   python checks/check_cli.py

import io
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

import cli                                                            # noqa: E402

FAILED = []
TMP = tempfile.mkdtemp()
EXAMPLE = os.path.join(ROOT, "examples", "patch_antenna.part.toml")


def check(label, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {label} {detail}")
    if not ok:
        FAILED.append(label)


def run(path, report=True, as_json=False, step=None):
    out = io.StringIO()
    code = cli.run(path, report, as_json, step, out=out)
    return code, out.getvalue()


def copy(text_change=None):
    path = os.path.join(TMP, "antenna.part.toml")
    shutil.copy(EXAMPLE, path)
    if text_change:
        with open(path, encoding="utf-8") as f:
            text = f.read()
        old, new = text_change
        assert old in text, old
        with open(path, "w", encoding="utf-8") as f:
            f.write(text.replace(old, new))
    return path


def box_bounds(plane, x0, y0, x1, y1, distance, before=()):
    """The exact bounds of a rectangle (x0, y0)-(x1, y1) sketched on `plane`, extruded by distance."""
    from geometry.kernel import bounds
    from model.extrude import Extrude
    from model.part import Part
    from model.rebuild import rebuild
    from model.sketch import Sketch

    part = Part()
    for feature in before:
        part.add_feature(feature)
    s = part.add_feature(Sketch("S", plane=plane))
    p = [s.add_point(str(x), str(y)) for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]
    for a, b in zip(p, p[1:] + p[:1]):
        s.add_line(a, b)
    part.add_feature(Extrude("E", profiles=[("L1", "L2", "L3", "L4")], sketch="S", distance=str(distance),
                             body="B"))
    result = rebuild(part)
    assert not result.feature_errors, result.feature_errors
    return bounds(result.shapes["B"]).round(9).tolist()


def check_planes():
    """The sketch planes' axes as REFERENCE.md's table states them (section 6; the skill points there)."""
    from model.extrude import Extrude
    from model.sketch import Sketch

    got = box_bounds("XY Plane", 1, 2, 3, 5, 1)
    check("REFERENCE table: XY Plane: x -> X, y -> Y, + distance -> +Z", got == [[1, 2, 0], [3, 5, 1]], f"{got}")
    got = box_bounds("XZ Plane", 1, 2, 3, 5, 1)
    check("REFERENCE table: XZ Plane: x -> X, y -> -Z, + distance -> +Y", got == [[1, 0, -5], [3, 1, -2]], f"{got}")
    got = box_bounds("YZ Plane", 1, 2, 3, 5, 1)
    check("REFERENCE table: YZ Plane: x -> Y, y -> Z, + distance -> +X", got == [[0, 1, 2], [1, 3, 5]], f"{got}")

    def substrate():
        s = Sketch("Sub", plane="XY Plane")
        p = [s.add_point(x, y) for x, y in (("-20", "-20"), ("20", "-20"), ("20", "20"), ("-20", "20"))]
        for a, b in zip(p, p[1:] + p[:1]):
            s.add_line(a, b)
        return [s, Extrude("SubExt", profiles=[("L1", "L2", "L3", "L4")], sketch="Sub", distance="1.6",
                           body="Substrate")]
    got = box_bounds("SubExt:end", 1, 2, 3, 5, 0.5, substrate())
    check("REFERENCE table: on Ext:end: the same x, y, up out of the body", got == [[1, 2, 1.6], [3, 5, 2.1]], f"{got}")
    got = box_bounds("SubExt:start", 1, 2, 3, 5, 0.5, substrate())
    check("REFERENCE table: on Ext:start: x the same, y mirrored, down out of the body (a ground plane)",
          got == [[1, -5, -0.5], [3, -2, 0]], f"{got}")
    got = box_bounds("SubExt:side.L2", 1, 0.2, 3, 0.6, 2, substrate())
    check("REFERENCE table: on Ext:side.L2 (x = 20, from (20, -20) up): x along L2 from its start, y out of the wall "
          "x x (here +Z), out of the body (+X)", got == [[20, -19, 0.2], [22, -17, 0.6]], f"{got}")


def main():
    path = copy()
    code, text = run(path)
    check("the example builds: exit 0, 'ok' last", code == 0 and text.strip().endswith("ok"), text[-200:])
    code, out = run(path, as_json=True)
    data = json.loads(out)
    bodies = {b["name"]: b for b in data["bodies"]}
    check("JSON: two bodies, Substrate and Copper", set(bodies) == {"Substrate", "Copper"}, f"{list(bodies)}")
    sub, cu = bodies.get("Substrate"), bodies.get("Copper")
    check("the substrate: 40 x 40 x 1.6, from z 0", sub is not None and math.isclose(sub["volume"], 2560, rel_tol=1e-9)
          and sub["bounds"] == [[-20, -20, 0], [20, 20, 1.6]], f"{sub}")
    want = (192 + 3 * (16 + 3 * math.pi)) * 0.035
    check("the copper on top: the patch and the feed with its exact arc bend",
          cu is not None and math.isclose(cu["volume"], want, rel_tol=1e-6)
          and cu["bounds"] == [[-8, -15.5, 1.6], [20, 6, 1.635]], f"{cu} want {want}")
    top = next(f for f in data["features"] if f["name"] == "TopSketch")
    check("the profiles as the extrusion names them", sorted(p["key"] for p in top["profiles"])
          == [["Feed"], ["L1", "L2", "L3", "L4"]], f"{top.get('profiles')}")
    check("the text names the parameters with values and the bodies with sizes",
          "bend_r = 2 * feed_w  ->  6" in text and "size 40 x 40 x 1.6 mm" in text, text[:300])

    step = os.path.join(TMP, "antenna.step")
    code, text = run(path, report=False, step=step)
    check("--export-step: written", code == 0 and os.path.getsize(step) > 1000 and "STEP written" in text, text)
    from OCP.STEPControl import STEPControl_Reader
    from geometry.kernel import solid_count
    reader = STEPControl_Reader()
    reader.ReadFile(step)
    reader.TransferRoots()
    check("  it reads back: two solids", solid_count(reader.OneShape()) == 2)

    wider = copy(('name = "sub_w"\nformula = "40"', 'name = "sub_w"\nformula = "50"'))
    data = json.loads(run(wider, as_json=True)[1])
    sub = next(b for b in data["bodies"] if b["name"] == "Substrate")
    cu = next(b for b in data["bodies"] if b["name"] == "Copper")
    check("sub_w = 50: the substrate wider, the feed still to its edge", sub["size"][0] == 50
          and cu["bounds"][1][0] == 25, f"{sub['size']} {cu['bounds']}")

    broken = copy(('radii = ["", "bend_r", ""]', 'radii = ["", "50", ""]'))
    os.remove(step)
    code, text = run(broken, step=step)
    check("a bend past its neighbour: exit 1, the sketch and the extrusion named, NOT OK",
          code == 1 and "TopSketch (sketch): FAILED" in text and "CopperExt (extrude): FAILED" in text
          and "NOT OK" in text, text)
    check("  the STEP still written, of the substrate (the failed ones named)",
          os.path.exists(step) and "without the failed TopSketch, CopperExt" in text)

    bad = os.path.join(TMP, "bad.part.toml")
    with open(bad, "w", encoding="utf-8") as f:
        f.write('format = "mycad-part"\nversion = 1\n[body]\ncolor = 1\n')
    other = os.path.join(TMP, "never.step")
    code, text = run(bad, step=other)
    check("an unreadable file: exit 2, the reason, nothing written",
          code == 2 and "cannot read the part" in text and not os.path.exists(other), text)

    check_planes()

    proc = subprocess.run([sys.executable, "-X", "importtime", os.path.join(ROOT, "main.py"), copy(), "--report"],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    check("main.py --report as a process: exit 0, the report, no Qt loaded",
          proc.returncode == 0 and "bodies:" in proc.stdout and "PySide6" not in proc.stderr,
          proc.stdout[-200:] + proc.stderr[-200:] if proc.returncode else "")

    def main_py(*args):
        proc = subprocess.run([sys.executable, os.path.join(ROOT, "main.py"), *args],
                              capture_output=True, text=True, encoding="utf-8", errors="replace")
        return proc.returncode, proc.stdout + proc.stderr

    path = copy()
    before = open(path, "rb").read()
    variant = os.path.join(TMP, "zz_sub_w_50.step")
    code, out = main_py(path, "--set", "sub_w=50", "--set", "h = 2", "--export-step", variant, "--report", "--json")
    data = json.loads(out[:out.rindex("}") + 1]) if "{" in out else {}
    sizes = {b["name"]: b["size"] for b in data.get("bodies", [])}
    check("--set sub_w=50 --set 'h = 2': the variant (substrate 50 x 40 x 2), exported", code == 0
          and sizes.get("Substrate") == [50, 40, 2] and os.path.getsize(variant) > 1000
          and next(p for p in data["parameters"] if p["name"] == "sub_w")["formula"] == "50", f"{code} {sizes}")
    check("  the part file unchanged", open(path, "rb").read() == before)
    other = os.path.join(TMP, "never2.step")
    code, out = main_py(path, "--set", "width=3", "--export-step", other)
    check("--set of no parameter: exit 2, the parameters named, nothing written", code == 2 and "sub_w" in out
          and not os.path.exists(other), out)
    code, out = main_py(path, "--set", "sub_w", "--report")
    check("--set without '=': exit 2", code == 2 and "NAME=FORMULA" in out, out)

    print("FAILED: " + ", ".join(FAILED) if FAILED else "all checks passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
