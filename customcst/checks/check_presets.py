# check_presets.py
#
# Mesh and monitors presets (project/presets.py, view/presets_panel.py), on
# a model written into a temp folder:
#   - files: model.presets/<kind>/<Name>.toml; a round trip; built-ins
#     first, then the model's; a file that does not read is skipped and
#     reported; unknown keys and bad values refused; names: no built-in's,
#     no duplicate, no path characters; rename, delete; a built-in cannot
#     be changed;
#   - apply: the mesh values copied; the monitors' offsets -> frequencies
#     from the far field's f0 (or the band's center); a frequency outside
#     the band: refused, the project unchanged; f0 outside the band refused;
#     from_project gives the offsets back; matching names the preset;
#   - the window: the Presets tab lists them; Save current writes a file;
#     editing a preset there changes no project (a template); Apply copies
#     it in and the tree's row says "preset: <name>"; a file copied in from
#     outside shows when the tab is opened; the control command `preset`.
#
#   python checks/check_presets.py

import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

from checks.check_disk_reload import write_model, write_types           # noqa: E402

FAILED = []


def check(label, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {label} {detail}")
    if not ok:
        FAILED.append(label)


def raises(action, text=""):
    from project.presets import PresetError
    try:
        action()
    except (PresetError, ValueError) as error:
        return text in str(error)
    return False


def module_checks(folder):
    from project import presets
    from project.project_file import Project

    step = os.path.join(folder, "model.step")
    names = [n for n, _, _ in presets.all_presets(step, "mesh")]
    check("built-in mesh presets, no files yet", names == ["Draft", "Sweep", "Fine", "Final"], str(names))
    final = presets.builtin("mesh")["Final"]
    check("Final: 50 / 12, the rest defaults", final["cells_per_wavelength"] == 50 and final["cells_per_feature"] == 12
          and final["max_ratio"] == 1.3 and final["slanted"] is True, str(final))

    values = dict(final, cells_per_wavelength=45.0, slanted=False)
    path = presets.save_preset(step, "mesh", "Mine", values)
    check("a file per preset: model.presets/mesh/Mine.toml",
          path == os.path.join(folder, "model.presets", "mesh", "Mine.toml") and os.path.isfile(path))
    own, errors = presets.load_presets(step, "mesh")
    check("round trip", own == {"Mine": values} and not errors, str(own))
    check("listed after the built-ins", [n for n, _, s in presets.all_presets(step, "mesh")][-1] == "Mine")

    with open(os.path.join(folder, "model.presets", "mesh", "Broken.toml"), "w") as f:
        f.write("cells_per_wavelength = [\n")
    with open(os.path.join(folder, "model.presets", "mesh", "Odd.toml"), "w") as f:
        f.write("cells_per_wavelength = 30.0\ncolour = 1\n")
    own, errors = presets.load_presets(step, "mesh")
    check("a broken file and one with an unknown key: skipped, reported", list(own) == ["Mine"]
          and len(errors) == 2 and any("unknown keys" in e for e in errors), str(errors))
    os.remove(os.path.join(folder, "model.presets", "mesh", "Broken.toml"))
    os.remove(os.path.join(folder, "model.presets", "mesh", "Odd.toml"))
    check("a bad value refused", raises(lambda: presets.validate("mesh", {"cells_per_wavelength": 1}),
                                         "cells"))
    check("names: a built-in's, a duplicate, a path refused",
          raises(lambda: presets.check_name(step, "mesh", "Final"), "built-in")
          and raises(lambda: presets.check_name(step, "mesh", "Mine"), "exists")
          and raises(lambda: presets.check_name(step, "mesh", "a/b"), "name"))
    check("a built-in cannot be changed", raises(lambda: presets.set_value(step, "mesh", "Final",
                                                                            "max_ratio", 1.2), "built-in"))
    presets.set_value(step, "mesh", "Mine", "max_ratio", "1.2")
    check("set_value writes the file", presets.load_presets(step, "mesh")[0]["Mine"]["max_ratio"] == 1.2)
    presets.rename_preset(step, "mesh", "Mine", "Coarse")
    check("rename renames the file", os.path.isfile(os.path.join(folder, "model.presets", "mesh", "Coarse.toml"))
          and "Mine" not in presets.load_presets(step, "mesh")[0])

    project = Project.load(step)
    project.set_setting("simulation", "f_min", 4.5)
    project.set_setting("simulation", "f_max", 7.0)
    presets.apply(project, "mesh", presets.builtin("mesh")["Fine"])
    check("apply mesh: the values copied", project.settings["mesh"]["cells_per_wavelength"] == 40
          and project.settings["mesh"]["cells_per_feature"] == 8)
    check("matching: Fine", presets.matching(project, "mesh", presets.all_presets(step, "mesh")) == "Fine")
    project.set_setting("mesh", "max_ratio", 1.25)
    check("a value changed by hand: no preset matches",
          presets.matching(project, "mesh", presets.all_presets(step, "mesh")) is None)

    spread = presets.builtin("monitors")["f0 ±2 %"]
    presets.apply(project, "monitors", spread)
    check("monitors without f0: around the band's center 5.75",
          project.settings["farfield"]["frequencies"] == "5.635, 5.75, 5.865",
          project.settings["farfield"]["frequencies"])
    project.set_setting("farfield", "f0", 5.757)
    presets.apply(project, "monitors", spread)
    check("with f0 5.757: 5.6419, 5.757, 5.8721", project.settings["farfield"]["frequencies"]
          == "5.6419, 5.757, 5.8721", project.settings["farfield"]["frequencies"])
    project.set_setting("farfield", "enabled", False)
    presets.apply(project, "monitors", spread)
    check("a monitors preset switches the far field on", project.settings["farfield"]["enabled"] is True)
    check("matching names the monitors preset",
          presets.matching(project, "monitors", presets.all_presets(step, "monitors")) == "f0 ±2 %")
    back = presets.from_project(project, "monitors")
    check("from_project gives the offsets back", back["offsets"] == "-2, 0, 2", back["offsets"])
    before = dict(project.settings["farfield"])
    check("a frequency outside the band: refused", raises(lambda: presets.apply(
        project, "monitors", presets.validate("monitors", {"offsets": "0, 50"})), "outside"))
    check("  the project unchanged", project.settings["farfield"] == before)
    check("f0 outside the band refused", raises(lambda: project.set_setting("farfield", "f0", 9.0), "outside"))
    project.save()
    check("f0 in the .toml", Project.load(step).settings["farfield"]["f0"] == 5.757)


def window_checks(folder):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QTreeWidgetItemIterator

    from project import presets
    from project.project_file import Project
    from view import control
    from view.main_window import MainWindow

    app = QApplication.instance() or QApplication(sys.argv)
    step = os.path.join(folder, "model.step")
    window = MainWindow(step)
    window._disk_timer.stop()
    window.show()
    window.wait_for_mesh()
    panel = window.presets_panel

    def names(kind):
        table = panel.tables[kind]
        return [table.item(r, 0).text() for r in range(table.rowCount())]

    def tree_notes():
        out, it = [], QTreeWidgetItemIterator(window.tree)
        while it.value():
            if it.value().text(0).startswith("preset:"):
                out.append(it.value().text(0))
            it += 1
        return out

    try:
        check("the Presets tab", window.side_tabs.indexOf(panel) >= 0)
        check("  mesh: built-ins and the model's", names("mesh") == ["Draft", "Sweep", "Fine", "Final", "Coarse"],
              str(names("mesh")))
        check("  monitors: built-ins", names("monitors") == ["f0", "f0 ±2 %"], str(names("monitors")))

        name = panel.save_current("mesh")
        app.processEvents()
        own = presets.load_presets(step, "mesh")[0]
        check("Save current: a file with the project's mesh", name in own
              and own[name]["max_ratio"] == window.project.settings["mesh"]["max_ratio"], str(name))

        mesh_before = dict(window.project.settings["mesh"])
        table = panel.tables["mesh"]
        row = names("mesh").index("Coarse")
        table.item(row, 1).setText("33")          # cells per wavelength of Coarse
        app.processEvents()
        check("editing a preset writes its file", presets.load_presets(step, "mesh")[0]["Coarse"]
              ["cells_per_wavelength"] == 33.0)
        check("  and changes no project (a template)", window.project.settings["mesh"] == mesh_before
              and Project.load(step).settings["mesh"] == mesh_before)

        error = window.apply_preset("mesh", "Coarse")
        window.wait_for_mesh()
        for _ in range(5):
            app.processEvents()
        check("Apply: copied into the project and its file", error is None
              and window.project.settings["mesh"]["cells_per_wavelength"] == 33.0
              and Project.load(step).settings["mesh"]["cells_per_wavelength"] == 33.0)
        check("  the tree says preset: Coarse", any(n.startswith("preset: Coarse") for n in tree_notes()),
              str(tree_notes()))

        error = window.apply_preset("monitors", "f0 ±2 %")
        for _ in range(5):
            app.processEvents()
        check("monitors preset applied: the far-field frequencies",
              error is None and window.project.settings["farfield"]["frequencies"] == "5.6419, 5.757, 5.8721",
              window.project.settings["farfield"]["frequencies"])
        check("  the tree's Far field says preset: f0 ±2 %",
              any(n.startswith("preset: f0 ±2 %") for n in tree_notes()), str(tree_notes()))
        check("an unknown preset: an error", window.apply_preset("mesh", "Nope") is not None)

        shutil.copy(os.path.join(folder, "model.presets", "mesh", "Coarse.toml"),
                    os.path.join(folder, "model.presets", "mesh", "Copied.toml"))
        window.side_tabs.setCurrentIndex(0)
        window.side_tabs.setCurrentWidget(panel)
        app.processEvents()
        check("a file copied in shows when the tab is opened", "Copied" in names("mesh"), str(names("mesh")))

        window.set_locked(True)
        answer = window.control.answer(f'{{"token": "{window.control.token}", "command": "preset", '
                                       f'"args": {{"kind": "mesh", "name": "Final"}}}}'.encode())
        window.wait_for_mesh()
        check("control: preset kind=mesh name=Final", answer["ok"]
              and window.project.settings["mesh"]["cells_per_wavelength"] == 50.0, str(answer)[:200])
        window.set_locked(False)
        check("  in describe", "preset" in control.describe(window)["commands"])
    finally:
        window.close()
        app.processEvents()


def main():
    folder = tempfile.mkdtemp()
    os.environ["CUSTOMCST_SETTINGS"] = os.path.join(folder, "settings.json")
    os.environ["CUSTOMCST_REMOTE"] = os.path.join(folder, "remote.toml")
    step = os.path.join(folder, "model.step")
    write_model(step)
    write_types(step, {"board": "dielectric", "patch": "pec"})
    try:
        module_checks(folder)
        window_checks(folder)
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    print("\nall ok" if not FAILED else f"\nFAILED: {FAILED}")
    sys.exit(1 if FAILED else 0)


if __name__ == "__main__":
    main()
