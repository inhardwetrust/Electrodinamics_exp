# check_path_profiles.py
#
# The saved Z0 profiles along the path (solver/profile_file.py, the tree's
# Z0 path > Profiles), on a model in a temp folder:
#   - every Compute is kept: two paths -> two entries "Profile 1", "Profile 2",
#     newest first; the same key again replaces its entry (name kept);
#   - an older single z0profiles/path.npz is moved in as "Profile 1";
#   - status: the current one, "the path changed", "the model ... changed";
#   - rename and delete; a damaged file is skipped;
#   - the window: the rows with current / outdated, a mesh setting change
#     makes all outdated, Show puts it in the profile window (outdated:
#     marked), Use its path restores the path (then it is current again),
#     rename in place, delete (after a yes), Delete all outdated.
#
#   python checks/check_path_profiles.py

import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

import numpy as np                                                       # noqa: E402

from checks.check_disk_reload import write_model, write_types           # noqa: E402

FAILED = []


def check(label, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {label} {detail}")
    if not ok:
        FAILED.append(label)


def path_profile(z0):
    from solver.line_profile import Section
    from solver.path_profile import PathProfile
    return PathProfile(sections=[Section(0.1e-3 * k, z0, 2.3) for k in range(4)],
                       points=[np.array([1e-3 * k, 0.0, 1.6e-3]) for k in range(4)],
                       across=[np.array([0.0, 1.0, 0.0])] * 4, half_width=2e-3)


def files(step, project, z0path, z0):
    from solver.profile_file import profile_key, save_profile
    from solver.result_file import fingerprint
    model = fingerprint(project)
    return save_profile(step, "path", path_profile(z0), profile_key(project, "path", z0path, model),
                        model_fingerprint=model, z0path=z0path)


def core(folder):
    from project.project_file import Project
    from solver.profile_file import (_write, delete_path_profile, list_path_profiles, load_profile,
                                     profile_key, profiles_dir, rename_path_profile)
    from solver.result_file import fingerprint

    step = os.path.join(folder, "core", "m.step")
    os.makedirs(os.path.dirname(step))
    write_model(step)
    project = Project.load(step)
    p1 = {"axis": "z", "at": 1.6, "half_width": 2.0, "points": [[-5.0, 0.0], [5.0, 0.0]]}
    p2 = dict(p1, points=[[-5.0, 0.0], [6.0, 0.0]])

    # an older single file first
    os.makedirs(profiles_dir(step))
    _write(os.path.join(profiles_dir(step), "path.npz"), path_profile(40.0),
           profile_key(project, "path", {"old": True}))
    entries = list_path_profiles(step)
    check("an older path.npz moved in as Profile 1", len(entries) == 1 and entries[0].name == "Profile 1"
          and not os.path.exists(os.path.join(profiles_dir(step), "path.npz")))
    delete_path_profile(entries[0])

    files(step, project, p1, 50.0)
    import time
    time.sleep(1.1)                         # another stamp
    files(step, project, p2, 51.0)
    entries = list_path_profiles(step)
    check("two paths: two entries, newest first", [e.name for e in entries] == ["Profile 2", "Profile 1"]
          and entries[0].z0path == p2 and entries[1].z0path == p1, str([e.name for e in entries]))
    rename_path_profile(entries[1], "short path")
    files(step, project, p1, 52.0)          # the same key again
    entries = list_path_profiles(step)
    check("the same key again replaces its entry, its name kept", len(entries) == 2
          and entries[1].name == "short path" and abs(entries[1].profile.z0[0] - 52.0) < 1e-9)
    model = fingerprint(project)
    key2 = profile_key(project, "path", p2, model)
    check("status: current / the path changed", entries[0].status(key2, model) == ("current", "")
          and entries[1].status(key2, model)[1] == "the path changed")
    check("load_profile finds the current one among them",
          abs(load_profile(step, "path", key2).z0[0] - 51.0) < 1e-9)
    project.set_setting("mesh", "cells_per_wavelength", 25.0)
    m2 = fingerprint(project)
    k2 = profile_key(project, "path", p2, m2)
    check("a mesh setting: all outdated, the model named", all(
        e.status(k2, m2)[0] == "outdated" and "model" in e.status(k2, m2)[1] for e in entries))
    with open(os.path.join(os.path.dirname(entries[0].path), "99999999-999999.npz"), "wb") as f:
        f.write(b"junk")
    check("a damaged file is skipped", len(list_path_profiles(step)) == 2)


def window_check(folder):
    from PySide6.QtWidgets import QApplication

    from project.project_file import Project
    from view.main_window import MainWindow

    step = os.path.join(folder, "win", "w.step")
    os.makedirs(os.path.dirname(step))
    write_model(step)
    write_types(step, {"board": "dielectric", "patch": "pec"})
    project = Project.load(step)
    p1 = {"axis": "z", "at": 1.6, "half_width": 2.0, "points": [[-4.0, 0.0], [4.0, 0.0]]}
    p2 = dict(p1, points=[[-4.0, 0.0], [3.0, 0.0]])
    files(step, project, p1, 50.0)
    import time
    time.sleep(1.1)
    files(step, project, p2, 51.0)
    project.z0path = p2
    project.save()

    app = QApplication.instance() or QApplication(sys.argv)
    window = MainWindow(step)
    window._disk_timer.stop()
    window.show()
    window.wait_for_mesh()
    app.processEvents()

    def rows():
        group = window._tree_item("z0path", None)
        prof = next((group.child(i) for i in range(group.childCount())
                     if group.child(i).data(0, 0x0100)[0] == "z0profiles"), None)
        return [] if prof is None else [prof.child(i) for i in range(prof.childCount())]

    try:
        r = rows()
        check("the window lists them: Profile 2 current, Profile 1 outdated",
              [(x.text(0), x.text(2)) for x in r] == [("Profile 2", "current"), ("Profile 1", "outdated")],
              str([(x.text(0), x.text(2)) for x in r]))
        window._show_path_profile(r[1].data(0, 0x0100)[1])
        app.processEvents()
        pw = window.profile_window
        check("Show: in the profile window, marked outdated", pw is not None and pw.isVisible()
              and abs(pw.profile.z0[0] - 50.0) < 1e-9 and "outdated" in pw.windowTitle().lower(),
              pw.windowTitle() if pw else "")
        window._show_path_profile(r[0].data(0, 0x0100)[1])
        app.processEvents()
        check("Show the current one: not outdated", abs(pw.profile.z0[0] - 51.0) < 1e-9
              and "outdated" not in pw.windowTitle().lower(), pw.windowTitle())

        entry = window._path_entry(r[1].data(0, 0x0100)[1])
        window._use_profile_path(entry)
        app.processEvents()
        window.wait_for_mesh()
        r = rows()
        check("Use its path: the project's path back, that profile current",
              window.project.z0path["points"] == p1["points"]
              and [(x.text(0), x.text(2)) for x in r] == [("Profile 2", "outdated"), ("Profile 1", "current")],
              str([(x.text(0), x.text(2)) for x in r]))

        r[0].setText(0, "long one")
        app.processEvents()
        app.processEvents()
        check("rename in place", [x.text(0) for x in rows()] == ["long one", "Profile 1"],
              str([x.text(0) for x in rows()]))

        window.project.z0path = dict(p1, points=[[-4.0, 0.0], [2.0, 0.0]])
        window._z0path_changed()
        window.wait_for_mesh()
        window._z0_profile_compute("path")
        app.processEvents()
        r = rows()
        check("Compute along a new path: a new current entry on top",
              [(x.text(0), x.text(2)) for x in r] == [("Profile 2", "current"), ("long one", "outdated"),
                                                     ("Profile 1", "outdated")]     # 2 is free again
              and len(window.profile_window.profile.sections) > 3,
              str([(x.text(0), x.text(2)) for x in r]))
        window._z0_profile_compute("path")
        app.processEvents()
        check("Compute again (same key): no new entry", len(rows()) == 3, str(len(rows())))
        window.ask = lambda *a, **k: True
        window._delete_path_profiles([window._path_entry(rows()[0].data(0, 0x0100)[1])])
        app.processEvents()

        window.project.set_setting("mesh", "cells_per_wavelength", 25.0)
        window._after_edit()
        window.wait_for_mesh()
        app.processEvents()
        check("a mesh setting: all outdated", [x.text(2) for x in rows()] == ["outdated", "outdated"])

        window.ask = lambda *a, **k: False
        window._delete_path_profiles([window._path_entry(rows()[0].data(0, 0x0100)[1])])
        app.processEvents()
        check("delete answered no: kept", len(rows()) == 2)
        window.ask = lambda *a, **k: True
        window._delete_path_profiles(list(window._path_entries.values()))
        app.processEvents()
        check("delete all outdated: gone (rows and files)", rows() == []
              and not os.listdir(os.path.join(os.path.splitext(step)[0] + ".results", "z0profiles", "path")))
    finally:
        window.close()
        app.processEvents()


def main():
    folder = tempfile.mkdtemp()
    os.environ["CUSTOMCST_SETTINGS"] = os.path.join(folder, "settings.json")
    os.environ["CUSTOMCST_REMOTE"] = os.path.join(folder, "remote.toml")
    try:
        core(folder)
        window_check(folder)
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    print("\nall ok" if not FAILED else f"\nFAILED: {FAILED}")
    sys.exit(1 if FAILED else 0)


if __name__ == "__main__":
    main()
