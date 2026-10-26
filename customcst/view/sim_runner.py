# sim_runner.py
#
# Runs the FDTD solver off the GUI thread: solver/simulation.py simulate()
# (one run per port, that port excited, the others terminated in their R,
# one column of the S matrix each), shared with the console runner run.py.

from PySide6.QtCore import QObject, QThread, Signal

from solver.result_file import SimulationResult  # noqa: F401  (re-exported for callers)
from solver.simulation import simulate


class _Worker(QObject):
    # port, t (s), energy level (dB below its peak), pulse length (s)
    progress = Signal(str, float, float, float)
    finished = Signal(object)                # SimulationResult or None
    failed = Signal(str)

    def __init__(self, materials, f_min, f_max, trim_pulse=False, end_db=-40.0, excite=None):
        super().__init__()
        self.materials, self.f_min, self.f_max = materials, f_min, f_max
        self.trim_pulse, self.end_db = trim_pulse, end_db
        self.excite = excite            # port names to excite, None = all
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        try:
            result = simulate(self.materials, self.f_min, self.f_max, self.trim_pulse, self.end_db, self.excite,
                              progress=self._report)
            self.finished.emit(result)
        except Exception as error:          # report anything to the UI
            self.failed.emit(f"{type(error).__name__}: {error}")

    def _report(self, port, t, level, pulse_duration):
        self.progress.emit(port, t, level, pulse_duration)
        return not self._stop


class SimulationRunner(QObject):
    progress = Signal(str, float, float, float)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._thread = None
        self._worker = None

    @property
    def running(self):
        return self._thread is not None

    def start(self, materials, f_min, f_max, trim_pulse=False, end_db=-40.0, excite=None):
        if self.running:
            return
        self._thread = QThread()
        self._worker = _Worker(materials, f_min, f_max, trim_pulse, end_db, excite)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self.progress)
        self._worker.finished.connect(self._done)
        self._worker.failed.connect(self._fail)
        self._thread.start()

    def stop(self):
        if self._worker is not None:
            self._worker.stop()

    def _cleanup(self):
        self._thread.quit()
        self._thread.wait()
        self._thread = None
        self._worker = None

    def _done(self, result):
        self._cleanup()
        self.finished.emit(result)

    def _fail(self, message):
        self._cleanup()
        self.failed.emit(message)
