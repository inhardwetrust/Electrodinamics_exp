# check_picking.py
#
# Picking in the 3D view (view/picking.py through GeometryView), on a box
# 10 x 6 x 2 mm read from STEP, in the default view:
#   - a click on each corner's pixel picks that CAD vertex exactly if the
#     corner is visible, and never the hidden one; visibility is found
#     independently (the ray from the camera enters the box before the
#     corner: slab test). X-ray picks the hidden corner too;
#   - a click 3 px beside a point of a visible edge picks that point (the
#     ray's closest point on the edge, exact in perspective); a click in a face's middle picks a point on that face's plane;
#   - a click on nothing picks nothing; two points: a click sets or moves
#     A, Shift + click B, |AB|; drags and clicks on nothing change nothing;
#     a double click on nothing removes both;
#   - a section removing the corner's half: that corner is not picked.
#
#   python checks/check_picking.py

import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

import numpy as np                                                    # noqa: E402
from vispy import app                                                 # noqa: E402
from vispy.util import keys                                           # noqa: E402

app.use_app("pyside6")
app.use_app().create()

from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox                       # noqa: E402
from OCP.gp import gp_Pnt                                             # noqa: E402

from geometry.step_io import read_step, write_step                    # noqa: E402
from view.geometry_view import GeometryView                          # noqa: E402
from view.picking import _project, _ray                               # noqa: E402

FAILED = []
LO, HI = np.array([0.0, 0.0, 0.0]), np.array([10.0, 6.0, 2.0])      # mm


