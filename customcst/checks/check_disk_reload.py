# check_disk_reload.py
#
# The window reloads a model that another program changed on disk
# (MainWindow.poll_disk: myCad exporting the STEP, Claude writing the
# .toml), on a model written into a temp folder:
#   - the .toml written outside: reloaded after it settled (one look sees
#     the change, the next one reloads), the new type in the window, the
#     camera as the user left it, a status message;
#   - the STEP and then the .toml written one after the other: one reload,
#     the new body there and typed;
#   - the window's own save: no reload;
#   - a .toml that does not read: the window stays as it was (no dialog,
#     the project kept), not retried until the file changes again; fixed,
#     it reloads;
#   - while a run goes on: no reload; after it, the reload comes;
#   - the timer itself: a change is picked up within a few DISK_POLL.
#
#   python checks/check_disk_reload.py

import os
import shutil
import sys
import tempfile
import time
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

FAILED = []


def check(label, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {label} {detail}")
    if not ok:
        FAILED.append(label)


def write_model(step, stub=False):
    from geometry.step_io import write_step
    from tools.make_test_models import box

    bodies = [("board", box(-10, -10, 0, 10, 10, 1.6), (0.2, 0.6, 0.2)),
              ("patch", box(-5, -5, 1.6, 5, 5, 1.635), (0.9, 0.6, 0.2))]
    if stub:
        bodies.append(("stub", box(5, -1, 1.6, 8, 1, 1.635), (0.9, 0.6, 0.2)))
    write_step(step, bodies)


def write_types(step, types):
    from geometry.step_io import read_step
    from project.project_file import Project

    bodies = {b.name: b for b in read_step(step).bodies}
    project = Project.load(step)
    for name, type_name in types.items():
        project.set_type(bodies[name], type_name)
    project.save()


def main():
    folder = tempfile.mkdtemp()
    os.environ["CUSTOMCST_SETTINGS"] = os.path.join(folder, "settings.json")
    os.environ["CUSTOMCST_REMOTE"] = os.path.join(folder, "remote.toml")
    from PySide6.QtWidgets import QApplication

    from view.main_window import DISK_POLL, MainWindow

    app = QApplication.instance() or QApplication(sys.argv)
    step = os.path.join(folder, "model.step")
    toml = os.path.join(folder, "model.toml")
    write_model(step)
    write_types(step, {"board": "dielectric"})

    window = MainWindow(step)
    window._disk_timer.stop()           # the looks are taken by hand below, the timer at the end
    window.show()
    window.wait_for_mesh()
    loads = []
    show_loaded = window._show_loaded
    window._show_loaded = lambda *args: (loads.append(args[0]), show_loaded(*args))

    def look(times=1):
        for _ in range(times):
            window.poll_disk()
            window.wait_for_mesh()
            app.processEvents()

    try:
        check("opened: patch without type", window.project.get("patch") is None)

        # ---- the .toml written outside
        camera = window.view.view.camera
        camera.set_state([1.0, 2.0, 0.5], 33.0, camera.state()["rotation"])
        app.processEvents()
        before = camera.state()
        write_types(step, {"patch": "pec"})
        look()
        check("one look after the change: not reloaded yet", not loads)
        look()
        assignment = window.project.get("patch")
        check("next look: reloaded, the patch is pec", loads == [step] and assignment is not None
              and assignment.type == "pec", str(loads))
        after = camera.state()
        check("the camera as the user left it", abs(after["distance"] - before["distance"]) < 1e-9
              and all(abs(a - b) < 1e-9 for a, b in zip(after["center"], before["center"])), str(after))
        check("status message", "reloaded" in window.statusBar().currentMessage(),
              window.statusBar().currentMessage())
        look(3)
        check("no more reloads while nothing changes", len(loads) == 1, str(loads))

        # ---- the STEP and then the .toml, one after the other
        loads.clear()
        write_model(step, stub=True)
        look()
        write_types(step, {"stub": "pec"})
        look()
        check("STEP then .toml: not reloaded while they still change", not loads, str(loads))
        look(3)
        names = {b.name for b in window.geometry.bodies}
        stub = window.project.get("stub")
        check("then one reload: the new body there and typed", len(loads) == 1 and "stub" in names
              and stub is not None and stub.type == "pec", f"{loads} {sorted(names)}")

        # ---- the window's own save
        loads.clear()
        board = next(b for b in window.geometry.bodies if b.name == "board")
        window.project.set_type(board, "pec")
        window._save()
        look(3)
        check("the window's own save: no reload", not loads, str(loads))
        window.project.set_type(board, "dielectric")
        window._save()

        # ---- a .toml that does not read
        loads.clear()
        project = window.project
        with open(toml, encoding="utf-8") as f:
            good = f.read()
        with open(toml, "w", encoding="utf-8") as f:
            f.write(good + "\n[bodies.broken\n")
        look(2)
        check("a broken .toml: the window as it was", not loads and window.project is project
              and QApplication.activeModalWidget() is None, str(loads))
        check("  said in the status bar", "does not read" in window.statusBar().currentMessage(),
              window.statusBar().currentMessage())
        reads = []
        load = window._reload_from_disk
        window._reload_from_disk = lambda *args: (reads.append(1), load(*args))
        look(3)
        check("  not retried while it stays broken", not reads, str(reads))
        window._reload_from_disk = load
        with open(toml, "w", encoding="utf-8") as f:
            f.write(good)
        look(2)
        check("  fixed: reloaded", len(loads) == 1 and window.project is not project, str(loads))

        # ---- while a run goes on
        loads.clear()
        runner = window.runner
        window.runner = SimpleNamespace(running=True)
        write_types(step, {"patch": "dielectric"})
        look(3)
        check("during a run: not reloaded", not loads, str(loads))
        window.runner = runner
        look()
        check("after it: reloaded", len(loads) == 1 and window.project.get("patch").type == "dielectric",
              str(loads))

        # ---- the timer
        loads.clear()
        window._disk_timer.start()
        write_types(step, {"patch": "pec"})
        end = time.perf_counter() + 6 * DISK_POLL / 1000
        while not loads and time.perf_counter() < end:
            app.processEvents()
            time.sleep(0.02)
        check("the timer picks the change up", loads == [step] and window.project.get("patch").type == "pec",
              str(loads))
    finally:
        window._disk_timer.stop()
        window.close()
        shutil.rmtree(folder, ignore_errors=True)

    print("\nall ok" if not FAILED else f"\nFAILED: {FAILED}")
    sys.exit(1 if FAILED else 0)


if __name__ == "__main__":
    main()
