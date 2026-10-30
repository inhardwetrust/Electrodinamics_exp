# ui.py
#
# Tells an open window what to do through its control channel, so the user
# sees it happen. For Claude and scripts. One client for every app that
# speaks the protocol (customcst: view/control.py; myCad: the same rules):
#
#   - the app listens on 127.0.0.1 and writes {"port", "token", "pid"} to
#     control.json beside its settings file: <APP>_CONTROL if set, else
#     the folder of <APP>_SETTINGS if set, else %APPDATA%/<app>/;
#   - a request is one JSON line {"token", "command", "args": {...}}, the
#     answer one JSON line {"ok": true, "result": ...} or {"ok": false,
#     "error": "..."};
#   - every app has describe, status, screenshot, lock, unlock, open,
#     reload; commands that change something need the lock (see
#     ../ARCHITECTURE.md "Control by Claude"); `status` has "busy": {...}.
# This file imports nothing of the apps: any of them can call it.
#
#   python tools/ui.py [--app customcst] COMMAND [ARGS]
#   python tools/ui.py describe                  the app and its commands (JSON)
#   python tools/ui.py status                    the window's state (JSON)
#   python tools/ui.py lock ["what I am doing"]  take control: the user only looks
#   python tools/ui.py unlock ["look at Run 3"]  give it back, a message in the status bar
#   python tools/ui.py open models/x.step        read a file (no dialog: errors come back)
#   python tools/ui.py reload                    the open file again
#   python tools/ui.py screenshot out.png        window.grab() into a PNG
#   python tools/ui.py wait [--server] [--timeout S]
#       until status shows nothing busy; --server (customcst): also until
#       the model's run on the server has ended (the window fetches it)
#   python tools/ui.py COMMAND key=value ...     any other command (values
#       read as JSON when they parse: n=3, on=true, names=["a","b"])
# customcst also: tab results | 3d | farfield; show "Run 2" "Run 3";
# run_server; fetch. `describe` lists an app's commands.
#
# Exit code 0, or 1 with the window's error on stderr (2: no window).
# From Python: `from tools.ui import send; send("tab", name="results")`.

import json
import os
import socket
import sys
import time

POSITIONAL = {"open": "path", "lock": "message", "unlock": "message", "tab": "name", "screenshot": "path"}
LISTS = {"show": "runs"}                # every word after the command, as one list


class NoWindow(Exception):
    pass


class CommandFailed(Exception):
    pass


def control_file(app="customcst"):
    """Where the app's window writes its port and token (see the top)."""
    key = app.upper()
    if os.environ.get(f"{key}_CONTROL"):
        return os.environ[f"{key}_CONTROL"]
    if os.environ.get(f"{key}_SETTINGS"):
        return os.path.join(os.path.dirname(os.environ[f"{key}_SETTINGS"]), "control.json")
    base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, app, "control.json")


def send(command, timeout=120.0, app="customcst", **args):
    """One command to the window; its result, or CommandFailed / NoWindow."""
    path = control_file(app)
    try:
        with open(path, encoding="utf-8") as f:
            info = json.load(f)
    except (OSError, ValueError):
        raise NoWindow(f"no {app} window to talk to ({path} missing)") from None
    request = json.dumps({"token": info["token"], "command": command, "args": args}, ensure_ascii=False)
    try:
        with socket.create_connection(("127.0.0.1", int(info["port"])), timeout=timeout) as s:
            s.sendall(request.encode("utf-8") + b"\n")
            data = b""
            while not data.endswith(b"\n"):
                chunk = s.recv(65536)
                if not chunk:
                    break
                data += chunk
    except OSError as error:
        raise NoWindow(f"the {app} window does not answer ({error}); is it still open?") from None
    answer = json.loads(data)
    if not answer.get("ok"):
        raise CommandFailed(answer.get("error", "failed"))
    return answer.get("result")


def wait(server=False, timeout=3600.0, every=1.0, app="customcst"):
    """Polls status until nothing is busy (see the top); returns the last status."""
    end = time.monotonic() + timeout
    while True:
        state = send("status", app=app)
        busy = any(state.get("busy", {}).values())
        run = state.get("server_run") or {}
        if server and run.get("state") in ("meshing", "running"):
            busy = True
        if not busy or time.monotonic() > end:
            return state
        time.sleep(every)


def _value(text):
    try:
        return json.loads(text)
    except ValueError:
        return text


def main(argv):
    app = "customcst"
    if argv[:1] == ["--app"] and len(argv) > 1:
        app, argv = argv[1], argv[2:]
    if not argv:
        print(open(__file__, encoding="utf-8").read().split("\n\n")[0])
        return 1
    command, rest = argv[0], argv[1:]
    try:
        if command == "wait":
            timeout = float(rest[rest.index("--timeout") + 1]) if "--timeout" in rest else 3600.0
            result = wait(server="--server" in rest, timeout=timeout, app=app)
        elif command in LISTS:
            result = send(command, app=app, **{LISTS[command]: rest})
        else:
            args = {}
            for i, word in enumerate(rest):
                if i == 0 and command in POSITIONAL and not word.startswith(POSITIONAL[command] + "="):
                    args[POSITIONAL[command]] = word        # lock "L = 11.4 now" stays one message
                elif "=" in word:
                    key, text = word.split("=", 1)
                    args[key] = _value(text)
                else:
                    raise CommandFailed(f"what is {word!r}? give it as key=value")
            result = send(command, app=app, **args)
    except NoWindow as error:
        print(error, file=sys.stderr)
        return 2
    except CommandFailed as error:
        print(f"{command}: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    sys.exit(main(sys.argv[1:]))
