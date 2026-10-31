# remote_panel.py
#
# The Remote tab of the left panel: where the console runner's machine is
# (host, port, user, password, the customcst folder there, its Python),
# saved in remote/remote.toml on every edit (remote/sync.py), with a Check
# button. RemoteRunner does one remote/sync.py operation (check, push, fetch,
# pull, launch, status, stop) off the GUI thread; the buttons under the
# Project tree start them (MainWindow).

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
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


def _size(n):
    return f"{n / 2**20:.1f} MB" if n >= 2**20 else f"{n / 2**10:.0f} kB"


class CleanDialog(QDialog):
    """
    The models on the server (sync.server_models) with a tick each: the
    ticked ones are deleted. Ticked at first: those without run files (what
    a failed or stopped run left). A model whose run is going cannot be
    ticked.
    """

    def __init__(self, models, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Clean the server")
        self.models = models
        table = QTableWidget(len(models), 4)
        table.setHorizontalHeaderLabels(["Model", "Size", "Runs there", "State"])
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        table.horizontalHeader().setStretchLastSection(True)
        for row, model in enumerate(models):
            name = QTableWidgetItem(model.name)
            name.setToolTip("\n".join(model.files))
            if model.active:
                name.setFlags(name.flags() & ~Qt.ItemIsUserCheckable & ~Qt.ItemIsEnabled)
                name.setCheckState(Qt.Unchecked)
            else:
                name.setFlags(name.flags() | Qt.ItemIsUserCheckable)
                name.setCheckState(Qt.Unchecked if model.runs else Qt.Checked)
            table.setItem(row, 0, name)
            state = "running: kept" if model.active else model.state or "-"
            for column, text in ((1, _size(model.size)), (2, str(model.runs)), (3, state)):
                item = QTableWidgetItem(text)
                if column < 3:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                table.setItem(row, column, item)
        self.table = table
        total = sum(m.size for m in models)
        note = QLabel(
            f"{len(models)} model(s) in models/ on the server, {_size(total)}. Ticked ones are deleted "
            "there (STEP, .toml, log, status, run files); Run on server pushes a model again. A model "
            "with runs there is not ticked: take them first (Fetch) if you still want them. Nothing "
            "here is touched.")
        note.setWordWrap(True)
        everything = QPushButton("Tick all")
        everything.clicked.connect(lambda: self.set_all(True))
        nothing = QPushButton("Tick none")
        nothing.clicked.connect(lambda: self.set_all(False))
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        buttons.addButton("Delete ticked", QDialogButtonBox.AcceptRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        row = QHBoxLayout()
        row.addWidget(everything)
        row.addWidget(nothing)
        row.addStretch(1)
        row.addWidget(buttons)
        layout = QVBoxLayout(self)
        layout.addWidget(note)
        layout.addWidget(table, 1)
        layout.addLayout(row)
        self.resize(560, 420)

    def set_all(self, on):
        for row, model in enumerate(self.models):
            if not model.active:
                self.table.item(row, 0).setCheckState(Qt.Checked if on else Qt.Unchecked)

    def ticked(self):
        return [m.name for row, m in enumerate(self.models)
                if not m.active and self.table.item(row, 0).checkState() == Qt.Checked]


class RemotePanel(QWidget):
    check_requested = Signal()
    clean_requested = Signal()

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
        self.clean_button = QPushButton("Clean server...")
        self.clean_button.setToolTip("List the models kept on the server and delete what is not needed "
                                     "(a finished run's files go by themselves after its Fetch)")
        self.clean_button.clicked.connect(self._clean)
        buttons = QHBoxLayout()
        buttons.addWidget(self.check_button)
        buttons.addWidget(self.clean_button)
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
            "(nothing connects by itself); Stop ends the run (nothing saved). Once a done run "
            "is fetched, its model's files on the server are deleted; Clean server... lists what "
            "else is kept there (failed or stopped runs, old models) and deletes the ticked. "
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

    def _clean(self):
        self.save()
        self.clean_requested.emit()
