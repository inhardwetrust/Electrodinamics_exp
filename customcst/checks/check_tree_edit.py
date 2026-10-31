# check_tree_edit.py
#
# Values typed into the Project tree (MainWindow._item_changed), on a
# model written into a temp folder:
#   - a decimal comma in a number: "4,5" GHz -> 4.5;
#   - a list of numbers (Far field > frequencies): commas, spaces or
#     semicolons separate, all values kept, sorted, in the .toml;
#   - a list with a value outside f min .. f max: refused, the old value
#     shown again;
#   - a body parameter with a decimal comma (eps_r "4,4" -> 4.4);
#   - Far field > on: unticked, no far field is computed, the frequencies
#     kept, saved, no new mesh; frequencies outside the band may be typed
#     while off, then ticking it on is refused (the tick goes back).
#
#   python checks/check_tree_edit.py

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


def main():
    folder = tempfile.mkdtemp()
    os.environ["CUSTOMCST_SETTINGS"] = os.path.join(folder, "settings.json")
    os.environ["CUSTOMCST_REMOTE"] = os.path.join(folder, "remote.toml")
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QTreeWidgetItemIterator

    from project.project_file import Project
    from view.main_window import MainWindow

    app = QApplication.instance() or QApplication(sys.argv)
    step = os.path.join(folder, "model.step")
    write_model(step)
    write_types(step, {"board": "dielectric", "patch": "pec"})

    window = MainWindow(step)
    window._disk_timer.stop()
    window.show()
    window.wait_for_mesh()

    def row(role, owner, param):
        it = QTreeWidgetItemIterator(window.tree)
        while it.value():
            item = it.value()
            if item.data(0, Qt.UserRole) == (role, owner, param):
                return item
            it += 1
        return None

    def type_in(role, owner, param, text):
        """As if typed into the value column: the item's text, then the change handler."""
        item = row(role, owner, param)
        item.setText(1, text)               # emits itemChanged -> _item_changed
        window.wait_for_mesh()
        for _ in range(5):
            app.processEvents()
        return row(role, owner, param)      # the tree may have been rebuilt

    try:
        item = type_in("setting", "simulation", "f_min", "4,5")
        check("a decimal comma: f min 4,5 -> 4.5", window.project.settings["simulation"]["f_min"] == 4.5,
              item.text(1))
        type_in("setting", "simulation", "f_max", "7")

        item = type_in("setting", "farfield", "frequencies", "5.9, 5.6, 5.757")
        stored = window.project.settings["farfield"]["frequencies"]
        check("frequencies with commas: all kept, sorted", stored == "5.6, 5.757, 5.9", stored)
        check("  shown as stored", item is not None and item.text(1).startswith("5.6, 5.757, 5.9"),
              item.text(1) if item else "no row")
        check("  in the .toml", Project.load(step).settings["farfield"]["frequencies"] == "5.6, 5.757, 5.9")

        type_in("setting", "farfield", "frequencies", "5.7 5.8;6")
        stored = window.project.settings["farfield"]["frequencies"]
        check("spaces and semicolons separate too", stored == "5.7, 5.8, 6", stored)

        item = type_in("setting", "farfield", "frequencies", "5.7, 9")
        stored = window.project.settings["farfield"]["frequencies"]
        check("a frequency outside the band: refused, the old value back", stored == "5.7, 5.8, 6"
              and "outside" in window.statusBar().currentMessage(), window.statusBar().currentMessage())

        type_in("param", "board", "eps_r", "4,4")
        check("a body parameter with a decimal comma: eps_r 4,4 -> 4.4",
              window.project.get("board").values["eps_r"] == 4.4)

        from project.settings import farfield_frequencies
        window.wait_for_mesh()
        mesh = window.mesh
        item = row("setting", "farfield", "enabled")
        item.setCheckState(1, Qt.Unchecked)
        for _ in range(5):
            app.processEvents()
        settings = window.project.settings
        check("far field off: none computed, the frequencies kept", not settings["farfield"]["enabled"]
              and farfield_frequencies(settings) == [] and settings["farfield"]["frequencies"] == "5.7, 5.8, 6")
        check("  saved, no new mesh", Project.load(step).settings["farfield"]["enabled"] is False
              and window.mesh is mesh and not window.mesh_busy())
        type_in("setting", "farfield", "frequencies", "5.7, 9")
        check("off: frequencies outside the band may be typed",
              window.project.settings["farfield"]["frequencies"] == "5.7, 9")
        row("setting", "farfield", "enabled").setCheckState(1, Qt.Checked)
        for _ in range(5):
            app.processEvents()
        item = row("setting", "farfield", "enabled")
        check("  then on is refused, the tick goes back", not window.project.settings["farfield"]["enabled"]
              and item.checkState(1) == Qt.Unchecked and "outside" in window.statusBar().currentMessage(),
              window.statusBar().currentMessage())
    finally:
        window.close()
        app.processEvents()
        shutil.rmtree(folder, ignore_errors=True)

    print("\nall ok" if not FAILED else f"\nFAILED: {FAILED}")
    sys.exit(1 if FAILED else 0)


if __name__ == "__main__":
    main()
