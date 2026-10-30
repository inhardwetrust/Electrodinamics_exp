# check_control.py
#
# The window's control channel (view/control.py, tools/ui.py), on a model
# written into a temp folder (settings, remote and control files there):
#   - control.json: the port and a token; describe: the app, protocol 1,
#     each command with its lock flag and help; a wrong token, a bad line, an
#     unknown command: refused;
#   - unlocked: status and screenshot work, a command that changes
#     something is refused ("not in control");
#   - lock: the banner shows, the toolbars and the side panel are off, the
#     3D view and the tabs stay on; refused while the user edits;
#   - locked: the disk watch does not reload (Claude reloads by command);
#     reload keeps the camera and takes the new types; open of a file that
#     does not read is an error, the window as it was; tab; show ticks the
#     named runs (an unknown name is an error); run_server on a model
#     that cannot run is an error;
#   - "Take control back": unlocked, Claude's next change refused;
#   - wait returns at once when nothing goes on; closing removes control.json.
#
#   python checks/check_control.py

import json
import os
import shutil
import socket
import sys
import tempfile
import threading

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

from checks.check_disk_reload import write_model, write_types           # noqa: E402

FAILED = []


def check(label, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {label} {detail}")
    if not ok:
        FAILED.append(label)


def fake_run(step, name, fingerprint):
    """A run file in the model's results (a real run is not needed to tick it)."""
    from solver.result_file import SimulationResult, save_result

    run = SimulationResult(np.array([1e9, 2e9]), ["p"], [50.0])
    run.s[(0, 0)] = np.array([0.1 + 0j, 0.2 + 0j])
    run.name, run.fingerprint = name, fingerprint
    save_result(step, run)


def main():
    folder = tempfile.mkdtemp()
    os.environ["CUSTOMCST_SETTINGS"] = os.path.join(folder, "settings.json")
    os.environ["CUSTOMCST_REMOTE"] = os.path.join(folder, "remote.toml")
    from PySide6.QtWidgets import QApplication

    from solver.result_file import fingerprint
    from project.project_file import Project
    from tools import ui
    from view.control import control_path
    from view.main_window import MainWindow

    app = QApplication.instance() or QApplication(sys.argv)

    def call(command, **args):
        """ui.send from a thread (the window answers in this one): (ok, result or error text)."""
        out = {}

        def run():
            try:
                out["value"] = (True, ui.send(command, timeout=30, **args))
            except (ui.CommandFailed, ui.NoWindow) as error:
                out["value"] = (False, str(error))

        thread = threading.Thread(target=run)
        thread.start()
        while thread.is_alive():
            app.processEvents()
            thread.join(0.01)
        window.wait_for_mesh()
        app.processEvents()
        return out["value"]

    step = os.path.join(folder, "model.step")
    write_model(step)
    write_types(step, {"board": "dielectric"})
    fp = fingerprint(Project.load(step))
    fake_run(step, "Run 1", fp)
    fake_run(step, "Run 2", fp)

    window = MainWindow(step)
    window._disk_timer.stop()           # the looks are taken by hand below
    window.show()
    window.wait_for_mesh()
    try:
        path = control_path()
        info = json.load(open(path, encoding="utf-8"))
        check("control.json beside the settings: port and token", os.path.dirname(path) == folder
              and info["port"] > 0 and len(info["token"]) == 32 and info["pid"] == os.getpid(), str(info))

        def raw(line):
            out = {}

            def run():
                with socket.create_connection(("127.0.0.1", info["port"]), timeout=10) as s:
                    s.sendall(line + b"\n")
                    data = b""
                    while not data.endswith(b"\n"):
                        data += s.recv(4096)
                    out["answer"] = json.loads(data)
            thread = threading.Thread(target=run)
            thread.start()
            while thread.is_alive():
                app.processEvents()
                thread.join(0.01)
            return out["answer"]

        check("the client finds the same file (ui.control_file)", ui.control_file("customcst") == path,
              ui.control_file("customcst"))
        answer = raw(json.dumps({"token": "0" * 32, "command": "status"}).encode())
        check("a wrong token: refused", not answer["ok"] and "token" in answer["error"], str(answer))
        answer = raw(b"not json")
        check("a bad line: refused", not answer["ok"], str(answer))
        ok, text = call("dance")
        check("an unknown command: refused, the known ones listed", not ok and "status" in text, text)

        ok, desc = call("describe")
        check("describe: app, protocol 1, commands with lock flag and help", ok and desc["app"] == "customcst"
              and desc["protocol"] == 1 and desc["commands"]["open"]["changes"]
              and not desc["commands"]["status"]["changes"]
              and all(c["help"] for c in desc["commands"].values()), str(desc)[:200])
        ok, state = call("status")
        check("status: the model, unlocked, the patch unassigned, two current runs", ok
              and state["model"] == step and not state["locked"] and state["unassigned"] == ["patch"]
              and sorted(r["name"] for r in state["runs"]) == ["Run 1", "Run 2"]
              and all(r["current"] for r in state["runs"]), json.dumps(state)[:300])
        shot = os.path.join(folder, "shot.png")
        ok, _ = call("screenshot", path=shot)
        check("screenshot works unlocked", ok and os.path.getsize(shot) > 1000)
        ok, text = call("tab", name="results")
        check("unlocked: a change is refused", not ok and "not in control" in text, text)

        # ---- lock
        busy = window.user_busy
        window.user_busy = lambda: True
        ok, text = call("lock")
        check("lock refused while the user edits", not ok and "editing" in text and not window.locked, text)
        window.user_busy = busy
        ok, _ = call("lock", message="tuning the patch")
        check("lock: the banner says it", ok and window.locked and window.lock_bar.isVisible()
              and "tuning the patch" in window.lock_label.text(), window.lock_label.text())
        check("  toolbars and side panel off, the view and the tabs on",
              not window.main_bar.isEnabled() and not window.plane_bar.isEnabled() and not window.dock.isEnabled()
              and window.tabs.isEnabled() and window.view.canvas.native.isEnabled())

        ok, _ = call("tab", name="results")
        check("tab results", ok and window.tabs.currentWidget() is window.results)
        codes = []
        thread = threading.Thread(target=lambda: codes.append(ui.main(["--app", "customcst", "tab", "farfield"])))
        thread.start()
        while thread.is_alive():
            app.processEvents()
            thread.join(0.01)
        check("the command line: ui.py --app customcst tab farfield", codes == [0]
              and window.tabs.currentWidget() is window.farfield_panel, str(codes))
        ok, text = call("tab", name="nowhere")
        check("an unknown tab: an error", not ok, text)

        ok, shown = call("show", runs=["Run 1"])
        keys = {r.path for r in window.runs if r.name == "Run 1"}
        check("show ticks the named run only", ok and window.shown == keys, str(window.shown))
        ok, text = call("show", runs=["Run 9"])
        check("show an unknown run: an error naming the runs", not ok and "Run 2" in text, text)

        # ---- locked: no disk reload, reload by command
        camera = window.view.view.camera
        camera.set_state([1.0, 2.0, 0.5], 33.0, camera.state()["rotation"])
        app.processEvents()
        write_types(step, {"patch": "pec"})
        for _ in range(3):
            window.poll_disk()
        check("locked: the disk watch does not reload", window.project.get("patch") is None)
        ok, state = call("reload")
        check("reload: the new type, the camera kept", ok and state["unassigned"] == []
              and state["bodies"]["patch"] == "pec" and abs(camera.state()["distance"] - 33.0) < 1e-9,
              json.dumps(state)[:200] if ok else state)
        check("  the runs now outdated (the types changed)", ok and not any(r["current"] for r in state["runs"]))

        bad = os.path.join(folder, "bad.step")
        with open(bad, "w") as f:
            f.write("not a STEP file")
        ok, text = call("open", path=bad)
        check("open a file that does not read: an error, the model kept", not ok and "does not read" in text
              and window.geometry.source == step and QApplication.activeModalWidget() is None, text)

        ok, text = call("run_server")
        check("run_server on a model that cannot run (no port): an error", not ok and "cannot run" in text, text)

        ok, state = call("status")
        state2 = None
        if ok:
            out = {}
            thread = threading.Thread(target=lambda: out.update(s=ui.wait(timeout=10, every=0.1)))
            thread.start()
            while thread.is_alive():
                app.processEvents()
                thread.join(0.01)
            state2 = out.get("s")
        check("wait returns when nothing goes on", state2 is not None and not any(state2["busy"].values()))

        # ---- the user takes the control back
        take = [b for b in window.lock_bar.findChildren(type(window.push_button)) if "back" in b.text()][0]
        take.click()
        app.processEvents()
        check("Take control back: unlocked, banner gone, panels on", not window.locked
              and not window.lock_bar.isVisible() and window.main_bar.isEnabled() and window.dock.isEnabled())
        ok, text = call("tab", name="3d")
        check("  Claude's next change refused", not ok and "not in control" in text, text)
        ok, _ = call("lock")
        ok2, _ = call("unlock", message="look at Run 2")
        check("unlock with a message in the status bar", ok and ok2 and not window.locked
              and "Run 2" in window.statusBar().currentMessage(), window.statusBar().currentMessage())
    finally:
        window.close()
        app.processEvents()
    check("closing removes control.json", not os.path.exists(control_path()))
    shutil.rmtree(folder, ignore_errors=True)

    print("\nall ok" if not FAILED else f"\nFAILED: {FAILED}")
    sys.exit(1 if FAILED else 0)


if __name__ == "__main__":
    main()
