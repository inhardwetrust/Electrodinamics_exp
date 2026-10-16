# results_panel.py
#
# The Results dock, like a VNA screen: one finished simulation (an S-matrix
# over frequency) shown in a chosen format (solver/vna.py FORMATS) for the
# checked traces S_jk, with a frequency marker and its readout, and
# Touchstone export. Nothing here re-runs the solver.

import os

import numpy as np
from PySide6.QtCore import Qt
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
        self.result = None
        self.default_dir = ""
        self._trace_boxes = {}          # (j, k) -> QCheckBox

        self.format_combo = QComboBox()
        self.format_combo.addItems(vna.FORMATS)
        self.format_combo.currentIndexChanged.connect(lambda _: self._redraw())

        self.trace_row = QHBoxLayout()
        self.trace_row.setSpacing(10)

        self.save_button = QPushButton("Save .sNp…")
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

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addLayout(controls)
        layout.addWidget(self.stack, 1)
        layout.addLayout(marker_row)
        layout.addWidget(self.readout)
        layout.addWidget(self.info)
        self._redraw()

    # =====================================================
    # Data
    # =====================================================

    def set_message(self, text):
        self.info.setText(text)

    def set_result(self, result):
        self.result = result
        self.save_button.setEnabled(result is not None)

        while self.trace_row.count():
            widget = self.trace_row.takeAt(0).widget()
            if widget is not None:
                widget.deleteLater()
        self._trace_boxes = {}

        if result is not None:
            n = len(result.ports)
            for j in range(n):
                for k in range(n):
                    box = QCheckBox(f"S{j + 1}{k + 1}")
                    box.setChecked(k == 0)       # column 1: S11, S21 (TX on port 1)
                    box.toggled.connect(lambda _: self._redraw())
                    self.trace_row.addWidget(box)
                    self._trace_boxes[j, k] = box

            self.marker_slider.blockSignals(True)
            self.marker_slider.setRange(0, len(result.frequencies) - 1)
            f = result.frequencies
            self.marker_slider.setValue(int(np.argmin(np.abs(f - 0.5 * (f[0] + f[-1])))))
            self.marker_slider.blockSignals(False)

            names = ", ".join(f"{k + 1} = {name} ({r:g} Ω)"
                              for k, (name, r) in enumerate(zip(result.ports, result.resistances)))
            self.info.setText(f"ports: {names}<br>" + "<br>".join(result.info))
        self._redraw()

    def _checked(self, reflection_only=False):
        traces = []
        for (j, k), box in self._trace_boxes.items():
            if box.isChecked() and (not reflection_only or j == k):
                traces.append((j, k))
        return traces

    def _color(self, j, k):
        n = len(self.result.ports)
        return j * n + k

    # =====================================================
    # Drawing
    # =====================================================

    def _redraw(self):
        r = self.result
        if r is None:
            self.stack.setCurrentWidget(self.line_plot)
            self.line_plot.clear()
            self.readout.setText("")
            self.marker_label.setText("")
            return

        fmt = self.format_combo.currentText()
        f = r.frequencies
        m = self.marker_slider.value()
        fm = f[m]
        self.marker_label.setText(f"{fm / 1e9:.4f} GHz")

        traces = self._checked(reflection_only=fmt in REFLECTION_ONLY)
        note = ""
        if fmt in REFLECTION_ONLY and len(traces) < len(self._checked()):
            note = f"  ({fmt}: reflection traces S11, S22, ... only)"

        if fmt == "Smith":
            self.stack.setCurrentWidget(self.smith)
            self.smith.set_data(
                [(f"S{j + 1}{k + 1}", r.s[j, k], self._color(j, k)) for j, k in traces], marker=m)
        else:
            self.stack.setCurrentWidget(self.line_plot)
            series, colors, y_label, y_range = [], [], "", None

            for j, k in traces:
                s = r.s[j, k]
                name = f"S{j + 1}{k + 1}"
                if fmt == "LogMag":
                    series.append((name, vna.logmag(s)))
                elif fmt == "LinMag":
                    series.append((name, np.abs(s)))
                elif fmt == "Phase":
                    series.append((name, vna.phase_deg(s)))
                elif fmt == "Group delay":
                    series.append((name, vna.group_delay(s, f) * 1e9))
                elif fmt == "SWR":
                    series.append((name, np.minimum(vna.swr(s), 1e3)))
                elif fmt == "Z in":
                    z = vna.gamma_to_z(s, r.resistances[j])
                    series += [(f"Re Z{j + 1}", z.real), (f"Im Z{j + 1}", z.imag)]
                    colors += [self._color(j, k), self._color(j, k) + 4]
                    continue
                colors.append(self._color(j, k))

            if fmt == "LogMag":
                y_label = "|S|, dB"
                low = min((np.nanmin(y) for _, y in series), default=-40.0)
                y_range = (max(-80.0, low - 3), 3.0)
            elif fmt == "LinMag":
                y_label, y_range = "|S|", (0.0, 1.05)
            elif fmt == "Phase":
                y_label, y_range = "phase, °", (-185.0, 185.0)
            elif fmt == "Group delay":
                y_label = "group delay, ns"
            elif fmt == "SWR":
                top = max((np.nanmax(y) for _, y in series), default=2.0)
                y_label, y_range = "SWR", (1.0, min(max(1.2 * top, 1.5), 10.0))
            elif fmt == "Z in":
                y_label = "Z, Ω"

            self.line_plot.set_data(f / 1e9, series, "f, GHz", y_label, y_range,
                                    marker_x=fm / 1e9, colors=colors)

        lines = [vna.marker_readout(fmt, r.s[j, k][m], fm, r.resistances[j], j, k)
                 for j, k in self._checked()]
        if fmt == "Group delay":
            lines = [line + f"  τg {vna.group_delay(r.s[j, k], f)[m] * 1e9:.4f} ns"
                     for line, (j, k) in zip(lines, self._checked())]
        self.readout.setText("\n".join(lines) + note)

    # =====================================================
    # Export
    # =====================================================

    def _save_touchstone(self):
        r = self.result
        if r is None:
            return
        if len(set(r.resistances)) > 1:
            QMessageBox.warning(self, "Touchstone",
                                "Ports have different R; Touchstone v1 needs one reference. "
                                f"Writing with R = {r.resistances[0]:g} Ω.")
        n = len(r.ports)
        start = os.path.join(self.default_dir, f"result.s{n}p")
        path, _ = QFileDialog.getSaveFileName(self, "Save Touchstone", start, f"Touchstone (*.s{n}p)")
        if path:
            vna.write_touchstone(path, r.frequencies, r.s, r.ports, r.resistances[0], r.comment)
