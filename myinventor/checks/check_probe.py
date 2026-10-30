# check_probe.py
#
# Measuring bodies (geometry/probe.py, tools/probe.py), on a part written
# into a temp folder:
#   A   a 10 x 10 x 2 block (x, y 0..10, z 0..2);
#   Cyl a cylinder r 2, 3 high, on A's top (center 5, 5);
#   C   a 2 x 2 x 2 block 0.5 mm off A (x 10.5..12.5), moved by +10 in y
#       (a placement: unplaced it is where its sketch has it);
#   D   a block x 8..12, y 0..2, z 0..2 overlapping A by 2 x 2 x 2.
# Checks:
#   - bodies: the volumes (200, 12 pi, 8, 16), C's bounds placed and
#     unplaced;
#   - slice z = 1: A's piece 100 mm² over x, y 0..10, D's 8; z = 3: the
#     cylinder's disc 4 pi exactly; a plane that misses everything: none;
#   - contacts: A - Cyl touching, nothing shared; A - D overlap 8; C
#     unplaced 0.5 from A, C - D overlap 6; pairs further than --near left out;
#   - the same file exported to STEP reads the same; the command line
#     (--json, a pattern, exit 2 for a name that matches nothing).
#
#   python checks/check_probe.py

import json
import math
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

from geometry import probe                                            # noqa: E402
from model.extrude import Extrude                                     # noqa: E402
from model.part import Part                                           # noqa: E402
from model.part_file import save                                      # noqa: E402
from model.sketch import Sketch                                       # noqa: E402
from model.transforms import Move                                     # noqa: E402

FAILED = []
TMP = tempfile.mkdtemp()
RECT = ("L1", "L2", "L3", "L4")


def check(label, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {label} {detail}")
    if not ok:
        FAILED.append(label)


def block(part, name, x0, y0, w, h, height, plane="XY Plane"):
    s = part.add_feature(Sketch(name + "Sketch", plane=plane))
    p = [s.add_point(str(x), str(y)) for x, y in ((x0, y0), (x0 + w, y0), (x0 + w, y0 + h), (x0, y0 + h))]
    for a, b in zip(p, p[1:] + p[:1]):
        s.add_line(a, b)
    part.add_feature(Extrude(name + "Ext", profiles=[RECT], sketch=name + "Sketch", distance=str(height), body=name))


def the_part():
    part = Part()
    block(part, "A", 0, 0, 10, 10, 2)
    s = part.add_feature(Sketch("CylSketch", plane="AExt:end"))
    s.add_circle(s.add_point("5", "5"), "2")
    part.add_feature(Extrude("CylExt", profiles=[("C1",)], sketch="CylSketch", distance="3", body="Cyl"))
    block(part, "C", 10.5, 0, 2, 2, 2)
    block(part, "D", 8, 0, 4, 2, 2)
    part.add_transform(Move("Shift", targets=["C"], offset=["0", "10", "0"]))
    return part


def close(a, b, tol=1e-6):
    return abs(a - b) <= tol * max(1.0, abs(b))


def main():
    path = os.path.join(TMP, "probe.part.toml")
    save(path, the_part())
    bodies = dict(probe.load(path))
    info = {n: probe.body_info(s) for n, s in bodies.items()}
    check("bodies: A 200, Cyl 12 pi, C 8, D 16 mm3", close(info["A"]["volume"], 200)
          and close(info["Cyl"]["volume"], 12 * math.pi, 1e-9) and close(info["C"]["volume"], 8)
          and close(info["D"]["volume"], 16), str({n: i["volume"] for n, i in info.items()}))
    check("  C placed: y 10..12", close(info["C"]["bounds"][0][1], 10) and close(info["C"]["bounds"][1][1], 12),
          str(info["C"]["bounds"]))
    unplaced = dict(probe.load(path, placed=False))
    check("  C unplaced: y 0..2", close(probe.body_info(unplaced["C"])["bounds"][1][1], 2))

    a = probe.section(bodies["A"], "z", 1.0)
    check("slice z = 1: A one piece of 100 mm2 over x, y 0..10", len(a) == 1 and close(a[0]["area"], 100)
          and close(a[0]["bounds"][0][0], 0) and close(a[0]["bounds"][1][1], 10), str(a))
    d = probe.section(bodies["D"], "z", 1.0)
    check("  D: 8 mm2", len(d) == 1 and close(d[0]["area"], 8), str(d))
    c = probe.section(bodies["Cyl"], "z", 3.0)
    check("slice z = 3: the cylinder's disc, 4 pi exactly, x 3..7", len(c) == 1
          and close(c[0]["area"], 4 * math.pi, 1e-9) and close(c[0]["bounds"][0][0], 3), str(c))
    check("  A at z = 3: nothing (the plane misses it)", probe.section(bodies["A"], "z", 3.0) == [])

    check("contacts: A - Cyl touching, nothing shared", probe.gap(bodies["A"], bodies["Cyl"]) < 1e-9
          and probe.overlap(bodies["A"], bodies["Cyl"]) < 1e-9)
    check("  A - D overlap 8 mm3", probe.gap(bodies["A"], bodies["D"]) < 1e-9
          and close(probe.overlap(bodies["A"], bodies["D"]), 8))
    check("  C unplaced 0.5 from A", close(probe.gap(unplaced["A"], unplaced["C"]), 0.5))

    def run(*args):
        out = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "probe.py"), *args],
                             capture_output=True, text=True, encoding="utf-8")
        return out.returncode, out.stdout

    code, out = run(path, "contacts", "--unplaced", "--near", "0.6", "--json")
    pairs = {(p["a"], p["b"]): p for p in json.loads(out)} if code == 0 else {}
    check("probe.py contacts --unplaced --near 0.6: A-Cyl, A-C 0.5, A-D 8, C-D (overlap 1.5 x 2 x 2 = 6)", code == 0
          and set(pairs) == {("A", "Cyl"), ("A", "C"), ("A", "D"), ("C", "D")}
          and close(pairs[("A", "C")]["gap"], 0.5) and close(pairs[("A", "D")]["overlap"], 8)
          and close(pairs[("C", "D")]["overlap"], 6), out[:300])
    code, out = run(path, "slice", "z=1", "A", "D")
    check("probe.py slice z=1 A D: text with the areas", code == 0 and "area 100 mm2" in out
          and "area 8 mm2" in out and "Cyl" not in out, out)
    code, out = run(path, "bodies", "Nothing*")
    check("a pattern that matches nothing: exit 2, the bodies named", code == 2 and "Cyl" in out, out)

    from geometry.step_io import export_part
    from model.part_file import load
    from model.rebuild import rebuild

    step = os.path.join(TMP, "probe.step")
    export_part(step, load(path), rebuild(load(path), folder=TMP))
    code, out = run(step, "bodies", "--json")
    got = {b["name"]: b for b in json.loads(out)} if code == 0 else {}
    check("the STEP: the same bodies and volumes", code == 0 and set(got) == set(info)
          and all(close(got[n]["volume"], info[n]["volume"], 1e-6) for n in info), str(list(got)))

    print("FAILED: " + ", ".join(FAILED) if FAILED else "all checks passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
