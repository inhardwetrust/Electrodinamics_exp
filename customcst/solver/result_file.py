# result_file.py
#
# Finished simulations live next to their STEP file, one file per run:
#   model.step -> model.results/20261018-125301.npz, ...
# (numpy archives, no pickle), so reopening the model shows the earlier runs
# and they can be compared.
#
# A run file holds: frequencies (Hz), port names and reference impedances,
# the S-matrix s[j, k, f], Z_in[k, f] (NaN where missing), the run's info
# lines and comment, its name (the user can rename it), the save time, the
# wall-clock time the whole run took, the eps_eff of each waveguide port's
# line (NaN for a lumped port; for moving the reference plane), a
# snapshot of the parameters it was computed with (JSON: settings, grid,
# materials, vias, ports) and a fingerprint of the inputs.
#
# Fingerprint: SHA-256 of the STEP file's bytes plus the project's body
# types, parameter values, hole rules and [simulation] / [mesh] settings
# (not [view]). When it differs from the current model, a run is still
# shown but marked as outdated.
#
# An older single-result file (model.npz, format 1) is moved into the folder
# as the first run the next time the results are listed.

import hashlib
import json
import os
import re
import time

import numpy as np

FORMAT_VERSION = 2


class SimulationResult:
    def __init__(self, frequencies, ports, resistances, comment=""):
        self.frequencies = frequencies
        self.ports = ports              # port names, in S-matrix order
        self.resistances = resistances  # reference impedance per port (Ohm)
        self.comment = comment
        self.s = {}                     # (j, k) -> complex array
        self.z_in = {}                  # port -> complex array
        self.info = []                  # text lines (steps, status, time)
        self.fingerprint = ""           # inputs the run was made from
        self.saved = ""                 # local time of saving, "" = not saved
        self.name = ""                  # shown in the Results list (renamable)
        self.params = {}                # what it was computed with (shown, not used)
        self.path = ""                  # its file, "" = not saved
        self.elapsed = float("nan")     # wall-clock seconds of the whole run, NaN = unknown
        self.eps_eff = [float("nan")] * len(ports)   # per port: its line's eps_eff, NaN = lumped / unknown


def format_duration(seconds):
    """12.3 s, 4 min 05 s, 1 h 02 min; "" when unknown."""
    if not np.isfinite(seconds):
        return ""
    if seconds < 60:
        return f"{seconds:.1f} s"
    minutes, s = divmod(int(round(seconds)), 60)
    if minutes < 60:
        return f"{minutes} min {s:02d} s"
    return f"{minutes // 60} h {minutes % 60:02d} min"


def results_dir(step_path):
    return os.path.splitext(os.fspath(step_path))[0] + ".results"


def legacy_path(step_path):
    return os.path.splitext(os.fspath(step_path))[0] + ".npz"


