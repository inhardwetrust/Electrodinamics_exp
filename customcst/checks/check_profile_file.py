# check_profile_file.py
#
# Z0 profiles kept on disk (solver/profile_file.py): model.results/z0profiles/.
#   - round trip of a profile along an axis and of a path profile (its
#     points, directions across, half width), NaN gaps with their reasons;
#   - loaded only with its key: a mesh or band setting, another path (for
#     the path's profile only) or another source make it outdated; the
#     same project reloaded from its file finds it again;
#   - a damaged file is outdated, not an error; the run listing does not
#     see the folder.
#
#   python checks/check_profile_file.py      (~2 s)

import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

import numpy as np                                                       # noqa: E402

from project.project_file import Project                                 # noqa: E402
from solver.line_profile import Profile, Section                         # noqa: E402
from solver.path_profile import PathProfile                              # noqa: E402
from solver.profile_file import load_profile, profile_key, profiles_dir, save_profile   # noqa: E402
from solver.result_file import list_results                              # noqa: E402

FAILED = []


def check(label, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {label} {detail}")
    if not ok:
        FAILED.append(label)


def same(a, b):
    sa, sb = a.sections, b.sections
    return (type(a) is type(b) and a.axis == b.axis and len(sa) == len(sb)
            and np.allclose(a.coordinates, b.coordinates)
            and np.allclose(a.z0, b.z0, equal_nan=True) and np.allclose(a.eps_eff, b.eps_eff, equal_nan=True)
            and [s.error for s in sa] == [s.error for s in sb])


def main():
    folder = tempfile.mkdtemp()
    try:
        step = os.path.join(folder, "line.stp")
        shutil.copy(os.path.join(ROOT, "models", "01_linie50x30_3.stp"), step)
        project = Project.load(step)
        path_settings = {"axis": "z", "at": 1.5, "half_width": 2.0, "points": [[0.0, 1.0], [5.0, 1.0]]}

        along_x = Profile(0, [Section(1e-3 * k, 50.0 + k, 3.0 + 0.01 * k) for k in range(5)]
                          + [Section(5e-3, error="no signal conductor")])
        key_x = profile_key(project, "x")
        save_profile(step, "x", along_x, key_x)
        loaded = load_profile(step, "x", key_x)
        check("axis profile: round trip (with a gap and its reason)", loaded is not None and same(loaded, along_x))

        on_path = PathProfile(sections=[Section(0.1e-3 * k, 49.0, 2.3) for k in range(4)],
                              points=[np.array([1e-3 * k, 1e-3, 1.5e-3]) for k in range(4)],
                              across=[np.array([0.0, 1.0, 0.0])] * 4, half_width=2e-3)
        key_path = profile_key(project, "path", path_settings)
        save_profile(step, "path", on_path, key_path)
        loaded = load_profile(step, "path", key_path)
        check("path profile: round trip (points, across, half width)",
              loaded is not None and same(loaded, on_path)
              and np.allclose(np.array(loaded.points), np.array(on_path.points))
              and np.allclose(np.array(loaded.across), np.array(on_path.across)) and loaded.half_width == 2e-3)

        reopened = Project.load(step)
        check("the same project reopened finds them",
              load_profile(step, "x", profile_key(reopened, "x")) is not None
              and load_profile(step, "path", profile_key(reopened, "path", path_settings)) is not None)
        check("another source does not", load_profile(step, "y", profile_key(project, "y")) is None
              and profile_key(project, "y") != key_x)
        moved = dict(path_settings, points=[[0.0, 1.0], [6.0, 1.0]])
        check("another path: the path's profile is outdated, the axis one is not",
              load_profile(step, "path", profile_key(project, "path", moved)) is None
              and profile_key(project, "x") == key_x)

        project.set_setting("mesh", "cells_per_wavelength", 25.0)
        check("a mesh setting makes them outdated", load_profile(step, "x", profile_key(project, "x")) is None)
        project = Project.load(step)
        project.set_setting("simulation", "f_max", 12.0)
        check("a band setting (the mesh's wavelength) makes them outdated",
              load_profile(step, "x", profile_key(project, "x")) is None)

        with open(os.path.join(profiles_dir(step), "z.npz"), "wb") as f:
            f.write(b"not an archive")
        check("a damaged file is outdated, not an error", load_profile(step, "z", profile_key(project, "z")) is None)
        runs, errors = list_results(step)
        check("the run listing does not see the profiles", not runs and not errors, f"{len(runs)} runs, {errors}")
    finally:
        shutil.rmtree(folder, ignore_errors=True)

    print("\nall ok" if not FAILED else f"\nFAILED: {', '.join(FAILED)}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
