# check_remote.py
#
# Push and pull (remote/sync.py, the Remote tab) against a real SSH/SFTP
# server: a paramiko server on localhost serving a temp folder as the
# remote (home /home/me, customcst in ~/software/customcst, run.py there):
#   - an unknown host key stops with its fingerprint; trusted, it connects
#     (the key goes to known_hosts beside the settings file);
#   - a wrong password, a path without run.py: clear errors;
#   - push: the STEP and .toml land in models/ byte for byte, a second
#     push replaces them, no .part files are left;
#   - pull: no runs there -> nothing; the remote's runs arrive in
#     model.results/, a second pull brings nothing new; a connection lost
#     half way leaves the local runs as they were and no temp folder; a
#     .toml changed here since the push comes back as the remote's bytes
#     (the same STEP does not);
#   - the window: Push and Pull buttons, the Remote tab's settings saved to
#     remote.toml, the pulled run listed and ticked; a mesh setting changed,
#     pushed and changed back: Pull asks, "yes" brings the pushed value
#     back into the open project, "no" keeps the local one.
#
#   python checks/check_remote.py [shot.png]     (~20 s)

import os
import posixpath
import shutil
import socket
import sys
import tempfile
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

import paramiko                                                          # noqa: E402

FAILED = []
MODEL = "01_linie50x30_3"
HOME = "/home/me"
USER, PASSWORD = "me", "secret"


