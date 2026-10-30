# check_control.py
#
# The window's control channel (view/control.py; the client is customcst's
# tools/ui.py --app mycad, loaded by path), on a copy of
# examples/patch_antenna.part.toml in a temp folder (settings and
# control.json there):
#   - control.json: the port and a token; the client finds it; a wrong
#     token, a bad line, an unknown command, wrong arguments: refused;
#     describe: app mycad, protocol 1, each command's lock flag and help;
#   - unlocked: status (the file, the report as cli.report) and screenshot
#     work, a change is refused ("not in control");
#   - lock refused with unsaved edits, in sketch mode, with a panel open;
#   - locked: the banner, the ribbon and the browser off, the window's
#     shortcuts dead (Ctrl+Z, which works unlocked), a click in 3D selects
#     nothing (it does unlocked), a middle drag still turns the camera, the
#     disk watch does not reload;
#   - set: two parameters, one undo step, the report back (Substrate's size);
#     a bad formula or an unknown name: an error, nothing changed;
#   - undo / redo; save (the file on disk matches); export_step (a STEP
#     beside, its note); reload by command keeps the camera; open of a file
#     that does not read: an error, the part as it was; view top; fit;
#     select a feature (its row current, its highlight);
#   - the command line: ui.py --app mycad set ...;
#   - Take control back: unlocked, Claude's next change refused; unlock
#     with a message; closing removes control.json;
#   - main.py --locked opens locked ("started by Claude").
#
#   python checks/check_control.py

import importlib.util
import json
import os
import shutil
import socket
import sys
import tempfile
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "checks"))
sys.stdout.reconfigure(encoding="utf-8")
TMP = tempfile.mkdtemp()
os.environ["MYCAD_SETTINGS"] = os.path.join(TMP, "settings.json")     # never the user's settings
os.environ.pop("MYCAD_CONTROL", None)

import numpy as np                                                    # noqa: E402

FAILED = []
UI = os.path.join(os.path.dirname(ROOT), "customcst", "tools", "ui.py")


