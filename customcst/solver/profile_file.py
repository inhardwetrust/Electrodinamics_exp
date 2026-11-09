# profile_file.py
#
# Computed Z0 profiles (solver/line_profile.py, solver/path_profile.py)
# kept on disk with the runs, so a model reopened with the same mesh shows
# them at once:
#   model.step -> model.results/z0profiles/x.npz, y, z      (the last one per axis)
#                 model.results/z0profiles/path/<stamp>.npz (every path profile)
# (numpy archives, no pickle; a folder, so the run listing, which reads
# the .npz files of model.results/, does not see them).
#
# Each file holds one profile and its key: SHA-256 of the results'
# fingerprint (solver/result_file.py: the STEP bytes, the body types and
# values, the hole rules, the [simulation] / [mesh] settings: all the mesh
# is made of), the source, the Z0 path for the path's profile (not in the
# fingerprint: a display setting) and PROFILE_VERSION (raised when the
# profile solvers change what they compute). A file whose key differs is
# outdated: not loaded as the current one. A damaged file is skipped,
# never an exception.
#
# The path's profiles are kept like the runs: one file each, with a name
# ("Profile N", renamable), the save time, the model's fingerprint and the
# Z0 path ([z0path]) they were computed for. The window lists them under
# Z0 path, current or outdated (the model / mesh changed, or the path),
# and deletes them on request. A new Compute with the same key replaces
# that one (the same result), keeping its name. An older single
# z0profiles/path.npz is moved in as the first one.

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field

import numpy as np

from solver.line_profile import Profile, Section
from solver.path_profile import PathProfile
from solver.result_file import fingerprint, results_dir

PROFILE_VERSION = 1
SOURCES = ("x", "y", "z", "path")


def profiles_dir(step_path):
    return os.path.join(results_dir(step_path), "z0profiles")


def path_profiles_dir(step_path):
    return os.path.join(profiles_dir(step_path), "path")


def profile_key(project, source, z0path=None, model_fingerprint=None):
    """The key of a profile along `source` of the project as it is now (z0path: the path's settings)."""
    digest = hashlib.sha256()
    digest.update((model_fingerprint or fingerprint(project)).encode())
    digest.update(json.dumps({"source": source, "version": PROFILE_VERSION,
                              "z0path": z0path if source == "path" else None},
                             sort_keys=True, default=_plain).encode())
    return digest.hexdigest()


def _plain(value):
    return np.asarray(value).tolist()


@dataclass
class PathEntry:
    """A saved path profile (its file) and what it was computed for."""
    path: str
    name: str
    saved: str
    key: str
    model: str = ""                 # the model's fingerprint ("" = not recorded: an older file)
    z0path: dict = None             # the [z0path] settings (None = not recorded)
    profile: object = field(default=None, repr=False)

    def status(self, key, model):
        """("current", "") or ("outdated", why) against the current key and model fingerprint."""
        if self.key == key:
            return "current", ""
        if self.model and self.model != model:
            return "outdated", "the model, the body types or the mesh / band settings changed"
        if self.model:
            return "outdated", "the path changed"
        return "outdated", "the model or the path changed"


def save_profile(step_path, source, profile, key, model_fingerprint="", z0path=None):
    """
    Writes (atomically) the profile along `source` with its key. Along an
    axis it replaces the one there; along the path it is a new entry (or
    replaces the entry with the same key). Returns the file.
    """
    if source == "path":
        return _save_path(step_path, profile, key, model_fingerprint, z0path)
    folder = profiles_dir(step_path)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"{source}.npz")
    _write(path, profile, key)
    return path


def _write(path, profile, key, extra=None):
    sections = profile.sections
    data = dict(
        version=np.array(PROFILE_VERSION),
        key=np.array(key),
        axis=np.array(int(profile.axis)),
        coordinate=np.array([s.coordinate for s in sections], dtype=np.float64),
        z0=np.array([s.z0 for s in sections], dtype=np.float64),
        eps_eff=np.array([s.eps_eff for s in sections], dtype=np.float64),
        error=np.array([s.error for s in sections], dtype=str),
    )
    if isinstance(profile, PathProfile):
        data.update(points=np.asarray(profile.points, dtype=np.float64).reshape(-1, 3),
                    across=np.asarray(profile.across, dtype=np.float64).reshape(-1, 3),
                    half_width=np.array(float(profile.half_width)))
    data.update(extra or {})
    temp = path + ".tmp.npz"
    np.savez_compressed(temp, **data)
    os.replace(temp, path)


