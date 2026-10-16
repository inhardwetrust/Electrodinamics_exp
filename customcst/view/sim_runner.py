# sim_runner.py
#
# Runs the FDTD solver off the GUI thread. One run per port (that port
# excited, the others terminated in their R) gives one column of the S
# matrix each. Results: frequencies, S[(j, k)] (b_j / a_k), Z_in per port.

import time

import numpy as np
from PySide6.QtCore import QObject, QThread, Signal

from solver.fdtd import FdtdSolver
from solver.sparams import input_impedance, s_column

FREQUENCY_POINTS = 401


class SimulationResult:
    def __init__(self, frequencies, ports, resistances, comment=""):
        self.frequencies = frequencies
        self.ports = ports              # port names, in S-matrix order
        self.resistances = resistances  # reference R per port (Ohm)
        self.comment = comment
        self.s = {}                     # (j, k) -> complex array
        self.z_in = {}                  # port -> complex array
        self.info = []                  # text lines (steps, status, time)


class _Worker(QObject):
    progress = Signal(str, float, float)     # port, t (s), energy level (dB)
    finished = Signal(object)                # SimulationResult or None
    failed = Signal(str)

    def __init__(self, materials, f_min, f_max):
        super().__init__()
        self.materials, self.f_min, self.f_max = materials, f_min, f_max
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        try:
            ports = list(self.materials.ports) + list(self.materials.waveports)
            names = [p.name for p in ports]
            f = np.linspace(self.f_min, self.f_max, FREQUENCY_POINTS) * 1e9
            result = SimulationResult(
                f, names, [p.resistance for p in ports],
                f"FDTD, {self.f_min:g}-{self.f_max:g} GHz, grid {self.materials.grid.shape}",
            )

            for k, active in enumerate(names):
                solver = FdtdSolver(self.materials, self.f_min, self.f_max, active_port=active)
                start = time.perf_counter()

                def report(t, level, active=active):
                    self.progress.emit(active, t, level)
                    return not self._stop

                status = solver.run(progress=report)
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

            self.finished.emit(result)
        except Exception as error:          # report anything to the UI
            self.failed.emit(f"{type(error).__name__}: {error}")


class SimulationRunner(QObject):
    progress = Signal(str, float, float)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._thread = None
        self._worker = None

    @property
    def running(self):
        return self._thread is not None

    def start(self, materials, f_min, f_max):
        if self.running:
            return
        self._thread = QThread()
        self._worker = _Worker(materials, f_min, f_max)
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
