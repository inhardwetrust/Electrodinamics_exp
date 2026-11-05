# farfield_sweep_panel.py
#
# The "Far field vs f" tab: the shown runs' far-field figures against
# frequency (solver/farfield.py sweep()), at every far-field frequency of a
# run: the listed ones and a sweep's ([farfield] sweep_points, evenly over
# the band).
#   - Show: "gain" (one dB axis: D, G, realized G in dBi, the radiation
#     efficiency and the mismatch in dB: realized = G + mismatch, G ~ D +
#     efficiency, so what eats the gain reads off the plot) or "beam"
#     (degrees: theta of the maximum (its phi, undefined for a beam along
#     z or a pattern round in phi, is in the readout), the -3 dB
#     beamwidths in the cuts phi = 0 and 90).
#   - Direction: the beam's maximum at each frequency, or a fixed (theta,
#     phi) (the nearest point of the 1 x 2 degree grid), e.g. the normal
#     theta 0 a link looks along.
#   - Polarization: as in the Far field tab (total, E theta, E phi,
#     Ludwig-3 x / y).
# The far-field values are points joined by lines (known only there).
# Two curves come from S at every frequency of the run: the mismatch
# 10 log10(1 - |S_kk|^2) (solid) and "realized G from S11" (dashed): G
# interpolated between the far-field points plus that mismatch, an
# estimate (G changes slowly over a band, the match does not).
# The primary (newest shown) run in full color, older shown runs dimmed.
# The band's outer tenths (EDGE): the pulse is 17-20 dB down there, and a
# run stopped at -40 or -50 dB leaves the far field off by up to ~10 %
# (a lossless dipole's efficiency 0.90 / 1.05 at -40, 1.08 / 0.97 at -50,
# within 2 % at -60): the readout warns at such a marker.
# A marker: the wheel over the plot steps it from one far-field frequency
# to the next, a click puts it at the nearest; with Ctrl the wheel steps
# through the run's S frequencies (401 over the band) and a click puts it
# where clicked: between two far-field points the readout is interpolated
# (farfield.sweep_at: D, G, the efficiency, the beamwidths linear; the
# mismatch from S11 there, exact; realized = G + mismatch). The readout
# under the plot lists the figures at the marker. frequency_changed tells
# the Far field tab (its pattern: the nearest far-field frequency),
# set_frequency moves the marker from there (to a far-field frequency).

import math

import numpy as np
from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QHBoxLayout, QLabel, QSpinBox, QVBoxLayout, QWidget,
)

from solver.farfield import SAME_HZ, mismatch_db, realized_from_s, sweep, sweep_at
from view.farfield_panel import COMPONENT_LABELS
from view.plot import LinePlot

VIEWS = {"gain": "Gain budget, dB", "beam": "Beam, degrees"}
EDGE = 0.9              # of the half band from its center: the pulse below ~-17 dB
EDGE_ACCURACY = -60.0   # dB: the stop level the far field needs there
DIRECTIONS = {"max": "beam maximum", "fixed": "fixed θ, φ"}
# Curves of the gain view: key -> (label, color index, default on)
GAIN_CURVES = {
    "directivity": ("D", 1, True),
    "gain": ("G", 0, True),
    "realized": ("Realized G", 2, True),
    "realized_s": ("Realized G from S11", 2, True),
    "efficiency": ("Efficiency", 3, True),
    "mismatch": ("Mismatch", 4, True),
}
# Curves of the beam view: key -> (label, color index)
BEAM_CURVES = {
    "theta": ("θ of the maximum", 0),
    "beamwidth_phi0": ("−3 dB width, cut φ 0", 2),
    "beamwidth_phi90": ("−3 dB width, cut φ 90", 3),
}


def _db(values):
    values = np.asarray(values, dtype=float)
    return np.where(values > 0, 10.0 * np.log10(np.maximum(values, 1e-300)), np.nan)


