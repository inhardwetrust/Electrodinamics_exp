# check_feature_highlight.py
#
# A feature selected in the browser highlights what it made (the view's
# set_highlight, see-through over everything):
#   - Plate: a 10 x 10 x 2 extrusion (new): its own sweep, x 0..10, z 0..2;
#   - Hole: a 2 x 2 cut through it: what it cut away (red), x 4..6;
#   - Ring: a revolution: its own ring;
#   - Board: an import: its bodies as read;
#   - Plate's body moved by z 20 (placement): the highlight moves along
#     (z 20..22), the cut's too;
#   - a body row, a sketch row: no highlight; back to a feature: on again;
#     a change (commit) keeps it on the selected feature.
#
#   python checks/check_feature_highlight.py

import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "checks"))
sys.stdout.reconfigure(encoding="utf-8")
TMP = tempfile.mkdtemp()
os.environ["MYCAD_SETTINGS"] = os.path.join(TMP, "settings.json")     # never the user's settings

import numpy as np                                                    # noqa: E402

from model.extrude import Extrude                                     # noqa: E402
from model.part import Part                                           # noqa: E402
from model.part_file import dumps                                     # noqa: E402
from model.revolve import Revolve                                     # noqa: E402
from model.sketch import Sketch                                       # noqa: E402
from model.transforms import Move                                     # noqa: E402

FAILED = []
RECT = ("L1", "L2", "L3", "L4")


def check(label, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {label} {detail}")
    if not ok:
        FAILED.append(label)


def rectangle(part, name, plane, x0, y0, w, h):
    s = part.add_feature(Sketch(name, plane=plane))
    p = [s.add_point(str(x), str(y)) for x, y in ((x0, y0), (x0 + w, y0), (x0 + w, y0 + h), (x0, y0 + h))]
    for a, b in zip(p, p[1:] + p[:1]):
        s.add_line(a, b)
    return s


def the_part():
    part = Part()
    rectangle(part, "S1", "XY Plane", 0, 0, 10, 10)
    part.add_feature(Extrude("Plate", profiles=[RECT], sketch="S1", distance="2", body="P"))
    rectangle(part, "S2", "XY Plane", 4, 4, 2, 2)
    part.add_feature(Extrude("Hole", profiles=[RECT], sketch="S2", distance="2", operation="cut", body="P"))
    s = rectangle(part, "S3", "XZ Plane", 20, 0, 2, 2)
    s.add_line(s.add_point("15", "0"), s.add_point("15", "1"), construction=True)
    part.add_feature(Revolve("Ring", profiles=[RECT], sketch="S3", axis="L5", body="R"))
    return part


def bounds(window):
    body = window.view.highlight_body
    if body is None:
        return None
    v = body.vertices / 1e-3
    return np.array([v.min(axis=0), v.max(axis=0)])


def main():
    from vispy import app

    app.use_app("pyside6")
    app.use_app().create()
    from vispy_errors import install as _watch_vispy

    vispy_errors = _watch_vispy()
    from check_import import write_source
    from check_sketch_line import settle
    from view.main_window import MainWindow

    window = MainWindow()
    window.ask_save_changes = lambda: "discard"
    errors = []
    window.show_error = errors.append
    window.resize(1200, 800)
    window.show()
    settle(300)
    path = os.path.join(TMP, "part.part.toml")
    with open(path, "w", encoding="utf-8") as f:
        f.write(dumps(the_part()))
    window.open_file(path)
    source = os.path.join(TMP, "src", "board.step")
    os.makedirs(os.path.dirname(source))
    write_source(source)
    board = window.import_step(source)
    settle()

    def select(key):
        window.tree.setCurrentItem(window._rows[key])
        settle(50)
        return bounds(window)

    b = select(("feature", "Plate"))
    check("Plate selected: its sweep highlighted, x 0..10, z 0..2, not as a cut",
          b is not None and np.allclose(b, [[0, 0, 0], [10, 10, 2]], atol=1e-6)
          and tuple(window.view.highlight.color.rgba)[:3] == (0.25, 0.6, 1.0), f"{b}")
    b = select(("feature", "Hole"))
    check("Hole: what it cut away (red), x 4..6", b is not None and np.allclose(b[:, 0], [4, 6], atol=1e-6)
          and window.view.highlight.color.rgba[0] == 1.0, f"{b}")
    b = select(("feature", "Ring"))
    check("Ring: its own ring (x 20..22 around x 15: x 8..22, z -2..0 as XZ's y runs down)",
          b is not None and np.allclose(b[:, 0], [8, 22], atol=1e-2), f"{b}")
    b = select(("feature", board))
    check("the import: its bodies as read (x 0..24: the substrate and the pieces)",
          b is not None and np.isclose(b[0][0], 0, atol=1e-6) and np.isclose(b[1][0], 24, atol=1e-6), f"{b}")
    select(("body", "P"))
    check("a body row: no highlight", window.view.highlight_body is None and not window.view.highlight.visible)
    select(("feature", "S1"))
    check("a sketch row: no highlight", window.view.highlight_body is None)

    window.part.add_transform(Move("Up", targets=["P"], offset=["0", "0", "20"]))
    window._commit()
    settle()
    b = select(("feature", "Plate"))
    check("P moved up by 20: Plate's highlight moves along (z 20..22)",
          b is not None and np.allclose(b[:, 2], [20, 22], atol=1e-6), f"{b}")
    b = select(("feature", "Hole"))
    check("  the cut's too", b is not None and np.allclose(b[:, 2], [20, 22], atol=1e-6), f"{b}")
    window.part.parameters  # a change: a commit keeps the highlight on the selected feature
    window.part.add_parameter("k", "1")
    window._commit()
    settle()
    check("a change (commit): Hole still highlighted", window.view.highlight_body is not None
          and np.allclose(bounds(window)[:, 0], [4, 6], atol=1e-6))
    check("no error dialogs", not errors, f"{errors}")
    window.close()
    check("no errors in VisPy callbacks", not vispy_errors, f"{vispy_errors[:3]}")
    print("FAILED: " + ", ".join(FAILED) if FAILED else "all checks passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
