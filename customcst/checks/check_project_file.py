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
        check("old file without material -> Custom", partial.get("a").material == "Custom"
              and partial.get("a").values["eps_r"] == 2.0)

        # --- a boolean setting (check box) -------------------------------------
        flags = Project(step)
        check("trim_pulse defaults to off", flags.settings["simulation"]["trim_pulse"] is False)
        flags.set_setting("simulation", "trim_pulse", True)
        flags.save()
        text = open(flags.path, encoding="utf-8").read()
        check("saved as TOML true", "trim_pulse = true" in text)
        check("read back as True", Project.load(step).settings["simulation"]["trim_pulse"] is True)
        ok, msg = raises(lambda: flags.set_setting("simulation", "trim_pulse", "maybe"), "true or false")
        check("not a bool rejected", ok, msg)

        # --- accuracy (a choice) -------------------------------------------------
        from project.settings import accuracy_db
        check("accuracy defaults to -40 dB", accuracy_db(Project(step).settings) == -40.0)
        flags.set_setting("simulation", "accuracy", -60)       # a number is fine too
        flags.save()
        back = Project.load(step)
        check("accuracy saved and read back", back.settings["simulation"]["accuracy"] == "-60"
              and accuracy_db(back.settings) == -60.0 and 'accuracy = "-60"' in open(back.path, encoding="utf-8").read())
        ok, msg = raises(lambda: flags.set_setting("simulation", "accuracy", "-35"), "one of")
        check("accuracy outside the list rejected", ok, msg)

        # --- excite (a port name or "all") --------------------------------------
        check("excite defaults to all", Project(step).settings["simulation"]["excite"] == "all")
        flags.set_setting("simulation", "excite", "  port1 ")
        flags.save()
        check("excite saved and read back (trimmed)",
              Project.load(step).settings["simulation"]["excite"] == "port1")
        ok, msg = raises(lambda: flags.set_setting("simulation", "excite", "   "), "must not be empty")
        check("empty excite rejected", ok, msg)

        # --- material library -------------------------------------------------
        lib = Project(step)
        check("built-in presets", {"Default", "FR4"} <= set(lib.materials), str(list(lib.materials)))
        lib.set_type(body("board"), "dielectric")
        check("new dielectric -> Default", lib.get("board").material == "Default"
              and lib.get("board").values["eps_r"] == 1.0)
        lib.set_material("board", "FR4")
        check("preset copies its values", lib.get("board").values["eps_r"] == 4.3
              and lib.get("board").values["tan_d"] == 0.02, str(lib.get("board").values))
        lib.set_value("board", "eps_r", 4.3)
        check("same value keeps the preset", lib.get("board").material == "FR4")
        check("users of a preset", lib.material_users("FR4") == ["board"])
        lib.set_type(body("pin"), "pec")
        ok, msg = raises(lambda: lib.set_material("pin", "FR4"), "only dielectrics")
        check("material on a PEC rejected", ok, msg)
        ok, msg = raises(lambda: lib.set_material("board", "Gold"), "unknown material")
        check("unknown preset rejected", ok, msg)
        lib.materials["Rogers 4003"] = {"eps_r": 3.55, "tan_d": 0.0027, "f_ref": 10.0, "mu_r": 1.0}
        lib.set_type(body("rf"), "dielectric")
        lib.set_material("rf", "Rogers 4003")
        lib.set_type(body("own"), "dielectric")
        lib.set_value("own", "eps_r", 2.5)
        check("edited value -> Custom", lib.get("own").material == "Custom")
        lib.save()

        back = Project.load(step)
        check("library saved in the TOML", back.materials == lib.materials, str(back.materials))
        check("materials of bodies saved",
              {n: a.material for n, a in back.assignments.items()} ==
              {n: a.material for n, a in lib.assignments.items()})
        check("values of bodies saved", {n: a.values for n, a in back.assignments.items()} ==
              {n: a.values for n, a in lib.assignments.items()})

        # The library wins over a hand-edited body copy; a TOML preset overrides the built-in.
        with open(os.path.join(folder, "model.toml"), "w", encoding="utf-8") as f:
            f.write('[bodies.b]\ntype = "dielectric"\nmaterial = "FR4"\neps_r = 9.9\n\n'
                    '[materials.FR4]\neps_r = 4.5\n')
        edited = Project.load(step)
        check("TOML preset overrides the built-in", edited.materials["FR4"]["eps_r"] == 4.5
              and edited.materials["FR4"]["tan_d"] == 0.02, str(edited.materials["FR4"]))
        check("body takes the library's values", edited.get("b").values["eps_r"] == 4.5)

        # --- editing the library ----------------------------------------------
        ed = Project(step)
        check("PTFE is built in", ed.materials.get("PTFE", {}).get("eps_r") == 2.1)
        ed.set_type(body("board"), "dielectric")
        ed.set_material("board", "FR4")
        ed.set_material_value("FR4", "eps_r", "4.5")
        check("editing a preset updates its bodies", ed.get("board").values["eps_r"] == 4.5
              and ed.get("board").material == "FR4")
        ok, msg = raises(lambda: ed.set_material_value("FR4", "eps_r", 0.5), ">= 1")
        check("invalid preset value rejected", ok and ed.materials["FR4"]["eps_r"] == 4.5, msg)
        ed.reset_material("FR4")
        check("reset built-in: library and bodies", ed.materials["FR4"]["eps_r"] == 4.3
              and ed.get("board").values["eps_r"] == 4.3)
        copy = ed.duplicate_material("FR4")
        check("duplicate gets a free name", copy == "FR4 copy" and ed.materials[copy] == ed.materials["FR4"]
              and ed.materials[copy] is not ed.materials["FR4"], copy)
        check("second duplicate numbered", ed.duplicate_material("FR4") == "FR4 copy 2")
        new = ed.add_material(ed.unique_material_name("New material"))
        check("new preset = dielectric defaults", ed.materials[new]["eps_r"] == 1.0, new)
        ed.set_material("board", copy)
        renamed = ed.rename_material(copy, "My FR4")
        check("rename keeps the order and the users", renamed == "My FR4"
              and list(ed.materials).index("My FR4") == 3 and ed.get("board").material == "My FR4",
              str(list(ed.materials)))
        for label, fn, needle in (
            ("rename built-in", lambda: ed.rename_material("FR4", "X"), "built-in"),
            ("delete built-in", lambda: ed.delete_material("PTFE"), "built-in"),
            ("delete a used preset", lambda: ed.delete_material("My FR4"), "used by board"),
            ("duplicate name", lambda: ed.add_material("PTFE"), "already exists"),
            ("reserved name", lambda: ed.add_material("Custom"), "reserved"),
            ("empty name", lambda: ed.rename_material("My FR4", "  "), "needs a name"),
        ):
            ok, msg = raises(fn, needle)
            check(f"rejected: {label}", ok, msg)
        ed.delete_material(new)
        check("delete an unused preset", new not in ed.materials)
        ed.save()
        back = Project.load(step)
        check("edited library round trip", back.materials == ed.materials
              and back.get("board").material == "My FR4", str(list(back.materials)))

        bad_materials = {
            "unknown preset": ('[bodies.b]\ntype = "dielectric"\nmaterial = "Gold"\n', "unknown material"),
            "material on PEC": ('[bodies.b]\ntype = "pec"\nmaterial = "FR4"\n', "only dielectrics"),
            "bad preset value": ('[materials.X]\neps_r = 0.1\n', "material 'X'"),
            "unknown preset key": ('[materials.X]\ncolor = 1\n', "unknown key"),
        }
        for label, (text, needle) in bad_materials.items():
            with open(os.path.join(folder, "model.toml"), "w", encoding="utf-8") as f:
                f.write(text)
            ok, msg = raises(lambda: Project.load(step), needle)
            check(f"bad file: {label}", ok, msg)
    finally:
        shutil.rmtree(folder)

    print("FAILED: " + ", ".join(FAILED) if FAILED else "all checks passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