def _save_path(step_path, profile, key, model, z0path):
    entries = list_path_profiles(step_path)
    same = next((e for e in entries if e.key == key), None)
    if same is not None:
        path, name = same.path, same.name
    else:
        folder = path_profiles_dir(step_path)
        os.makedirs(folder, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        path, n = os.path.join(folder, f"{stamp}.npz"), 1
        while os.path.exists(path):
            n += 1
            path = os.path.join(folder, f"{stamp}-{n}.npz")
        numbers = [int(m.group(1)) for e in entries for m in [re.fullmatch(r"Profile (\d+)", e.name)] if m]
        name = f"Profile {max(numbers, default=0) + 1}"
    _write(path, profile, key, _path_extra(name, time.strftime("%Y-%m-%d %H:%M:%S"), model, z0path))
    return path


def _path_extra(name, saved, model, z0path):
    return dict(name=np.array(name), saved=np.array(saved), model=np.array(model or ""),
                z0path=np.array(json.dumps(z0path, default=_plain) if z0path is not None else ""))


def _read(path):
    """(profile, key, extra {name, saved, model, z0path}) of a file, or None (damaged / another version)."""
    try:
        with np.load(path, allow_pickle=False) as data:
            if int(data["version"]) != PROFILE_VERSION:
                return None
            sections = [Section(float(c), float(z), float(e), str(err))
                        for c, z, e, err in zip(data["coordinate"], data["z0"], data["eps_eff"], data["error"])]
            axis = int(data["axis"])
            if "points" in data:
                profile = PathProfile(axis=axis, sections=sections, points=list(data["points"]),
                                      across=list(data["across"]), half_width=float(data["half_width"]))
            else:
                profile = Profile(axis, sections)
            extra = {k: str(data[k]) for k in ("name", "saved", "model", "z0path") if k in data}
            return profile, str(data["key"]), extra
    except Exception:           # a damaged file is outdated, not an error
        return None


def load_profile(step_path, source, key):
    """The saved profile along `source` if its key is `key`, else None."""
    if source == "path":
        return next((e.profile for e in list_path_profiles(step_path) if e.key == key), None)
    path = os.path.join(profiles_dir(step_path), f"{source}.npz")
    if not os.path.isfile(path):
        return None
    read = _read(path)
    return read[0] if read is not None and read[1] == key else None


def list_path_profiles(step_path):
    """The saved path profiles, newest first (damaged files skipped); moves an older path.npz in."""
    _migrate_legacy(step_path)
    folder = path_profiles_dir(step_path)
    entries = []
    if not os.path.isdir(folder):
        return entries
    for file in sorted(os.listdir(folder), reverse=True):
        if not file.endswith(".npz") or file.endswith(".tmp.npz"):
            continue
        path = os.path.join(folder, file)
        read = _read(path)
        if read is None:
            continue
        profile, key, extra = read
        try:
            z0path = json.loads(extra["z0path"]) if extra.get("z0path") else None
        except ValueError:
            z0path = None
        entries.append(PathEntry(path, extra.get("name") or file[:-4], extra.get("saved", ""), key,
                                 extra.get("model", ""), z0path, profile))
    return entries


def _migrate_legacy(step_path):
    old = os.path.join(profiles_dir(step_path), "path.npz")
    if not os.path.isfile(old):
        return
    read = _read(old)
    if read is None:
        return
    profile, key, _ = read
    folder = path_profiles_dir(step_path)
    os.makedirs(folder, exist_ok=True)
    when = time.localtime(os.path.getmtime(old))
    path = os.path.join(folder, time.strftime("%Y%m%d-%H%M%S", when) + ".npz")
    _write(path, profile, key, _path_extra("Profile 1", time.strftime("%Y-%m-%d %H:%M:%S", when), "", None))
    os.remove(old)


def rename_path_profile(entry, name):
    """A new name for a saved path profile (its file rewritten)."""
    read = _read(entry.path)
    if read is None:
        raise OSError(f"{entry.path} does not read")
    profile, key, extra = read
    try:
        z0path = json.loads(extra["z0path"]) if extra.get("z0path") else None
    except ValueError:
        z0path = None
    _write(entry.path, profile, key, _path_extra(name, extra.get("saved", ""), extra.get("model", ""), z0path))
    entry.name = name


def delete_path_profile(entry):
    if os.path.isfile(entry.path):
        os.remove(entry.path)
