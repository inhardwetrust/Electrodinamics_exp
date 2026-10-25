# tdr_window.py
#
# Time domain reflectometry of the shown runs (solver/tdr.py) in a tool
# window, on the Z0 profile's own axis: where along the line each run
# reflects, next to the profile's Z0 there.
#   - the runs are the ones shown in Results (ticked in Project >
#     Results), one color each; the port picks S_jj;
#   - the profile is one the main window keeps for the current mesh (X,
#     Y, Z or Path; the box lists them all, a profile not computed for
#     this mesh has a Compute button: the main window computes it in the
#     Z0 profile window and saves it with the runs). A waveguide port
#     on it ties the time to the profile's coordinate by the sections'
#     eps_eff (a coax, then a board: each at its own speed); without one
#     the axis is the round trip time;
#   - low pass: the step as an impedance (top) and the impulse response
#     (bottom, rho per reflection); band pass: the envelope |rho| only
#     (no DC needed, e.g. behind a DC block);
#   - with a profile also: its Z0 (thin) and the profile as a TDR sees it
#     (its cascade's S11 through the same transform, on the newest run's
#     band): where that and a run differ, the 3D field differs from the
#     cross-sections;
#   - the resolution (the step's 10-90 % rise) is written under the plot:
#     closer reflections merge, short pads read lower than their Z0;
#   - the mouse wheel moves the marker: over the profile's sections (and
#     the main window moves the 3D plane or the path's section there, as
#     the profile window does), else in steps of a tenth of the
#     resolution.

import numpy as np
from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QPushButton, QSplitter, QVBoxLayout, QWidget

from project.settings import C0
from solver import tdr
from view.plot import LinePlot

MM, PS = 1e-3, 1e-12
GUIDE = 50.0
COLOR_Z0, COLOR_SEEN, COLOR_GUIDE = 6, 7, 6
SOURCES = {"x": 0, "y": 1, "z": 2, "path": "path"}      # the profile box's items -> the main window's keys
EMPTY_TEXT = "no run shown: tick a run in Project > Results"


