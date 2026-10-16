# check_result_file.py
#
# Saved simulations (solver/result_file.py): model.step -> model.npz.
#   - round trip: frequencies, ports, impedances, S (missing traces stay
#     missing), Z_in, info, comment, fingerprint, save time;
#   - the fingerprint changes with a parameter, a body type, a setting or
#     the STEP bytes, and not with the [view] up axis;
#   - no file -> None.
#
#   python checks/check_result_file.py      (~2 s)

import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

import numpy as np                                                       # noqa: E402

from geometry.step_io import read_step                                   # noqa: E402
from project.project_file import Project                                 # noqa: E402
from solver.result_file import (                                         # noqa: E402
    SimulationResult, fingerprint, load_result, result_path, save_result,
)

FAILED = []


def check(label, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {label} {detail}")
    if not ok:
        FAILED.append(label)


def main():
    folder = tempfile.mkdtemp()
    try:
        step = os.path.join(folder, "line.stp")
        shutil.copy(os.path.join(ROOT, "models", "01_linie50x30_3.stp"), step)
        shutil.copy(os.path.join(ROOT, "models", "01_linie50x30_3.toml"), os.path.join(folder, "line.toml"))
        check("no file -> None", load_result(step) is None)

        project = Project.load(step)
        f = np.linspace(1e9, 5e9, 7)
        result = SimulationResult(f, ["port1", "port2"], [50.0, 46.08], "FDTD, test")
        result.s[0, 0] = 0.1 * np.exp(1j * f / 1e9)
        result.s[1, 0] = 0.9 * np.exp(-1j * f / 1e9)        # one run only: column 2 missing
        result.z_in["port1"] = 50 + 1j * f / 1e9
        result.info = ["port1: converged, 1000 steps", "Ω ε test"]
        result.fingerprint = fingerprint(project)
        path = save_result(step, result)
        check("file next to the STEP", path == os.path.join(folder, "line.npz") == result_path(step), path)

        back = load_result(step)
        check("frequencies", np.array_equal(back.frequencies, f))
        check("ports and impedances", back.ports == result.ports and back.resistances == result.resistances,
              f"{back.ports} {back.resistances}")
        check("S traces", sorted(back.s) == [(0, 0), (1, 0)]
              and all(np.array_equal(back.s[k], result.s[k]) for k in result.s), str(sorted(back.s)))
        check("Z in", list(back.z_in) == ["port1"] and np.array_equal(back.z_in["port1"], result.z_in["port1"]))
        check("info, comment, time", back.info == result.info and back.comment == result.comment
              and back.saved == result.saved and back.saved != "", f"{back.info} {back.saved}")
        check("fingerprint kept", back.fingerprint == fingerprint(project))

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
