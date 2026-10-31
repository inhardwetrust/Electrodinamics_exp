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
#     kept, saved (a new mesh: the auto padding shrinks); frequencies outside the band may be typed
#     while off, then ticking it on is refused (the tick goes back);
#   - the auto padding: λ/4 at the band's center, λ/2 at the lowest
#     far-field frequency with the far field on, an explicit one as it is;
#     the fingerprint changes with it only then (far field on, padding 0);
#     in the window: ticking the far field on with auto padding makes a new
#     mesh and the "padding now" row follows; with an explicit padding no
#     new mesh and no such row.
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
        window.wait_for_mesh()
        check("  saved; a new mesh (auto padding: λ/2 -> λ/4)", Project.load(step).settings["farfield"]["enabled"]
              is False and window.mesh is not mesh)
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

        # ---- the auto padding follows the far field
        from project.settings import auto_padding, padding_m
        from solver.result_file import fingerprint
        type_in("setting", "farfield", "frequencies", "5.5, 6")
        settings = window.project.settings
        lam4 = 299_792_458.0 / 5.75e9 / 4
        check("auto, far field off: λ/4 at the band's center", abs(padding_m(settings) - lam4) < 1e-12,
              auto_padding(settings)[1])
        off_print = fingerprint(window.project)
        type_in("setting", "farfield", "frequencies", "5.6, 6")
        check("  far field off: its frequencies do not change the fingerprint", fingerprint(window.project) == off_print)

        def padding_row():
            section = window._tree_item("section", "simulation")
            rows = [section.child(i) for i in range(section.childCount())]
            return next((r.text(0) for r in rows if r.data(0, Qt.UserRole) == ("note", "padding", None)), None)

        check("  the padding now row: λ/4", (padding_row() or "").startswith("padding now: auto 13.0 mm (λ/4"),
              str(padding_row()))
        window.wait_for_mesh()
        mesh = window.mesh
        row("setting", "farfield", "enabled").setCheckState(1, Qt.Checked)
        window.wait_for_mesh()
        for _ in range(5):
            app.processEvents()
        settings = window.project.settings
        lam2 = 299_792_458.0 / 5.6e9 / 2
        check("far field on: λ/2 at its lowest frequency 5.6 GHz", settings["farfield"]["enabled"]
              and abs(padding_m(settings) - lam2) < 1e-12, auto_padding(settings)[1])
        check("  a new mesh (another domain)", window.mesh is not mesh)
        check("  the padding now row follows", (padding_row() or "").startswith("padding now: auto 26.8 mm (λ/2 at 5.6"),
              str(padding_row()))
        on_print = fingerprint(window.project)
        check("  the fingerprint changes (padding 0, far field on)", on_print != off_print)
        type_in("setting", "farfield", "frequencies", "5.8, 6")
        check("  the lowest far-field frequency moves it: another fingerprint", fingerprint(window.project) != on_print)

        type_in("setting", "simulation", "padding", "20")
        window.wait_for_mesh()
        mesh = window.mesh
        explicit_on = fingerprint(window.project)
        row("setting", "farfield", "enabled").setCheckState(1, Qt.Unchecked)
        window.wait_for_mesh()
        for _ in range(5):
            app.processEvents()
        check("an explicit padding: as it is, no padding now row", abs(padding_m(window.project.settings) - 0.02) < 1e-12
              and padding_row() is None, str(padding_row()))
        check("  the far field off: no new mesh, the same fingerprint", window.mesh is mesh
              and fingerprint(window.project) == explicit_on)
    finally:
        window.close()
        app.processEvents()
        shutil.rmtree(folder, ignore_errors=True)

    print("\nall ok" if not FAILED else f"\nFAILED: {FAILED}")
    sys.exit(1 if FAILED else 0)


if __name__ == "__main__":
    main()