class TdrWindow(QWidget):
    section_picked = Signal(object, int)        # (profile, index of the section under the marker)
    profile_changed = Signal()                  # another profile (the path shows in 3D while it is the path's)
    profile_requested = Signal(str)             # compute a Z0 profile for this port: "path" or "x", "y", "z"
    hidden = Signal()

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Tool)
        self.setWindowTitle("TDR")
        self.resize(900, 640)
        self.runs = []                  # shown SimulationResults, oldest first
        self.profiles = {}              # {0, 1, 2 or "path": Profile} of the current mesh
        self.waveports = {}             # {port name: WavePortGeometry}
        self._marker = None             # x of the marker (plot units)
        self._unit = None               # "mm" or "ps": the axis the marker was set on
        self._xs = None                 # marker positions (plot units), the profile's sections if tied
        self._tied = None               # (profile, LineMap) when the axis is the profile's coordinate
        self._readings = []             # (name, x, value, impulse or None, unit) for the readout
        self.path_drawn = False         # the project has a Z0 path (set by the main window)

        self.port_box = QComboBox()
        self.port_box.setToolTip("Which reflection S_jj")
        self.profile_box = QComboBox()
        self.profile_box.setToolTip("The Z0 profile to tie the time to (computed in Z₀ ▸ Z₀ profile…)")
        self.mode_box = QComboBox()
        self.mode_box.addItems(["Low pass", "Band pass"])
        self.mode_box.setToolTip("Low pass: the step (impedance) and the impulse, continued to DC. "
                                 "Band pass: the envelope |rho| over the band alone (behind a DC block)")
        self.window_box = QComboBox()
        self.window_box.addItems(list(tdr.WINDOWS))
        self.window_box.setCurrentText("Normal")
        self.window_box.setToolTip("Kaiser window: Minimum is sharpest but rings, Maximum smoothest")
        self.axis_box = QComboBox()
        self.axis_box.addItems(["Position", "Time"])
        self.axis_box.setToolTip("Position: the profile's coordinate (needs a profile with the port on it); "
                                 "Time: the round trip from the port's plane")
        for box in (self.port_box, self.profile_box, self.mode_box, self.window_box, self.axis_box):
            box.currentIndexChanged.connect(lambda _: self._redraw())
        self.profile_box.currentIndexChanged.connect(lambda _: self.profile_changed.emit())
        self._chosen = {}               # box -> the item the user picked (kept while it exists)
        for box in (self.port_box, self.profile_box):
            box.activated.connect(lambda _, b=box: self._chosen.__setitem__(b, b.currentText()))
        bar = QHBoxLayout()
        for label, box in (("port", self.port_box), ("profile", self.profile_box), ("mode", self.mode_box),
                           ("window", self.window_box), ("axis", self.axis_box)):
            bar.addWidget(QLabel(label))
            bar.addWidget(box)
            bar.addSpacing(6)
        self.profile_button = QPushButton("Compute Z₀ profile")
        self.profile_button.setToolTip("No profile of this mesh to tie the time to: compute one (along the "
                                       "Z0 path if drawn, else along the port's axis)")
        self.profile_button.clicked.connect(lambda: self.profile_requested.emit(self._wanted_profile()))
        bar.addWidget(self.profile_button)
        bar.addStretch(1)

        self.top = LinePlot()
        self.bottom = LinePlot()
        for plot in (self.top, self.bottom):
            plot.empty_text = EMPTY_TEXT
            plot.installEventFilter(self)
            plot.setToolTip("Mouse wheel: move the marker (Ctrl: 10 steps)")
        splitter = QSplitter(Qt.Vertical)
        splitter.addWidget(self.top)
        splitter.addWidget(self.bottom)
        splitter.setSizes([3, 2])

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
        layout.addLayout(bar)
        layout.addWidget(splitter, 1)
        layout.addWidget(self.readout)
        layout.addWidget(self.info)

    # =====================================================
    # Data
    # =====================================================

    def set_data(self, runs, profiles, waveports):
        """The shown runs (oldest first), the kept profiles of the mesh, the waveguide ports."""
        self.runs = list(runs)
        self.profiles = dict(profiles)
        self.waveports = dict(waveports)
        self._fill(self.port_box, self._ports(), self._default_port())
        items = ["none", "X", "Y", "Z"] + (["Path"] if self.path_drawn or "path" in self.profiles else [])
        before = self.profile_box.currentText()
        self._fill(self.profile_box, items, self._default_profile(items))
        self._redraw()
        if self.profile_box.currentText() != before:
            self.profile_changed.emit()

    def _fill(self, box, items, default):
        """The box's items; the user's pick while it exists, else the default (a profile computed meanwhile)."""
        chosen = self._chosen.get(box)
        box.blockSignals(True)
        box.clear()
        box.addItems(items)
        box.setCurrentText(chosen if chosen in items else default)
        box.blockSignals(False)

    def _ports(self):
        names = []
        for run in self.runs:
            for j, name in enumerate(run.ports):
                if (j, j) in run.s and name not in names:
                    names.append(name)
        return names

    def _default_port(self):
        run = self.runs[-1] if self.runs else None
        excited = sorted(j for j, k in run.s if j == k) if run else []
        return run.ports[excited[0]] if excited else ""

    def _default_profile(self, items):
        """
        A computed one: the path's, else the one along the port's axis;
        none computed: the one Compute would make (the path if drawn, else
        the port's axis), so the button says what it computes.
        """
        port = self.waveports.get(self.port_box.currentText())
        axis = "XYZ"[port.axis] if port is not None and port.ok else None
        if "path" in self.profiles:
            return "Path"
        if axis is not None and SOURCES[axis.lower()] in self.profiles:
            return axis
        for key, name in ((0, "X"), (1, "Y"), (2, "Z")):
            if key in self.profiles:
                return name
        return "Path" if "Path" in items else (axis or "none")

    def _wanted_profile(self):
        """The profile Compute makes: the one picked, or (none picked) the path's if drawn, else the port's axis."""
        text = self.profile_box.currentText()
        if text not in ("", "none"):
            return text.lower()
        port = self.waveports.get(self.port_box.currentText())
        if self.path_drawn or port is None or not port.ok:
            return "path"
        return "xyz"[port.axis]

    @property
    def on_path(self):
        return self.profile_box.currentText() == "Path"

    def _profile(self):
        text = self.profile_box.currentText()
        return None if text in ("", "none") else self.profiles.get(SOURCES[text.lower()])

    def _tie(self, profile, port_name):
        """(LineMap, note): the line from the port's plane along the profile, or (None, why not)."""
        if profile is None:
            return None, ""
        port = self.waveports.get(port_name)
        if port is None or not port.ok:
            return None, f"{port_name} is not a waveguide port: no line to tie to the profile (time axis)."
        along = "the path" if profile.axis < 0 else "XYZ"[profile.axis]
        origin = tdr.port_origin(profile.axis, profile.coordinates, getattr(profile, "points", None),
                                 port.axis, port.direction, port.plane, port.center)
        if origin is None:
            return None, (f"{port_name} feeds along {'XYZ'[port.axis]}, the profile runs along {along}: "
                          "not tied (time axis).")
        x, sign, off = origin
        line = tdr.line_map(profile.coordinates, profile.z0, profile.eps_eff, x, sign)
        if line is None:
            return None, f"no profile section from {port_name}'s plane on: not tied (time axis)."
        note = ""
        if off > 1.5 * MM:
            note = (f"{port_name}'s center is {off / MM:.1f} mm off the path: its plane is put at the "
                    "nearest point. ")
        return line, note

    # =====================================================
    # Drawing
    # =====================================================

    def _redraw(self):
        port = self.port_box.currentText()
        runs = [(n, r) for n, r in enumerate(self.runs)
                if port in r.ports and (r.ports.index(port),) * 2 in r.s]
        if not runs:
            for plot in (self.top, self.bottom):
                plot.clear()
            self.readout.setText("")
            self.info.setText("")
            self._xs = self._tied = None
            return

        low = self.mode_box.currentText() == "Low pass"
        mode = "low pass" if low else "band pass"
        beta = tdr.WINDOWS[self.window_box.currentText()]
        profile = self._profile()
        line, note = self._tie(profile, port)
        wanted = self._wanted_profile()
        self.profile_button.setText("Compute Z₀ profile along " + ("the path" if wanted == "path" else wanted.upper()))
        self.profile_button.setVisible(profile is None)
        if profile is None:
            picked = self.profile_box.currentText()
            why = "no profile picked" if picked in ("", "none") else f"the profile along {picked} is not computed"
            note = (f"Not tied to the geometry: {why} for this mesh. Compute it (it is saved with the runs "
                    "until the model or its settings change): then the axis is the position along it and the "
                    "wheel moves the 3D plane or the path's section.")
        by_position = line is not None and self.axis_box.currentText() == "Position"
        self._tied = (profile, line) if by_position else None
        notes = [note] if note else []

        responses = []
        for n, run in runs:
            j = run.ports.index(port)
            responses.append((n, run, run.resistances[j], tdr.transform(run.frequencies, run.s[j, j], mode, beta)))
        newest = responses[-1]
        rise = newest[3].rise

        if line is not None:
            end_t = 2.0 * float(line.delay[-1]) + 1.5 * rise
        else:
            end_t = max(tdr.support(r.frequencies, r.s[r.ports.index(port), r.ports.index(port)])
                        for _, r in runs) + 2.0 * rise
        end_t = min(end_t, float(newest[3].t[-1]))

        def x_of(t):
            return line.coordinate(t) / MM if by_position else t / PS

        several = len(responses) > 1
        top, bottom, readings = [], [], []
        for n, run, r_ref, resp in responses:
            keep = resp.t <= end_t
            x = x_of(resp.t[keep])
            name = run.name if several else "TDR"
            if low:
                z = resp.impedance(r_ref)[keep]
                top.append((f"{name}", x, z, n, False))
                bottom.append((f"{name}", x, resp.impulse[keep], n, False))
                readings.append((name, x, z, resp.impulse[keep], "Ω"))
                if resp.doubtful:
                    notes.append(f"{run.name}: S(DC) {resp.dc.real:+.3f} ± {resp.dc_spread:.3f} is not "
                                 "determined by the band (a slow tail below f_min, e.g. a DC block): the "
                                 "step's level drifts; Band pass shows where the reflections are.")
            else:
                top.append((f"{name}", x, resp.impulse[keep], n, False))
                readings.append((name, x, resp.impulse[keep], None, ""))

        if line is not None:
            run, r_ref = newest[1], newest[2]
            seen = tdr.transform(run.frequencies, tdr.cascade_s11(line, run.frequencies, r_ref), mode, beta)
            keep = seen.t <= end_t
            u_end = float(line.u[-1])
            if by_position:
                profile_x = line.origin + line.sign * line.u
                profile_x = profile_x / MM
            else:
                profile_x = 2.0 * line.delay / PS
            if low:
                top.insert(0, ("profile Z0", profile_x, line.z0, COLOR_Z0, True))
                top.insert(1, ("profile as TDR", x_of(seen.t[keep]), seen.impedance(r_ref)[keep], COLOR_SEEN, False))
                bottom.insert(0, ("profile as TDR", x_of(seen.t[keep]), seen.impulse[keep], COLOR_SEEN, False))
                readings.insert(0, ("profile as TDR", x_of(seen.t[keep]), seen.impedance(r_ref)[keep],
                                    seen.impulse[keep], "Ω"))
                readings.insert(0, ("profile Z0", profile_x, line.z0, None, "Ω"))
            else:
                top.insert(0, ("profile as TDR", x_of(seen.t[keep]), seen.impulse[keep], COLOR_SEEN, False))
                readings.insert(0, ("profile as TDR", x_of(seen.t[keep]), seen.impulse[keep], None, ""))
            if by_position:
                self._xs = np.sort(np.asarray(profile.coordinates) / MM)
                lo, hi = sorted((line.origin / MM, (line.origin + line.sign * u_end) / MM))
                self._xs = self._xs[(self._xs >= lo - 1e-9) & (self._xs <= hi + 1e-9)]
            else:
                self._xs = None
            notes.append("profile as TDR: the profile's sections as a cascade of uniform lines, terminated in "
                         f"{r_ref:g} Ω, through the same transform on {run.name}'s band.")
        else:
            self._xs = None
        if self._xs is None or not len(self._xs):
            span = x_of(np.array([0.0, end_t]))
            step = x_of(np.array([0.0, rise / 10.0]))
            self._xs = np.arange(min(span), max(span) + 1e-12, abs(step[1] - step[0]) or 1.0)

        unit = "mm" if by_position else "ps"
        if unit != self._unit:
            self._marker, self._unit = None, unit
        if self._marker is None or not (self._xs.min() <= self._marker <= self._xs.max()):
            self._marker = float(self._xs[len(self._xs) // 2])
        x_label = ("along the path, mm" if profile is not None and profile.axis < 0 else
                   f"{'XYZ'[profile.axis]}, mm" if profile is not None else "") if by_position else \
            "round trip from the port's plane, ps"
        if low:
            # The TDR curves set the range (the profile's sharp Z0 may leave it): their
            # bumps are an Ohm or two, the profile's steps ten.
            values = np.concatenate([c[2][np.isfinite(c[2])] for c in top if not c[4]] or [np.array([GUIDE])])
            lo, hi = float(min(values.min(), GUIDE - 3)), float(max(values.max(), GUIDE + 3))
            pad = 0.15 * (hi - lo)
            guide = (f"{GUIDE:g} Ω", np.array([min(c[1].min() for c in top), max(c[1].max() for c in top)]),
                     np.array([GUIDE, GUIDE]), COLOR_GUIDE, True)
            self.top.set_data(top + [guide], x_label, "Z (step), Ω", (lo - pad, min(hi + pad, 500.0)), self._marker)
            self.bottom.set_data(bottom, x_label, "impulse, ρ", None, self._marker)
            self.bottom.show()
        else:
            self.top.set_data(top, x_label, "envelope |ρ|", None, self._marker)
            self.bottom.hide()
        self._readings = readings

        speed = ""
        if line is not None:
            eps = float(np.median(line.eps_eff))
            speed = f" ≈ {rise / 2.0 * C0 / np.sqrt(eps) / MM:.1f} mm of line at eps_eff {eps:.2f}"
        what = "step rise 10-90 %" if low else "peak width at half height"
        self.info.setText(f"Resolution: {what} {rise / PS:.0f} ps{speed}: closer reflections merge, a short "
                          "pad reads lower than its own Z0. " + " ".join(notes))
        self._read()

    def _read(self):
        lines = []
        where = (f"{self._marker:.3f} mm" if self._tied is not None else f"{self._marker:.1f} ps")
        for name, x, main, impulse, unit in self._readings:
            order = np.argsort(x)
            if not (x.min() <= self._marker <= x.max()):
                continue
            value = float(np.interp(self._marker, x[order], main[order]))
            text = f"{name:>16}: {value:8.2f} {unit}" if unit else f"{name:>16}: |ρ| {value:.4f}"
            if impulse is not None:
                text += f"   ρ {float(np.interp(self._marker, x[order], impulse[order])):+.4f}"
            lines.append(text)
        self.readout.setText(f"at {where}\n" + "\n".join(lines))
        for plot in (self.top, self.bottom):
            plot.marker_x = self._marker
            plot.update()

    # =====================================================
    # Marker
    # =====================================================

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Wheel and self._xs is not None and len(self._xs):
            notches = event.angleDelta().y() / 120.0
            step = 10 if event.modifiers() & Qt.ControlModifier else 1
            index = int(np.argmin(np.abs(self._xs - self._marker))) + int(round(notches * step))
            self._marker = float(self._xs[max(0, min(len(self._xs) - 1, index))])
            self._read()
            if self._tied is not None:
                profile = self._tied[0]
                index = int(np.argmin(np.abs(profile.coordinates / MM - self._marker)))
                self.section_picked.emit(profile, index)
            return True
        return super().eventFilter(watched, event)

    def hideEvent(self, event):
        super().hideEvent(event)
        self.hidden.emit()