def check(label, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {label} {detail}")
    if not ok:
        FAILED.append(label)


def load_client():
    """customcst's tools/ui.py by path (both projects have a `geometry` package; ui.py imports neither)."""
    spec = importlib.util.spec_from_file_location("mycad_check_ui", UI)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    from vispy import app

    app.use_app("pyside6")
    app.use_app().create()
    from vispy_errors import install as _watch_vispy

    vispy_errors = _watch_vispy()
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from check_sketch_line import settle, shield
    from check_view import mouse
    from view.control import control_path
    from view.main_window import MainWindow

    os.environ["MYCAD_SETTINGS"] = os.path.join(TMP, "settings.json")     # the helpers' tops pointed it elsewhere
    ui = load_client()
    qt = QApplication.instance()

    def in_thread(function):
        """Runs function in a thread while this one serves the window (it answers in the GUI thread)."""
        out = {}

        def run():
            try:
                out["value"] = function()
            except Exception as error:          # noqa: BLE001 (reported by the caller)
                out["error"] = error
        thread = threading.Thread(target=run)
        thread.start()
        while thread.is_alive():
            qt.processEvents()
            thread.join(0.01)
        qt.processEvents()
        if "error" in out:
            raise out["error"]
        return out["value"]

    def call(command, **args):
        """ui.send: (ok, result or the error text)."""
        try:
            return True, in_thread(lambda: ui.send(command, timeout=60, app="mycad", **args))
        except (ui.CommandFailed, ui.NoWindow) as error:
            return False, str(error)

    path = os.path.join(TMP, "patch.part.toml")
    shutil.copy(os.path.join(ROOT, "examples", "patch_antenna.part.toml"), path)
    window = MainWindow(path)
    window.disk_timer.stop()                # polled by hand below
    dialogs = []
    window.show_error = dialogs.append
    window.ask_save_changes = lambda: "discard"
    window.resize(1200, 800)
    window.show()
    shield(window)
    settle(300)
    native = window.view.canvas.native
    center = (native.width() // 2, native.height() // 2)
    try:
        cpath = control_path()
        info = json.load(open(cpath, encoding="utf-8"))
        check("control.json beside the settings: port, token, pid", os.path.dirname(cpath) == TMP
              and info["port"] > 0 and len(info["token"]) == 32 and info["pid"] == os.getpid(), str(info))
        check("the client finds the same file (ui.control_file mycad)",
              os.path.normcase(ui.control_file("mycad")) == os.path.normcase(cpath), ui.control_file("mycad"))

        def raw(line):
            def run():
                with socket.create_connection(("127.0.0.1", info["port"]), timeout=10) as s:
                    s.sendall(line + b"\n")
                    data = b""
                    while not data.endswith(b"\n"):
                        data += s.recv(4096)
                    return json.loads(data)
            return in_thread(run)

        answer = raw(json.dumps({"token": "0" * 32, "command": "status"}).encode())
        check("a wrong token: refused", not answer["ok"] and "token" in answer["error"], str(answer))
        answer = raw(b"not json")
        check("a bad line: refused", not answer["ok"], str(answer))
        answer = raw(json.dumps({"token": info["token"], "command": "status", "args": [1]}).encode())
        check("args not an object: refused", not answer["ok"], str(answer))
        ok, text = call("dance")
        check("an unknown command: refused, the known ones listed", not ok and "status" in text and "set" in text, text)
        ok, text = call("status", colour="red")
        check("wrong arguments: an error, not a crash", not ok and "status" in text, text)

        ok, desc = call("describe")
        check("describe: app mycad, protocol 1, commands with lock flag and help", ok and desc["app"] == "mycad"
              and desc["protocol"] == 1 and desc["commands"]["set"]["changes"]
              and not desc["commands"]["status"]["changes"] and not desc["commands"]["lock"]["changes"]
              and all(c["help"] for c in desc["commands"].values()), str(desc)[:200])
        ok, state = call("status")
        bodies = {b["name"]: b for b in state["report"]["bodies"]} if ok else {}
        check("status: the file, unlocked, not modified, busy, the report (Substrate 40 x 40 x 1.6)", ok
              and os.path.normcase(state["file"]) == os.path.normcase(path) and not state["locked"]
              and not state["modified"] and state["busy"] == {} and state["editing"] is None
              and state["panel"] is None and state["report"]["ok"]
              and np.allclose(bodies["Substrate"]["size"], [40, 40, 1.6]), json.dumps(state)[:300])
        shot = os.path.join(TMP, "shot.png")
        ok, _ = call("screenshot", path=shot)
        check("screenshot works unlocked", ok and os.path.getsize(shot) > 1000)
        ok, text = call("set", sub_w=50)
        check("unlocked: a change is refused", not ok and "not in control" in text
              and window.part.parameter("sub_w").formula == "40", text)

        # ---- unlocked, the user's own means work (so their being dead locked means something)
        window.part.add_parameter("k", "1")
        window._commit()
        settle()
        ok, text = call("lock")
        check("lock refused with unsaved edits", not ok and "unsaved" in text and not window.locked, text)
        window.activateWindow()
        native.setFocus()
        QTest.keyClick(native, Qt.Key_Z, Qt.ControlModifier)
        settle()
        check("unlocked: Ctrl+Z undoes (the shortcut works)", not window.doc.modified
              and all(p.name != "k" for p in window.part.parameters))

        def click(at):
            mouse(native, QEvent.MouseButtonPress, at, Qt.LeftButton)
            mouse(native, QEvent.MouseButtonRelease, at, Qt.NoButton)
            settle(50)

        click(center)
        check("unlocked: a click in 3D selects", window.view.selected is not None, str(window.view.selected))
        window.view._click_select(None)

        sketch = [f.name for f in window.part.features if f.TYPE == "sketch"][0]
        window.edit_sketch(sketch)
        settle()
        ok, text = call("lock")
        check("lock refused in sketch mode", not ok and "sketch" in text and not window.locked, text)
        window.finish_sketch()
        settle()
        window.start_extrude(edit="SubstrateExt")
        settle()
        ok, text = call("lock")
        check("lock refused with a panel open (Extrude)", not ok and "extrude" in text and not window.locked, text)
        window._end_extrude()
        settle()

        # ---- locked
        ok, _ = call("lock", message="tuning the patch")
        settle()
        check("lock: the banner says it", ok and window.locked and window.lock_bar.isVisible()
              and "tuning the patch" in window.lock_label.text(), window.lock_label.text())
        check("  the ribbon and the browser off, the view on", not window.ribbon.isEnabled()
              and not window.side_tabs.isEnabled() and native.isEnabled() and window.view_buttons.isEnabled())

        ok, report = call("set", sub_w="50", h=1.0)
        sizes = {b["name"]: b["size"] for b in report["bodies"]} if ok else {}
        check("set two parameters: the report back (Substrate 50 x 40 x 1)", ok
              and np.allclose(sizes["Substrate"], [50, 40, 1.0]), str(sizes))
        check("  one undo step, modified", window.doc.modified and len(window.doc._undo) >= 1
              and window.part.parameter("h").formula == "1.0")
        steps = len(window.doc._undo)
        ok, text = call("set", sub_w="50 +", h="2")
        check("a formula that does not parse: an error, nothing changed", not ok and "sub_w" in text
              and window.part.parameter("h").formula == "1.0" and len(window.doc._undo) == steps, text)
        ok, text = call("set", width="3")
        check("an unknown parameter: an error naming the parameters", not ok and "sub_l" in text, text)

        QTest.keyClick(native, Qt.Key_Z, Qt.ControlModifier)
        settle()
        check("locked: Ctrl+Z does nothing (the shortcut is dead)", window.part.parameter("h").formula == "1.0")
        QTest.keyClick(native, Qt.Key_Delete)
        click(center)
        check("locked: a click in 3D selects nothing", window.view.selected is None, str(window.view.selected))
        camera = window.view.view.camera
        before = camera.state()
        start = np.array(center)
        mouse(native, QEvent.MouseButtonPress, start, Qt.MiddleButton, which=Qt.MiddleButton)
        for i in range(1, 9):
            QTest.qWait(15)
            mouse(native, QEvent.MouseMove, start + (10 * i, 0), Qt.MiddleButton)
        mouse(native, QEvent.MouseButtonRelease, start + (80, 0), Qt.NoButton, which=Qt.MiddleButton)
        settle(100)
        check("locked: a middle drag still turns the camera",
              not np.allclose(before["rotation"], camera.state()["rotation"]))

        ok, _ = call("undo")
        check("undo: h back to 1.6", ok and window.part.parameter("h").formula == "1.6")
        ok, _ = call("redo")
        check("redo: h 1.0 again", ok and window.part.parameter("h").formula == "1.0")

        ok, text = call("save")
        on_disk = open(path, encoding="utf-8").read()
        check("save: the file on disk has the new formulas, not modified", ok and 'formula = "1.0"' in on_disk
              and not window.doc.modified, text)
        step = os.path.join(TMP, "out.step")
        ok, out = call("export_step", path=step)
        check("export_step: the STEP written", ok and os.path.getsize(step) > 1000 and out["note"] == "", str(out))

        # the disk watch does not reload while locked; reload does, the camera kept
        with open(path, encoding="utf-8") as f:
            text = f.read()
        with open(path, "w", encoding="utf-8") as f:
            f.write(text.replace('name = "sub_l"\nformula = "40"', 'name = "sub_l"\nformula = "30"'))
        os.utime(path, None)
        for _ in range(3):
            window.poll_disk()
        check("locked: the disk watch does not reload", window.part.parameter("sub_l").formula == "40")
        camera.set_view_state([1.0, 2.0, 0.5], 33.0, camera.state()["rotation"])
        ok, state = call("reload")
        sizes = {b["name"]: b["size"] for b in state["report"]["bodies"]} if ok else {}
        check("reload: the file's sub_l 30, the camera kept", ok and np.allclose(sizes["Substrate"], [50, 30, 1.0])
              and abs(camera.state()["distance"] - 33.0) < 1e-9, str(sizes))

        bad = os.path.join(TMP, "bad.part.toml")
        with open(bad, "w") as f:
            f.write("this is not toml [")
        ok, text = call("open", path=bad)
        check("open a file that does not read: an error, the part kept", not ok and "does not read" in text
              and os.path.normcase(window.doc.path) == os.path.normcase(path)
              and QApplication.activeModalWidget() is None and not dialogs, text)

        window.view.reset_view()
        iso = camera.state()["rotation"]
        ok, _ = call("view", name="top")
        r = np.array(camera.state()["rotation"])
        window.view.reset_view()
        window.view_buttons.buttons["Top"].click()
        check("view top: as the Top button", ok and np.allclose(r, camera.state()["rotation"])
              and not np.allclose(r, iso))
        ok, text = call("view", name="sideways")
        check("an unknown view: an error", not ok and "iso" in text, text)
        camera.set_view_state([100.0, 0.0, 0.0], 500.0, camera.state()["rotation"])
        ok, _ = call("fit")
        check("fit: centered on the model, the direction kept", ok and abs(camera.state()["center"][0]) < 1e-6
              and np.allclose(camera.state()["rotation"], r) and camera.state()["distance"] < 200)

        ok, _ = call("select", kind="feature", name="CopperExt")
        check("select a feature: its row current, its highlight", ok
              and window._key_of(window.tree.currentItem()) == ("feature", "CopperExt")
              and window.view.highlight_body is not None)
        ok, text = call("select", kind="body", name="Nothing")
        check("select something missing: an error naming what there is", not ok and "Substrate" in text, text)

        code = in_thread(lambda: ui.main(["--app", "mycad", "set", "patch_w=18"]))
        check("the command line: ui.py --app mycad set patch_w=18", code == 0
              and window.part.parameter("patch_w").formula == "18", str(code))
        ok, state = call("status")
        check("status: locked, modified", ok and state["locked"] and state["modified"])

        # ---- the user takes the control back
        window.take_back_button.click()
        settle()
        check("Take control back: unlocked, banner gone, panels on", not window.locked
              and not window.lock_bar.isVisible() and window.ribbon.isEnabled() and window.side_tabs.isEnabled()
              and not window.view.look_only)
        ok, text = call("set", patch_w="20")
        check("  Claude's next change refused", not ok and "not in control" in text
              and window.part.parameter("patch_w").formula == "18", text)
        QTest.keyClick(native, Qt.Key_Z, Qt.ControlModifier)
        settle()
        check("  the shortcuts back (Ctrl+Z undoes patch_w)", window.part.parameter("patch_w").formula == "16")
        QTest.keyClick(native, Qt.Key_Y, Qt.ControlModifier)
        settle()
        window.save()
        ok, _ = call("lock")
        ok2, _ = call("unlock", message="look at the patch")
        check("unlock with a message in the status bar", ok and ok2 and not window.locked
              and "patch" in window.statusBar().currentMessage(), window.statusBar().currentMessage())
        check("no error dialogs", not dialogs, str(dialogs))
    finally:
        window.close()
        qt.processEvents()
    check("closing removes control.json", not os.path.exists(cpath))

    # main.py --locked: the window opens under Claude's control
    import main as entry

    windows = []
    real_run = type(app.use_app()).run
    type(app.use_app()).run = lambda self: windows.extend(w for w in QApplication.topLevelWidgets()
                                                          if isinstance(w, MainWindow) and w.isVisible())
    try:
        entry.windowed([path, "--locked"], app)
    finally:
        type(app.use_app()).run = real_run
    opened = windows[0] if windows else None
    check("main.py --locked: opened locked, 'started by Claude'", opened is not None and opened.locked
          and "started by Claude" in opened.lock_label.text())
    if opened is not None:
        opened.close()
        qt.processEvents()

    check("no VisPy callback errors", not vispy_errors, str(vispy_errors))
    shutil.rmtree(TMP, ignore_errors=True)
    print("\nall ok" if not FAILED else f"\nFAILED: {FAILED}")
    sys.exit(1 if FAILED else 0)


if __name__ == "__main__":
    main()
