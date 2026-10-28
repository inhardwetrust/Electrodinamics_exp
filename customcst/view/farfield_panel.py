# farfield_panel.py
#
# The "Far field" tab: the radiation pattern of the shown runs (ticked in
# Project > Results) that carry a far field (Simulation > Monitors > Far
# field), as a polar cut (solver/farfield.py cut()):
#   - frequency: one of the primary run's far-field frequencies; an older
#     shown run is drawn (dimmed) when it has the same one;
#   - quantity: directivity, gain or realized gain, dBi;
#   - polarization: total, E_theta, E_phi, Ludwig-3 x / y (co / cross for
#     a patch with its E along x);
#   - cut: phi = const (a vertical plane through z; 0 = xz, 90 = yz) or
#     theta = const (a cone around z; 90 = the xy plane), at an angle;
#   - range: the dB span of the plot below its top (the maximum, rounded up).
# Under the plot: the cut's maximum, its -3 dB beamwidth and front-to-back
# (theta 0 against 180), and D, G, realized G and efficiency at the
# frequency (farfield.figures()).

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QSpinBox, QVBoxLayout, QWidget,
)

from solver.farfield import FLOOR_DBI, beamwidth, cut, figures
from view.polar_plot import PolarPlot

QUANTITY_LABELS = {"directivity": "Directivity", "gain": "Gain", "realized": "Realized gain"}
COMPONENT_LABELS = {"total": "Total", "theta": "E θ", "phi": "E φ",
                    "ludwig3_x": "Ludwig-3 X", "ludwig3_y": "Ludwig-3 Y"}
PLANES = {"phi": "φ = const (vertical plane)", "theta": "θ = const (cone around z)"}
SPANS = (20, 30, 40, 50, 60)
SAME_FREQUENCY = 1e6        # Hz: an older run's frequency counts as the same within this


class FarFieldPanel(QWidget):
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

        for widget in (self.frequency, self.port, self.quantity, self.component, self.span):
            widget.currentIndexChanged.connect(self._redraw)
        self.plane.currentIndexChanged.connect(self._plane_changed)
        self.angle.valueChanged.connect(self._redraw)

        controls = QHBoxLayout()
        for label, widget in (("Frequency", self.frequency), ("Port", self.port), ("", self.quantity),
                              ("", self.component), ("Cut", self.plane), ("at", self.angle),
                              ("Range", self.span)):
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

        layout = QVBoxLayout(self)
        layout.addLayout(controls)
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
            for f in ff["frequencies"]:
                self.frequency.addItem(f"{f / 1e9:g} GHz", float(f))
            if old_f is not None:
                near = [abs(float(f) - old_f) for f in ff["frequencies"]]
                if min(near) < SAME_FREQUENCY:
                    self.frequency.setCurrentIndex(near.index(min(near)))
        for combo in (self.frequency, self.port):
            combo.blockSignals(False)
        several = self.port.count() > 1
        self.port.setVisible(several)
        self.port_label.setVisible(several)

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
        self.info.setText("<br>".join(lines))
