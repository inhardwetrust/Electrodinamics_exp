# results_panel.py
#
# The Results tab, like a VNA screen: finished simulations (S-matrices over
# frequency) shown in a chosen format (solver/vna.py FORMATS) for the
# checked traces S_jk, with a frequency marker and its readout, and
# Touchstone export. Nothing here re-runs the solver.
#
# Phase: in (-180, 180] by default, so it jumps by 360 where it crosses
# 180; "Unwrap" draws it continuous along frequency (from the band's first
# point) and adds the continuous value to the readout.
#
# Several runs can be shown at once (ticked in Project > Results): the
# newest of them is the primary one (full color; marker, readout and
# export follow it), older ones are dimmed for comparison. Runs may have
# different bands and port sets: each curve keeps its own frequencies.
#
# The mouse wheel over the plot moves the marker (one point per notch,
# ten with Ctrl).
#
# Reference planes (like CST's "distance to reference plane"): each
# waveguide port's plane can be moved along its own line, + into the
# structure (toward the DUT), - away from it. The port's reference
# impedance is that line's Z0, so the move is exact for a lossless line:
#   S'_jk = S_jk exp(j beta_j d_j) exp(j beta_k d_k),  beta = 2 pi f sqrt(eps_eff) / c0
# (the reflection of a port turns by 2 beta d, |S| does not change).
# Applies to every shown run (by port name), the readout and the export.
#
# Normalize to (like a VNA's port impedance or CST's "normalize to fixed
# impedance"): every port's reference becomes one R (50 Ohm by default)
# instead of its own line's Z0 (solver/vna.py renormalize), after the
# reference planes are moved. Compares with a 50 Ohm VNA when the model's
# coax is not exactly 50 Ohm. A multi-port run needs its full S matrix
# (all ports excited); otherwise it stays at the ports' own Z0.

import os

import numpy as np
from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QHBoxLayout, QLabel, QMessageBox,
    QFrame, QPushButton, QScrollArea, QSlider, QSplitter, QStackedWidget, QVBoxLayout, QWidget,
)

from project.settings import C0
from solver import vna
from view.plot import LinePlot
from view.smith import SmithChart

REFLECTION_ONLY = ("SWR", "Smith", "Z in")
TEXT_HEIGHT = 170           # px: the readout / status / info area under the plot (scrolls)


