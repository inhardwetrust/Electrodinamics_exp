# check_import.py
#
# The Import feature (model/importstep.py, step_io.read_bodies). Its source:
# a STEP we write ourselves (export_part): Substrate 10 x 10 x 1 (green),
# Copper 4 x 4 x 0.5 on it (gold), Pieces: two 1 x 1 x 1 cubes apart (one
# body of two solids).
#   - imported: three bodies "Board:<name>" in the file's order, the same
#     volumes, bounds and colors; Pieces one body of two solids;
#   - an extrusion joined to an imported body: the volumes add;
#   - renaming an imported body renames it in the import's list; the TOML
#     round trip;
#   - the file changed since (another content, as many bodies): built from
#     it, the feature marked failed ("changed"); another number of bodies:
#     it fails; no file: it fails;
#   - the report (CLI) lists the import and its bodies;
#   - the window: Import without a saved part asks to save first; import:
#     the file copied into <name>.files, the feature, its bodies; the copy
#     changed: marked, Reload clears it; Save As elsewhere: the file goes
#     along (<new name>.files), the part rebuilds there.
#
#   python checks/check_import.py [--no-window]

import io
import math
import os
import shutil
import sys
import tempfile
import tomllib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "checks"))
sys.stdout.reconfigure(encoding="utf-8")
TMP = tempfile.mkdtemp()
os.environ["MYCAD_SETTINGS"] = os.path.join(TMP, "settings.json")     # never the user's settings

import numpy as np                                                    # noqa: E402

from geometry import kernel                                           # noqa: E402
from geometry.step_io import export_part, file_sha256, read_bodies   # noqa: E402
from model.extrude import Extrude                                     # noqa: E402
from model.importstep import Import, body_names                       # noqa: E402
from model.part import Part                                           # noqa: E402
from model.part_file import dumps, from_data                          # noqa: E402
from model.rebuild import rebuild                                     # noqa: E402
from model.sketch import Sketch                                       # noqa: E402

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


def source_part(size=10):
    part = Part()
    rectangle(part, "S1", "XY Plane", 0, 0, size, 10)
    part.add_feature(Extrude("E1", profiles=[RECT], sketch="S1", distance="1", body="Substrate"))
    rectangle(part, "S2", "E1:end", 3, 3, 4, 4)
    part.add_feature(Extrude("E2", profiles=[RECT], sketch="S2", distance="0.5", body="Copper"))
    s = rectangle(part, "S3", "XY Plane", 20, 0, 1, 1)
    p = [s.add_point(x, y) for x, y in (("23", "0"), ("24", "0"), ("24", "1"), ("23", "1"))]
    for a, b in zip(p, p[1:] + p[:1]):
        s.add_line(a, b)
    part.add_feature(Extrude("E3", profiles=[RECT, ("L5", "L6", "L7", "L8")], sketch="S3", distance="1",
                             body="Pieces"))
    part.set_body_color("Substrate", "#208040")
    part.set_body_color("Copper", "#d4a017")
    return part


def write_source(path, size=10):
    part = source_part(size)
    result = rebuild(part)
    export_part(path, part, result)
    return part, result


def import_part(folder, step="board.files/board.step"):
    read = read_bodies(os.path.join(folder, step))
    part = Part()
    part.add_feature(Import("Board", file=step, sha256=file_sha256(os.path.join(folder, step)),
                            bodies=body_names("Board", read)))
    return part


