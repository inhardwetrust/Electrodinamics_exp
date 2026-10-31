# control.py
#
# The window's control channel: Claude (or a script) tells the open window
# what to do, and the user sees it happen. `tools/ui.py` is the client.
#
# The window listens on 127.0.0.1 only, on a free port, and writes
#   {"port": 51234, "token": "<random hex>", "pid": 1234}
# to control.json beside the app settings (CUSTOMCST_CONTROL overrides the
# path; tests set CUSTOMCST_SETTINGS, which moves it too). A request is one
# JSON line {"token": ..., "command": "status", "args": {...}}, the answer
# one JSON line {"ok": true, "result": ...} or {"ok": false, "error": "..."}.
# A wrong token is refused. The last window opened owns the file; closing
# removes it only if it is still its own.
#
# Locked (MainWindow.set_locked): Claude is in control and the user only
# looks (camera, tabs, the results); a banner over the window says so and
# its button gives the control back. Commands that change anything need
# the lock and fail once the user has taken it back, so Claude never acts
# under the user's hands. Reading (status, screenshot) works always.
#
# Commands (see COMMANDS):
#   describe                     the app, the protocol version, each command
#                                (does it need the lock, its help line)
#   status                       model, lock, busy work, bodies, runs, ...
#   lock [message]               take control (refused while the user edits)
#   unlock [message]             give it back; message in the status bar
#   open path / reload           read a model / the open one again (no dialog:
#                                a file that does not read is an error answer)
#   tab name                     3d | results | farfield
#   show runs=[names]            tick these runs in Results (all others off)
#   screenshot path              window.grab() into a PNG
#   run_server / fetch           the Remote tab's ▶ Run on server / Fetch
#   preset kind name             apply a mesh / monitors preset (project/presets.py)
# Long work (meshing, a transfer, the server's run) goes on after the
# answer: `status` tells when it is done (tools/ui.py wait).

import hmac
import json
import os
import secrets

from PySide6.QtCore import QObject
from PySide6.QtNetwork import QHostAddress, QTcpServer

from app_settings import settings_path

MAX_LINE = 1 << 20
APP = "customcst"
PROTOCOL = 1                    # the request / answer format and the lock rules (see the top)


def control_path():
    override = os.environ.get("CUSTOMCST_CONTROL")
    if override:
        return override
    return os.path.join(os.path.dirname(settings_path()), "control.json")


class CommandError(Exception):
    """A command that cannot be done now: its text is the answer."""


