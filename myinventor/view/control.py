# control.py
#
# The window's control channel: Claude (or a script) tells the open window
# what to do, and the user sees it happen. The client is customcst's
# `../customcst/tools/ui.py --app mycad` (it imports nothing of either app);
# the protocol and the lock rules are customcst's (view/control.py there),
# kept identical so one client drives both.
#
# The window listens on 127.0.0.1 only, on a free port, and writes
#   {"port": 51234, "token": "<random hex>", "pid": 1234}
# to control.json beside the app settings (MYCAD_CONTROL overrides the path;
# tests set MYCAD_SETTINGS, which moves it too). A request is one JSON line
# {"token": ..., "command": "status", "args": {...}}, the answer one JSON
# line {"ok": true, "result": ...} or {"ok": false, "error": "..."}. A wrong
# token is refused. The last window opened owns the file; closing removes
# it only if it is still its own.
#
# Locked (MainWindow.set_locked): Claude is in control and the user only
# looks (rotate, pan, zoom, the view buttons, hover); a banner over the
# window says so and its button gives the control back. Commands that
# change anything need the lock and fail once the user has taken it back,
# so Claude never acts under the user's hands. Reading (describe, status,
# screenshot) works always. Locked, the disk watch does not reload: Claude
# writes the file and sends `reload`, or edits through `set`.
#
# Commands (see COMMANDS); paths are made absolute in the window's own
# folder, so give absolute ones:
#   describe                    the app, the protocol version, each command
#   status                      file, modified, locked, busy, the sketch edited,
#                               the panel open, and cli.report() of the part
#   lock [message]              take control (refused while the user is in the
#                               middle of something, or has unsaved edits)
#   unlock [message]            give it back; message in the status bar
#   open path / reload          read a part / the open one again (no dialog:
#                               a file that does not read is an error answer)
#   set NAME=FORMULA ...        parameters, one undo step; answer: the report
#   save                        write the part (and its STEP if STEP on Save)
#   export_step path            the bodies to a STEP file
#   view name / fit             a standard view (iso, top, ...) / frame the model
#   select kind name            a browser row: body | feature | group | transform | datum
#   undo / redo
#   screenshot path             window.grab() into a PNG
# All work is done before the answer: `status` has "busy": {} for the
# protocol's `ui.py wait`.

import hmac
import json
import os
import secrets

from PySide6.QtCore import QObject
from PySide6.QtNetwork import QHostAddress, QTcpServer

import cli
from app_settings import settings_path

MAX_LINE = 1 << 20
APP = "mycad"
PROTOCOL = 1                    # the request / answer format and the lock rules (customcst's)


def control_path():
    override = os.environ.get("MYCAD_CONTROL")
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
            if not isinstance(args, dict):
                raise TypeError
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
    """File, modified, locked, busy, the sketch edited, the panel open, and the part's report (as cli.py --json)."""
    doc = window.doc
    return {
        "app": APP,
        "file": doc.path,
        "modified": doc.modified,
        "changed_on_disk": doc.changed_on_disk(),
        "locked": window.locked,
        "busy": {},                         # everything is done before the answer
        "editing": window.editing,
        "panel": window.open_panel(),
        "can_undo": doc.can_undo,
        "can_redo": doc.can_redo,
        "message": window.statusBar().currentMessage(),
        "report": cli.report(window.part, window.full_result()),
    }


def lock(window, message="Claude is working: look only"):
    """Take control (message on the banner); refused while the user is in the middle of something or has unsaved edits."""
    if window.locked:
        window.set_locked(True, message)
        return "locked"
    busy = window.user_busy()
    if busy:
        raise CommandError(f"the user is in the middle of something ({busy}): try later")
    if window.doc.modified:
        raise CommandError("the user has unsaved edits: they save or discard them first (ask them)")
    window.set_locked(True, message)
    return "locked"


def unlock(window, message=""):
    """Give control back; message in the status bar."""
    window.set_locked(False)
    if message:
        window.statusBar().showMessage(message)
    return "unlocked"


def open_part(window, path):
    """Open the part at path (absolute; no dialog: a file that does not read is the error, the window as it was)."""
    error = window.open_quiet(os.path.abspath(path))
    if error:
        raise CommandError(error)
    return status(window)


def reload(window):
    """Read the open part file again (one undo step), the camera kept."""
    error = window.reload_quiet()
    if error:
        raise CommandError(error)
    return status(window)


def set_parameters(window, **formulas):
    """Parameters NAME=FORMULA (one or more) as one undo step; the answer is the report. A formula that fails: nothing changed."""
    if not formulas:
        raise CommandError("set NAME=FORMULA ...: no parameter given")
    error = window.set_parameters_quiet({name: _formula(value) for name, value in formulas.items()})
    if error:
        raise CommandError(error)
    return cli.report(window.part, window.full_result())


def _formula(value):
    """ui.py reads values as JSON: 11.4 comes as a number."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return repr(value)
    raise CommandError(f"a formula must be text or a number, not {value!r}")


def save(window):
    """Write the part to its file (and name.step beside it if STEP on Save is on)."""
    error = window.save_quiet()
    if error:
        raise CommandError(error)
    return window.statusBar().currentMessage()


def export_step(window, path):
    """The bodies to a STEP file at path (absolute, overwritten); the answer has the note on what was left out."""
    path = os.path.abspath(path)
    try:
        note = window.export_step(path)
    except (OSError, ValueError) as error:
        raise CommandError(f"STEP not written: {error}") from None
    return {"path": path, "note": note}


VIEWS = ("iso", "top", "bottom", "front", "back", "left", "right")


def view(window, name):
    """A standard view: iso | top | bottom | front | back | left | right."""
    name = str(name).lower()
    if name not in VIEWS:
        raise CommandError(f"view must be one of {VIEWS}")
    if name == "iso":
        window.view.reset_view()
    else:
        window.view.look_from(name.capitalize())
    return name


def fit(window):
    """Frame the whole model, the direction of view kept."""
    window.view.fit()
    return "fit"


SELECT_KINDS = ("body", "feature", "group", "transform", "datum")


def select(window, kind, name):
    """Select a browser row as a click does (its highlight): kind body | feature | group | transform | datum."""
    if kind not in SELECT_KINDS:
        raise CommandError(f"kind must be one of {SELECT_KINDS}")
    if not window.select_row((kind, name)):
        names = sorted(k[1] for k in window._rows if k[0] == kind)
        raise CommandError(f"no {kind} named {name!r}; there are: {names}")
    return [kind, name]


def undo(window):
    """Undo the last edit (the answer: the report)."""
    if not window.doc.can_undo:
        raise CommandError("nothing to undo")
    window.undo()
    return cli.report(window.part, window.full_result())


def redo(window):
    """Redo the edit undone last (the answer: the report)."""
    if not window.doc.can_redo:
        raise CommandError("nothing to redo")
    window.redo()
    return cli.report(window.part, window.full_result())


def screenshot(window, path):
    """window.grab() into a PNG at path (absolute)."""
    path = os.path.abspath(path)
    if not window.grab().save(path):
        raise CommandError(f"cannot write {path}")
    return path


# name: (function, changes the window: needs the lock)
COMMANDS = {
    "describe": (describe, False),
    "status": (status, False),
    "screenshot": (screenshot, False),
    "lock": (lock, False),
    "unlock": (unlock, True),
    "open": (open_part, True),
    "reload": (reload, True),
    "set": (set_parameters, True),
    "save": (save, True),
    "export_step": (export_step, True),
    "view": (view, True),
    "fit": (fit, True),
    "select": (select, True),
    "undo": (undo, True),
    "redo": (redo, True),
}
