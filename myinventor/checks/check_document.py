# check_document.py
#
# The document layer (model/), no window:
#   - formulas: arithmetic, ^ as power, functions in degrees, constants;
#     code, attributes, strings, unknown functions are refused; overflow
#     and division by zero are errors, not crashes;
#   - the parameter table: values in dependency order (a parameter may use
#     one below it); every error names its parameter (syntax, unknown name,
#     a cycle, a dependency with an error);
#   - rename rewrites the formulas (whole names only: w -> width leaves
#     w2 alone), a bad or taken name is refused, a used parameter cannot be
#     deleted;
#   - the file: a part round-trips through TOML exactly (parameters in
#     order, comments, origin visibility, features of a test type), the
#     text is valid TOML; broken files are refused with a message naming the
#     place, and the file on disk is untouched;
#   - features: unknown type, unknown field, a missing required field, a
#     wrong choice, a reference to a feature below or to nothing;
#     rename_reference / formulas;
#   - the document: modified flag, commit / undo / redo, save / open,
#     changed on disk, the name from the file;
#   - rebuild: values and errors; a failing feature and the ones that use it.
#
#   python checks/check_document.py

import os
import sys
import tempfile
import tomllib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

from model.document import Document                                   # noqa: E402
from model.expressions import ExpressionError, evaluate, evaluate_parameters, names   # noqa: E402
from model.features import Feature, Field, feature_type                # noqa: E402
from model.part import Part, PartError                                # noqa: E402
from model.part_file import PartFileError, dumps, load, save          # noqa: E402
from model.rebuild import rebuild                                     # noqa: E402

FAILED = []
TMP = tempfile.mkdtemp()


