# check_console_run.py
#
# The console runner (run.py) makes the window's run: on a copy of the
# 01 line model (two ports, 4-5 GHz),
#   - --dry-run meshes, sizes and saves nothing;
#   - a run (a subprocess, as on another machine) saves one run file with
#     both S columns, exit code 0;
#   - the window opened on the model lists it as current (the same
#     fingerprint) with the parameters it would record itself;
#   - S equals the window's own path (solver/simulation.py simulate() on
#     the window's materials) to float precision;
#   - a model that cannot run (no port) exits with 2, saves nothing;
#   - --excite runs one port: one S column.
#
#   python checks/check_console_run.py      (~1 min)

import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

import numpy as np                                                       # noqa: E402

FAILED = []
MODEL = "01_linie50x30_3"


def check(label, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {label} {detail}")
    if not ok:
        FAILED.append(label)


def run(*args):
    done = subprocess.run([sys.executable, os.path.join(ROOT, "run.py"), *args, "--every", "5"],
                          capture_output=True, text=True, encoding="utf-8")
    return done.returncode, done.stdout + done.stderr


def main():
    folder = tempfile.mkdtemp()
    os.environ["CUSTOMCST_SETTINGS"] = os.path.join(folder, "settings.json")
    try:
        step = os.path.join(folder, f"{MODEL}.stp")
        shutil.copy(os.path.join(ROOT, "models", f"{MODEL}.stp"), step)
        from geometry.step_io import read_step
        from project.project_file import Project
        bodies = {b.name: b for b in read_step(step).bodies}
        project = Project.load(step)            # fresh: no project file beside the copy
        for name, kind in (("port1", "port"), ("port2", "port"), ("substrate", "dielectric"),
                           ("line", "pec"), ("gnd", "pec")):
            project.set_type(bodies[name], kind)
        project.set_setting("simulation", "f_min", 4.0)
        project.set_setting("simulation", "f_max", 5.0)
        project.save()

        from solver.result_file import list_results, read_result
        code, out = run(step, "--dry-run")
        check("--dry-run: sizes the model, saves nothing",
              code == 0 and "memory" in out and not list_results(step)[0], f"exit {code}")

        code, out = run(step)
        runs = list_results(step)[0]
        check("a run saves one run file with both S columns",
              code == 0 and len(runs) == 1 and set(runs[0].s) == {(0, 0), (1, 0), (0, 1), (1, 1)},
              f"exit {code}, {len(runs)} runs" + ("" if code == 0 else "\n" + out[-2000:]))
        if not runs:
            return

        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication(sys.argv)     # noqa: F841
        from view.main_window import MainWindow
        from solver.simulation import parameters, simulate
        window = MainWindow(step)
        window.wait_for_mesh()
        console = read_result(runs[0].path)
        check("the window lists it as current", window._is_current(window.runs[-1])
              and window.runs[-1].path == runs[0].path)
        own = parameters(window.project, window.mesh, window.materials, window.via_bodies)
        check("with the parameters the window records", own == console.params)
        sim = window.project.settings["simulation"]
        mine = simulate(window.materials, sim["f_min"], sim["f_max"], sim["trim_pulse"], -40.0)
        worst = max(np.max(np.abs(mine.s[key] - console.s[key])) for key in console.s)
        check("S equals the window's own run", worst < 1e-6, f"max |dS| {worst:.2e}")
        window.close()

        code, out = run(step, "--excite", "port2")
        runs = list_results(step)[0]
        check("--excite port2: one S column", code == 0 and len(runs) == 2
              and set(runs[-1].s) == {(0, 1), (1, 1)}, f"exit {code}")

        project = Project.load(step)
        project.set_type(bodies["port1"], "pec")
        project.set_type(bodies["port2"], "pec")
        project.save()
        code, out = run(step)
        check("no port: exit 2, nothing saved", code == 2 and len(list_results(step)[0]) == 2
              and "cannot simulate" in out, f"exit {code}")
    finally:
        shutil.rmtree(folder, ignore_errors=True)

    print("\nall checks passed" if not FAILED else f"\nFAILED: {', '.join(FAILED)}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
