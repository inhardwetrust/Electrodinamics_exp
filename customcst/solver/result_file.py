# result_file.py
#
# A finished simulation lives next to its STEP file: model.step ->
# model.npz (numpy archive, no pickle), so reopening the model shows the
# last results without running again.
#
# Contents: frequencies (Hz), port names and reference impedances, the
# S-matrix s[j, k, f], Z_in[k, f] (NaN where missing), the run's info
# lines and comment, the save time, and a fingerprint of the inputs.
#
# Fingerprint: SHA-256 of the STEP file's bytes plus the project's body
# types, parameter values and [simulation] / [mesh] settings (not [view]).
# When it differs from the current model, the results are still shown but
# marked as outdated.

import hashlib
import json
import os
import time

import numpy as np

FORMAT_VERSION = 1


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


def result_path(step_path):
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
    }
    digest.update(json.dumps(inputs, sort_keys=True, ensure_ascii=False).encode("utf-8"))
    return digest.hexdigest()


def save_result(step_path, result):
    """Writes atomically (temp file + replace)."""
    n, f = len(result.ports), len(result.frequencies)
    s = np.full((n, n, f), np.nan + 0j)
    for (j, k), values in result.s.items():
        s[j, k] = values
    z_in = np.full((n, f), np.nan + 0j)
    for k, name in enumerate(result.ports):
        if name in result.z_in:
            z_in[k] = result.z_in[name]

    result.saved = time.strftime("%Y-%m-%d %H:%M")
    path = result_path(step_path)
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
    )
    os.replace(temp, path)
    return path


def load_result(step_path):
    """The saved SimulationResult next to step_path, or None if there is none."""
    path = result_path(step_path)
    if not os.path.isfile(path):
        return None
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
    return result
