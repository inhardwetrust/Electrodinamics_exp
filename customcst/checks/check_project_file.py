# check_project_file.py
#
# Level 1 checks: assignments survive save/load exactly (including awkward
# body names), invalid files and values are rejected with a message naming
# the body, and type/kind rules hold.
#
#   python checks/check_project_file.py

import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from geometry.body import Body                                  # noqa: E402
from project.project_file import Project, ProjectError          # noqa: E402

FAILED = []
sys.stdout.reconfigure(encoding="utf-8")


def check(label, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {label} {detail}")
    if not ok:
        FAILED.append(label)


def raises(fn, needle):
    try:
        fn()
    except (ProjectError, ValueError) as error:
        return needle in str(error), str(error)
    return False, "no error"


def body(name, kind="solid"):
    return Body(name, kind, [[0, 0, 0]], [[0, 0, 0]], [[0, 0, 1]], [])


def main():
    folder = tempfile.mkdtemp()
    try:
        step = os.path.join(folder, "model.step")

        empty = Project.load(step)
        check("no file -> empty project", empty.assignments == {})

        project = Project(step)
        names = ["substrate", "arm#1", 'odd "name" ü']
        project.set_type(body(names[0]), "dielectric")
        project.set_value(names[0], "eps_r", "4.3")
        project.set_value(names[0], "tan_d", 0.025)
        project.set_type(body(names[1]), "pec")
        project.set_type(body(names[2], "sheet"), "pec")
        project.save()

        again = Project.load(step)
        same = {n: (a.type, a.values) for n, a in project.assignments.items()}
        loaded = {n: (a.type, a.values) for n, a in again.assignments.items()}
        check("round trip", same == loaded, str(loaded))
        check("file name", os.path.isfile(os.path.join(folder, "model.toml")))

        project.set_type(body(names[0]), "dielectric")
        check("re-assigning the same type keeps values",
              project.get(names[0]).values["eps_r"] == 4.3)

        ok, msg = raises(lambda: project.set_type(body("s", "sheet"), "dielectric"), "sheet")
        check("dielectric sheet rejected", ok, msg)
        ok, msg = raises(lambda: project.set_value(names[0], "eps_r", 0.5), ">= 1")
        check("eps_r < 1 rejected", ok, msg)
        ok, msg = raises(lambda: project.set_value(names[0], "eps_r", "abc"), "not a number")
        check("text rejected", ok, msg)

        bad_files = {
            "unknown type": ('[bodies.a]\ntype = "gold"\n', "unknown type"),
            "unknown key": ('[bodies.a]\ntype = "pec"\neps_r = 2\n', "unknown key"),
            "bad value": ('[bodies.a]\ntype = "dielectric"\neps_r = -1\n', "'a'"),
            "syntax": ("[bodies.a\n", "model.toml"),
        }
        for label, (text, needle) in bad_files.items():
            with open(os.path.join(folder, "model.toml"), "w", encoding="utf-8") as f:
                f.write(text)
            ok, msg = raises(lambda: Project.load(step), needle)
            check(f"bad file: {label}", ok, msg)

        with open(os.path.join(folder, "model.toml"), "w", encoding="utf-8") as f:
            f.write('[bodies.a]\ntype = "dielectric"\neps_r = 2\n')
        partial = Project.load(step)
        check("missing params take defaults", partial.get("a").values["mu_r"] == 1.0)

        class G:
            bodies = [body("a")]
        partial.set_type(body("gone"), "pec")
        check("orphans reported", partial.orphans(G) == ["gone"])
    finally:
        shutil.rmtree(folder)

    print("FAILED: " + ", ".join(FAILED) if FAILED else "all checks passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