class ControlServer(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.token = secrets.token_hex(16)
        self.path = control_path()
        self.server = QTcpServer(self)
        self.server.newConnection.connect(self._connected)
        self.error = ""
        if not self.server.listen(QHostAddress.LocalHost, 0):
            self.error = self.server.errorString()
            return
        info = {"port": self.server.serverPort(), "token": self.token, "pid": os.getpid()}
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            temp = self.path + ".part"
            with open(temp, "w", encoding="utf-8") as f:
                json.dump(info, f)
            os.replace(temp, self.path)
        except OSError as error:
            self.error = str(error)

    def close(self):
        self.server.close()
        try:
            with open(self.path, encoding="utf-8") as f:
                mine = json.load(f).get("token") == self.token
        except (OSError, ValueError):
            return
        if mine:
            try:
                os.remove(self.path)
            except OSError:
                pass

    def _connected(self):
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            socket.buffer = b""
            socket.readyRead.connect(lambda s=socket: self._read(s))
            socket.disconnected.connect(socket.deleteLater)

    def _read(self, socket):
        socket.buffer += bytes(socket.readAll())
        while b"\n" in socket.buffer:
            line, socket.buffer = socket.buffer.split(b"\n", 1)
            answer = self.answer(line)
            socket.write((json.dumps(answer, ensure_ascii=False) + "\n").encode("utf-8"))
            socket.flush()
        if len(socket.buffer) > MAX_LINE:
            socket.abort()

    def answer(self, line):
        """One request line -> the answer dict."""
        try:
            request = json.loads(line)
            token = str(request.get("token", ""))
            command = str(request["command"])
            args = request.get("args") or {}
        except (ValueError, KeyError, AttributeError, TypeError):
            return {"ok": False, "error": "not a request: one JSON line with token, command, args"}
        if not hmac.compare_digest(token, self.token):
            return {"ok": False, "error": "wrong token"}
        handler = COMMANDS.get(command)
        if handler is None:
            return {"ok": False, "error": f"unknown command {command!r}; known: {', '.join(COMMANDS)}"}
        function, changes = handler
        window = self.window
        if changes and not window.locked:
            return {"ok": False, "error": "not in control: the window is not locked (lock first; "
                                          "if it was locked, the user took control back)"}
        try:
            return {"ok": True, "result": function(window, **args)}
        except CommandError as error:
            return {"ok": False, "error": str(error)}
        except TypeError as error:          # wrong arguments
            return {"ok": False, "error": f"{command}: {error}"}


# =====================================================
# Commands: function(window, **args) -> JSON-able result
# =====================================================

def describe(window):
    """The app, the protocol version, each command: does it need the lock, its help line."""
    return {"app": APP, "protocol": PROTOCOL,
            "commands": {name: {"changes": changes, "help": (function.__doc__ or "").strip()}
                         for name, (function, changes) in COMMANDS.items()}}


def status(window):
    """Model, lock, busy work, bodies (types), unassigned, orphans, problem, shorts, cells, runs."""
    project, geometry = window.project, window.geometry
    bodies = [b.name for b in geometry.bodies] if geometry else []
    types = {name: project.get(name).type for name in bodies if project and project.get(name)}
    current = window._current_fingerprint() if project else None
    return {
        "app": APP,
        "model": geometry.source if geometry else None,
        "locked": window.locked,
        "busy": {"mesh": window.mesh_busy(), "local_run": window.runner.running,
                 "transfer": window.remote.running},
        "server_run": window._remote_state,
        "tab": TAB_NAMES[window.tabs.currentIndex()] if window.tabs.currentIndex() < len(TAB_NAMES) else None,
        "project_error": geometry is not None and project is None,
        "bodies": types,
        "unassigned": [name for name in bodies if name not in types],
        "orphans": project.orphans(geometry) if project and geometry else [],
        "problem": window._simulation_problem() if geometry else "no model",
        "shorts": list(window.materials.shorts) if window.materials is not None else [],
        "cells": int(window.mesh.grid.cell_count) if window.mesh is not None and window.mesh.ok else None,
        "runs": [{"name": r.name, "saved": r.saved, "current": current is not None and r.fingerprint == current,
                  "shown": (r.path or id(r)) in window.shown} for r in window.runs],
        "message": window.statusBar().currentMessage(),
    }


def lock(window, message="Claude is working: look only"):
    """Take control (message on the banner); refused while the user edits."""
    if window.locked:
        window.set_locked(True, message)
        return "locked"
    if window.user_busy():
        raise CommandError("the user is editing (a field in the tree, or a dialog is open): try later")
    window.set_locked(True, message)
    return "locked"


def unlock(window, message=""):
    """Give control back; message in the status bar."""
    window.set_locked(False)
    if message:
        window.statusBar().showMessage(message)
    return "unlocked"


def open_model(window, path):
    """Read the model at path (no dialog: a file that does not read is the error)."""
    path = os.path.abspath(path)
    error = window.load_quiet(path)
    if error:
        raise CommandError(error)
    return status(window)


def reload(window):
    """Read the open model again, the camera kept."""
    if window.geometry is None:
        raise CommandError("no model open")
    error = window.load_quiet(window.geometry.source, keep_camera=True)
    if error:
        raise CommandError(error)
    return status(window)


TAB_NAMES = ("3d", "results", "farfield")


def tab(window, name):
    """Show a tab: 3d | results | farfield."""
    if name not in TAB_NAMES:
        raise CommandError(f"tab must be one of {TAB_NAMES}")
    window.tabs.setCurrentIndex(TAB_NAMES.index(name))
    return name


def show(window, runs):
    """Tick these runs (by name) in Results, the others off."""
    by_name = {r.name: r for r in window.runs}
    missing = [name for name in runs if name not in by_name]
    if missing:
        raise CommandError(f"no run named {missing}; runs: {list(by_name)}")
    window._set_shown({by_name[name].path or id(by_name[name]) for name in runs})
    return list(runs)


def screenshot(window, path):
    """window.grab() into a PNG at path."""
    path = os.path.abspath(path)
    if not window.grab().save(path):
        raise CommandError(f"cannot write {path}")
    return path


def _remote(window, operation):
    if window.geometry is None or window.project is None:
        raise CommandError("no model with a readable project open")
    if window.remote.running:
        raise CommandError("a transfer is going on")
    if operation == "launch":
        problem = window._simulation_problem()
        if problem:
            raise CommandError(f"cannot run yet: {problem}")
        if window.materials.shorts:
            raise CommandError(f"shorts on this mesh: {window.materials.shorts}")
        if window._remote_active():
            raise CommandError("its run on the server is going already")
    if not window.remote_panel.config().complete:
        raise CommandError("the Remote tab is not filled in")
    window._remote_start(operation)
    if not window.remote.running:
        raise CommandError(window.statusBar().currentMessage() or f"{operation} did not start")
    return "started"


def run_server(window):
    """The Remote tab's Run on server (a model that can run, no shorts)."""
    return _remote(window, "launch")


def preset(window, kind, name):
    """Apply a preset: kind mesh | monitors, its name (built-in or the model's)."""
    if kind not in ("mesh", "monitors"):
        raise CommandError("kind must be mesh or monitors")
    error = window.apply_preset(kind, name)
    if error:
        raise CommandError(error)
    return status(window)


def fetch(window):
    """The Remote tab's Fetch."""
    return _remote(window, "fetch")


# name: (function, changes the window: needs the lock)
COMMANDS = {
    "describe": (describe, False),
    "status": (status, False),
    "screenshot": (screenshot, False),
    "lock": (lock, False),
    "unlock": (unlock, True),
    "open": (open_model, True),
    "reload": (reload, True),
    "tab": (tab, True),
    "show": (show, True),
    "run_server": (run_server, True),
    "fetch": (fetch, True),
    "preset": (preset, True),
}