def fingerprint(project):
    """Hash of everything the results depend on: geometry, types, settings."""
    digest = hashlib.sha256()
    with open(project.step_path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    inputs = {
        "bodies": {name: [a.type, a.values] for name, a in sorted(project.assignments.items())},
        "settings": {name: project.settings[name] for name in sorted(project.settings)},
        "holes": [sorted(project.hole_diameters.items()), sorted(project.hole_single.items())],
    }
    digest.update(json.dumps(inputs, sort_keys=True, ensure_ascii=False).encode("utf-8"))
    return digest.hexdigest()


def save_result(step_path, result):
    """
    A new run file in the results folder (named by the save time); the
    result gets its path, save time and, if it has none, a name "Run N".
    Returns the path.
    """
    folder = results_dir(step_path)
    os.makedirs(folder, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path, n = os.path.join(folder, f"{stamp}.npz"), 1
    while os.path.exists(path):
        n += 1
        path = os.path.join(folder, f"{stamp}-{n}.npz")
    result.saved = time.strftime("%Y-%m-%d %H:%M:%S")
    if not result.name:
        result.name = f"Run {_next_run_number(list_results(step_path)[0])}"
    write_result(path, result)
    return path


def write_result(path, result):
    """Writes (or rewrites, e.g. after a rename) one run file atomically."""
    n, f = len(result.ports), len(result.frequencies)
    s = np.full((n, n, f), np.nan + 0j)
    for (j, k), values in result.s.items():
        s[j, k] = values
    z_in = np.full((n, f), np.nan + 0j)
    for k, name in enumerate(result.ports):
        if name in result.z_in:
            z_in[k] = result.z_in[name]

    temp = path + ".tmp.npz"
    np.savez_compressed(
        temp,
        version=np.array(FORMAT_VERSION),
        frequencies=np.asarray(result.frequencies, dtype=np.float64),
        ports=np.array(result.ports, dtype=str),
        resistances=np.asarray(result.resistances, dtype=np.float64),
        s=s,
        z_in=z_in,
        info=np.array(result.info, dtype=str),
        comment=np.array(result.comment),
        fingerprint=np.array(result.fingerprint),
        saved=np.array(result.saved),
        name=np.array(result.name),
        params=np.array(json.dumps(result.params, ensure_ascii=False)),
        elapsed=np.array(float(result.elapsed)),
        eps_eff=np.asarray(result.eps_eff, dtype=np.float64),
    )
    os.replace(temp, path)
    result.path = path


def read_result(path):
    with np.load(path, allow_pickle=False) as data:
        if int(data["version"]) > FORMAT_VERSION:
            raise ValueError(f"{path}: written by a newer version (format {int(data['version'])})")
        ports = [str(p) for p in data["ports"]]
        result = SimulationResult(data["frequencies"], ports,
                                  [float(r) for r in data["resistances"]], str(data["comment"]))
        s, z_in = data["s"], data["z_in"]
        for j in range(len(ports)):
            for k in range(len(ports)):
                if not np.all(np.isnan(s[j, k])):
                    result.s[j, k] = s[j, k]
        for k, name in enumerate(ports):
            if not np.all(np.isnan(z_in[k])):
                result.z_in[name] = z_in[k]
        result.info = [str(line) for line in data["info"]]
        result.fingerprint = str(data["fingerprint"])
        result.saved = str(data["saved"])
        if "name" in data:
            result.name = str(data["name"])
        if "params" in data:
            result.params = json.loads(str(data["params"]))
        if "elapsed" in data:
            result.elapsed = float(data["elapsed"])
        if "eps_eff" in data and len(data["eps_eff"]) == len(ports):
            result.eps_eff = [float(e) for e in data["eps_eff"]]
    result.path = path
    return result


def list_results(step_path):
    """
    ([SimulationResult] oldest first, [error texts]) of the model's runs.
    A damaged file is skipped with an error text, never an exception.
    """
    _migrate_legacy(step_path)
    folder = results_dir(step_path)
    runs, errors = [], []
    if os.path.isdir(folder):
        for name in sorted(os.listdir(folder)):
            if not name.endswith(".npz") or name.endswith(".tmp.npz"):
                continue
            try:
                runs.append(read_result(os.path.join(folder, name)))
            except Exception as error:          # a damaged file must not hide the others
                errors.append(f"{name}: {error}")
    runs.sort(key=lambda r: (r.saved, r.path))
    for n, run in enumerate(runs, start=1):
        run.name = run.name or f"Run {n}"
    return runs, errors


def rename_result(result, name):
    name = str(name).strip()
    if not name:
        raise ValueError("a result needs a name")
    result.name = name
    write_result(result.path, result)


def delete_result(result):
    if result.path and os.path.isfile(result.path):
        os.remove(result.path)
    result.path = ""


def _next_run_number(runs):
    numbers = [int(m.group(1)) for r in runs if (m := re.fullmatch(r"Run (\d+)", r.name))]
    return max(numbers, default=len(runs)) + 1


def _migrate_legacy(step_path):
    """model.npz (one result, format 1) -> the first run in model.results/."""
    old = legacy_path(step_path)
    if not os.path.isfile(old):
        return
    try:
        result = read_result(old)
    except Exception:           # leave a file we cannot read where it is
        return
    folder = results_dir(step_path)
    os.makedirs(folder, exist_ok=True)
    stamp = re.sub(r"\D", "", result.saved)[:12] or time.strftime("%Y%m%d%H%M")
    path = os.path.join(folder, f"{stamp[:8]}-{stamp[8:]}00-imported.npz")
    result.name = result.name or "Run 1"
    write_result(path, result)
    os.remove(old)
