# results_panel.py
#
# The Results tab, like a VNA screen: finished simulations (S-matrices over
# frequency) shown in a chosen format (solver/vna.py FORMATS) for the
# checked traces S_jk, with a frequency marker and its readout, and
# Touchstone export. Nothing here re-runs the solver.
#
# Several runs can be shown at once (ticked in Project > Results): the
# newest of them is the primary one (full color; marker, readout and
# export follow it), older ones are dimmed for comparison. Runs may have
# different bands and port sets: each curve keeps its own frequencies.
#
# The mouse wheel over the plot moves the marker (one point per notch,
# ten with Ctrl).

import os

import numpy as np
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QLabel, QMessageBox,
    QPushButton, QSlider, QStackedWidget, QVBoxLayout, QWidget,
)

from solver import vna
from view.plot import LinePlot
from view.smith import SmithChart

REFLECTION_ONLY = ("SWR", "Smith", "Z in")


class ResultsPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.runs = []                  # shown runs, oldest first
        self.default_dir = ""
        self._trace_boxes = {}          # (j, k) -> QCheckBox
        self._ports = 0                 # trace boxes are for this many ports

        self.format_combo = QComboBox()
        self.format_combo.addItems(vna.FORMATS)
        self.format_combo.currentIndexChanged.connect(lambda _: self._redraw())

        self.trace_row = QHBoxLayout()
        self.trace_row.setSpacing(10)

        self.save_button = QPushButton("Save .sNp…")
        self.save_button.setToolTip("Touchstone file of the newest shown run")
        self.save_button.clicked.connect(self._save_touchstone)
        self.save_button.setEnabled(False)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("Format:"))
        controls.addWidget(self.format_combo)
        controls.addSpacing(12)
        controls.addWidget(QLabel("Traces:"))
        controls.addLayout(self.trace_row)
        controls.addStretch(1)
        controls.addWidget(self.save_button)

        self.line_plot = LinePlot()
        self.smith = SmithChart()
        for widget in (self.line_plot, self.smith):
            widget.installEventFilter(self)             # wheel -> marker
            widget.setToolTip("Mouse wheel: move the marker (Ctrl: 10 points)")
        self.stack = QStackedWidget()
        self.stack.addWidget(self.line_plot)
        self.stack.addWidget(self.smith)

        self.marker_slider = QSlider(Qt.Horizontal)
        self.marker_slider.valueChanged.connect(lambda _: self._redraw())
        self.marker_label = QLabel()
        self.marker_label.setMinimumWidth(110)
        marker_row = QHBoxLayout()
        marker_row.addWidget(QLabel("Marker:"))
        marker_row.addWidget(self.marker_slider, 1)
        marker_row.addWidget(self.marker_label)

        self.readout = QLabel()
        mono = QFont("Consolas")
        mono.setStyleHint(QFont.Monospace)
        self.readout.setFont(mono)
        self.readout.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.info = QLabel()
        self.info.setStyleSheet("color: gray")
        self.info.setWordWrap(True)
        self.status = QLabel()          # current / outdated
        self.status.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addLayout(controls)
        layout.addWidget(self.stack, 1)
        layout.addLayout(marker_row)
        layout.addWidget(self.readout)
        layout.addWidget(self.status)
        layout.addWidget(self.info)
        self._redraw()

    # =====================================================
    # Data
    # =====================================================

    @property
    def primary(self):
        """The newest shown run (full color; the marker and export follow it)."""
        return self.runs[-1] if self.runs else None

    def set_message(self, text):
        self.info.setText(text)

    def set_status(self, text, color="gray"):
        self.status.setText(text)
        self.status.setStyleSheet(f"color: {color}")

    def set_results(self, runs):
        """runs: the shown SimulationResults, oldest first (may be empty)."""
        marker_f = self._marker_frequency()
        self.runs = list(runs)
        self.save_button.setEnabled(bool(self.runs))
        n = max((len(r.ports) for r in self.runs), default=0)
        if n != self._ports:
            self._rebuild_trace_boxes(n)

        if self.runs:
            f = self.primary.frequencies
            self.marker_slider.blockSignals(True)
            self.marker_slider.setRange(0, len(f) - 1)
            target = marker_f if marker_f is not None else 0.5 * (f[0] + f[-1])
            self.marker_slider.setValue(int(np.argmin(np.abs(f - target))))
            self.marker_slider.blockSignals(False)

            r = self.primary
            names = ", ".join(f"{k + 1} = {name} ({z:g} Ω)"
                              for k, (name, z) in enumerate(zip(r.ports, r.resistances)))
            title = f"<b>{r.name}</b> ({r.saved})" if r.name else ""
            others = (f"<br>dimmed: {', '.join(x.name for x in self.runs[:-1])}"
                      if len(self.runs) > 1 else "")
            self.info.setText(f"{title}<br>ports: {names}<br>" + "<br>".join(r.info) + others)
        else:
            self.info.setText("")
        self._redraw()

    def _rebuild_trace_boxes(self, n):
        while self.trace_row.count():
            widget = self.trace_row.takeAt(0).widget()
            if widget is not None:
                widget.deleteLater()
        self._trace_boxes = {}
        for j in range(n):
            for k in range(n):
                box = QCheckBox(f"S{j + 1}{k + 1}")
                box.setChecked(k == 0)       # column 1: S11, S21 (TX on port 1)
                box.toggled.connect(lambda _: self._redraw())
                self.trace_row.addWidget(box)
                self._trace_boxes[j, k] = box
        self._ports = n

    def _marker_frequency(self):
        if not self.runs:
            return None
        f = self.primary.frequencies
        return float(f[min(self.marker_slider.value(), len(f) - 1)])

    def _checked(self, reflection_only=False):
        return [(j, k) for (j, k), box in self._trace_boxes.items()
                if box.isChecked() and (not reflection_only or j == k)]

    def _color(self, j, k):
        return j * max(self._ports, 1) + k

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Wheel and self.runs:
            notches = event.angleDelta().y() / 120.0
            step = 10 if event.modifiers() & Qt.ControlModifier else 1
            value = self.marker_slider.value() + int(round(notches * step))
            self.marker_slider.setValue(max(self.marker_slider.minimum(),
                                            min(self.marker_slider.maximum(), value)))
            return True
        return super().eventFilter(watched, event)

    # =====================================================
    # Drawing
    # =====================================================

    def _redraw(self):
        if not self.runs:
            self.stack.setCurrentWidget(self.line_plot)
            self.line_plot.clear()
            self.readout.setText("")
            self.marker_label.setText("")
            return

        fmt = self.format_combo.currentText()
        fm = self._marker_frequency()
        self.marker_label.setText(f"{fm / 1e9:.4f} GHz")

        traces = self._checked(reflection_only=fmt in REFLECTION_ONLY)
        note = ""
        if fmt in REFLECTION_ONLY and len(traces) < len(self._checked()):
            note = f"  ({fmt}: reflection traces S11, S22, ... only)"
        several = len(self.runs) > 1

        def label(text, run):
            return f"{text} · {run.name}" if several else text

        if fmt == "Smith":
            self.stack.setCurrentWidget(self.smith)
            items = []
            for run in self.runs:
                dim = run is not self.primary
                m = _index_at(run.frequencies, fm)
                for j, k in traces:
                    if (j, k) in run.s:
                        items.append((label(f"S{j + 1}{k + 1}", run), run.s[j, k], self._color(j, k), dim, m))
            self.smith.set_data(items)
        else:
            self.stack.setCurrentWidget(self.line_plot)
            curves = []
            for run in self.runs:
                dim = run is not self.primary
                f = run.frequencies
                for j, k in traces:
                    if (j, k) not in run.s:
                        continue
                    s = run.s[j, k]
                    name = label(f"S{j + 1}{k + 1}", run)
                    color = self._color(j, k)
                    if fmt == "Z in":
                        z = vna.gamma_to_z(s, run.resistances[j])
                        curves += [(label(f"Re Z{j + 1}", run), f / 1e9, z.real, color, dim),
                                   (label(f"Im Z{j + 1}", run), f / 1e9, z.imag, color + 4, dim)]
                        continue
                    y = {"LogMag": lambda: vna.logmag(s),
                         "LinMag": lambda: np.abs(s),
                         "Phase": lambda: vna.phase_deg(s),
                         "Group delay": lambda: vna.group_delay(s, f) * 1e9,
                         "SWR": lambda: np.minimum(vna.swr(s), 1e3)}[fmt]()
                    curves.append((name, f / 1e9, y, color, dim))

            y_label, y_range = "", None
            if fmt == "LogMag":
                y_label = "|S|, dB"
                low = min((np.nanmin(c[2]) for c in curves), default=-40.0)
                y_range = (max(-80.0, low - 3), 3.0)
            elif fmt == "LinMag":
                y_label, y_range = "|S|", (0.0, 1.05)
            elif fmt == "Phase":
                y_label, y_range = "phase, °", (-185.0, 185.0)
            elif fmt == "Group delay":
                y_label = "group delay, ns"
            elif fmt == "SWR":
                top = max((np.nanmax(c[2]) for c in curves), default=2.0)
                y_label, y_range = "SWR", (1.0, min(max(1.2 * top, 1.5), 10.0))
            elif fmt == "Z in":
                y_label = "Z, Ω"
            self.line_plot.set_data(curves, "f, GHz", y_label, y_range, marker_x=fm / 1e9)

        # Readout: the primary run, then the dimmed ones at the same frequency.
        lines = []
        for run in reversed(self.runs):
            m = _index_at(run.frequencies, fm)
            if m is None:
                continue
            prefix = f"[{run.name}] " if several else ""
            for j, k in self._checked():
                if (j, k) not in run.s:
                    continue
                line = prefix + vna.marker_readout(fmt, run.s[j, k][m], run.frequencies[m],
                                                   run.resistances[j], j, k)
                if fmt == "Group delay":
                    line += f"  τg {vna.group_delay(run.s[j, k], run.frequencies)[m] * 1e9:.4f} ns"
                lines.append(line)
        self.readout.setText("\n".join(lines) + note)

    # =====================================================
    # Export
    # =====================================================

    def _save_touchstone(self):
        r = self.primary
        if r is None:
            return
        if len(set(r.resistances)) > 1:
            QMessageBox.warning(self, "Touchstone",
                                "Ports have different R; Touchstone v1 needs one reference. "
                                f"Writing with R = {r.resistances[0]:g} Ω.")
        n = len(r.ports)
        safe = "".join(c if c.isalnum() or c in "-_ " else "_" for c in r.name).strip() or "result"
        start = os.path.join(self.default_dir, f"{safe}.s{n}p")
        path, _ = QFileDialog.getSaveFileName(self, "Save Touchstone", start, f"Touchstone (*.s{n}p)")
        if path:
            vna.write_touchstone(path, r.frequencies, r.s, r.ports, r.resistances[0], r.comment)


def _index_at(frequencies, f):
    """Index of the point nearest f, or None if f is outside the run's band."""
    if f is None or not frequencies[0] - 1e-6 * abs(frequencies[0]) <= f <= frequencies[-1] * (1 + 1e-6):
        return None
    return int(np.argmin(np.abs(frequencies - f)))
