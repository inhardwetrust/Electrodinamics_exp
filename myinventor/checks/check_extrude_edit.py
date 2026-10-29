# check_extrude_edit.py
#
# The Extrude command's preview, and editing an extrusion. The sketch: a
# 4 x 5 rectangle with a circle of r = 1 inside (a ring and a disc).
#   - preview (a new extrusion): none without a profile; the ring at 10 mm
#     shows a see-through body of (20 - pi) * 10 mm³ (from its triangles); a
#     new distance updates it; a bad distance hides it; the disc added makes
#     it 200 mm³; Cancel leaves no preview, no feature;
#   - edit (double click on Extrusion1 in the browser): the panel says Edit
#     Extrusion1, holds the distance formula, the ring taken; rolled back
#     (no Solid1 before it),
#     the preview in its place; only regions of its own sketch; the distance
#     set to a parameter and the disc added: Apply changes the feature (one
#     undo step), Solid1 = 20 * h, shown again, no preview; undo brings the
#     old extrusion back;
#   - Edit Extrusion from the right-click menu, then Cancel (Esc): nothing
#     changes, the body shows again.
#
#   python checks/check_extrude_edit.py

import math
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "checks"))
sys.stdout.reconfigure(encoding="utf-8")

TMP = tempfile.mkdtemp()
os.environ["MYCAD_SETTINGS"] = os.path.join(TMP, "settings.json")     # never the user's settings

from vispy import app                                                 # noqa: E402

app.use_app("pyside6")
app.use_app().create()

from vispy_errors import install as _watch_vispy                      # noqa: E402

VISPY_ERRORS = _watch_vispy()

from PySide6.QtCore import Qt                                        # noqa: E402
from PySide6.QtTest import QTest                                      # noqa: E402

from check_extrude import rect                                        # noqa: E402
from geometry import kernel                                           # noqa: E402
from check_sketch_line import click, pixel, settle, shield           # noqa: E402
from view.main_window import MainWindow                               # noqa: E402

FAILED = []
MM = 1e-3
RING = ("profile", "Sketch1", ("L1", "L2", "L3", "L4"))
DISC = ("profile", "Sketch1", ("C1",))


def check(label, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {label} {detail}")
    if not ok:
        FAILED.append(label)


def exact(window):
    """Extrusion1's exact volume (OCP, mm^3): the triangles of a round hole fall short of it."""
    return kernel.volume(window.result.outputs["Extrusion1"].shape)


def preview_volume(view):
    body = view.preview_body
    return None if body is None or not view.preview.visible else body.volume / MM ** 3


def main():
    window = MainWindow()
    window.ask_save_changes = lambda: "discard"
    errors = []
    window.show_error = errors.append
    window.resize(1200, 800)
    window.show()
    settle(300)
    shield(window)
    view = window.view
    native = view.canvas.native
    window.part.add_parameter("h", "7")
    window.new_sketch("XY Plane")
    s = window.part.feature("Sketch1")
    rect(s, 0, 0, 4, 5)
    s.add_circle(s.add_point("2", "2.5"), "1")
    window._commit()
    frame = view.sketch_frame
    ring = (20 - math.pi)

    # --- preview of a new extrusion
    window.extrude_action.trigger()
    settle()
    panel = window.extrude_panel
    check("no profile: no preview", preview_volume(view) is None)
    click(native, pixel(view, frame, (0.5, 0.5)))
    v = preview_volume(view)
    check("the ring at 10 mm: a preview of (20 - pi) * 10", v is not None and math.isclose(v, ring * 10, rel_tol=1e-3),
          f"{v}")
    panel.distance.setText("2.5")
    v = preview_volume(view)
    check("a new distance updates it", v is not None and math.isclose(v, ring * 2.5, rel_tol=1e-3), f"{v}")
    panel.distance.setText("2 +")
    check("a bad distance hides it", preview_volume(view) is None)
    panel.distance.setText("10")
    click(native, pixel(view, frame, (2, 2.5)))
    v = preview_volume(view)
    check("the disc added: 200", v is not None and math.isclose(v, 200.0, rel_tol=1e-3), f"{v}")
    QTest.keyClick(native, Qt.Key_Escape)
    settle()
    check("Cancel: no preview, no feature", preview_volume(view) is None and len(window.part.features) == 1)

    # --- a first extrusion: the ring by 3
    window.extrude_action.trigger()
    click(native, pixel(view, frame, (0.5, 0.5)))
    window.extrude_panel.distance.setText("3")
    window.extrude_panel.apply_button.click()
    settle()
    check("Extrusion1: the ring by 3 (exact OCP volume)", math.isclose(exact(window), ring * 3, rel_tol=1e-9),
          f"{exact(window):.6f}")

    # --- edit it (double click)
    window._row_double_clicked(window._rows[("feature", "Extrusion1")])
    settle()
    panel = window.extrude_panel
    check("double click: the panel edits Extrusion1, with its distance and its ring",
          panel is not None and panel.windowTitle() == "Edit Extrusion1" and panel.distance.text() == "3"
          and view.profile_selection == [RING], f"{view.profile_selection}")
    check("  rolled back: no Solid1 yet (it is this feature's), the preview in its place",
          "Solid1" not in view._bodies and math.isclose(preview_volume(view) or 0, ring * 3, rel_tol=1e-3))
    check("  regions of its own sketch only", {p[0] for p in view._profiles} == {RING, DISC})
    panel.distance.setText("h")
    click(native, pixel(view, frame, (2, 2.5)))
    v = preview_volume(view)
    check("  the distance h (7) and the disc added: the preview 140", v is not None
          and math.isclose(v, 140.0, rel_tol=1e-3), f"{v}")
    steps = len(window.doc._undo)
    panel.apply_button.click()
    settle()
    feature = window.part.feature("Extrusion1")
    body = window.result.bodies[0]
    check("Apply changes the feature: profiles ring + disc, distance h",
          sorted(feature.profiles) == sorted([RING[2], DISC[2]]) and feature["distance"] == "h"
          and [f.name for f in window.part.features] == ["Sketch1", "Extrusion1"])
    check("  Solid1 = 20 * 7 = 140, shown again, no preview, one undo step",
          math.isclose(body.volume / MM ** 3, 140.0, rel_tol=1e-6) and view._bodies["Solid1"].mesh.visible
          and preview_volume(view) is None and len(window.doc._undo) == steps + 1)
    window.undo()
    check("undo brings the old extrusion back (the ring by 3)",
          math.isclose(exact(window), ring * 3, rel_tol=1e-9)
          and window.part.feature("Extrusion1")["distance"] == "3")

    # --- Edit Extrusion from the menu, then Cancel
    actions = dict(window.context_actions(window._rows[("feature", "Extrusion1")]))
    check("the menu: Edit Extrusion, Delete", list(actions) == ["Edit Extrusion", "Delete"], f"{list(actions)}")
    actions["Edit Extrusion"]()
    settle()
    window.extrude_panel.distance.setText("50")
    QTest.keyClick(native, Qt.Key_Escape)
    settle()
    check("Cancel: nothing changes, the body shows again", window.part.feature("Extrusion1")["distance"] == "3"
          and view._bodies["Solid1"].mesh.visible and preview_volume(view) is None and window.extrude_panel is None)
    check("no error dialogs", not errors, f"{errors}")
    window.close()
    check("no errors in VisPy callbacks", not VISPY_ERRORS, f"{VISPY_ERRORS[:3]}")
    print("FAILED: " + ", ".join(FAILED) if FAILED else "all checks passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
