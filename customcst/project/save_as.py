# save_as.py
#
# Save As of a model: everything that belongs to model.step goes along under
# the new name, so the copy opens anywhere on its own:
#   model.step        -> new.step          (the bytes: the fingerprint stays)
#   model.toml        -> new.toml          (the header line names new.step)
#   model.results/    -> new.results/      (runs, z0profiles; NOT remote.json:
#                                           the server runs were launched for
#                                           the original, they stay with it)
#   model.presets/    -> new.presets/
#   model.npz         -> new.npz           (a format-1 result, if still there)
# Not copied: model.status.json (run.py's status of a run going now).
# Nothing in these files holds a path: a run's fingerprint is the STEP's
# bytes plus the project's contents, so the copied runs stay current.

import os
import shutil

from project.presets import presets_dir
from project.project_file import project_path
from solver.result_file import legacy_path, results_dir

SKIPPED_IN_RESULTS = ("remote.json", "remote.json.tmp")
STEP_EXTENSIONS = (".step", ".stp")


class SaveAsError(Exception):
    pass


def model_files(step_path):
    """{what: path} of a model's files beside its STEP (those that exist or not)."""
    return {"step": os.fspath(step_path), "project": project_path(step_path),
            "results": results_dir(step_path), "presets": os.path.dirname(presets_dir(step_path, "x")),
            "legacy": legacy_path(step_path)}


def existing(step_path):
    """The model's files (and folders) that exist, for an overwrite question."""
    return [p for p in model_files(step_path).values() if os.path.exists(p)]


def _same(a, b):
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def save_as(src, dst, overwrite=False):
    """
    Copies the model at src (STEP + its files) to dst. A dst model that
    exists is an error unless overwrite: then its files are removed first.
    Returns the lines of what was done.
    """
    src, dst = os.path.abspath(src), os.path.abspath(dst)
    if os.path.splitext(dst)[1].lower() not in STEP_EXTENSIONS:
        raise SaveAsError(f"{os.path.basename(dst)}: the name must end in .step or .stp")
    if not os.path.isfile(src):
        raise SaveAsError(f"{src} does not exist")
    if _same(os.path.splitext(src)[0], os.path.splitext(dst)[0]):
        raise SaveAsError("that is the model itself (same name)")
    old = existing(dst)
    if old and not overwrite:
        raise SaveAsError("exists: " + ", ".join(os.path.basename(p) for p in old))
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    for path in old:
        if os.path.isdir(path):
            shutil.rmtree(path)
        else:
            os.remove(path)

    a, b = model_files(src), model_files(dst)
    lines = []
    shutil.copy2(a["step"], b["step"])
    lines.append(f"{os.path.basename(b['step'])}")
    if os.path.isfile(a["project"]):
        with open(a["project"], encoding="utf-8") as file:
            text = file.read()
        header = f"# customcst project for {os.path.basename(a['step'])}"
        if text.startswith(header):
            text = f"# customcst project for {os.path.basename(b['step'])}" + text[len(header):]
        with open(b["project"], "w", encoding="utf-8", newline="") as file:
            file.write(text)
        lines.append(os.path.basename(b["project"]))
    if os.path.isdir(a["results"]):
        shutil.copytree(a["results"], b["results"], ignore=lambda folder, names: [
            n for n in names if _same(folder, a["results"]) and n in SKIPPED_IN_RESULTS])
        runs = [n for n in os.listdir(b["results"]) if n.endswith(".npz")]
        lines.append(f"{os.path.basename(b['results'])}/ ({len(runs)} runs)")
        if os.path.isfile(os.path.join(a["results"], "remote.json")):
            lines.append("server launches not copied: they stay with the original")
    if os.path.isdir(a["presets"]):
        shutil.copytree(a["presets"], b["presets"])
        lines.append(os.path.basename(b["presets"]) + "/")
    if os.path.isfile(a["legacy"]):
        shutil.copy2(a["legacy"], b["legacy"])
        lines.append(os.path.basename(b["legacy"]))
    return lines
