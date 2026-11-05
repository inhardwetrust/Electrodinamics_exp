# farfield_panel.py
#
# The "Far field" tab: the radiation pattern of the shown runs (ticked in
# Project > Results) that carry a far field (Simulation > Monitors > Far
# field), as a polar cut (solver/farfield.py cut()):
#   - frequency: one of the primary run's far-field frequencies (the listed
#     ones, and a sweep's marked "sweep"), from the list or the slider under
#     it (the wheel steps through them); an older shown run is drawn
#     (dimmed) when it has the same one. frequency_changed tells the Far
#     field vs f tab, whose marker moves it back (set_frequency);
#   - quantity: directivity, gain or realized gain, dBi;
#   - polarization: total, E_theta, E_phi, Ludwig-3 x / y (co / cross for
#     a patch with its E along x);
#   - cut: phi = const (a vertical plane through z; 0 = xz, 90 = yz) or
#     theta = const (a cone around z; 90 = the xy plane), at an angle;
#   - range: the dB span of the plot below its top (the maximum, rounded up).
# Under the plot: the cut's maximum, its -3 dB beamwidth and front-to-back
# (theta 0 against 180), and D, G, realized G and efficiency at the
# frequency (farfield.figures()).
#
# Show in 3D: the primary run's pattern with the same frequency, quantity,
# polarization and range as a colored surface in the 3D view
# (farfield.surface(): radius in dB from max - range to max; colors blue ->
# red over the same range), emitted as pattern_changed.
# Cut in 3D: the cut's plane (a see-through disk; a cone for theta = const)
# and its curve in the same scale as the surface (it lies on it), emitted
# as cut_changed, with the 3D surface or without it.

import numpy as np
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QHBoxLayout, QLabel, QSlider, QSpinBox, QVBoxLayout, QWidget,
)

from solver.farfield import FLOOR_DBI, beamwidth, cut, cut_curve, cut_sheet, figures, named_mask, surface
from view.polar_plot import PolarPlot

QUANTITY_LABELS = {"directivity": "Directivity", "gain": "Gain", "realized": "Realized gain"}
COMPONENT_LABELS = {"total": "Total", "theta": "E θ", "phi": "E φ",
                    "ludwig3_x": "Ludwig-3 X", "ludwig3_y": "Ludwig-3 Y"}
PLANES = {"phi": "φ = const (vertical plane)", "theta": "θ = const (cone around z)"}
SPANS = (20, 30, 40, 50, 60)
SAME_FREQUENCY = 1e6        # Hz: an older run's frequency counts as the same within this
# The 3D pattern's colors from the bottom of the range to the maximum (like CST's).
COLOR_STOPS = np.array([[0.0, 0.05, 0.15, 0.65], [0.25, 0.0, 0.55, 1.0], [0.5, 0.0, 0.85, 0.35],
                        [0.75, 1.0, 0.85, 0.0], [1.0, 0.9, 0.1, 0.05]])
PATTERN_ALPHA = 0.8


def pattern_colors(fraction):
    """RGBA (N, 4) for values 0..1 of the range (blue -> cyan -> green -> yellow -> red)."""
    t = np.clip(np.asarray(fraction, dtype=float), 0.0, 1.0)
    rgb = np.stack([np.interp(t, COLOR_STOPS[:, 0], COLOR_STOPS[:, c]) for c in (1, 2, 3)], axis=-1)
    return np.concatenate([rgb, np.full((len(t), 1), PATTERN_ALPHA)], axis=1)


