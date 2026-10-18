# check_result_file.py
#
# Saved simulations (solver/result_file.py): model.step -> model.results/,
# one file per run.
#   - round trip: frequencies, ports, impedances, S (missing traces stay
#     missing), Z_in, info, comment, fingerprint, save time, name, params;
#   - several runs: listed oldest first, named Run 1, Run 2 ...; rename and
#     delete persist; a damaged file is reported and skipped;
#   - an older single-result model.npz moves into the folder as a run;
#   - the fingerprint changes with a parameter, a body type, a setting, a
#     hole rule or the STEP bytes, and not with the [view] up axis.
#
#   python checks/check_result_file.py      (~3 s)

import os
import shutil
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

import numpy as np                                                       # noqa: E402

from geometry.step_io import read_step                                   # noqa: E402
from project.project_file import Project                                 # noqa: E402
from solver.result_file import (                                         # noqa: E402
    SimulationResult, delete_result, fingerprint, legacy_path, list_results, read_result,
    format_duration, rename_result, results_dir, save_result, write_result,
)

FAILED = []


def check(label, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {label} {detail}")
    if not ok:
        FAILED.append(label)


def make_result(scale=1.0):
    f = np.linspace(1e9, 5e9, 7)
    result = SimulationResult(f, ["port1", "port2"], [50.0, 46.08], "FDTD, test")
    result.s[0, 0] = 0.1 * scale * np.exp(1j * f / 1e9)
    result.s[1, 0] = 0.9 * np.exp(-1j * f / 1e9)        # one run only: column 2 missing
    result.z_in["port1"] = 50 + 1j * f / 1e9
    result.info = ["port1: converged, 1000 steps", "Ω ε test"]
    result.params = {"simulation": {"f_min": 1.0, "f_max": 5.0}, "grid": "10 × 10 × 10", "vias": 3}
    result.elapsed = 83.4
    result.eps_eff = [2.1, float("nan")]                 # a coax waveport, a lumped port
    return result


def main():
    folder = tempfile.mkdtemp()
    try:
        step = os.path.join(folder, "line.stp")
        shutil.copy(os.path.join(ROOT, "models", "01_linie50x30_3.stp"), step)
        shutil.copy(os.path.join(ROOT, "models", "01_linie50x30_3.toml"), os.path.join(folder, "line.toml"))
        runs, errors = list_results(step)
        check("no runs yet", runs == [] and errors == [])

        project = Project.load(step)
        first = make_result()
        first.fingerprint = fingerprint(project)
        path = save_result(step, first)
        check("file in model.results/", os.path.dirname(path) == results_dir(step)
              and results_dir(step) == os.path.join(folder, "line.results"), path)

        back = read_result(path)
        check("frequencies", np.array_equal(back.frequencies, first.frequencies))
        check("ports and impedances", back.ports == first.ports and back.resistances == first.resistances)
        check("S traces", sorted(back.s) == [(0, 0), (1, 0)]
              and all(np.array_equal(back.s[k], first.s[k]) for k in first.s))
        check("Z in", list(back.z_in) == ["port1"] and np.array_equal(back.z_in["port1"], first.z_in["port1"]))
        check("info, comment, time", back.info == first.info and back.comment == first.comment
              and back.saved == first.saved != "")
        check("name and params", back.name == "Run 1" and back.params == first.params,
              f"{back.name} {back.params}")
        check("fingerprint kept", back.fingerprint == fingerprint(project))
        check("run time kept", back.elapsed == 83.4 and format_duration(back.elapsed) == "1 min 23 s",
              format_duration(back.elapsed))
        check("port lines kept", back.eps_eff[0] == 2.1 and np.isnan(back.eps_eff[1]), str(back.eps_eff))
        check("time formats", format_duration(12.34) == "12.3 s" and format_duration(3725) == "1 h 02 min"
              and format_duration(float("nan")) == "")

        time.sleep(1.1)                              # a later save time
        second = make_result(scale=2.0)
        save_result(step, second)
        runs, _ = list_results(step)
        check("two runs, oldest first", [r.name for r in runs] == ["Run 1", "Run 2"], str([r.name for r in runs]))

        rename_result(runs[0], "  coarse mesh  ")
        runs, _ = list_results(step)
        check("rename persists (trimmed)", runs[0].name == "coarse mesh" and np.allclose(runs[0].s[0, 0], first.s[0, 0]))
        try:
            rename_result(runs[0], "   ")
            check("empty name rejected", False)
        except ValueError:
            check("empty name rejected", True)

        third = make_result()
        save_result(step, third)
        check("numbering continues", third.name == "Run 3", third.name)
        delete_result(runs[1])
        runs, _ = list_results(step)
        check("delete persists", [r.name for r in runs] == ["coarse mesh", "Run 3"], str([r.name for r in runs]))

        with open(os.path.join(results_dir(step), "broken.npz"), "wb") as f:
            f.write(b"not a numpy archive")
        runs, errors = list_results(step)
        check("damaged file reported and skipped", len(runs) == 2 and len(errors) == 1 and "broken" in errors[0],
              str(errors))
        os.remove(os.path.join(results_dir(step), "broken.npz"))

        # An older single-result file next to the STEP moves into the folder.
        step2 = os.path.join(folder, "old.stp")
        shutil.copy(step, step2)
        legacy = make_result()
        legacy.saved = "2026-10-16 23:09"
        write_result(legacy_path(step2), legacy)
        runs, _ = list_results(step2)
        check("legacy model.npz imported as a run", len(runs) == 1 and not os.path.exists(legacy_path(step2))
              and runs[0].saved == "2026-10-16 23:09" and runs[0].name == "Run 1", str([r.path for r in runs]))

        # Fingerprint.
        base = fingerprint(project)
        project.set_up("x")
        check("view up does not change the fingerprint", fingerprint(project) == base)
        project.set_value("substrate", "eps_r", 4.4)
        check("a parameter changes it", fingerprint(project) != base)
        project.set_value("substrate", "eps_r", 4.3)
        check("restored -> same again", fingerprint(project) == base)
        project.set_setting("simulation", "f_max", 6.0)
        check("a setting changes it", fingerprint(project) != base)
        project.set_setting("simulation", "f_max", 5.0)
        project.set_hole_diameter(0.3, "via")
        check("a hole rule changes it", fingerprint(project) != base)
        project.set_hole_diameter(0.3, None)
        project.remove_type("port2")
        check("a body type changes it", fingerprint(project) != base)
        project = Project.load(step)
        with open(step, "ab") as fh:
            fh.write(b"\n")
        check("the STEP bytes change it", fingerprint(project) != base)
        read_step(step)                         # still a valid STEP
    finally:
        shutil.rmtree(folder)

    print("FAILED: " + ", ".join(FAILED) if FAILED else "all checks passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