def swr_of_mismatch(mismatch):
    """SWR from 10 log10(1 - |Γ|^2) in dB."""
    gamma = math.sqrt(max(0.0, 1.0 - 10.0 ** (mismatch / 10.0)))
    return float("inf") if gamma >= 1.0 else (1.0 + gamma) / (1.0 - gamma)


def weak_edge(run, frequency):
    """
    The run's stop level (dB) when `frequency` (Hz) lies in the band's outer
    tenths and the run stopped above EDGE_ACCURACY, else None.
    """
    sim = getattr(run, "params", {}).get("simulation", {})
    try:
        f_min, f_max, accuracy = float(sim["f_min"]), float(sim["f_max"]), float(sim["accuracy"])
    except (KeyError, TypeError, ValueError):
        return None
    center, half = 0.5 * (f_min + f_max), 0.5 * (f_max - f_min)
    if accuracy > EDGE_ACCURACY + 1e-9 and abs(frequency / 1e9 - center) > EDGE * half:
        return accuracy
    return None


def band_within(f, values, level=3.0):
    """(f_lo, f_hi) around the maximum of `values` where they stay within `level` dB of it, or None."""
    good = np.isfinite(values)
    if good.sum() < 2:
        return None
    f, values = np.asarray(f)[good], np.asarray(values)[good]
    peak = int(np.argmax(values))
    floor = values[peak] - level

    def edge(step):
        i = peak
        while 0 <= i + step < len(values) and values[i + step] >= floor:
            i += step
        j = i + step
        if not 0 <= j < len(values):
            return f[i], False                  # still within at the end of the range
        return f[i] + (f[j] - f[i]) * (values[i] - floor) / (values[i] - values[j]), True

    (lo, lo_in), (hi, hi_in) = edge(-1), edge(1)
    return lo, hi, lo_in and hi_in