class FarFieldPanel(QWidget):
    # (points (N, 3) on the unit scale, faces, RGBA colors) or None
    pattern_changed = Signal(object)
    cut_changed = Signal(object)        # (curve (N, 3), sheet vertices, sheet faces) at radius 0..1, or None
    frequency_changed = Signal(float)   # Hz: the frequency shown (the user's choice)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.runs = []                  # shown runs with a far field, oldest first

        self.frequency = QComboBox()
        self.port = QComboBox()
        self.quantity = QComboBox()
        for key, label in QUANTITY_LABELS.items():
            self.quantity.addItem(label, key)
        self.component = QComboBox()
        for key, label in COMPONENT_LABELS.items():
            self.component.addItem(label, key)
        self.component.setToolTip("Ludwig-3 X / Y: the x / y polarization around +z "
                                  "(co / cross for a patch with its E along x)")
        self.plane = QComboBox()
        for key, label in PLANES.items():
            self.plane.addItem(label, key)
        self.angle = QSpinBox()
        self.angle.setRange(0, 359)
        self.angle.setSuffix("°")
        self.angle.setToolTip("φ of the vertical plane (0 = xz, 90 = yz), or θ of the cone (90 = xy)")
        self.span = QComboBox()
        for value in SPANS:
            self.span.addItem(f"{value} dB", value)
        self.span.setCurrentIndex(SPANS.index(40))
        self.show_3d = QCheckBox("Show in 3D")
        self.show_3d.setToolTip("The pattern as a colored surface around the model in the 3D View tab "
                                "(this frequency, quantity, polarization and range)")
        self.show_3d.toggled.connect(self._redraw)
        self.cut_3d = QCheckBox("Cut in 3D")
        self.cut_3d.setToolTip("The cut's plane and its curve around the model in the 3D View tab "
                               "(the curve in the scale of the 3D pattern: it lies on it)")
        self.cut_3d.toggled.connect(self._redraw)

        self.slider = QSlider(Qt.Horizontal)
        self.slider.setToolTip("The far-field frequencies of the run (the wheel steps through them)")
        self.slider.setTickPosition(QSlider.TicksBelow)
        self.slider.valueChanged.connect(self.frequency.setCurrentIndex)
        self.frequency.currentIndexChanged.connect(self._frequency_picked)
        for widget in (self.port, self.quantity, self.component, self.span):
            widget.currentIndexChanged.connect(self._redraw)
        self.plane.currentIndexChanged.connect(self._plane_changed)
        self.angle.valueChanged.connect(self._redraw)

        controls = QHBoxLayout()
        for label, widget in (("Frequency", self.frequency), ("Port", self.port), ("", self.quantity),
                              ("", self.component), ("Cut", self.plane), ("at", self.angle),
                              ("Range", self.span), ("", self.show_3d), ("", self.cut_3d)):
            if label:
                controls.addWidget(QLabel(label))
            controls.addWidget(widget)
        controls.addStretch(1)
        self.port_label = controls.itemAt(2).widget()

        self.plot = PolarPlot()
        self.plot.empty_text = ("no far field in the shown runs: set Simulation > Monitors > Far field "
                                "(frequencies), then Simulate")
        self.info = QLabel()
        self.info.setWordWrap(True)
        self.info.setTextInteractionFlags(Qt.TextSelectableByMouse)

        self.slider_label = QLabel()
        sweep_row = QHBoxLayout()
        sweep_row.addWidget(self.slider, 1)
        sweep_row.addWidget(self.slider_label)

        layout = QVBoxLayout(self)
        layout.addLayout(controls)
        layout.addLayout(sweep_row)
        layout.addWidget(self.plot, 1)
        layout.addWidget(self.info)
        self._refresh_choices()

    @property
    def primary(self):
        return self.runs[-1] if self.runs else None

    def set_results(self, runs):
        """runs: the shown SimulationResults, oldest first; those without a far field are left out."""
        self.runs = [run for run in runs if run.farfield]
        self._refresh_choices()
        self._redraw()

    def _refresh_choices(self):
        """Frequencies and ports of the primary run (the current choice kept when it is still there)."""
        run = self.primary
        old_f, old_port = self.frequency.currentData(), self.port.currentData()
        for combo in (self.frequency, self.port):
            combo.blockSignals(True)
            combo.clear()
        if run is not None:
            for port in run.farfield:
                self.port.addItem(port, port)
            if old_port in run.farfield:
                self.port.setCurrentIndex(list(run.farfield).index(old_port))
            ff = run.farfield[self.port.currentData()]
            for f, named in zip(ff["frequencies"], named_mask(ff)):
                self.frequency.addItem(f"{f / 1e9:g} GHz" + ("" if named else " · sweep"), float(f))
            if old_f is not None:
                near = [abs(float(f) - old_f) for f in ff["frequencies"]]
                if min(near) < SAME_FREQUENCY:
                    self.frequency.setCurrentIndex(near.index(min(near)))
        for combo in (self.frequency, self.port):
            combo.blockSignals(False)
        self._sync_slider()
        several = self.port.count() > 1
        self.port.setVisible(several)
        self.port_label.setVisible(several)

    def _sync_slider(self):
        """The slider over the frequency list, at its choice (shown when there are several)."""
        self.slider.blockSignals(True)
        self.slider.setRange(0, max(self.frequency.count() - 1, 0))
        self.slider.setValue(max(self.frequency.currentIndex(), 0))
        self.slider.blockSignals(False)
        self.slider.setVisible(self.frequency.count() > 1)
        self.slider_label.setVisible(self.frequency.count() > 1)
        self.slider_label.setText(f"{self.frequency.currentIndex() + 1} / {self.frequency.count()}")

    def _frequency_picked(self):
        self._sync_slider()
        self._redraw()
        if self.frequency.currentData() is not None:
            self.frequency_changed.emit(float(self.frequency.currentData()))

    def set_frequency(self, frequency):
        """Show the run's far-field frequency nearest `frequency` (Hz), without frequency_changed."""
        values = [self.frequency.itemData(i) for i in range(self.frequency.count())]
        if not values:
            return
        near = [abs(v - frequency) for v in values]
        index = near.index(min(near))
        if index != self.frequency.currentIndex():
            self.frequency.blockSignals(True)
            self.frequency.setCurrentIndex(index)
            self.frequency.blockSignals(False)
            self._sync_slider()
            self._redraw()

    def _plane_changed(self):
        self.angle.blockSignals(True)
        self.angle.setValue(0 if self.plane.currentData() == "phi" else 90)
        self.angle.setRange(0, 359 if self.plane.currentData() == "phi" else 180)
        self.angle.blockSignals(False)
        self._redraw()

    def _arrays(self, run, frequency):
        """(far-field arrays, frequency index) of a run's port at `frequency`, or None."""
        port = self.port.currentData()
        ff = run.farfield.get(port) or next(iter(run.farfield.values()))
        near = [abs(float(f) - frequency) for f in ff["frequencies"]]
        k = near.index(min(near))
        return (ff, k) if near[k] < SAME_FREQUENCY else None

    def _redraw(self):
        run, frequency = self.primary, self.frequency.currentData()
        if run is None or frequency is None:
            self.plot.clear()
            self.info.setText("")
            self.pattern_changed.emit(None)
            self.cut_changed.emit(None)
            return
        plane, at = self.plane.currentData(), float(self.angle.value())
        quantity, component = self.quantity.currentData(), self.component.currentData()

        curves, skipped = [], []
        for n, shown in enumerate(self.runs):
            arrays = self._arrays(shown, frequency)
            if arrays is None:
                skipped.append(shown.name)
                continue
            angles, values = cut(*arrays, plane, at, quantity, component)
            curves.append((f"{shown.name}: {COMPONENT_LABELS[component]}, {frequency / 1e9:g} GHz",
                           angles, values, len(self.runs) - 1 - n, shown is not run))

        ff, k = self._arrays(run, frequency)
        angles, values = cut(ff, k, plane, at, quantity, component)
        peak_angle, width = beamwidth(angles, values)
        top = 5.0 * (int(max(c[2].max() for c in curves) // 5.0) + 1)
        if plane == "phi":
            sides = (f"φ = {at:g}°", f"φ = {(at + 180) % 360:g}°")
            where = f"θ {peak_angle if peak_angle <= 180 else 360 - peak_angle:g}° " \
                    f"at φ {at if peak_angle <= 180 else (at + 180) % 360:g}°"
        else:
            sides = (f"θ = {at:g}°", "")
            where = f"φ {peak_angle:g}°"
        self.plot.set_data(curves, top, self.span.currentData(), "dBi", sides, peak_angle)

        head = f"<b>{run.name}</b>, {frequency / 1e9:g} GHz, {QUANTITY_LABELS[quantity].lower()}, " \
               f"{COMPONENT_LABELS[component]}: "
        if values.max() <= FLOOR_DBI + 1.0:
            lines = [head + "no field of this polarization in the cut (a symmetry plane of the model?)"]
        else:
            lines = [head + f"cut maximum {values.max():.2f} dBi at {where}, -3 dB beamwidth "
                     + (f"{width:.1f}°" if width is not None else "— (round)")]
        if plane == "phi" and component == "total":
            front = values[int(abs(angles - 0.0).argmin())]
            back = values[int(abs(angles - 180.0).argmin())]
            lines[0] += f", front / back (θ 0 / 180) {front - back:.1f} dB"
        x = figures(ff)[k]
        lines.append(f"D {x['directivity']:.2f} dBi, G {x['gain']:.2f} dBi, realized {x['realized']:.2f} dBi, "
                     f"radiation efficiency {100 * x['efficiency']:.1f} %, maximum at θ {x['theta']:g}°, "
                     f"φ {x['phi']:g}°")
        if int(ff["cut_edges"]):
            lines.append("<span style='color:#c80'>⚠ a body crossed the far-field box: "
                         "more padding for a reliable pattern</span>")
        if skipped:
            lines.append(f"not at {frequency / 1e9:g} GHz: {', '.join(skipped)}")
        self._emit_pattern(ff, k, quantity, component, lines)
        self._emit_cut(ff, k, plane, at, angles, values, quantity, component)
        self.info.setText("<br>".join(lines))

    def _emit_cut(self, ff, k, plane, at, angles, values, quantity, component):
        if not self.cut_3d.isChecked():
            self.cut_changed.emit(None)
            return
        span = float(self.span.currentData())
        top = float(surface(ff, k, quantity, component, span)[2].max())    # the 3D pattern's maximum
        vertices, faces = cut_sheet(plane, at)
        self.cut_changed.emit((cut_curve(plane, at, angles, values, top, span), vertices, faces))

    def _emit_pattern(self, ff, k, quantity, component, lines):
        if not self.show_3d.isChecked():
            self.pattern_changed.emit(None)
            return
        span = float(self.span.currentData())
        points, faces, db = surface(ff, k, quantity, component, span)
        top = float(db.max())
        self.pattern_changed.emit((points, faces, pattern_colors((db - (top - span)) / span)))
        lines.append(f"3D View: blue {top - span:.1f} → red {top:.1f} dBi, the radius in dB over the same "
                     f"range")
