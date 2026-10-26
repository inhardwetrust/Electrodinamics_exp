# record.py
#
# What this machine started on servers and took back from them, beside the
# model's runs: model.results/remote.json. Local only (no connection): the
# window shows a run going on a server as a pending row from it, without
# asking the server, and Fetch knows what it already took.
#   {"launches": [{"server": "user@host", "stamp": "20261026-205103",
#                  "started": "2026-10-26 20:51:03",   (this machine's clock)
#                  "pid": 10668, "fingerprint": "...",  (the model as sent)
#                  "state": "meshing|running|done|failed|stopped|died",
#                  "message": "", "progress": "", "seen": "...", (last status read)
#                  "result": "<the server's file>", "local": "<the local file>"}],
#    "pulled": {"user@host": {"<the server's file>": "<the local file>"}}}
# A launch is pending until its run is taken ("local" set) or forgotten.
# Written atomically under a lock: Fetch (a worker thread) and the window
# both update it.

import json
import os
import threading
import time

from solver.result_file import results_dir

NAME = "remote.json"
_lock = threading.Lock()


def path(step_path):
    return os.path.join(results_dir(step_path), NAME)


def load(step_path):
    """The record; a missing or damaged file gives an empty one."""
    try:
        with open(path(step_path), encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    if not isinstance(data.get("launches"), list):
        data["launches"] = []
    if not isinstance(data.get("pulled"), dict):
        data["pulled"] = {}
    return data


def update(step_path, change):
    """change(data) on the record, written back atomically; returns the data."""
    with _lock:
        data = load(step_path)
        change(data)
        target = path(step_path)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        temp = target + ".tmp"
        with open(temp, "w", encoding="utf-8") as file:
            json.dump(data, file, indent=1, ensure_ascii=False)
        os.replace(temp, target)
        return data


def add_launch(step_path, server, status, fingerprint):
    """A run just started on `server` (its first status); returns the launch."""
    now = time.localtime()
    launch = {"server": server, "stamp": time.strftime("%Y%m%d-%H%M%S", now),
              "started": time.strftime("%Y-%m-%d %H:%M:%S", now), "pid": status.get("pid"),
              "fingerprint": fingerprint, "state": status.get("state", "meshing"), "message": "",
              "progress": "", "seen": time.strftime("%Y-%m-%d %H:%M:%S", now), "result": None, "local": None}
    update(step_path, lambda data: data["launches"].append(launch))
    return launch


def note_status(step_path, server, status, progress=""):
    """A status read from `server`: updates the launch of that pid (if this machine started it)."""
    if not status or not isinstance(status.get("pid"), int):
        return

    def change(data):
        for launch in data["launches"]:
            if launch.get("server") == server and launch.get("pid") == status["pid"]:
                launch.update(state=status.get("state"), message=status.get("message", ""), progress=progress,
                              seen=time.strftime("%Y-%m-%d %H:%M:%S"))
                if status.get("result"):
                    launch["result"] = status["result"]
    update(step_path, change)


def launch_of(data, server, remote_name):
    return next((launch for launch in data["launches"]
                 if launch.get("server") == server and launch.get("result") == remote_name), None)


def was_pulled(step_path, server, remote_name):
    return remote_name in load(step_path)["pulled"].get(server, {})


def note_pulled(step_path, server, names):
    """{the server's file: the local file} taken; their launches are no longer pending."""
    def change(data):
        data["pulled"].setdefault(server, {}).update(names)
        for remote_name, local_name in names.items():
            launch = launch_of(data, server, remote_name)
            if launch is not None:
                launch["local"] = local_name
    update(step_path, change)


def pending(step_path):
    """The launches whose run is not here (yet), oldest first."""
    return [launch for launch in load(step_path)["launches"] if not launch.get("local")]


def forget(step_path, stamp):
    """Drops a launch from the pending ones (its run, if it comes, is still taken)."""
    def change(data):
        data["launches"] = [launch for launch in data["launches"] if launch.get("stamp") != stamp]
    update(step_path, change)