class ResultsPanel(QWidget):
    shifts_changed = Signal()           # a reference plane moved (self.shifts)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.runs = []                  # shown runs, oldest first
        self.default_dir = ""
        self._trace_boxes = {}          # (j, k) -> QCheckBox
        self._ports = 0                 # trace boxes are for this many ports
        self.shifts = {}                # port name -> reference plane shift (mm, + = into the model)
        self._shift_boxes = {}          # port name -> QDoubleSpinBox
        self._cache = {}                # id(run) -> (key, s dict, references, note)

        self.format_combo = QComboBox()
        self.format_combo.addItems(vna.FORMATS)
        self.format_combo.currentIndexChanged.connect(lambda _: self._redraw())
        self.unwrap_check = QCheckBox("Unwrap")
        self.unwrap_check.setToolTip("Phase continuous along frequency, without the jumps at ±180°")
        self.unwrap_check.toggled.connect(lambda _: self._redraw())
        self.unwrap_check.setVisible(False)

        self.trace_row = QHBoxLayout()
        self.trace_row.setSpacing(10)

        self.save_button = QPushButton("Save .sNp…")
        self.save_button.setToolTip("Touchstone file of the newest shown run")
        self.save_button.clicked.connect(self._save_touchstone)
        self.save_button.setEnabled(False)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("Format:"))
        controls.addWidget(self.format_combo)
        controls.addWidget(self.unwrap_check)
        controls.addSpacing(12)
        controls.addWidget(QLabel("Traces:"))
        controls.addLayout(self.trace_row)
        controls.addStretch(1)
        self.normalize_check = QCheckBox("Normalize to")
        self.normalize_check.setToolTip("Refer S to one impedance for all ports (like a VNA), "
                                        "instead of each port's own line Z0")
        self.normalize_check.toggled.connect(lambda _: self._redraw())
        self.normalize_box = QDoubleSpinBox()
        self.normalize_box.setRange(0.1, 10000.0)
        self.normalize_box.setDecimals(1)
        self.normalize_box.setValue(50.0)
        self.normalize_box.setSuffix(" Ω")
        self.normalize_box.valueChanged.connect(lambda _: self._redraw())
        controls.addWidget(self.normalize_check)
        controls.addWidget(self.normalize_box)
        controls.addSpacing(12)
        controls.addWidget(self.save_button)

        self.line_plot = LinePlot()
        self.smith = SmithChart()
        for widget in (self.line_plot, self.smith):
            widget.installEventFilter(self)             # wheel -> marker
            widget.setToolTip("Mouse wheel: move the marker (Ctrl: 10 points)")
        self.stack = QStackedWidget()
        self.stack.addWidget(self.line_plot)
        self.stack.addWidget(self.smith)

        self.shift_row = QHBoxLayout()
        self.shift_row.setSpacing(6)

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

        # The plot keeps its height: the text under it scrolls in its own
        # area (a splitter: the extra height of the window goes to the plot,
        # the border can be dragged).
        plot = QWidget()
        plot_layout = QVBoxLayout(plot)
        plot_layout.setContentsMargins(0, 0, 0, 0)
        plot_layout.addWidget(self.stack, 1)
        plot_layout.addLayout(marker_row)

        text = QWidget()
        text_layout = QVBoxLayout(text)
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.addWidget(self.readout)
        text_layout.addWidget(self.status)
        text_layout.addWidget(self.info)
        text_layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidget(text)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)

        self.splitter = QSplitter(Qt.Vertical)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.addWidget(plot)
        self.splitter.addWidget(scroll)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 0)
        self.splitter.setSizes([10000, TEXT_HEIGHT])

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addLayout(controls)
        layout.addLayout(self.shift_row)
        layout.addWidget(self.splitter, 1)
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
        if self.runs and not any(jk in self.primary.s for jk in self._checked()):
            self._default_traces()
        self._rebuild_shift_boxes()

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

    def _rebuild_shift_boxes(self):
        """One spin box per waveguide port of the primary run (lumped ports have no line)."""
        r = self.primary
        names = [n for n, e in zip(r.ports, r.eps_eff) if np.isfinite(e)] if r else []
        if names == list(self._shift_boxes):
            return
        while self.shift_row.count():
            widget = self.shift_row.takeAt(0).widget()
            if widget is not None:
                # Off the panel now: deleteLater alone left the old label and
                # box drawn under the new ones until the deferred delete ran.
                widget.setParent(None)
                widget.deleteLater()
        self._shift_boxes = {}
        if not names:
            return
        label = QLabel("Reference plane:")
        label.setToolTip("Move a waveguide port's reference plane along its own line "
                         "(+ into the model, toward the DUT; - away from it). Changes the "
                         "phase only: |S| and SWR stay. Like CST's distance to reference plane.")
        self.shift_row.addWidget(label)
        for name in names:
            k = r.ports.index(name)
            box = QDoubleSpinBox()
            box.setRange(-1000.0, 1000.0)
            box.setDecimals(2)
            box.setSingleStep(0.5)
            box.setSuffix(" mm")
            box.setValue(self.shifts.get(name, 0.0))
            box.setToolTip(f"port {k + 1} ({name}), line eps_eff {r.eps_eff[k]:.3f}: "
                           "+ moves the plane into the model")
            box.valueChanged.connect(lambda value, n=name: self._set_shift(n, value))
            self.shift_row.addWidget(QLabel(f"{k + 1} {name}"))
            self.shift_row.addWidget(box)
            self._shift_boxes[name] = box
        self.shift_row.addStretch(1)

    def _set_shift(self, name, value):
        self.shifts[name] = float(value)
        self._redraw()
        self.shifts_changed.emit()

    def _moved(self, run, j, k):
        """S_jk of a run with the reference planes moved (see the header)."""
        s = run.s[j, k]
        phase = 0.0
        for p in (j, k):
            d = self.shifts.get(run.ports[p], 0.0) * 1e-3
            if d and np.isfinite(run.eps_eff[p]):
                phase = phase + 2 * np.pi * run.frequencies * np.sqrt(run.eps_eff[p]) / C0 * d
        return s * np.exp(1j * phase) if np.any(phase) else s

    def _shown(self, run):
        """(S dict, port references in Ohm, note): planes moved, then normalized (see the header)."""
        r_new = self.normalize_box.value() if self.normalize_check.isChecked() else None
        key = (tuple(sorted(self.shifts.items())), r_new)
        cached = self._cache.get(id(run))
        if cached is not None and cached[0] == key and cached[1] is run:
            return cached[2:]
        s = {jk: self._moved(run, *jk) for jk in run.s}
        references = list(run.resistances)
        notes = []
        moved = [f"{name} {self.shifts[name]:+g} mm" for name, e in zip(run.ports, run.eps_eff)
                 if self.shifts.get(name) and np.isfinite(e)]
        if moved:
            notes.append("reference planes moved: " + ", ".join(moved))
        if r_new is not None and not np.allclose(references, r_new):
            n = len(run.ports)
            if all((j, k) in s for j in range(n) for k in range(n)):
                s = vna.renormalize(s, references, r_new)
                references = [r_new] * n
                notes.append(f"normalized to {r_new:g} Ω")
            else:
                notes.append(f"not normalized to {r_new:g} Ω: needs the full S matrix (excite all ports)")
        self._cache[id(run)] = (key, run, s, references, "; ".join(notes))
        return s, references, "; ".join(notes)

    def _s(self, run, j, k):
        return self._shown(run)[0][j, k]

    def _reference(self, run, j):
        return self._shown(run)[1][j]

    def _shift_note(self, run):
        return self._shown(run)[2]

    def _default_traces(self):
        """Tick the column of the first excited port of the primary run (S11, S21 ...)."""
        columns = sorted({k for _, k in self.primary.s})
        for (j, k), box in self._trace_boxes.items():
            box.blockSignals(True)
            box.setChecked(bool(columns) and k == columns[0])
            box.blockSignals(False)

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
        self.unwrap_check.setVisible(fmt == "Phase")
        unwrap = fmt == "Phase" and self.unwrap_check.isChecked()
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
                        items.append((label(f"S{j + 1}{k + 1}", run), self._s(run, j, k), self._color(j, k), dim, m))
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
                    s = self._s(run, j, k)
                    name = label(f"S{j + 1}{k + 1}", run)
                    color = self._color(j, k)
                    if fmt == "Z in":
                        z = vna.gamma_to_z(s, self._reference(run, j))
                        curves += [(label(f"Re Z{j + 1}", run), f / 1e9, z.real, color, dim),
                                   (label(f"Im Z{j + 1}", run), f / 1e9, z.imag, color + 4, dim)]
                        continue
                    y = {"LogMag": lambda: vna.logmag(s),
                         "LinMag": lambda: np.abs(s),
                         "Phase": lambda: vna.phase_deg(s, unwrap),
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
                y_label, y_range = "phase, °", None if unwrap else (-185.0, 185.0)
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
                s = self._s(run, j, k)
                line = prefix + vna.marker_readout(fmt, s[m], run.frequencies[m],
                                                   self._reference(run, j), j, k)
                if unwrap:
                    line += f"  unwrapped {vna.phase_deg(s, True)[m]:.1f}°"
                if fmt == "Group delay":
                    line += f"  τg {vna.group_delay(s, run.frequencies)[m] * 1e9:.4f} ns"
                lines.append(line)
        moved = self._shift_note(self.primary)
        self.readout.setText("\n".join(lines) + note + (f"\n({moved})" if moved else ""))

    # =====================================================
    # Export
    # =====================================================

    def _save_touchstone(self):
        r = self.primary
        if r is None:
            return
        references = self._shown(r)[1]
        if len(set(references)) > 1:
            QMessageBox.warning(self, "Touchstone",
                                "Ports have different R; Touchstone v1 needs one reference "
                                "(tick Normalize to). "
                                f"Writing with R = {references[0]:g} Ω.")
        n = len(r.ports)
        missing = [f"S{j + 1}{k + 1}" for j in range(n) for k in range(n) if (j, k) not in r.s]
        if missing:
            QMessageBox.warning(self, "Touchstone",
                                f"{r.name} has no {', '.join(missing)}: only some ports were excited. "
                                "A .sNp file needs the full matrix: set Simulation > excite to all "
                                "ports and run again.")
            return
        safe = "".join(c if c.isalnum() or c in "-_ " else "_" for c in r.name).strip() or "result"
        start = os.path.join(self.default_dir, f"{safe}.s{n}p")
        path, _ = QFileDialog.getSaveFileName(self, "Save Touchstone", start, f"Touchstone (*.s{n}p)")
        if path:
            s = {jk: self._s(r, *jk) for jk in r.s}
            moved = self._shift_note(r)
            vna.write_touchstone(path, r.frequencies, s, r.ports, references[0],
                                 r.comment + (f"; {moved}" if moved else ""))


def _index_at(frequencies, f):
    """Index of the point nearest f, or None if f is outside the run's band."""
    if f is None or not frequencies[0] - 1e-6 * abs(frequencies[0]) <= f <= frequencies[-1] * (1 + 1e-6):
        return None
    return int(np.argmin(np.abs(frequencies - f)))