class FarFieldSweepPanel(QWidget):
    frequency_changed = Signal(float)   # Hz: the marker moved here (the user's choice)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.runs = []                  # shown runs with a far field, oldest first
        self.marker = None              # Hz: one of the primary run's far-field frequencies
        self._cache = {}                # (run id, port, direction, component) -> sweep()

        self.port = QComboBox()
        self.view = QComboBox()
        for key, label in VIEWS.items():
            self.view.addItem(label, key)
        self.direction = QComboBox()
        for key, label in DIRECTIONS.items():
            self.direction.addItem(label, key)
        self.direction.setToolTip("Where D, G and realized G are read at each frequency: the beam's "
                                  "maximum (it may move with frequency), or one fixed direction")
        self.theta = QSpinBox()
        self.theta.setRange(0, 180)
        self.theta.setSuffix("°")
        self.theta.setToolTip("θ from +z (0: the normal of a patch in the xy plane)")
        self.phi = QSpinBox()
        self.phi.setRange(0, 358)
        self.phi.setSingleStep(2)
        self.phi.setSuffix("°")
        self.phi.setToolTip("φ from +x toward +y (the pattern is stored every 2°)")
        self.component = QComboBox()
        for key, label in COMPONENT_LABELS.items():
            self.component.addItem(label, key)
        self.component.setToolTip("The polarization of U (Ludwig-3 X / Y: co / cross for a patch with its "
                                  "E along x)")
        for widget in (self.port, self.view, self.direction, self.component):
            widget.currentIndexChanged.connect(self._redraw)
        for widget in (self.theta, self.phi):
            widget.valueChanged.connect(self._redraw)

        self.curves = {}
        for key, (label, _, on) in GAIN_CURVES.items():
            box = QCheckBox(label)
            box.setChecked(on)
            box.toggled.connect(self._redraw)
            self.curves[key] = box
        self.curves["realized_s"].setToolTip("G interpolated between the far-field frequencies, plus the "
                                             "mismatch from S11 at every frequency: an estimate")
        self.curves["efficiency"].setToolTip("Radiation efficiency (radiated / accepted power), dB")
        self.curves["mismatch"].setToolTip("10 log10(1 - |S11|²): the share of the incident power the "
                                           "antenna accepts, dB (from S, every frequency)")

        controls = QHBoxLayout()
        for label, widget in (("Port", self.port), ("Show", self.view), ("at", self.direction),
                              ("θ", self.theta), ("φ", self.phi), ("", self.component)):
            if label:
                controls.addWidget(QLabel(label))
            controls.addWidget(widget)
        controls.addStretch(1)
        self.port_label = controls.itemAt(0).widget()
        self.curve_row = QHBoxLayout()
        self.curve_row.addWidget(QLabel("Curves"))
        for box in self.curves.values():
            self.curve_row.addWidget(box)
        self.curve_row.addStretch(1)

        self.plot = LinePlot()
        self.plot.empty_text = ("no far field in the shown runs: Simulation > Monitors > Far field "
                                "(sweep points for a curve over the band), then Simulate")
        self.plot.setToolTip("Wheel: the marker to the next far-field frequency; click: to the nearest.\n"
                             "Ctrl + wheel: in fine steps (the run's S frequencies), Ctrl + click: right there; "
                             "between far-field points the figures are interpolated")
        self.plot.installEventFilter(self)
        self.info = QLabel()
        self.info.setWordWrap(True)
        self.info.setTextInteractionFlags(Qt.TextSelectableByMouse)

        layout = QVBoxLayout(self)
        layout.addLayout(controls)
        layout.addLayout(self.curve_row)
        layout.addWidget(self.plot, 1)
        layout.addWidget(self.info)
        self._refresh_choices()

    # =====================================================
    # Inputs
    # =====================================================

    @property
    def primary(self):
        return self.runs[-1] if self.runs else None

    def set_results(self, runs):
        """runs: the shown SimulationResults, oldest first; those without a far field are left out."""
        self.runs = [run for run in runs if run.farfield]
        live = {id(run) for run in self.runs}
        self._cache = {key: value for key, value in self._cache.items() if key[0] in live}
        self._refresh_choices()
        self._redraw()

    def _refresh_choices(self):
        run = self.primary
        old = self.port.currentData()
        self.port.blockSignals(True)
        self.port.clear()
        if run is not None:
            for port in run.farfield:
                self.port.addItem(port, port)
            if old in run.farfield:
                self.port.setCurrentIndex(list(run.farfield).index(old))
        self.port.blockSignals(False)
        several = self.port.count() > 1
        self.port.setVisible(several)
        self.port_label.setVisible(several)

    def frequencies(self):
        """Hz: the primary run's far-field frequencies (the marker's stops)."""
        run = self.primary
        if run is None:
            return np.zeros(0)
        return np.asarray(self._ff(run)["frequencies"], float)

    def fine_stops(self):
        """Hz: the Ctrl + wheel stops: the primary run's S frequencies within its far-field range."""
        stops = self.frequencies()
        if len(stops) < 2:
            return stops
        s = self._s_kk(self.primary)
        fine = (s[0] if s is not None else
                np.linspace(stops.min(), stops.max(), 10 * (len(stops) - 1) + 1))
        fine = fine[(fine >= stops.min() - SAME_HZ) & (fine <= stops.max() + SAME_HZ)]
        return np.unique(np.concatenate([fine, stops]))

    def set_frequency(self, frequency, snap=True):
        """
        The marker at `frequency` (Hz): at the nearest far-field frequency
        (snap), or right there within their range (interpolated); without
        frequency_changed.
        """
        stops = self.frequencies()
        if not len(stops):
            return
        if snap:
            self.marker = float(stops[int(np.argmin(np.abs(stops - frequency)))])
        else:
            self.marker = float(np.clip(frequency, stops.min(), stops.max()))
        self._redraw()

    def _move_marker(self, frequency, snap=True):
        self.set_frequency(frequency, snap)
        if self.marker is not None:
            self.frequency_changed.emit(self.marker)

    def _step(self, stops, direction):
        """The next stop beyond the marker in a direction (+1 / -1), or the last one."""
        now = self.marker if self.marker is not None else stops[0]
        ahead = stops[stops > now + SAME_HZ] if direction > 0 else stops[stops < now - SAME_HZ]
        if not len(ahead):
            return float(stops[-1] if direction > 0 else stops[0])
        return float(ahead[0] if direction > 0 else ahead[-1])

    def eventFilter(self, watched, event):
        if watched is self.plot and self.primary is not None:
            stops = self.frequencies()
            fine = bool(event.modifiers() & Qt.ControlModifier) if hasattr(event, "modifiers") else False
            if event.type() == QEvent.Wheel and len(stops):
                direction = 1 if event.angleDelta().y() > 0 else -1
                self._move_marker(self._step(self.fine_stops() if fine else stops, direction), snap=not fine)
                return True
            if event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                x = self.plot.x_at(event.position().x())
                if x is not None:
                    self._move_marker(x * 1e9, snap=not fine)
                    return True
        return super().eventFilter(watched, event)

    # =====================================================
    # Figures
    # =====================================================

    def _ff(self, run):
        port = self.port.currentData()
        return run.farfield.get(port) or next(iter(run.farfield.values()))

    def _port_of(self, run):
        port = self.port.currentData()
        return port if port in run.farfield else next(iter(run.farfield))

    def _direction(self):
        if self.direction.currentData() == "fixed":
            return float(self.theta.value()), float(self.phi.value())
        return None

    def figures(self, run):
        """sweep() of a run's port in the chosen direction and polarization (cached)."""
        direction, component = self._direction(), self.component.currentData()
        key = (id(run), self._port_of(run), direction, component)
        if key not in self._cache:
            self._cache[key] = sweep(self._ff(run), direction, component)
        return self._cache[key]

    def at_marker(self, run=None):
        """sweep_at() of a run (the primary) at the marker: interpolated between far-field points."""
        run = run or self.primary
        return sweep_at(self.figures(run), self.marker, self._s_kk(run))

    def _s_kk(self, run):
        """(Hz, S_kk) of the run's excited port, or None when the run has no column for it."""
        port = self._port_of(run)
        if port not in run.ports:
            return None
        k = run.ports.index(port)
        s = np.asarray(run.s[(k, k)])
        return (np.asarray(run.frequencies, float), s) if np.all(np.isfinite(s)) else None

    # =====================================================
    # Drawing
    # =====================================================

    def _redraw(self):
        fixed = self.direction.currentData() == "fixed"
        for widget in (self.theta, self.phi):
            widget.setEnabled(fixed)
        gain_view = self.view.currentData() == "gain"
        for box in self.curves.values():
            box.setVisible(gain_view)
        run = self.primary
        if run is None:
            self.plot.clear()
            self.info.setText("")
            return
        stops = self.frequencies()
        if (self.marker is None or not len(stops)
                or not stops.min() - SAME_HZ <= self.marker <= stops.max() + SAME_HZ):
            self.marker = float(stops[0]) if len(stops) else None

        curves = []
        for n, shown in enumerate(self.runs):
            dim = shown is not run
            suffix = f" · {shown.name}" if dim else ""
            sw = self.figures(shown)
            f = sw["frequency"] / 1e9
            if gain_view:
                s = self._s_kk(shown)
                for key, (label, color, _) in GAIN_CURVES.items():
                    if not self.curves[key].isChecked():
                        continue
                    if key == "realized_s":
                        if s is not None:
                            fs, values = realized_from_s(sw, s[0], s[1])
                            curves.append((label + suffix, fs / 1e9, values, color, dim, "dash"))
                    elif key == "mismatch":
                        if s is not None:
                            curves.append((label + suffix, s[0] / 1e9, mismatch_db(s[1]), color, dim, "line"))
                        else:
                            curves.append((label + suffix, f, sw["mismatch"], color, dim, "points"))
                    elif key == "efficiency":
                        curves.append((label + suffix, f, _db(sw["efficiency"]), color, dim, "points"))
                    else:
                        curves.append((label + suffix, f, sw[key], color, dim, "points"))
            else:
                for key, (label, color) in BEAM_CURVES.items():
                    if fixed and key == "theta":
                        continue                # the fixed direction itself
                    curves.append((label + suffix, f, sw[key], color, dim, "points"))
        self.plot.set_data(curves, "GHz", "dBi, dB" if gain_view else "degrees",
                           marker_x=None if self.marker is None else self.marker / 1e9)
        self.info.setText("<br>".join(self._readout(run)))

    def _readout(self, run):
        sw = self.figures(run)
        if self.marker is None:
            return []
        s = self._s_kk(run)
        x = self.at_marker(run)
        k, f = x["index"], x["frequency"]
        component = COMPONENT_LABELS[self.component.currentData()]
        where = (f"θ {x['theta']:g}°, φ {x['phi']:g}°" if self._direction() is not None
                 else f"the maximum (θ {x['theta']:g}°, φ {x['phi']:g}°"
                      + ("" if x["between"] is None else f" at {sw['frequency'][k] / 1e9:g} GHz") + ")")
        if x["between"] is None:
            kind = f"{'listed' if x['named'] else 'sweep'}, {k + 1} of {len(sw['frequency'])}"
        else:
            kind = (f"<span style='color:#c80'>interpolated</span> between {x['between'][0] / 1e9:g} and "
                    f"{x['between'][1] / 1e9:g} GHz")
        eff_db = float(_db(x["efficiency"]))
        mismatch = x["mismatch"]
        source = " from S11 here" if x["between"] is not None and s is not None else ""
        lines = [
            f"<b>{run.name}</b>, {f / 1e9:.5g} GHz ({kind}), {component} at "
            f"{where}: D {x['directivity']:.2f} dBi, G {x['gain']:.2f} dBi, realized "
            f"{x['realized']:.2f} dBi",
            f"realized = G + mismatch {mismatch:.2f} dB{source} (SWR {swr_of_mismatch(mismatch):.2f}); "
            f"G − D = {x['gain'] - x['directivity']:.2f} dB, radiation efficiency "
            f"{100 * x['efficiency']:.1f} % ({eff_db:.2f} dB); −3 dB width: cut φ 0 "
            + (f"{x['beamwidth_phi0']:.1f}°" if np.isfinite(x["beamwidth_phi0"]) else "—")
            + ", φ 90 " + (f"{x['beamwidth_phi90']:.1f}°" if np.isfinite(x["beamwidth_phi90"]) else "—"),
        ]
        accuracy = weak_edge(run, f)
        if accuracy is not None:
            lines.append(f"<span style='color:#c80'>⚠ near the band's edge the pulse is 17-20 dB down: the "
                         f"far field here is good to ~10 % at the run's {accuracy:g} dB stop level "
                         f"(accuracy {EDGE_ACCURACY:g} for it)</span>")
        if len(sw["frequency"]) >= 2:
            fs, values = realized_from_s(sw, s[0], s[1]) if s is not None else (sw["frequency"], sw["realized"])
            band = band_within(fs, values)
            if band is not None:
                lo, hi, closed = band
                peak = int(np.nanargmax(values))
                lines.append(f"realized gain at most {values[peak]:.2f} dBi ({fs[peak] / 1e9:.4g} GHz); within "
                             f"3 dB of it: {lo / 1e9:.4g}–{hi / 1e9:.4g} GHz"
                             + ("" if closed else " (reaches the band's edge)")
                             + (" (from S11 between the far-field points)" if s is not None else ""))
        else:
            lines.append("one far-field frequency: set Far field > sweep points for a curve over the band")
        return lines
