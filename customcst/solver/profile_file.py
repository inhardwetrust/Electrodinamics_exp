# profile_file.py
#
# Computed Z0 profiles (solver/line_profile.py, solver/path_profile.py)
# kept on disk with the runs, so a model reopened with the same mesh shows
# them at once:
#   model.step -> model.results/z0profiles/x.npz, y, z, path.npz
# (numpy archives, no pickle; a folder, so the run listing, which reads
# the .npz files of model.results/, does not see them).
#
# Each file holds one profile and its key: SHA-256 of the results'
# fingerprint (solver/result_file.py: the STEP bytes, the body types and
# values, the hole rules, the [simulation] / [mesh] settings: all the mesh
# is made of), the source, the Z0 path for the path's profile (not in the
# fingerprint: a display setting) and PROFILE_VERSION (raised when the
# profile solvers change what they compute). A file whose key differs is
# outdated: not loaded, overwritten by the next Compute. A damaged file is
# skipped, never an exception.

import hashlib
import json
import os

import numpy as np

from solver.line_profile import Profile, Section
from solver.path_profile import PathProfile
from solver.result_file import fingerprint, results_dir

PROFILE_VERSION = 1
SOURCES = ("x", "y", "z", "path")


def profiles_dir(step_path):
    return os.path.join(results_dir(step_path), "z0profiles")


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


def save_profile(step_path, source, profile, key):
    """Writes (atomically) the profile along `source` with its key."""
    folder = profiles_dir(step_path)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"{source}.npz")
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
    temp = path + ".tmp.npz"
    np.savez_compressed(temp, **data)
    os.replace(temp, path)
    return path


def load_profile(step_path, source, key):
    """The saved profile along `source` if its key is `key`, else None."""
    path = os.path.join(profiles_dir(step_path), f"{source}.npz")
    if not os.path.isfile(path):
        return None
    try:
        with np.load(path, allow_pickle=False) as data:
            if int(data["version"]) != PROFILE_VERSION or str(data["key"]) != key:
                return None
            sections = [Section(float(c), float(z), float(e), str(err))
                        for c, z, e, err in zip(data["coordinate"], data["z0"], data["eps_eff"], data["error"])]
            axis = int(data["axis"])
            if "points" in data:
                return PathProfile(axis=axis, sections=sections, points=list(data["points"]),
                                   across=list(data["across"]), half_width=float(data["half_width"]))
            return Profile(axis, sections)
    except Exception:           # a damaged file is outdated, not an error
        return None
