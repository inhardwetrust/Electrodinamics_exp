# check_remote_run.py
#
# Running on the server from the app (remote/sync.py launch / status /
# stop, the Run on server and Stop buttons) against the local SSH server
# of check_remote.py, which here also runs commands: the start command
# runs the real run.py on the model in its folder (through a run.py there
# that points run.lock at that folder); kill -0, ps and kill -TERM act on
# that process (kill -TERM as Ctrl+Break: Windows has no SIGTERM to send).
#   - a wrong Python: the start fails with the log's words;
#   - launch: pushed and started, the status says meshing / running;
#     a second start is refused (one run at a time), the run finishes:
#     "done" with its result, the lock released, pull brings it;
#   - stop: the run ends "stopped", nothing saved, the lock released;
#   - a run killed hard: "died", its lock no hindrance to the next start;
#   - the window: opening asks nothing of the server; Run on server: the
#     progress bar, Stop on, a pending row; closed while it goes and opened
#     again: the pending row from the record (no connection), Stop on;
#     Fetch follows it again, the finished run is taken by itself as this
#     model's next Run N at the launch's local time, ticked, current.
#
#   python checks/check_remote_run.py      (~2 min)

import os
import re
import shutil
import signal
import site
import subprocess
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8")

import check_remote as cr                                                # noqa: E402

FAILED = []
MODEL = cr.MODEL
SHIM = """import os, sys
sys.path.insert(0, {root!r})
import run as real
real.LOCK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "run.lock")
sys.exit(real.main(sys.argv[1:]))
"""
START = re.compile(r"^cd (?P<root>\S+) \|\| exit 1; nohup (?P<python>\S+) run\.py (?P<step>\S+) > (?P<log>\S+) "
                   r"2>&1 < /dev/null & echo \$!$")


