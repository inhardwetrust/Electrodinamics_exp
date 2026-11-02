# check_suppress.py
#
# A suppressed transform (its eye off in the browser's Placement): skipped
# everywhere, as if it were not there. The part: Block, 2 x 2 x 1 at the
# origin, in group G; Shift moves group G by (10, 0, 0), Lift moves Block by
# (0, 0, 5): a body after its group, which fails (the order rule).
#   - the rebuild: Lift fails while Shift is on; Shift suppressed: Lift is
#     fine (only what is applied counts), Block at x 0..2, z 5..6;
#   - the report: Shift "suppressed": true, no error, and the text says
#     "suppressed: not applied";
#   - the file: `suppressed = true` only on Shift; it reads back the same;
#   - the window: Shift's row has an eye, on; a click off: Block shown at
#     x 0..2, one undo step, modified; undo: on again, x 10..12; editing a
#     suppressed Move (Apply) keeps it suppressed.
#
#   python checks/check_suppress.py

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

from cli import report, text                                          # noqa: E402
from model.extrude import Extrude                                     # noqa: E402
from model.part import Part                                           # noqa: E402
from model.part_file import dumps, load                               # noqa: E402
from model.rebuild import rebuild                                     # noqa: E402
from model.sketch import Sketch                                       # noqa: E402
from model.transforms import Move                                     # noqa: E402

FAILED = []


def check(label, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {label} {detail}")
    if not ok:
        FAILED.append(label)


def the_part():
    part = Part()
    s = part.add_feature(Sketch("BlockSketch", plane="XY Plane"))
    p = [s.add_point(x, y) for x, y in (("0", "0"), ("2", "0"), ("2", "2"), ("0", "2"))]
    for a, b in zip(p, p[1:] + p[:1]):
        s.add_line(a, b)
    part.add_feature(Extrude("BlockExt", profiles=[("L1", "L2", "L3", "L4")], sketch="BlockSketch", distance="1",
                             body="Block"))
    part.add_to_group("G", ["Block"])
    part.transforms.append(Move("Shift", targets=["group:G"], offset=["10", "0", "0"]))
    part.transforms.append(Move("Lift", targets=["Block"], offset=["0", "0", "5"]))
    return part


def bounds(result):
    from geometry import kernel

    return np.round(kernel.bounds(result.shapes["Block"]), 9).tolist()


def model_checks():
    part = the_part()
    result = rebuild(part, tessellate=False)
    check("Shift on: Lift fails (a body after its group)", "Lift" in result.feature_errors
          and "Shift" not in result.feature_errors, str(result.feature_errors))
    part.transform("Shift").values["suppressed"] = True
    result = rebuild(part, tessellate=False)
    check("Shift suppressed: Lift fine, Block at x 0..2, z 5..6", not result.feature_errors
          and bounds(result) == [[0, 0, 5], [2, 2, 6]], f"{result.feature_errors} {bounds(result)}")
    data = report(part, result)
    rows = {t["name"]: t for t in data["transforms"]}
    check("the report: Shift suppressed, no error; the text says so", rows["Shift"]["suppressed"] is True
          and "error" not in rows["Shift"] and "suppressed: not applied" in text(data), str(rows["Shift"]))
    written = dumps(part)
    check("the file: suppressed = true on Shift only", written.count("suppressed") == 1
          and "suppressed = true" in written, written[written.find("[[transform]]"):][:300])
    copy = os.path.join(TMP, "back.part.toml")
    with open(copy, "w", encoding="utf-8") as f:
        f.write(written)
    back = load(copy)
    check("  it reads back the same", back.transform("Shift")["suppressed"] is True
          and back.transform("Lift")["suppressed"] is False)


def window_checks():
    from vispy import app

    app.use_app("pyside6")
    app.use_app().create()
    from vispy_errors import install as _watch_vispy

    vispy_errors = _watch_vispy()
    from PySide6.QtCore import Qt

    from check_sketch_line import settle, shield
    from view.main_window import MainWindow

    part = the_part()
    part.transforms.pop()                       # Shift only
    path = os.path.join(TMP, "suppress.part.toml")
    with open(path, "w", encoding="utf-8") as f:
        f.write(dumps(part))
    window = MainWindow()
    window.ask_save_changes = lambda: "discard"
    errors = []
    window.show_error = errors.append
    window.resize(1200, 800)
    window.show()
    shield(window)
    settle(200)
    window.open_file(path)
    settle()

    def shown_x():
        v = window.view._bodies["Block"].body.vertices / 1e-3
        return round(float(v[:, 0].min()), 6), round(float(v[:, 0].max()), 6)

    row = window._rows.get(("transform", "Shift"))
    check("the window: Shift's row has an eye, on; Block at x 10..12", row is not None
          and bool(row.flags() & Qt.ItemIsUserCheckable) and row.checkState(0) == Qt.Checked
          and shown_x() == (10.0, 12.0), str(shown_x()))
    steps = len(window.doc._undo)
    row.setCheckState(0, Qt.Unchecked)
    settle()
    check("  eye off: Block shown at x 0..2, one undo step, modified", shown_x() == (0.0, 2.0)
          and len(window.doc._undo) == steps + 1 and window.doc.modified
          and window.part.transform("Shift")["suppressed"], str(shown_x()))
    check("  the row stays, its eye off", window._rows[("transform", "Shift")].checkState(0) == Qt.Unchecked)
    window.start_move(edit="Shift")
    settle()
    window._apply_move()
    settle()
    check("editing the suppressed Move (Apply): still suppressed", window.part.transform("Shift")["suppressed"]
          and shown_x() == (0.0, 2.0), str(shown_x()))
    window.undo()
    window.undo()
    settle()
    check("undo: applied again, x 10..12, the eye on", shown_x() == (10.0, 12.0)
          and window._rows[("transform", "Shift")].checkState(0) == Qt.Checked, str(shown_x()))
    check("no error dialogs", not errors, str(errors))
    window.close()
    check("no errors in VisPy callbacks", not vispy_errors, f"{vispy_errors[:3]}")


def main():
    model_checks()
    window_checks()
    print("FAILED: " + ", ".join(FAILED) if FAILED else "all checks passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
