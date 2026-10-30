# ui.py
#
# Tells the open customcst window what to do (its control channel,
# view/control.py), so the user sees it happen. For Claude and scripts.
#
#   python tools/ui.py status                    the window's state (JSON)
#   python tools/ui.py lock ["what I am doing"]  take control: the user only looks
#   python tools/ui.py unlock ["look at Run 3"]  give it back, a message in the status bar
#   python tools/ui.py open models/x.step        read a model (no dialog: errors come back)
#   python tools/ui.py reload                    the open model again, the camera kept
#   python tools/ui.py tab results               3d | results | farfield
#   python tools/ui.py show "Run 2" "Run 3"      tick these runs (the others off)
#   python tools/ui.py screenshot out.png        window.grab() into a PNG
#   python tools/ui.py run_server                ▶ Run on server (the Remote tab's button)
#   python tools/ui.py fetch                     Fetch
#   python tools/ui.py wait [--server] [--timeout S]
#       until the mesh and any transfer are done; --server: also until the
#       model's run on the server has ended (the window fetches it itself)
#
# Exit code 0, or 1 with the window's error on stderr (2: no window).
# From Python: `from tools.ui import send; send("tab", name="results")`.

import json
import os
import socket
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from view.control import control_path                                   # noqa: E402

POSITIONAL = {"open": "path", "lock": "message", "unlock": "message", "tab": "name", "screenshot": "path"}


class NoWindow(Exception):
    pass


class CommandFailed(Exception):
    pass


def send(command, timeout=120.0, **args):
    """One command to the window; its result, or CommandFailed / NoWindow."""
    try:
        with open(control_path(), encoding="utf-8") as f:
            info = json.load(f)
    except (OSError, ValueError):
        raise NoWindow(f"no customcst window to talk to ({control_path()} missing)") from None
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
        raise NoWindow(f"the window does not answer ({error}); is it still open?") from None
    answer = json.loads(data)
    if not answer.get("ok"):
        raise CommandFailed(answer.get("error", "failed"))
    return answer.get("result")


def wait(server=False, timeout=3600.0, every=1.0):
    """Polls status until the window's work is done (see the top); returns the last status."""
    end = time.monotonic() + timeout
    while True:
        state = send("status")
        busy = any(state["busy"].values())
        run = state.get("server_run") or {}
        if server and run.get("state") in ("meshing", "running"):
            busy = True
        if not busy or time.monotonic() > end:
            return state
        time.sleep(every)


def main(argv):
    if not argv:
        print(__doc__ if __doc__ else open(__file__, encoding="utf-8").read().split("\n\n")[0])
        return 1
    command, rest = argv[0], argv[1:]
    try:
        if command == "wait":
            timeout = float(rest[rest.index("--timeout") + 1]) if "--timeout" in rest else 3600.0
            result = wait(server="--server" in rest, timeout=timeout)
        elif command == "show":
            result = send("show", runs=rest)
        elif command in POSITIONAL and rest:
            result = send(command, **{POSITIONAL[command]: rest[0]})
        else:
            result = send(command)
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
