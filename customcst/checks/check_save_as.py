# check_save_as.py
#
# Save As (project/save_as.py, the window's Save As..., the save_as command),
# on a model in a temp folder (settings, remote and control files there):
#   - the copy in another folder: the STEP's bytes, the project file (its
#     header names the new STEP, the rest the same), the runs (current: the
#     fingerprint is the same), z0profiles, presets; NOT remote.json; no
#     status.json; nothing in the copy names the original's files;
#   - the copy opens on its own after the original's folder is gone;
#   - refused: the same name, a name without .step/.stp, an existing model
#     there (unless overwrite: then its old runs are gone);
#   - the window: save_as unlocked is refused; locked it copies the model as
#     shown (a type set in the window but not written yet is in the copy),
#     opens the copy (title, status model), the original untouched.
#
#   python checks/check_save_as.py

import os
import shutil
import sys
import tempfile
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

from checks.check_control import fake_run                               # noqa: E402
from checks.check_disk_reload import write_model, write_types           # noqa: E402

FAILED = []


def check(label, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {label} {detail}")
    if not ok:
        FAILED.append(label)


def all_text(folder):
    """Every file's bytes under folder, joined (to look for the old name)."""
    out = b""
    for base, _, names in os.walk(folder):
        for n in names:
            with open(os.path.join(base, n), "rb") as f:
                out += f.read()
    return out


def core(folder):
    from project.project_file import Project
    from project.save_as import SaveAsError, save_as
    from solver.result_file import fingerprint, list_results

    a = os.path.join(folder, "orig", "model_orig.step")
    os.makedirs(os.path.dirname(a))
    write_model(a)
    write_types(a, {"board": "dielectric", "patch": "pec"})
    fp = fingerprint(Project.load(a))
    fake_run(a, "Run 1", fp)
    fake_run(a, "Run 2", fp)
    res = os.path.join(folder, "orig", "model_orig.results")
    os.makedirs(os.path.join(res, "z0profiles"))
    open(os.path.join(res, "z0profiles", "x.npz"), "wb").write(b"z0")
    open(os.path.join(res, "remote.json"), "w").write('{"launches": []}')
    os.makedirs(os.path.join(folder, "orig", "model_orig.presets", "mesh"))
    open(os.path.join(folder, "orig", "model_orig.presets", "mesh", "My.toml"), "w").write("cells_per_wavelength = 25\n")
    open(os.path.join(folder, "orig", "model_orig.status.json"), "w").write("{}")

    b = os.path.join(folder, "elsewhere", "deep", "copy.stp")
    lines = save_as(a, b)
    d = os.path.dirname(b)
    names = sorted(os.listdir(d))
    check("copied: step, toml, results, presets; no status.json", names ==
          ["copy.presets", "copy.results", "copy.stp", "copy.toml"], str(names))
    check("the STEP's bytes", open(a, "rb").read() == open(b, "rb").read())
    ta, tb = open(a[:-5] + ".toml", encoding="utf-8").read(), open(b[:-4] + ".toml", encoding="utf-8").read()
    check("the project: header names copy.stp, the rest the same",
          tb.startswith("# customcst project for copy.stp") and ta.split("\n", 1)[1] == tb.split("\n", 1)[1])
    check("remote.json not copied, z0profiles and presets are",
          not os.path.exists(os.path.join(d, "copy.results", "remote.json"))
          and os.path.isfile(os.path.join(d, "copy.results", "z0profiles", "x.npz"))
          and os.path.isfile(os.path.join(d, "copy.presets", "mesh", "My.toml")), str(lines))
    check("nothing in the copy names the original", b"model_orig" not in all_text(d))

    shutil.rmtree(os.path.join(folder, "orig_moved"), ignore_errors=True)
    os.rename(os.path.join(folder, "orig"), os.path.join(folder, "orig_moved"))
    runs, _ = list_results(b)
    fpb = fingerprint(Project.load(b))
    check("the copy alone: its two runs, current", sorted(r.name for r in runs) == ["Run 1", "Run 2"]
          and all(r.fingerprint == fpb for r in runs) and fpb == fp)
    os.rename(os.path.join(folder, "orig_moved"), os.path.join(folder, "orig"))

    for label, dst, kw in (("the same name", a[:-5] + ".stp", {}), ("no .step/.stp", b[:-4] + ".txt", {}),
                           ("an existing model there", b, {})):
        try:
            save_as(a, dst, **kw)
            check(f"refused: {label}", False)
        except SaveAsError as error:
            check(f"refused: {label}", True, str(error))
    os.remove(os.path.join(d, "copy.results", os.listdir(os.path.join(d, "copy.results"))[0]))  # one run less there
    open(os.path.join(d, "copy.results", "stale.npz"), "wb").write(b"old")
    save_as(a, b, overwrite=True)
    check("overwrite: the old copy's files replaced", not os.path.exists(os.path.join(d, "copy.results", "stale.npz"))
          and len(list_results(b)[0]) == 2)
    return a


def window_check(folder, a):
    from PySide6.QtWidgets import QApplication

    from geometry.step_io import read_step
    from project.project_file import Project
    from tools import ui
    from view.main_window import MainWindow

    app = QApplication.instance() or QApplication(sys.argv)
    window = MainWindow(a)
    window._disk_timer.stop()
    window.show()
    window.wait_for_mesh()

    def call(command, **args):
        out = {}

        def run():
            try:
                out["value"] = (True, ui.send(command, timeout=30, **args))
            except (ui.CommandFailed, ui.NoWindow) as error:
                out["value"] = (False, str(error))
        thread = threading.Thread(target=run)
        thread.start()
        while thread.is_alive():
            app.processEvents()
            thread.join(0.01)
        window.wait_for_mesh()
        app.processEvents()
        return out["value"]

    try:
        c = os.path.join(folder, "win", "from_window.stp")
        ok, text = call("save_as", path=c)
        check("save_as unlocked: refused", not ok and "not in control" in text and not os.path.exists(c), text)
        call("lock", message="save as")
        # a change in the window not yet on disk: the copy must take it
        window.project.set_type({b.name: b for b in read_step(a).bodies}["patch"], "dielectric")
        ok, state = call("save_as", path=c)
        check("save_as locked: the copy open", ok and state["model"] == os.path.abspath(c)
              and "from_window.stp" in window.windowTitle(), str(state)[:200] if ok else state)
        check("the copy has the window's state", Project.load(c).get("patch").type == "dielectric")
        ok, text = call("save_as", path=c)
        check("the copy onto itself: refused", not ok and "itself" in text, text)
        call("open", path=a)
        res = os.path.splitext(a)[0] + ".results"
        check("the original keeps its runs and its server record",
              len([n for n in os.listdir(res) if n.endswith(".npz")]) == 2
              and os.path.isfile(os.path.join(res, "remote.json")))
        ok, text = call("save_as", path=c)
        check("again without overwrite: refused", not ok and "exists" in text, text)
        ok, _ = call("save_as", path=c, overwrite=True)
        check("with overwrite=true: done", ok)
        call("unlock")
    finally:
        window.close()
        app.processEvents()


def main():
    folder = tempfile.mkdtemp()
    os.environ["CUSTOMCST_SETTINGS"] = os.path.join(folder, "settings.json")
    os.environ["CUSTOMCST_REMOTE"] = os.path.join(folder, "remote.toml")
    try:
        a = core(folder)
        window_check(folder, a)
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    print("\nall ok" if not FAILED else f"\nFAILED: {FAILED}")
    sys.exit(1 if FAILED else 0)


if __name__ == "__main__":
    main()
