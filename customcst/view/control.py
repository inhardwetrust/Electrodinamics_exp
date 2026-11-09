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
#   save_as path [overwrite]     the open model under a new name (STEP, project
#                                file, runs, presets copied: project/save_as.py),
#                                then the copy open; overwrite=true replaces a
#                                model there
#   tab name                     3d | results | farfield | farfield_f
#   show runs=[names]            tick these runs in Results (all others off)
#   screenshot path              window.grab() into a PNG
#   run_server / fetch           the Remote tab's ▶ Run on server / Fetch;
#                                fetch take_server=true|false: what a fetch does
#                                when the server's model files differ (no dialog)
#   preset kind name             apply a mesh / monitors preset (project/presets.py)
#   farfield ...                 the Far field tab: frequency (GHz), plane phi|theta,
#                                at (deg), quantity, component, span, show3d, cut3d
#   farfield_f ...               the Far field vs f tab: frequency (GHz, the marker),
#                                show gain|beam, direction max|fixed, theta, phi (deg),
#                                component, curves [names], port; answers the figures
#   local_mesh body [remove] [cells_per_wavelength, cells_per_feature, max_step]
#                                a body's local mesh: created from the main mesh's
#                                values, the given ones set; remove=true drops it
#   view name [ortho]            iso, top, bottom, front, back, left, right
#   section axis at [on]         the section plane: x|y|z at mm, shown or not
#   stop_server                  the server's run stopped (no question)
#   clean_server [names] [list]  list the server's models (status: server_models),
#                                or delete these (never one whose run goes)
#   clear_notices                empty the notices
# Locked, the window opens no dialog (MainWindow.notify / ask): a message
# or a question it would have shown is a notice in `status` (and the status
# bar); a question takes its default (a command sets it: fetch take_server,
# stop_server). A new host key is the one question left to the user: locked,
# it is answered no and noted.
# Long work (meshing, a transfer, the server's run) goes on after the
# answer: `status` tells when it is done (tools/ui.py wait).

import hmac
import json
import os
import re
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
        "remote": {"running": window.remote.running, "last": window.remote_panel.status.text()},
        "notices": list(window.notices),
        "server_models": None if window.server_models is None else [
            {"name": m.name, "size": m.size, "runs": m.runs, "state": m.state, "active": m.active}
            for m in window.server_models],
    }


def lock(window, message="Claude is working: look only"):
    """Take control (message on the banner); refused while the user edits."""
    if window.locked:
        window.set_locked(True, message)
        return "locked"
    if window.user_busy():
        raise CommandError("the user is editing (a field in the tree, or a dialog is open): try later")
    window.set_locked(True, message)
    window.fetch_take_server = False        # a fresh lock: the safe answers until a command says otherwise
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


def save_as_model(window, path, overwrite=False):
    """The open model copied to path (with its project, runs, presets), the copy opened."""
    error = window.save_model_as(os.path.abspath(path), bool(overwrite))
    if error:
        raise CommandError(error)
    return status(window)


TAB_NAMES = ("3d", "results", "farfield", "farfield_f")


def tab(window, name):
    """Show a tab: 3d | results | farfield | farfield_f."""
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


def local_mesh(window, body, remove=False, cells_per_wavelength=None, cells_per_feature=None, max_step=None):
    """A body's local mesh: created (main mesh's values), values set (cells_per_wavelength, cells_per_feature, max_step mm), or remove=true."""
    project = window.project
    if project is None or window.geometry is None:
        raise CommandError("no model open (or its project file has errors)")
    if remove:
        if body not in project.local_mesh:
            raise CommandError(f"{body!r} has no local mesh")
        project.remove_local_mesh(body)
    else:
        if not any(b.name == body for b in window.geometry.bodies):
            raise CommandError(f"no body {body!r} in the model")
        values = {k: v for k, v in (("cells_per_wavelength", cells_per_wavelength),
                                    ("cells_per_feature", cells_per_feature), ("max_step", max_step))
                  if v is not None}
        trial = dict(project.local_mesh.get(body, {}))
        project.create_local_mesh(body)
        try:
            for key, value in values.items():
                project.set_local_mesh_value(body, key, value)
        except ValueError as error:
            if trial:
                project.local_mesh[body] = trial
            else:
                project.remove_local_mesh(body)
            raise CommandError(str(error)) from None
    window._after_edit()
    return {"local_mesh": dict(project.local_mesh.get(body, {})), "status": status(window)}


def fetch(window, take_server=None):
    """The Remote tab's Fetch; take_server: take the server's model files where they differ (else keep ours)."""
    if take_server is not None:
        window.fetch_take_server = bool(take_server)    # also for the fetch after a followed run
    return _remote(window, "fetch")


