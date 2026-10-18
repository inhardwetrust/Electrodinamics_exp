# sim_runner.py
#
# Runs the FDTD solver off the GUI thread. One run per port (that port
# excited, the others terminated in their R) gives one column of the S
# matrix each. Results: frequencies, S[(j, k)] (b_j / a_k), Z_in per port.

import time

import numpy as np
from PySide6.QtCore import QObject, QThread, Signal

from solver.fdtd import FdtdSolver
from solver.result_file import SimulationResult  # noqa: F401  (re-exported for callers)
from solver.sparams import input_impedance, s_column

FREQUENCY_POINTS = 401


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
        begin = time.perf_counter()
        try:
            ports = list(self.materials.ports) + list(self.materials.waveports)
            names = [p.name for p in ports]
            f = np.linspace(self.f_min, self.f_max, FREQUENCY_POINTS) * 1e9
            result = SimulationResult(
                f, names, [p.resistance for p in ports],
                f"FDTD, {self.f_min:g}-{self.f_max:g} GHz, grid {self.materials.grid.shape}",
            )
            result.eps_eff = [getattr(getattr(p, "mode", None), "eps_eff", float("nan")) for p in ports]

            for k, active in enumerate(names):
                if self.excite is not None and active not in self.excite:
                    continue                    # not excited: its column of S stays empty
                solver = FdtdSolver(self.materials, self.f_min, self.f_max, active_port=active,
                                    trim_pulse=self.trim_pulse)
                start = time.perf_counter()

                def report(t, level, active=active, solver=solver):
                    self.progress.emit(active, t, level, solver.pulse.duration)
                    return not self._stop

                status = solver.run(end_db=self.end_db, progress=report)
                if status == "stopped":
                    self.finished.emit(None)
                    return

                column = s_column(solver, f)
                for j, name in enumerate(names):
                    result.s[j, k] = column[name]
                result.z_in[active] = input_impedance(solver, f)
                result.info.append(
                    f"{active}: {status}, {solver.step_index} steps ({solver.t * 1e9:.2f} ns), "
                    f"{time.perf_counter() - start:.1f} s, {int(np.prod(solver.shape)):,} cells with PML"
                )

            result.elapsed = time.perf_counter() - begin
            self.finished.emit(result)
        except Exception as error:          # report anything to the UI
            self.failed.emit(f"{type(error).__name__}: {error}")


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
