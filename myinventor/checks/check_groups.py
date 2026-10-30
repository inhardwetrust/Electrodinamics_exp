# check_groups.py
#
# Groups of bodies (model/part.py Group, [[group]]):
#   - add_to_group makes a group, moves a body out of its old group (a body
#     in one group at most), a group left empty goes; remove_from_group;
#     rename_group (a taken name refused); ungroup (the bodies stay);
#     renaming a body renames it in its group; a TOML round trip;
#   - refused in a file: a body in two groups, no bodies, a name twice, an
#     unknown key;
#   - the window: under Solid Bodies a folder per group with its bodies, the
#     others beside; the group's tick hides / shows all its bodies (the view
#     only, as a body's); selecting it selects them all and tells their
#     volume; the Group submenu moves a body into a new group (named
#     Group1, one undo step), Remove from Group, Rename..., Ungroup;
#     several body rows selected (Shift / Ctrl) or Shift + clicks on bodies
#     in 3D: all selected, the Group submenu groups them all (existing groups
#     or New Group), the new group's row current; an import makes a group;
#     a selected group renamed: Move takes its new name; ungrouped: Move
#     takes no gone group (it did: group:<gone> in its targets).
#
#   python checks/check_groups.py [--no-window]

import os
import sys
import tempfile
import tomllib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "checks"))
sys.stdout.reconfigure(encoding="utf-8")
TMP = tempfile.mkdtemp()
os.environ["MYCAD_SETTINGS"] = os.path.join(TMP, "settings.json")     # never the user's settings

from model.extrude import Extrude                                     # noqa: E402
from model.part import Part, PartError                                # noqa: E402
from model.part_file import PartFileError, dumps, from_data          # noqa: E402
from model.sketch import Sketch                                       # noqa: E402

FAILED = []
RECT = ("L1", "L2", "L3", "L4")