def clear_notices(window):
    """Empty the notices (what dialogs would have said while locked)."""
    window.notices.clear()
    return "cleared"


VIEWS = ("iso", "top", "bottom", "front", "back", "left", "right")


def view(window, name="iso", ortho=None):
    """A standard view of the 3D View tab: iso, top, bottom, front, back, left, right; ortho true|false."""
    if name not in VIEWS:
        raise CommandError(f"name must be one of {VIEWS}")
    if window.geometry is None:
        raise CommandError("no model open")
    if ortho is not None:
        window.view.set_orthographic(bool(ortho))
    if name == "iso":
        window.view.reset_view()
    else:
        window.view.look_from(name.capitalize())
    window.tabs.setCurrentIndex(TAB_NAMES.index("3d"))
    return name


def section(window, axis, at, on=True):
    """The section plane: axis x|y|z, at (mm), on (shown) or off."""
    if axis not in ("x", "y", "z"):
        raise CommandError("axis must be x, y or z")
    if window.geometry is None:
        raise CommandError("no model open")
    index = "xyz".index(axis)
    lo, hi = window.geometry.bounds
    coordinate = float(at) * 1e-3
    if not lo[index] - 1e-12 <= coordinate <= hi[index] + 1e-12:
        raise CommandError(f"{axis} = {at} mm is outside the model ({lo[index] * 1e3:g}..{hi[index] * 1e3:g} mm)")
    if window.mesh_axis.currentIndex() != index:
        window.mesh_axis.setCurrentIndex(index)              # centers the plane
    window._mesh_coordinate = coordinate
    window._configure_mesh_slider()
    window.section_check.setChecked(bool(on))
    window._show_mesh_plane()
    window.tabs.setCurrentIndex(TAB_NAMES.index("3d"))
    return {"axis": axis, "at_mm": round(window._mesh_coordinate * 1e3, 4), "on": bool(on)}


def farfield(window, frequency=None, plane=None, at=None, quantity=None, component=None, span=None,
             show3d=None, cut3d=None, port=None):
    """The Far field tab: frequency GHz, plane phi|theta, at deg, quantity, component, span dB, show3d, cut3d."""
    panel = window.farfield_panel
    if panel.primary is None:
        raise CommandError("no shown run with a far field (tick one: show)")

    def choose(combo, value, what):
        for i in range(combo.count()):
            if combo.itemData(i) == value:
                combo.setCurrentIndex(i)
                return
        raise CommandError(f"{what} must be one of {[combo.itemData(i) for i in range(combo.count())]}")

    widgets = (panel.frequency, panel.port, panel.quantity, panel.component, panel.plane, panel.angle,
               panel.span, panel.show_3d, panel.cut_3d)
    for widget in widgets:
        widget.blockSignals(True)
    try:
        if port is not None:
            choose(panel.port, port, "port")
            panel._refresh_choices()
        if frequency is not None:
            values = [panel.frequency.itemData(i) for i in range(panel.frequency.count())]
            near = [abs(v / 1e9 - float(frequency)) for v in values]
            panel.frequency.setCurrentIndex(near.index(min(near)))
        if quantity is not None:
            choose(panel.quantity, quantity, "quantity")
        if component is not None:
            choose(panel.component, component, "component")
        if plane is not None:
            choose(panel.plane, plane, "plane")
            panel.angle.setRange(0, 359 if plane == "phi" else 180)
        if at is not None:
            panel.angle.setValue(int(round(float(at))))
        if span is not None:
            choose(panel.span, int(span), "span")
        if show3d is not None:
            panel.show_3d.setChecked(bool(show3d))
        if cut3d is not None:
            panel.cut_3d.setChecked(bool(cut3d))
    finally:
        for widget in widgets:
            widget.blockSignals(False)
    panel._sync_slider()
    panel._redraw()
    if frequency is not None:
        panel.frequency_changed.emit(float(panel.frequency.currentData()))
    window.tabs.setCurrentWidget(panel)
    text = re.sub(r"<[^>]+>", "", panel.info.text().replace("<br>", "\n"))
    return {"frequency_ghz": panel.frequency.currentData() / 1e9, "plane": panel.plane.currentData(),
            "at": panel.angle.value(), "quantity": panel.quantity.currentData(),
            "component": panel.component.currentData(), "show3d": panel.show_3d.isChecked(),
            "cut3d": panel.cut_3d.isChecked(), "info": text}