def check(label, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {label} {detail}")
    if not ok:
        FAILED.append(label)


def slab_entry(origin, direction):
    """Distance along the ray to where it enters the box (inf if it misses)."""
    with np.errstate(divide="ignore", invalid="ignore"):
        t1, t2 = (LO - origin) / direction, (HI - origin) / direction
    near = np.nanmax(np.minimum(t1, t2))
    far = np.nanmin(np.maximum(t1, t2))
    return near if near <= far else np.inf


class Event:
    def __init__(self, pos, button=1, modifiers=()):
        self.pos, self.button, self.modifiers = pos, button, modifiers


def main():
    path = os.path.join(tempfile.mkdtemp(), "box.step")
    write_step(path, [("box", BRepPrimAPI_MakeBox(gp_Pnt(*LO), gp_Pnt(*HI)).Shape(), None)])
    view = GeometryView(canvas_size=(800, 600), show=True)
    view.set_geometry(read_step(path))
    for _ in range(20):
        app.process_events()
    transform = view._bodies["box"].mesh.get_transform("visual", "canvas")

    corners = np.array([[(HI if (n >> a) & 1 else LO)[a] for a in range(3)] for n in range(8)])
    xy, _ = _project(transform, corners)
    hidden = []
    for corner, pixel in zip(corners, xy):
        origin, direction = _ray(transform, pixel)
        hidden.append(slab_entry(origin, direction) < np.dot(corner - origin, direction) - 1e-3)
    check("the default view hides one corner", sum(hidden) == 1, f"{sum(hidden)} hidden")

    for corner, pixel, behind in zip(corners, xy, hidden):
        pick = view.pick_at(pixel)
        got = pick is not None and pick.kind == "vertex" and np.allclose(pick.point, corner, atol=1e-6)
        if behind:
            check(f"hidden corner {corner} not picked", not got, f"{pick.kind if pick else None}")
            view.set_xray(True)
            pick = view.pick_at(pixel)
            view.set_xray(False)
            check("  X-ray picks it", pick is not None and pick.kind == "vertex"
                  and np.allclose(pick.point, corner, atol=1e-6))
        else:
            check(f"corner {corner}", got, f"{pick.kind if pick else None}")

    # Edges: the 12 box edges, those with both ends visible. A click 3 px
    # beside the 3D point at a quarter and three quarters of the edge picks
    # that point (to a pixel's size): not the screen fraction, which in
    # perspective is off along an edge going into the depth.
    edges = [(i, i | 1 << a) for i in range(8) for a in range(3) if not (i >> a) & 1]
    tested, worst, worst_screen = 0, 0.0, 0.0
    for i, j in edges:
        if hidden[i] or hidden[j]:
            continue
        p, q = corners[i], corners[j]
        a, b = xy[i], xy[j]
        normal = np.array([-(b - a)[1], (b - a)[0]]) / np.linalg.norm(b - a)
        for f in (0.25, 0.75):
            target = p + f * (q - p)
            pixel = _project(transform, target[None])[0][0]
            for side in (1, -1):
                pick = view.pick_at(pixel + 3 * side * normal)
                if pick is not None and pick.kind == "edge":
                    break
            s = np.dot(pick.point - p, q - p) / np.dot(q - p, q - p) if pick else -1
            on = pick is not None and pick.kind == "edge"                 and np.linalg.norm(p + s * (q - p) - pick.point) < 1e-6
            error = np.linalg.norm(pick.point - target) if pick else np.inf
            screen = np.dot(pixel - a, b - a) / np.dot(b - a, b - a)      # the old way
            worst_screen = max(worst_screen, np.linalg.norm(p + screen * (q - p) - target))
            worst = max(worst, error)
            check(f"edge {p} - {q} at {f}", on and error < 0.05,
                  f"{pick.kind if pick else None}, off by {error * 1e3:.1f} um")
        tested += 1
    check("visible edges tested", tested >= 6, str(tested))
    print(f"  worst: {worst * 1e3:.1f} um; the screen fraction would be off by {worst_screen * 1e3:.0f} um")

    # Faces: the middle of each face whose normal faces the camera.
    origin, direction = _ray(transform, (400, 300))
    for axis in range(3):
        for side, value in ((-1, LO[axis]), (1, HI[axis])):
            if side * direction[axis] >= 0:
                continue                    # back face
            center = 0.5 * (LO + HI)
            center[axis] = value
            pick = view.pick_at(_project(transform, center[None])[0][0])
            ok = pick is not None and pick.kind == "face" and abs(pick.point[axis] - value) < 1e-6 \
                and np.linalg.norm(pick.point - center) < 0.05
            check(f"face {'xyz'[axis]} = {value}", ok,
                  f"{pick.kind if pick else None} {None if pick is None else np.round(pick.point, 4)}")

    check("a click on nothing picks nothing", view.pick_at((5, 5)) is None)

    # Two points: a click sets or moves A, Shift + click B; drags, clicks on
    # nothing change nothing; a double click on nothing removes both.
    def click(pos, shift=False, drag=0):
        modifiers = (keys.SHIFT,) if shift else ()
        view._mouse_press(Event(pos, modifiers=modifiers))
        view._mouse_release(Event(np.asarray(pos) + drag, modifiers=modifiers))

    def at(n):
        """Which point (0 = A, 1 = B) is on corner n, or None."""
        hits = [i for i, p in enumerate(view.picks) if p is not None and np.allclose(p.point, corners[n])]
        return hits[0] if hits else None

    calls = []
    view.on_pick = calls.append
    seen = [n for n in range(8) if not hidden[n]]
    a, b, c = seen[0], seen[-1], seen[1]
    click(xy[a], drag=20)
    check("a drag picks nothing", calls == [] and not any(view.picks))
    click(xy[a])
    check("a click sets A", at(a) == 0 and view.picks[1] is None and len(calls) == 1)
    click(xy[b], shift=True)
    check("Shift + click sets B", at(a) == 0 and at(b) == 1)
    distance = np.linalg.norm(view.picks[1].point - view.picks[0].point)
    check("|AB|", abs(distance - np.linalg.norm(corners[b] - corners[a])) < 1e-9, f"{distance:.6f} mm")
    click(xy[c])
    check("a click moves A, B stays", at(c) == 0 and at(b) == 1)
    click(xy[a], shift=True)
    check("Shift + click moves B", at(c) == 0 and at(a) == 1)
    click(xy[b], shift=True, drag=20)
    n = len(calls)
    click((5, 5))
    check("Shift + drag, a click on nothing: no change", at(c) == 0 and at(a) == 1 and len(calls) == n)
    view._double_click(Event(xy[b]))
    check("a double click on the model keeps them", at(c) == 0 and at(a) == 1)
    view._double_click(Event((5, 5)))
    check("a double click on nothing removes both", view.picks == [None, None] and calls[-1] == [None, None])

    # A section keeping z <= 1: a top corner is not picked any more.
    top = [n for n in range(8) if corners[n][2] == HI[2] and not hidden[n]][0]
    view.set_section(2, 1e-3, keep_below=True)
    pick = view.pick_at(xy[top])
    check("section: a removed corner is not picked",
          pick is None or not np.allclose(pick.point, corners[top]), f"{None if pick is None else pick.kind}")
    view.set_section(None)

    print("FAILED: " + ", ".join(FAILED) if FAILED else "all checks passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