def check(label, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {label} {detail}")
    if not ok:
        FAILED.append(label)


class Shell:
    """The commands remote/sync.py sends, done on the local disk standing for the remote."""

    def __init__(self, disk):
        self.disk = disk
        self.started = {}           # pid -> (Popen, args text)

    def real(self, path):
        return os.path.join(self.disk, path.lstrip("/"))

    def __call__(self, command):
        from run import _alive
        match = START.match(command)
        if match:
            root = self.real(match["root"])
            log = open(self.real(match["log"]), "wb")
            python = match["python"]
            if not os.path.isfile(os.path.join(root, python)):
                log.write(f"nohup: failed to run command '{python}': No such file or directory\n".encode())
                log.close()
                process = subprocess.Popen([sys.executable, "-c", "pass"])
                process.wait()
                return 0, f"{process.pid}\n"
            # the base interpreter with the venv's packages: a Windows venv's python.exe is a
            # launcher whose child (another pid) runs the script; on Linux it is a symlink
            env = dict(os.environ, PYTHONPATH=os.pathsep.join(site.getsitepackages()))
            process = subprocess.Popen([sys._base_executable, os.path.join(root, "run.py"), match["step"],
                                        "--every", "2"],
                                       cwd=root, stdout=log, stderr=subprocess.STDOUT, env=env,
                                       creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
            self.started[process.pid] = (process, f"{python} run.py {match['step']}")
            return 0, f"{process.pid}\n"
        match = re.match(r"^kill -0 (\d+)$", command)
        if match:
            return (0 if _alive(int(match[1])) else 1), ""
        match = re.match(r"^ps -o args= -p (\d+)$", command)
        if match:
            entry = self.started.get(int(match[1]))
            return (0, entry[1] + "\n") if entry else (1, "")
        match = re.match(r"^kill -TERM (\d+)$", command)
        if match and int(match[1]) in self.started:
            os.kill(int(match[1]), signal.CTRL_BREAK_EVENT)
            return 0, ""
        return 127, f"not emulated: {command}\n"


def exec_server(shell):
    class Server(cr._Server):
        def check_channel_exec_request(self, channel, command):
            def answer():
                code, out = shell(command.decode())
                channel.sendall(out.encode())
                channel.send_exit_status(code)
                channel.close()
            threading.Thread(target=answer, daemon=True).start()
            return True
    return Server


def wait_for(status_of, states, timeout=180.0, ready=lambda data: True):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        data = status_of()
        if data is not None and data.get("state") in states and ready(data):
            return data
        time.sleep(0.5)
    return status_of()


def main():
    folder = tempfile.mkdtemp()
    os.environ["CUSTOMCST_SETTINGS"] = os.path.join(folder, "settings.json")
    os.environ["CUSTOMCST_REMOTE"] = os.path.join(folder, "local", "remote", "remote.toml")
    try:
        return run(folder)
    finally:
        time.sleep(1.0)
        shutil.rmtree(folder, ignore_errors=True)


def run(folder):
    from remote import sync
    from solver.result_file import results_dir

    disk = os.path.join(folder, "disk")
    customcst = os.path.join(disk, "home", "me", "software", "customcst")
    os.makedirs(os.path.join(customcst, ".venv", "bin"))
    open(os.path.join(customcst, ".venv", "bin", "python"), "w").close()     # "it is there"
    with open(os.path.join(customcst, "run.py"), "w", encoding="utf-8") as file:
        file.write(SHIM.format(root=ROOT))
    shell = Shell(disk)
    port, key = cr.serve(disk, exec_server(shell))

    local = os.path.join(folder, "local")
    os.makedirs(local)
    step = os.path.join(local, f"{MODEL}.stp")
    shutil.copy(os.path.join(ROOT, "models", f"{MODEL}.stp"), step)
    shutil.copy(os.path.join(ROOT, "models", f"{MODEL}.toml"), os.path.splitext(step)[0] + ".toml")
    config = sync.RemoteConfig(host="127.0.0.1", port=port, user=cr.USER, password=cr.PASSWORD,
                               path="~/software/customcst")
    try:
        sync.check(config)
    except sync.UnknownHost as error:
        sync.trust_host(error.host, error.key)
    lock = os.path.join(customcst, "run.lock")

    def status():
        return sync.status(config, step)

    # a wrong Python
    sync.push(config, step)
    try:
        sync.start(sync.RemoteConfig(**{**vars(config), "python": "venv/bin/python3"}), step)
        check("a wrong Python: refused", False)
    except sync.RemoteError as error:
        check("a wrong Python: the start fails with the log's words", "failed to run command" in str(error),
              str(error).splitlines()[0])

    # launch, a second start refused, done, pulled
    first = sync.launch(config, step)
    check("launch: started, its status read", first.get("state") in ("meshing", "running")
          and isinstance(first.get("pid"), int), f"{first.get('state')}, pid {first.get('pid')}")
    try:
        sync.start(config, step)
        check("a second start while it runs: refused", False)
    except sync.RemoteError as error:
        check("a second start while it runs: refused", "one at a time" in str(error), str(error))
    running = wait_for(status, ("running", "done", "failed"),       # "running" comes before its first port
                       ready=lambda data: data.get("state") != "running" or data.get("port") is not None)
    check("status while it goes: port, progress", running is not None and running.get("state") in ("running", "done")
          and running.get("port") is not None and running.get("pulse_pct") is not None,
          f"{running.get('state')} {running.get('port')} {running.get('pulse_pct')}")
    done = wait_for(status, ("done", "failed", "died"))
    check("the run finishes: done, its result named, the lock released",
          done.get("state") == "done" and done.get("result", "").endswith(".npz") and not os.path.exists(lock),
          f"{done.get('state')} {done.get('result')} {done.get('message')}")
    got = sync.pull(config, step)
    check("pull brings it (under a local name)", list(got.taken) == [done.get("result")], str(got.taken))

    # stop
    sync.start(config, step)
    wait_for(status, ("running",))
    pid = sync.stop(config, step)
    stopped = wait_for(status, ("stopped", "done", "failed", "died"), 60)
    saved = len(os.listdir(os.path.join(customcst, "models", f"{MODEL}.results")))
    check("stop: the run ends stopped, nothing saved, the lock released",
          stopped.get("state") == "stopped" and stopped.get("pid") == pid and saved == 1
          and not os.path.exists(lock), f"{stopped.get('state')}, {saved} result(s)")
    try:
        sync.stop(config, step)
        check("stop with nothing going: an error", False)
    except sync.RemoteError as error:
        check("stop with nothing going: an error", "no run" in str(error), str(error))

    # killed hard
    started = sync.start(config, step)
    wait_for(status, ("running",))
    shell.started[started["pid"]][0].kill()
    shell.started[started["pid"]][0].wait()
    died = status()
    check("killed hard: died, the lock left behind", died.get("state") == "died" and os.path.exists(lock),
          died.get("message", ""))
    again = sync.start(config, step)
    check("the next start takes the dead run's lock over", again.get("pid") != started["pid"], f"pid {again.get('pid')}")
    sync.stop(config, step)
    wait_for(status, ("stopped",), 60)

    # the window: Run, closed while it goes, opened again (no connection: the
    # pending row from the record), Fetch: followed again, taken when done
    from PySide6.QtCore import QCoreApplication, QElapsedTimer
    from PySide6.QtWidgets import QApplication, QMessageBox
    from project.project_file import Project
    from remote import record
    app = QApplication.instance() or QApplication(sys.argv)
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
    QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.Ok)
    from view.main_window import MainWindow
    shutil.rmtree(results_dir(step))                        # the record with it: all is new here
    project = Project.load(step)                            # a narrow band: a longer pulse, a longer run
    project.set_setting("simulation", "f_min", 1.0)
    project.set_setting("simulation", "f_max", 1.25)
    project.save()
    sync.save_config(config)
    timer = QElapsedTimer()

    def spin(until, ms):
        timer.restart()
        while not until() and timer.elapsed() < ms:
            QCoreApplication.processEvents()
            time.sleep(0.01)
        return until()

    def pending_rows(window):
        item = window._results_item()
        return [item.child(i) for i in range(item.childCount())
                if item.child(i).data(0, Qt.UserRole)[0] == "pending"]

    from PySide6.QtCore import Qt
    window = MainWindow(step)
    window.wait_for_mesh()
    window._remote_timer.setInterval(500)
    check("opened: nothing asked of the server, Run on, Stop off, no pending row",
          not window.remote.running and not window.remote_watch.running and window.run_remote_button.isEnabled()
          and not window.stop_remote_button.isEnabled() and not pending_rows(window))
    window.run_remote_button.click()
    going = spin(lambda: window._remote_active() and window._remote_state.get("state") == "running"
                 and not window._remote_bar.isHidden(), 60000)
    rows = pending_rows(window)
    check("Run: going, the progress bar, Stop on, Run off, a pending row",
          going and window.stop_remote_button.isEnabled() and not window.run_remote_button.isEnabled()
          and len(rows) == 1 and rows[0].text(2).startswith("running"),
          f"{window._remote_bar.format()} | {[r.text(2) for r in rows]}")
    window.close()

    window = MainWindow(step)
    window.wait_for_mesh()
    window._remote_timer.setInterval(500)
    rows = pending_rows(window)
    launch = record.pending(step)[0] if record.pending(step) else {}
    check("opened again: no connection, the pending row from the record, Stop on, no progress bar",
          not window.remote.running and not window.remote_watch.running and len(rows) == 1
          and window.stop_remote_button.isEnabled() and window._remote_bar.isHidden(),
          f"{[r.text(2) for r in rows]}")
    window.fetch_button.click()
    followed = spin(lambda: not window._remote_bar.isHidden(), 30000)
    check("Fetch while it goes: followed again (the progress bar)", followed, window.remote_panel.status.text())
    # the server also holds the earlier run (no launch from here: taken as it comes, at the time
    # of taking), so the launched one is Run 2, placed at its launch time
    taken = spin(lambda: not pending_rows(window) and len(window.runs) == 2 and not window.remote.running, 240000)
    run = next((r for r in window.runs if os.path.basename(r.path) == f"{launch.get('stamp')}.npz"), None)
    check("done: taken by itself, the pending row gone, Run 2 at the launch's local time, ticked",
          taken and run is not None and run.name == "Run 2" and run.saved == launch.get("started")
          and run.path in window.shown and run.fingerprint == launch.get("fingerprint")
          and any("computed on" in line for line in run.info),
          f"{[(r.name, r.saved, os.path.basename(r.path)) for r in window.runs]} / {launch.get('started')}")
    check("the run is current (the model as sent)", window._is_current(run) if run else False)
    check("then: Run on, Stop off", window.run_remote_button.isEnabled() and not window.stop_remote_button.isEnabled())
    window.close()

    print("\nall checks passed" if not FAILED else f"\nFAILED: {', '.join(FAILED)}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