def check(label, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {label} {detail}")
    if not ok:
        FAILED.append(label)


# ---------------------------------------------------------------- the server

class _Handle(paramiko.SFTPHandle):
    def stat(self):
        return paramiko.SFTPAttributes.from_stat(os.fstat(self.readfile.fileno()))

    def chattr(self, attr):
        return paramiko.SFTP_OK


def _sftp_on(disk):
    """An SFTP server interface serving the folder `disk` as '/'."""
    class Sftp(paramiko.SFTPServerInterface):
        def canonicalize(self, path):
            return posixpath.normpath(path if path.startswith("/") else posixpath.join(HOME, path))

        def _real(self, path):
            return os.path.join(disk, self.canonicalize(path).lstrip("/"))

        def _attrs(self, real, name=None):
            attrs = paramiko.SFTPAttributes.from_stat(os.stat(real))
            if name is not None:
                attrs.filename = name
            return attrs

        def list_folder(self, path):
            try:
                real = self._real(path)
                return [self._attrs(os.path.join(real, n), n) for n in os.listdir(real)]
            except OSError as error:
                return paramiko.SFTPServer.convert_errno(error.errno)

        def stat(self, path):
            try:
                return self._attrs(self._real(path))
            except OSError as error:
                return paramiko.SFTPServer.convert_errno(error.errno)

        lstat = stat

        def open(self, path, flags, attr):
            try:
                fd = os.open(self._real(path), flags | getattr(os, "O_BINARY", 0), 0o666)
            except OSError as error:
                return paramiko.SFTPServer.convert_errno(error.errno)
            mode = "wb" if flags & os.O_WRONLY else "r+b" if flags & os.O_RDWR else "rb"
            handle = _Handle(flags)
            handle.readfile = handle.writefile = os.fdopen(fd, mode)
            return handle

        def remove(self, path):
            os.remove(self._real(path))
            return paramiko.SFTP_OK

        def rename(self, old, new):
            if os.path.exists(self._real(new)):
                return paramiko.SFTP_FAILURE
            os.rename(self._real(old), self._real(new))
            return paramiko.SFTP_OK

        def posix_rename(self, old, new):
            os.replace(self._real(old), self._real(new))
            return paramiko.SFTP_OK

        def mkdir(self, path, attr):
            os.mkdir(self._real(path))
            return paramiko.SFTP_OK
    return Sftp


class _Server(paramiko.ServerInterface):
    def check_auth_password(self, username, password):
        ok = (username, password) == (USER, PASSWORD)
        return paramiko.AUTH_SUCCESSFUL if ok else paramiko.AUTH_FAILED

    def get_allowed_auths(self, username):
        return "password"

    def check_channel_request(self, kind, chanid):
        return paramiko.OPEN_SUCCEEDED if kind == "session" else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED


def serve(disk):
    """Starts the server; returns (port, host key)."""
    key = paramiko.RSAKey.generate(2048)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(8)

    def loop():
        while True:
            connection, _ = listener.accept()
            transport = paramiko.Transport(connection)
            transport.add_server_key(key)
            transport.set_subsystem_handler("sftp", paramiko.SFTPServer, _sftp_on(disk))
            try:
                transport.start_server(server=_Server())
            except (paramiko.SSHException, EOFError, OSError):
                pass
    threading.Thread(target=loop, daemon=True).start()
    return listener.getsockname()[1], key


# ---------------------------------------------------------------- the checks

def main():
    folder = tempfile.mkdtemp()
    os.environ["CUSTOMCST_SETTINGS"] = os.path.join(folder, "settings.json")
    os.environ["CUSTOMCST_REMOTE"] = os.path.join(folder, "local", "remote", "remote.toml")
    try:
        return run(folder)
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def run(folder):
    from remote import sync
    from solver.result_file import results_dir

    disk = os.path.join(folder, "disk")                     # the remote's '/'
    customcst = os.path.join(disk, "home", "me", "software", "customcst")
    os.makedirs(customcst)
    open(os.path.join(customcst, "run.py"), "w").close()
    port, key = serve(disk)

    local = os.path.join(folder, "local")
    os.makedirs(local)
    step = os.path.join(local, f"{MODEL}.stp")
    shutil.copy(os.path.join(ROOT, "models", f"{MODEL}.stp"), step)
    shutil.copy(os.path.join(ROOT, "models", f"{MODEL}.toml"), os.path.splitext(step)[0] + ".toml")

    config = sync.RemoteConfig(host="127.0.0.1", port=port, user=USER, password=PASSWORD,
                               path="~/software/customcst")
    try:
        sync.check(config)
        check("an unknown host key stops", False)
    except sync.UnknownHost as error:
        check("an unknown host key stops, with its fingerprint",
              error.key == key and sync.fingerprint(key).startswith("SHA256:"), sync.fingerprint(key))
        sync.trust_host(error.host, error.key)
    where = sync.check(config)
    check("trusted: connects, finds run.py", where.endswith("/home/me/software/customcst")
          and os.path.isfile(sync.known_hosts_path()), where)

    for label, bad, expect in (("a wrong password", {"password": "nope"}, "authentication failed"),
                               ("a path without run.py", {"path": "~/software"}, "no run.py")):
        try:
            sync.check(sync.RemoteConfig(**{**vars(config), **bad}))
            check(f"{label}: an error", False)
        except sync.RemoteError as error:
            check(f"{label}: a clear error", expect in str(error), str(error))

    # push
    remote_step = sync.push(config, step)
    models = os.path.join(customcst, "models")
    same = all(open(os.path.join(models, os.path.basename(p)), "rb").read() == open(p, "rb").read()
               for p in (step, os.path.splitext(step)[0] + ".toml"))
    check("push: STEP and .toml in models/, byte for byte", same and remote_step.endswith(f"models/{MODEL}.stp"),
          remote_step)
    with open(os.path.splitext(step)[0] + ".toml", "a", encoding="utf-8") as file:
        file.write("\n# edited\n")
    sync.push(config, step)
    toml = open(os.path.join(models, f"{MODEL}.toml"), encoding="utf-8").read()
    check("push again: replaced, no .part left", toml.endswith("# edited\n")
          and not [n for n in os.listdir(models) if n.endswith(".part")], str(sorted(os.listdir(models))))

    # pull
    got = sync.pull(config, step)
    check("pull: no runs there, model the same: nothing", got.runs == [] and got.model == {}, str(got.model.keys()))
    toml_path = os.path.splitext(step)[0] + ".toml"
    pushed = open(toml_path, "rb").read()
    with open(toml_path, "a", encoding="utf-8") as file:
        file.write("# changed after the push\n")
    got = sync.pull(config, step)
    check("pull: a .toml changed since the push comes back (the same STEP does not)",
          list(got.model) == [toml_path] and got.model[toml_path] == pushed, str(list(got.model)))
    sync.write_local(got.model)
    check("write_local: the remote's .toml in place", open(toml_path, "rb").read() == pushed
          and not os.path.exists(toml_path + ".pull.tmp"))
    remote_runs = os.path.join(models, f"{MODEL}.results")
    os.makedirs(remote_runs)
    source = os.path.join(ROOT, "models", f"{MODEL}.results")
    run_file = sorted(n for n in os.listdir(source) if n.endswith(".npz"))[0]
    shutil.copy(os.path.join(source, run_file), remote_runs)
    with open(os.path.join(remote_runs, "20990101-000000.npz"), "wb") as file:
        file.write(os.urandom(50000))
    open(os.path.join(remote_runs, "20990101-000001.tmp.npz"), "wb").close()     # being written

    original = paramiko.SFTPClient.get
    calls = []

    def broken(self, remotepath, localpath, callback=None, **kw):
        calls.append(remotepath)
        if len(calls) == 2:
            raise OSError("connection lost")
        return original(self, remotepath, localpath, callback=callback, **kw)
    paramiko.SFTPClient.get = broken
    try:
        sync.pull(config, step)
        check("a broken pull raises", False)
    except OSError:
        pass
    finally:
        paramiko.SFTPClient.get = original
    leftovers = [n for n in os.listdir(local) if n.startswith(".pull-")]
    runs_left = os.listdir(results_dir(step)) if os.path.isdir(results_dir(step)) else []
    check("a broken pull: no local run, no temp folder", not runs_left and not leftovers,
          f"runs {runs_left}, leftovers {leftovers}")

    got = sync.pull(config, step).runs
    check("pull: the remote's runs arrive (not the one being written)",
          sorted(got) == sorted([run_file, "20990101-000000.npz"])
          and sorted(os.listdir(results_dir(step))) == sorted(got)
          and not [n for n in os.listdir(local) if n.startswith(".pull-")], str(got))
    check("pull again: nothing new", sync.pull(config, step).runs == [])
    for where in (results_dir(step), remote_runs):          # the junk: the window lists runs
        os.remove(os.path.join(where, "20990101-000000.npz"))

    # the window
    from PySide6.QtCore import QCoreApplication, QElapsedTimer
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    from view.main_window import MainWindow
    window = MainWindow(step)
    window.wait_for_mesh()
    check("Push on: the model can run", window.push_button.isEnabled())
    window.project.set_setting("mesh", "cells_per_wavelength", 22.0)
    window._after_edit()                                    # an edit: the mesh is rebuilt
    building = (not window.push_button.isEnabled()
                and "still being built" in window.push_button.toolTip())
    window.wait_for_mesh()
    check("Push off while the mesh is rebuilt, on again after", building and window.push_button.isEnabled(),
          window.push_button.toolTip())
    window.project.set_setting("mesh", "cells_per_wavelength", 20.0)
    window._after_edit()
    window.wait_for_mesh()
    toml_before = open(toml_path, "rb").read()
    for name in ("port1", "port2"):
        window.project.set_type(next(b for b in window.geometry.bodies if b.name == name), "pec")
    window._after_edit()
    window.wait_for_mesh()
    off = not window.push_button.isEnabled()
    window._remote_start("push")                            # a click on the disabled button: nothing
    check("Push off for a model that cannot run (no port), with the reason", off
          and not window.remote.running and "port" in window.push_button.toolTip(),
          window.push_button.toolTip())
    with open(toml_path, "wb") as file:                     # the ports back
        file.write(toml_before)
    window.load(step)
    window.wait_for_mesh()

    def finish():
        timer = QElapsedTimer()
        timer.start()
        while window.remote.running and timer.elapsed() < 30000:
            QCoreApplication.processEvents()
        app.processEvents()

    panel = window.remote_panel
    panel.host.setText("127.0.0.1")
    panel.port.setValue(port)
    panel.user.setText(USER)
    panel.password.setText(PASSWORD)
    panel.path.setText("~/software/customcst")
    panel.save()
    saved = sync.load_config()
    check("Remote tab: saved to remote.toml", saved == config, f"{saved}")
    window.side_tabs.setCurrentWidget(panel)
    panel.check_button.click()
    finish()
    check("Check button: connected", panel.status.text().startswith("connected"), panel.status.text())

    os.remove(os.path.join(models, f"{MODEL}.stp"))
    window.push_button.click()
    finish()
    check("Push button: the STEP is there again", os.path.isfile(os.path.join(models, f"{MODEL}.stp")),
          window.remote_panel.status.text())

    shutil.rmtree(results_dir(step))
    window._load_runs(step)
    before = len(window.runs)
    window.pull_button.click()
    finish()
    check("Pull button: the run is listed, ticked, Results shown", before == 0 and len(window.runs) == 1
          and os.path.basename(window.runs[0].path) == run_file and window.runs[0].path in window.shown
          and window.tabs.currentWidget() is window.results, window.remote_panel.status.text())

    # the user's case: a mesh setting changed, pushed, changed back, pulled
    from PySide6.QtWidgets import QMessageBox
    answers = []
    original_question = QMessageBox.question
    QMessageBox.question = staticmethod(lambda *args, **kw: answers.pop(0))
    try:
        for answer, expect in ((QMessageBox.No, 20.0), (QMessageBox.Yes, 25.0)):
            window.project.set_setting("mesh", "cells_per_wavelength", 25.0)
            window._save()
            window.push_button.click()
            finish()
            window.project.set_setting("mesh", "cells_per_wavelength", 20.0)
            window._save()
            answers.append(answer)
            window.pull_button.click()
            finish()
            value = window.project.settings["mesh"]["cells_per_wavelength"]
            check(f"Pull, the server's .toml differs, '{'yes' if answer == QMessageBox.Yes else 'no'}': "
                  f"cells per wavelength {expect:g}", value == expect and not answers,
                  f"{value:g}; {window.remote_panel.status.text()}")
    finally:
        QMessageBox.question = original_question
    if len(sys.argv) > 1:
        window.side_tabs.setCurrentWidget(panel)
        app.processEvents()
        window.grab().save(sys.argv[1])
    window.close()

    print("\nall checks passed" if not FAILED else f"\nFAILED: {', '.join(FAILED)}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