def check_model():
    folder = os.path.join(TMP, "model")
    os.makedirs(os.path.join(folder, "board.files"))
    step = os.path.join(folder, "board.files", "board.step")
    source, built = write_source(step)
    part = import_part(folder)
    result = rebuild(part, folder=folder)
    names = part.feature("Board").bodies
    same = all(math.isclose(kernel.volume(result.shapes[f"Board:{n}"]), kernel.volume(built.shapes[n]), rel_tol=1e-9)
               and np.allclose(kernel.bounds(result.shapes[f"Board:{n}"]), kernel.bounds(built.shapes[n]), atol=1e-6)
               for n in ("Substrate", "Copper", "Pieces"))
    check("imported: three bodies named Board:<name> in the file's order, the same volumes and bounds",
          names == ["Board:Substrate", "Board:Copper", "Board:Pieces"] and not result.feature_errors and same,
          f"{names} {result.feature_errors}")
    colors = result.colors
    check("  the file's colors, Pieces one body of two solids",
          np.allclose(colors["Board:Substrate"], (0x20 / 255, 0x80 / 255, 0x40 / 255), atol=2e-3)
          and np.allclose(colors["Board:Copper"], (0xd4 / 255, 0xa0 / 255, 0x17 / 255), atol=2e-3)
          and kernel.solid_count(result.shapes["Board:Pieces"]) == 2, f"{colors}")

    rectangle(part, "S", "XY Plane", 0, 0, 2, 2)
    part.add_feature(Extrude("E", profiles=[RECT], sketch="S", distance="-1", operation="join",
                             body="Board:Substrate"))
    result = rebuild(part, folder=folder)
    check("an extrusion joined to an imported body: 100 + 4",
          math.isclose(kernel.volume(result.shapes["Board:Substrate"]), 104, rel_tol=1e-9), f"{result.feature_errors}")
    part.rename_body("Board:Copper", "Patch")
    text = dumps(part)
    check("renaming an imported body renames it in the import's list; TOML round trip",
          part.feature("Board").bodies == ["Board:Substrate", "Patch", "Board:Pieces"]
          and "Patch" in rebuild(part, folder=folder).shapes
          and from_data(tomllib.loads(text)).to_dict() == part.to_dict())

    write_source(step, size=12)                 # another content, three bodies still
    result = rebuild(part, folder=folder)
    check("the file changed (as many bodies): built from it, marked failed", "changed" in
          result.feature_errors.get("Board", "") and math.isclose(
              kernel.volume(result.shapes["Board:Substrate"]), 120 + 4, rel_tol=1e-9), f"{result.feature_errors}")
    part.feature("Board").bodies.append("Board:Extra")
    error = rebuild(part, folder=folder).feature_errors.get("Board", "")
    check("  the part names another number of bodies: it fails", "has 3 bodies" in error, error)
    part.feature("Board").bodies.pop()
    part.feature("Board").values["file"] = "board.files/gone.step"
    error = rebuild(part, folder=folder).feature_errors.get("Board", "")
    check("  no file: it fails", "no file" in error, error)

    import cli

    part = import_part(folder)
    path = os.path.join(folder, "board.part.toml")
    with open(path, "w", encoding="utf-8") as f:
        f.write(dumps(part))
    out = io.StringIO()
    code = cli.run(path, True, False, None, out)
    check("the report: the import, its file and bodies; ok", code == 0
          and "board.files/board.step: Board:Substrate, Board:Copper, Board:Pieces" in out.getvalue(),
          out.getvalue()[-300:])


def check_window():
    from vispy import app

    app.use_app("pyside6")
    app.use_app().create()
    from vispy_errors import install as _watch_vispy

    vispy_errors = _watch_vispy()
    from check_sketch_line import settle
    from view.main_window import MainWindow

    source = os.path.join(TMP, "sources", "sma connector.step")
    os.makedirs(os.path.dirname(source))
    write_source(source)
    window = MainWindow()
    window.ask_save_changes = lambda: "discard"
    errors = []
    window.show_error = errors.append
    window.resize(1200, 800)
    window.show()
    settle(300)
    window.ask_import_path = lambda: source
    window.import_dialog()
    check("Import without a saved part: asks to save first", errors and "Save the part first" in errors[-1]
          and not window.part.features)
    errors.clear()
    folder = os.path.join(TMP, "win")
    os.makedirs(folder)
    window._write(os.path.join(folder, "board.part.toml"))
    name = window.import_dialog()
    feature = window.part.feature(name) if name else None
    copied = os.path.join(folder, "board.files", "sma connector.step")
    check("imported: the file copied into board.files, the feature named after it, its bodies",
          name == "sma_connector" and os.path.isfile(copied) and feature["file"] == "board.files/sma connector.step"
          and [b.name for b in window.result.bodies] == feature.bodies and len(feature.bodies) == 3,
          f"{name} {feature.to_dict() if feature else None}")
    check("  in the browser: its bodies under Solid Bodies", all(("body", b) in window._rows for b in feature.bodies))
    write_source(copied, size=12)
    window._refresh()
    check("the copy changed: marked failed", "changed" in window.result.feature_errors.get(name, ""))
    window.reload_import(name)
    check("  Reload: accepted (no error, the new sha256)", name not in window.result.feature_errors
          and window.part.feature(name)["sha256"] == file_sha256(copied))
    other = os.path.join(TMP, "elsewhere")
    os.makedirs(other)
    window._write(os.path.join(other, "renamed.part.toml"))
    moved = os.path.join(other, "renamed.files", "sma connector.step")
    check("Save As elsewhere: the file goes along (renamed.files), the part rebuilds there",
          os.path.isfile(moved) and window.part.feature(name)["file"] == "renamed.files/sma connector.step"
          and not window.result.feature_errors and len(window.result.bodies) == 3)
    check("no error dialogs", not errors, f"{errors}")
    window.close()
    check("no errors in VisPy callbacks", not vispy_errors, f"{vispy_errors[:3]}")


def main():
    check_model()
    if "--no-window" not in sys.argv:
        check_window()
    shutil.rmtree(TMP, ignore_errors=True)
    print("FAILED: " + ", ".join(FAILED) if FAILED else "all checks passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