def check(label, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {label} {detail}")
    if not ok:
        FAILED.append(label)


def three_boxes():
    """Bodies A (8), B (8), C (8): 2 x 2 x 2 cubes apart."""
    part = Part()
    for i, name in enumerate("ABC"):
        s = part.add_feature(Sketch(f"S{name}", plane="XY Plane"))
        x0 = 5 * i
        p = [s.add_point(str(x), str(y)) for x, y in ((x0, 0), (x0 + 2, 0), (x0 + 2, 2), (x0, 2))]
        for a, b in zip(p, p[1:] + p[:1]):
            s.add_line(a, b)
        part.add_feature(Extrude(f"E{name}", profiles=[RECT], sketch=f"S{name}", distance="2", body=name))
    return part


def check_model():
    part = three_boxes()
    part.add_to_group("G", ["A", "B"])
    check("add_to_group: G of A and B", [(g.name, g.bodies) for g in part.groups] == [("G", ["A", "B"])])
    part.add_to_group("H", ["B"])
    check("  B into H: out of G (a body in one group at most)",
          [(g.name, g.bodies) for g in part.groups] == [("G", ["A"]), ("H", ["B"])])
    part.add_to_group("H", ["A"])
    check("  A into H too: G left empty goes", [(g.name, g.bodies) for g in part.groups] == [("H", ["B", "A"])])
    part.remove_from_group(["B"])
    part.rename_body("A", "Alpha")
    check("remove_from_group B; renaming A renames it in its group",
          [(g.name, g.bodies) for g in part.groups] == [("H", ["Alpha"])])
    part.add_to_group("G", ["C"])
    try:
        part.rename_group("H", "G")
        check("rename_group to a taken name: refused", False)
    except PartError as error:
        check("rename_group to a taken name: refused", True, str(error))
    part.rename_group("H", "Pair")
    text = dumps(part)
    check("rename_group; TOML round trip", [g.name for g in part.groups] == ["Pair", "G"]
          and from_data(tomllib.loads(text)).to_dict() == part.to_dict() and "[[group]]" in text)
    part.ungroup("Pair")
    check("ungroup: the group goes, the body stays", [g.name for g in part.groups] == ["G"]
          and "Alpha" in part.body_names())

    head = dumps(three_boxes())
    for label, rows in (("a body in two groups", 'name = "G"\nbodies = ["A"]\n[[group]]\nname = "H"\nbodies = ["A"]\n'),
                        ("no bodies", 'name = "G"\nbodies = []\n'),
                        ("a name twice", 'name = "G"\nbodies = ["A"]\n[[group]]\nname = "G"\nbodies = ["B"]\n'),
                        ("an unknown key", 'name = "G"\nbodies = ["A"]\ncolor = "#ff0000"\n')):
        try:
            from_data(tomllib.loads(head + "\n[[group]]\n" + rows))
            check(f"refused in a file: {label}", False)
        except PartFileError as error:
            check(f"refused in a file: {label}", "group" in str(error), str(error))


def check_window():
    from vispy import app

    app.use_app("pyside6")
    app.use_app().create()
    from vispy_errors import install as _watch_vispy

    vispy_errors = _watch_vispy()
    from PySide6.QtCore import Qt

    from check_sketch_line import settle
    from view.main_window import MainWindow

    part = three_boxes()
    part.add_to_group("G", ["A", "B"])
    path = os.path.join(TMP, "boxes.part.toml")
    with open(path, "w", encoding="utf-8") as f:
        f.write(dumps(part))
    window = MainWindow()
    window.ask_save_changes = lambda: "discard"
    errors = []
    window.show_error = errors.append
    answers = []
    window.ask_text = lambda title, label, text: answers.pop(0) if answers else None
    window.resize(1200, 800)
    window.show()
    settle(300)
    window.open_file(path)
    settle()
    rows = window._rows
    group = rows.get(("group", "G"))
    check("Solid Bodies: a folder G with A and B, C beside", group is not None
          and rows[("body", "A")].parent() is group and rows[("body", "B")].parent() is group
          and rows[("body", "C")].parent() is group.parent())
    group.setCheckState(0, Qt.Unchecked)
    settle()
    check("  G's tick off: A and B hidden, C shown",
          window.view._visible.get("A") is False and window.view._visible.get("B") is False
          and window.view._visible.get("C", True) and rows[("body", "A")].checkState(0) == Qt.Unchecked)
    window._rows[("group", "G")].setCheckState(0, Qt.Checked)
    settle()
    check("  on again: shown", window.view._visible.get("A") and window.view._visible.get("B"))
    window.tree.setCurrentItem(window._rows[("group", "G")])
    settle()
    check("  selecting G selects A and B, the info tells their volume (16)",
          sorted(window.view.selection) == [("body", "A"), ("body", "B")] and "16 mm" in window.info.text(),
          window.info.text())
    window.tree.setCurrentItem(window._rows[("body", "B")])
    actions = dict(window.context_actions(window._rows[("body", "B")]))
    submenu = dict(actions.get("Group", []))
    check("a grouped body's menu: Rename..., Group (G, New Group), Remove from Group",
          list(actions) == ["Rename...", "Group", "Remove from Group"] and list(submenu) == ["G", "New Group"],
          f"{list(actions)} {list(submenu)}")
    steps = len(window.doc._undo)
    submenu["New Group"]()
    settle()
    check("  Group > New Group: B moves into Group1 (one undo step), its row current",
          [(g.name, g.bodies) for g in window.part.groups] == [("G", ["A"]), ("Group1", ["B"])]
          and len(window.doc._undo) == steps + 1
          and window._rows[("body", "B")].parent() is window._rows[("group", "Group1")]
          and window.tree.currentItem() is window._rows[("group", "Group1")])
    dict(window.context_actions(window._rows[("body", "B")]))["Remove from Group"]()
    settle()
    check("  Remove from Group: Group1 goes (empty)", [g.name for g in window.part.groups] == ["G"])

    window.tree.clearSelection()
    window.tree.setCurrentItem(window._rows[("body", "A")])
    window._rows[("body", "C")].setSelected(True)
    settle()
    check("several rows selected (A, C): both selected in 3D", sorted(window.view.selection) ==
          [("body", "A"), ("body", "C")], f"{window.view.selection}")
    actions = dict(window.context_actions(window._rows[("body", "C")]))
    check("  their menu: Group (G, New Group), Remove from Group (A is in G), no Rename",
          list(actions) == ["Group", "Remove from Group"] and list(dict(actions["Group"])) == ["G", "New Group"])
    dict(actions["Group"])["New Group"]()
    settle()
    check("  New Group: both in Group1 (A out of G, G gone: empty? no, B is out already)",
          [(g.name, sorted(g.bodies)) for g in window.part.groups] == [("Group1", ["A", "C"])],
          f"{[(g.name, g.bodies) for g in window.part.groups]}")
    window.part.ungroup("Group1")
    window._commit()
    window.part.add_to_group("G", ["A", "B"])
    window._commit()
    settle()
    view = window.view
    view.select(None)
    view._toggle(("face", "A", 0))
    view._toggle(("face", "C", 0))
    settle()
    check("Shift + clicks on A and C in 3D: both bodies selected, their rows too",
          sorted(view.selection) == [("body", "A"), ("body", "C")]
          and window._rows[("body", "A")].isSelected() and window._rows[("body", "C")].isSelected(),
          f"{view.selection}")
    view._toggle(("face", "A", 1))
    check("  A again: out", view.selection == [("body", "C")], f"{view.selection}")
    window.tree.setCurrentItem(window._rows[("group", "G")])     # G selected, then renamed
    settle()
    answers.append("Connector")
    dict(window.context_actions(window._rows[("group", "G")]))["Rename..."]()
    settle()
    check("a group's Rename...", [g.name for g in window.part.groups] == ["Connector"]
          and ("group", "Connector") in window._rows)
    window.move_action.trigger()
    settle()
    check("  Move with the renamed group selected: it takes group:Connector",
          window._moving["targets"] == ["group:Connector"], f"{window._moving['targets']}")
    window.move_panel.reject()
    settle()
    dict(window.context_actions(window._rows[("group", "Connector")]))["Ungroup"]()
    settle()
    check("  Ungroup: no group, the bodies stay", not window.part.groups and len(window.result.bodies) == 3
          and window._rows[("body", "A")].parent() is window._rows[("body", "C")].parent())
    window.move_action.trigger()
    settle()
    check("  then Move: no target of the gone group (the bug: group:Connector kept)",
          not any(t.startswith("group:") for t in window._moving["targets"]), f"{window._moving['targets']}")
    window.move_panel.reject()
    settle()

    from check_import import write_source

    source = os.path.join(TMP, "src", "sma.step")
    os.makedirs(os.path.dirname(source))
    write_source(source)
    name = window.import_step(source)
    settle()
    check("an import makes a group of its bodies", [(g.name, g.bodies) for g in window.part.groups]
          == [(name, window.part.feature(name).bodies)] and ("group", name) in window._rows)
    check("no error dialogs", not errors, f"{errors}")
    window.close()
    check("no errors in VisPy callbacks", not vispy_errors, f"{vispy_errors[:3]}")


def main():
    check_model()
    if "--no-window" not in sys.argv:
        check_window()
    print("FAILED: " + ", ".join(FAILED) if FAILED else "all checks passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