def check(label, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {label} {detail}")
    if not ok:
        FAILED.append(label)


def raises(error_type, fn, *args, **kwargs):
    """The message if fn(*args, **kwargs) raises error_type, else None."""
    try:
        fn(*args, **kwargs)
    except error_type as error:
        return str(error)
    return None


# A feature type for the checks only (real ones come with the geometry).
@feature_type
class _Pad(Feature):
    TYPE = "test_pad"
    FIELDS = (
        Field("on", "ref"),
        Field("size", "exprs"),
        Field("height", "expr"),
        Field("operation", "choice", "new body", ("new body", "join", "cut")),
        Field("body", "text", ""),
        Field("flip", "bool", False),
    )

    def build(self, ctx):
        h = evaluate(self["height"], ctx.values)
        if h <= 0:
            raise ValueError(f"height must be > 0, got {h:g}")
        return h


def check_formulas():
    cases = {"1.6": 1.6, "2^3": 8.0, "2**3": 8.0, "-L/2 + 1": -4.0, "sqrt(9) + abs(-1)": 4.0,
             "sin(30)": 0.5, "atan2(1, 1)": 45.0, "max(1, L, 3)": 10.0, "pi": 3.141592653589793,
             "7 % 4": 3.0, "1e-3": 0.001}
    got = {text: evaluate(text, {"L": 10.0}) for text in cases}
    check("formulas evaluate", all(abs(got[t] - v) < 1e-12 for t, v in cases.items()), f"{got}")
    refused = ["__import__('os')", "L.real", "'abc'", "foo(1)", "a if b else c", "[1]", "1 < 2",
               "lambda: 1", "sqrt(x=1)", "", "True"]
    bad = [t for t in refused if raises(ExpressionError, evaluate, t, {"L": 1.0, "a": 1, "b": 1, "c": 1}) is None]
    check("code, attributes, strings, unknown functions are refused", not bad, f"accepted: {bad}")
    check("overflow is an error", raises(ExpressionError, evaluate, "10^10^10", {}) is not None)
    check("division by zero is an error", raises(ExpressionError, evaluate, "1/0", {}) is not None)
    check("names() skips functions and constants", names("sqrt(a) + pi * b") == {"a", "b"})


def check_parameters():
    values, errors = evaluate_parameters([("a", "b * 2"), ("b", "3"), ("c", "a + b")])
    check("dependency order (a uses b below it)", values == {"a": 6.0, "b": 3.0, "c": 9.0} and not errors,
          f"{values} {errors}")
    values, errors = evaluate_parameters([("x", "y + 1"), ("y", "x + 1"), ("z", "x"), ("u", "nope"),
                                          ("s", "1 +"), ("t", "s"), ("ok", "2")])
    check("cycle: both named", "circular" in errors.get("x", "") and "circular" in errors.get("y", ""), f"{errors}")
    check("uses a parameter on a cycle", "'x'" in errors.get("z", ""), errors.get("z"))
    check("unknown name", "unknown name 'nope'" in errors.get("u", ""), errors.get("u"))
    check("syntax error", "syntax" in errors.get("s", ""), errors.get("s"))
    check("uses one with an error", "'s'" in errors.get("t", ""), errors.get("t"))
    check("the others still evaluate", values == {"ok": 2.0}, f"{values}")

    part = Part()
    part.add_parameter("w", "1.6")
    part.add_parameter("w2", "w * 2")
    part.add_parameter("gap", "w + w2")
    part.rename_parameter("w", "width")
    check("rename rewrites whole names only",
          [p.formula for p in part.parameters] == ["1.6", "width * 2", "width + w2"],
          f"{[p.formula for p in part.parameters]}")
    check("a taken name is refused", raises(PartError, part.rename_parameter, "w2", "gap") is not None)
    check("a bad name is refused", all(raises(PartError, part.add_parameter, n) for n in ("2x", "sin", "if", "a b")))
    message = raises(PartError, part.delete_parameter, "width")
    check("a used parameter cannot be deleted (users named)", message is not None and "w2" in message, message)
    part.delete_parameter("gap")
    check("an unused one can", [p.name for p in part.parameters] == ["width", "w2"])
    check("free names p1, p2", part.add_parameter() == "p1" and part.add_parameter() == "p2")


def sample_part():
    part = Part()
    part.add_parameter("L", "11.46", "patch length")
    part.add_parameter("h", "L / 10")
    part.datum("XY Plane").visible = True
    part.features.append(_Pad("Pad1", on="XY Plane", size=["L", "L/2"], height="h", body='patch "top"'))
    part.features.append(_Pad("Pad2", on="Pad1", size=["1", "2"], height="0.5", operation="join", flip=True))
    return part


def check_file():
    part = sample_part()
    path = os.path.join(TMP, "sample.part.toml")
    save(path, part)
    with open(path, encoding="utf-8") as f:
        text = f.read()
    check("the file is valid TOML", isinstance(tomllib.loads(text), dict))
    check("the file starts with the format", text.startswith('format = "mycad-part"\nversion = 1\n'))
    back = load(path)
    check("round trip: the same part", back.to_dict() == part.to_dict())
    check("round trip: parameter order and comment",
          [(p.name, p.formula, p.comment) for p in back.parameters]
          == [("L", "11.46", "patch length"), ("h", "L / 10", "")])
    check("round trip: origin visibility", [d.name for d in back.origin if d.visible] == ["XY Plane"])
    check("round trip: features", back.features == part.features and back.features[1]["flip"] is True)
    check("a new part writes only the header", dumps(Part()) == 'format = "mycad-part"\nversion = 1\n')

    head = 'format = "mycad-part"\nversion = 1\n'
    feature = '[[feature]]\nname = "Pad1"\ntype = "test_pad"\non = "XY Plane"\nsize = ["1", "2"]\nheight = "1"\n'
    broken = {
        "not TOML": ("format = = 1", "not valid TOML"),
        "no format": ("version = 1\n", "not a myCad part"),
        "newer version": ('format = "mycad-part"\nversion = 99\n', "newer"),
        "unknown top key": (head + "colour = 1\n", "unknown key"),
        "unknown origin item": (head + '[origin]\nvisible = ["AB Plane"]\n', "AB Plane"),
        "parameter without formula": (head + '[[parameter]]\nname = "a"\n', "parameter 1"),
        "bad parameter name": (head + '[[parameter]]\nname = "1a"\nformula = "1"\n', "parameter 1"),
        "duplicate parameter": (head + '[[parameter]]\nname = "a"\nformula = "1"\n'
                                       '[[parameter]]\nname = "a"\nformula = "2"\n', "parameter 2"),
        "unknown feature type": (head + '[[feature]]\nname = "X"\ntype = "warp"\n', "unknown type 'warp'"),
        "unknown feature field": (head + feature + 'colour = "red"\n', "unknown field"),
        "missing field": (head + '[[feature]]\nname = "Pad1"\ntype = "test_pad"\non = "XY Plane"\n'
                                 'size = ["1"]\n', "'height' is required"),
        "wrong choice": (head + feature + 'operation = "melt"\n', "must be one of"),
        "reference to nothing": (head + feature.replace('"XY Plane"', '"Sketch9"'), "Sketch9"),
        "reference below": (head + feature.replace('"XY Plane"', '"Pad2"')
                            + feature.replace("Pad1", "Pad2"), "Pad2"),
        "duplicate feature": (head + feature + feature, "taken"),
        "feature named like a datum": (head + feature.replace('name = "Pad1"', 'name = "X Axis"'), "taken"),
    }
    for label, (text, expected) in broken.items():
        path = os.path.join(TMP, "broken.part.toml")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        message = raises(PartFileError, load, path)
        with open(path, encoding="utf-8") as f:
            untouched = f.read() == text
        check(f"refused: {label}", message is not None and expected in message and untouched, f"{message}")


def check_features():
    pad = sample_part().features[0]
    check("references", pad.references() == ["XY Plane"])
    check("formulas", pad.formulas() == [("size[0]", "L"), ("size[1]", "L/2"), ("height", "h")])
    part = sample_part()
    part.rename_parameter("L", "length")
    check("rename reaches feature formulas", part.features[0]["size"] == ["length", "length/2"])
    message = raises(PartError, part.delete_parameter, "h")
    check("a parameter used by a feature cannot be deleted", message is not None and "Pad1" in message, message)
    check("a wrong value type is refused", raises(Exception, _Pad, "P", on="XY Plane", size="1", height="1"))


def check_document():
    doc = Document()
    check("a new document: Part1, not modified, no undo", doc.name == "Part1" and not doc.modified
          and not doc.can_undo)
    doc.part.add_parameter("a", "1")
    doc.commit()
    doc.part.parameter("a").formula = "2"
    doc.commit()
    check("edits: modified, undo", doc.modified and doc.can_undo)
    check("commit without a change records nothing", not doc.commit())
    doc.undo()
    check("undo: the formula is back", doc.part.parameter("a").formula == "1" and doc.can_redo)
    doc.undo()
    check("undo to the start: not modified", not doc.modified and not doc.part.parameters)
    doc.redo()
    doc.redo()
    check("redo twice: the last edit", doc.part.parameter("a").formula == "2")
    doc.part.parameter("a").formula = "3"
    doc.commit()
    check("a new edit clears redo", not doc.can_redo)

    path = os.path.join(TMP, "My Patch.part.toml")
    doc.save(path)
    check("saved: not modified, the name from the file", not doc.modified and doc.name == "My Patch")
    reopened = Document.open(path)
    check("open: the same part", reopened.part.to_dict() == doc.part.to_dict() and not reopened.modified)
    check("not changed on disk", not reopened.changed_on_disk())
    os.utime(path, ns=(1, 1))
    check("changed on disk is seen", reopened.changed_on_disk())
    doc.part.parameter("a").formula = "bad +"
    doc.revert()
    check("revert drops an uncommitted edit", doc.part.parameter("a").formula == "3")


def check_rename_compat():
    """The program was myinventor before: its files and its settings still load."""
    path = os.path.join(TMP, "old.part.toml")
    with open(path, "w", encoding="utf-8") as f:
        f.write('format = "myinventor-part"\nversion = 1\n[[parameter]]\nname = "a"\nformula = "1"\n')
    check("a file written as myinventor-part still opens", load(path).parameter("a").formula == "1")
    check("new files are written as mycad-part", dumps(Part()).startswith('format = "mycad-part"'))

    import json
    import app_settings
    saved = {k: os.environ.get(k) for k in ("MYCAD_SETTINGS", "APPDATA")}
    appdata = os.path.join(TMP, "appdata")
    os.makedirs(os.path.join(appdata, "myinventor"))
    with open(os.path.join(appdata, "myinventor", "settings.json"), "w", encoding="utf-8") as f:
        json.dump({"version": 1, "recent_files": ["C:/old.part.toml"]}, f)
    try:
        os.environ.pop("MYCAD_SETTINGS", None)
        os.environ["APPDATA"] = appdata
        settings = app_settings.AppSettings.load()
        check("old settings (APPDATA/myinventor) are read once", settings.recent_files == ["C:/old.part.toml"])
        settings.save()
        check("  and saved to APPDATA/myCad", os.path.isfile(os.path.join(appdata, "myCad", "settings.json")))
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def check_rebuild():
    part = sample_part()
    part.add_parameter("bad", "nope * 2")
    result = rebuild(part)
    check("rebuild: values", abs(result.values["h"] - 1.146) < 1e-12, f"{result.values}")
    check("rebuild: parameter errors", "bad" in result.errors)
    outputs = {k: round(v, 12) for k, v in result.outputs.items()}
    check("rebuild: features build", outputs == {"Pad1": 1.146, "Pad2": 0.5} and not result.feature_errors,
          f"{result.outputs} {result.feature_errors}")
    part.parameter("h").formula = "-1"
    result = rebuild(part)
    check("a failing feature and the one using it",
          "height must be > 0" in result.feature_errors.get("Pad1", "")
          and "'Pad1'" in result.feature_errors.get("Pad2", ""), f"{result.feature_errors}")


def main():
    check_formulas()
    check_parameters()
    check_file()
    check_features()
    check_document()
    check_rebuild()
    check_rename_compat()
    print("FAILED: " + ", ".join(FAILED) if FAILED else "all checks passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
