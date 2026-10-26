# remote_panel.py
#
# The Remote tab of the left panel: where the console runner's machine is
# (host, port, user, password, the customcst folder there, its Python),
# saved in remote/remote.toml on every edit (remote/sync.py), with a Check
# button. RemoteRunner does one remote/sync.py operation (check, push, fetch,
# pull, launch, status, stop) off the GUI thread; the buttons under the
# Project tree start them (MainWindow).

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtWidgets import (
    QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from remote import sync


class _Worker(QObject):
    progress = Signal(str)
    finished = Signal(str, object)          # operation, its return value
    failed = Signal(str, object)            # message, UnknownHost or None

    def __init__(self, operation, config, step_path):
        super().__init__()
        self.operation, self.config, self.step_path = operation, config, step_path

    def run(self):
        try:
            if self.operation == "check":
                value = sync.check(self.config)
            else:
                value = getattr(sync, self.operation)(self.config, self.step_path, progress=self.progress.emit)
            self.finished.emit(self.operation, value)
        except sync.UnknownHost as error:
            self.failed.emit(str(error), error)
        except Exception as error:          # report anything to the UI
            self.failed.emit(f"{type(error).__name__}: {error}" if not isinstance(error, sync.RemoteError)
                             else str(error), None)


class RemoteRunner(QObject):
    progress = Signal(str)
    finished = Signal(str, object)
    failed = Signal(str, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._thread = None
        self._worker = None

    @property
    def running(self):
        return self._thread is not None

    def start(self, operation, config, step_path=None):
        if self.running:
            return
        self._thread = QThread()
        self._worker = _Worker(operation, config, step_path)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self.progress)
        self._worker.finished.connect(self._done)
        self._worker.failed.connect(self._fail)
        self._thread.start()

    def wait(self):
        """Blocks until a running operation ends (closing the window)."""
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait()

    def _cleanup(self):
        self._thread.quit()
        self._thread.wait()
        self._thread = None
        self._worker = None

    def _done(self, operation, value):
        self._cleanup()
        self.finished.emit(operation, value)

    def _fail(self, message, unknown):
        self._cleanup()
        self.failed.emit(message, unknown)


class RemotePanel(QWidget):
    check_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        config = sync.load_config()
        self.host = QLineEdit(config.host)
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(config.port)
        self.user = QLineEdit(config.user)
        self.password = QLineEdit(config.password)
        self.password.setEchoMode(QLineEdit.Password)
        self.password.setPlaceholderText("empty: SSH keys / agent")
        self.path = QLineEdit(config.path)
        self.path.setPlaceholderText("~/software/customcst")
        self.python = QLineEdit(config.python)
        self.python.setPlaceholderText(sync.RemoteConfig.python)
        self.python.setToolTip("The Python that runs run.py: relative to Path, or absolute")
        for edit in (self.host, self.user, self.password, self.path, self.python):
            edit.editingFinished.connect(self.save)
        self.port.editingFinished.connect(self.save)

        form = QFormLayout()
        form.addRow("Host", self.host)
        form.addRow("Port", self.port)
        form.addRow("User", self.user)
        form.addRow("Password", self.password)
        form.addRow("Path", self.path)
        form.addRow("Python", self.python)

        self.check_button = QPushButton("Check")
        self.check_button.setToolTip("Connect and look for run.py in the path")
        self.check_button.clicked.connect(self._check)
        buttons = QHBoxLayout()
        buttons.addWidget(self.check_button)
        buttons.addStretch(1)

        self.status = QLabel()
        self.status.setWordWrap(True)

        hint = QLabel(
            "The machine that runs run.py (README \"Running on another machine\"). Path is its "
            "customcst folder, the one with run.py; Python runs it (relative to Path). Under "
            "the Project tree: Push copies this model's STEP and .toml into models/ there, "
            "replacing them; Run on server pushes and starts run.py (one run at a time on the "
            "server): a pending row in Results, followed while the window is open (progress in "
            "the status bar), taken when done; Fetch asks the server and takes finished runs "
            "(nothing connects by itself); Stop ends the run (nothing saved). "
            "Saved in remote/remote.toml, which is not in git: "
            "the password is kept there as plain text, so prefer an SSH key (leave it empty)."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray")

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addLayout(buttons)
        layout.addWidget(self.status)
        layout.addStretch(1)
        layout.addWidget(hint)

    def config(self):
        return sync.RemoteConfig(host=self.host.text().strip(), port=self.port.value(),
                                 user=self.user.text().strip(), password=self.password.text(),
                                 path=self.path.text().strip(),
                                 python=self.python.text().strip() or sync.RemoteConfig.python)

    def save(self):
        config = self.config()
        if config != sync.load_config():
            try:
                sync.save_config(config)
            except OSError as error:
                self.show_status(f"not saved: {error}", error=True)

    def show_status(self, text, error=False):
        self.status.setText(text)
        self.status.setStyleSheet("color: #c22" if error else "")

    def _check(self):
        self.save()
        self.check_requested.emit()