def farfield_f(window, frequency=None, show=None, direction=None, theta=None, phi=None, component=None,
               curves=None, port=None, snap=True):
    """
    The Far field vs f tab: frequency GHz (the marker, the Far field tab follows; snap=false: right
    there, interpolated between far-field points), show gain|beam, direction max|fixed, theta / phi deg,
    component, curves [names of the gain view], port.
    """
    panel = window.farfield_sweep_panel
    if panel.primary is None:
        raise CommandError("no shown run with a far field (tick one: show)")

    def choose(combo, value, what):
        for i in range(combo.count()):
            if combo.itemData(i) == value:
                combo.setCurrentIndex(i)
                return
        raise CommandError(f"{what} must be one of {[combo.itemData(i) for i in range(combo.count())]}")

    if curves is not None:
        curves = [curves] if isinstance(curves, str) else list(curves)
        unknown = [c for c in curves if c not in panel.curves]
        if unknown:
            raise CommandError(f"unknown curves {unknown}: one of {list(panel.curves)}")
    if theta is not None and not 0 <= float(theta) <= 180:
        raise CommandError("theta must be within 0..180 degrees")
    widgets = [panel.port, panel.view, panel.direction, panel.theta, panel.phi, panel.component,
               *panel.curves.values()]
    for widget in widgets:
        widget.blockSignals(True)
    try:
        if port is not None:
            choose(panel.port, port, "port")
        if show is not None:
            choose(panel.view, show, "show")
        if direction is not None:
            choose(panel.direction, direction, "direction")
        if theta is not None:
            panel.theta.setValue(int(round(float(theta))))
        if phi is not None:
            panel.phi.setValue(int(round(float(phi) / 2.0)) * 2 % 360)
        if component is not None:
            choose(panel.component, component, "component")
        if curves is not None:
            for key, box in panel.curves.items():
                box.setChecked(key in curves)
    finally:
        for widget in widgets:
            widget.blockSignals(False)
    if frequency is not None:
        panel._move_marker(float(frequency) * 1e9, snap=bool(snap))
    panel._redraw()
    window.tabs.setCurrentWidget(panel)
    x = panel.at_marker()

    def number(x):
        return None if x != x else round(float(x), 4)

    return {"frequency_ghz": panel.marker / 1e9, "show": panel.view.currentData(),
            "direction": panel.direction.currentData(), "theta": panel.theta.value(), "phi": panel.phi.value(),
            "component": panel.component.currentData(),
            "curves": [key for key, box in panel.curves.items() if box.isChecked()],
            "at_marker": {key: number(x[key]) for key in
                          ("directivity", "gain", "realized", "efficiency", "mismatch", "theta", "phi",
                           "beamwidth_phi0", "beamwidth_phi90")}
                         | {"listed": x["named"] and x["between"] is None,
                            "interpolated_between_ghz": None if x["between"] is None
                            else [b / 1e9 for b in x["between"]]},
            "info": re.sub(r"<[^>]+>", "", panel.info.text().replace("<br>", chr(10)))}


def stop_server(window):
    """Stop the model's run on the server (no question: the command is the confirmation)."""
    if not window._remote_active():
        raise CommandError("no run of this model is going on the server (as last seen)")
    window.confirmed = True
    try:
        return _remote(window, "stop")
    finally:
        window.confirmed = False


def clean_server(window, names=None, list=False):
    """The server's models: list=true lists them (status: server_models), names=[...] deletes these."""
    if window.remote.running:
        raise CommandError("a transfer is going on")
    if not window.remote_panel.config().complete:
        raise CommandError("the Remote tab is not filled in")
    if list:
        window.server_models = None
        window._remote_start("server_models")
        return "listing: read status server_models when the transfer is done (ui.py wait)"
    if not names:
        raise CommandError("give names=[...] to delete, or list=true")
    window._clean_names = [str(n) for n in names]
    window._remote_start("clean")
    return "deleting: the result in status remote.last when the transfer is done"


# name: (function, changes the window: needs the lock)
COMMANDS = {
    "describe": (describe, False),
    "status": (status, False),
    "screenshot": (screenshot, False),
    "lock": (lock, False),
    "unlock": (unlock, True),
    "open": (open_model, True),
    "reload": (reload, True),
    "save_as": (save_as_model, True),
    "tab": (tab, True),
    "show": (show, True),
    "run_server": (run_server, True),
    "fetch": (fetch, True),
    "preset": (preset, True),
    "local_mesh": (local_mesh, True),
    "farfield": (farfield, True),
    "farfield_f": (farfield_f, True),
    "view": (view, True),
    "section": (section, True),
    "stop_server": (stop_server, True),
    "clean_server": (clean_server, True),
    "clear_notices": (clear_notices, False),
}
