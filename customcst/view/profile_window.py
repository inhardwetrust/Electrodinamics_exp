# profile_window.py
#
# The Z0 profile as a plot: Z0 of every cross-section along an axis
# (solver/line_profile.py) or along the drawn Z0 path
# (solver/path_profile.py), a 50 Ohm guide line, and a marker. The
# "along" box picks the source (X, Y, Z or Path); each source keeps its
# own profile in the main window, so switching shows the kept one at once.
# While one computes, a progress bar shows how far (sections done of all;
# along the path first each segment's setup), the box and Compute wait.
# The mouse wheel over the plot moves the marker (one section per notch,
# ten with Ctrl) and tells the main window (section_picked): along an axis
# it moves the mesh plane there, along the path it marks the section
# across the path in the 3D view. Nothing is computed on opening: the
# Compute button asks the main window (compute_requested), which keeps the
# result until the mesh (or the path) changes.

import numpy as np
from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QProgressBar, QPushButton, QVBoxLayout,
                               QWidget)

from view.plot import LinePlot

MM = 1e-3
GUIDE = 50.0            # Ohm: the guide line
COLOR_Z0, COLOR_GUIDE = 0, 6
EMPTY_TEXT = "no profile: press Compute"
SOURCES = ("x", "y", "z", "path")       # the "along" box; index = the axis for x, y, z


class ProfileWindow(QWidget):
    section_picked = Signal(int)            # index of the section under the marker
    compute_requested = Signal(str)         # the source: "x", "y", "z" or "path"
    source_changed = Signal(str)
    hidden = Signal()

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Tool)
        self.setWindowTitle("Z0 profile")
        self.resize(820, 460)
        self.profile = None
        self.source = "x"
        self._index = 0

        self.plot = LinePlot()
        self.plot.empty_text = EMPTY_TEXT
        self.plot.installEventFilter(self)
        self.plot.setToolTip("Mouse wheel: move the marker and the 3D plane or path section (Ctrl: 10 sections)")
        self.readout = QLabel()
        mono = QFont("Consolas")
        mono.setStyleHint(QFont.Monospace)
        self.readout.setFont(mono)
        self.readout.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.info = QLabel()
        self.info.setStyleSheet("color: gray")
        self.info.setWordWrap(True)

        self.along = QComboBox()
        self.along.addItems(["X", "Y", "Z", "Path"])
        self.along.setToolTip("Sections along an axis (the grid planes), or along the Z0 path "
                              "(square to it, drawn with Z₀ path on the toolbar)")
        self.along.currentIndexChanged.connect(lambda i: self.source_changed.emit(SOURCES[i]))
        self.compute_button = QPushButton("Compute")
        self.compute_button.clicked.connect(lambda: self.compute_requested.emit(self.source))
        self.state = QLabel()
        self.progress = QProgressBar()
        self.progress.setMinimumWidth(200)
        self.progress.setFormat("%v / %m")
        self.progress.hide()
        bar = QHBoxLayout()
        bar.addWidget(QLabel("along"))
        bar.addWidget(self.along)
        bar.addWidget(self.compute_button)
        bar.addWidget(self.progress)
        bar.addWidget(self.state, 1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addLayout(bar)
        layout.addWidget(self.plot, 1)
        layout.addWidget(self.readout)
        layout.addWidget(self.info)

    def _select(self, source):
        """Show `source` in the along box, without signalling."""
        self.source = source
        self.along.blockSignals(True)
        self.along.setCurrentIndex(SOURCES.index(source))
        self.along.blockSignals(False)

    def _title(self, suffix=""):
        where = "the Z0 path" if self.source == "path" else self.source.upper()
        self.setWindowTitle(f"Z0 profile along {where}" + (f" - {suffix}" if suffix else ""))

    def set_profile(self, profile, source, accuracy=""):
        self.profile = profile
        self._select(source)
        self._title()
        z0 = profile.z0
        missing = int(np.count_nonzero(np.isnan(z0)))
        if source == "path":
            text = ("Z0 of a uniform line with each cross-section square to the path, from the geometry "
                    "on a fine grid of its own (about converged; not the 3D grid's view): exact on "
                    "uniform stretches, indicative on tapers, steps and bends; not the input impedance. ")
        else:
            text = ("Z0 of a uniform line with each grid cross-section (quasi-TEM, as the FDTD grid sees it): "
                    "exact on uniform stretches, indicative on tapers and steps; not the input impedance. "
                    + (f"<b>At this mesh Z0 of narrow lines is {accuracy}</b> (see Mesh in the tree). "
                       if accuracy else ""))
        self.info.setText(text + (f"Gaps: {missing} section{'s' * (missing > 1)} without a single signal "
                                  "conductor." if missing else ""))
        self._index = min(self._index, max(len(profile.sections) - 1, 0))
        self.compute_button.setEnabled(False)
        where = "the path" if source == "path" else source
        self.state.setText(f"{len(profile.sections)} sections along {where}")
        self._redraw()

    def set_empty(self, source, reason=""):
        """No profile along `source` for the current mesh (and path) yet."""
        self.profile = None
        self._select(source)
        self._title()
        self.compute_button.setEnabled(not reason)
        if reason:
            self.state.setText(reason)
        else:
            what = ("cross-sections along the Z0 path" if source == "path"
                    else f"every grid section along {source}")
            self.state.setText(f"Not computed: press Compute ({what}).")
        self.info.setText("")
        self._redraw()

    def mark_outdated(self, what="the mesh changed"):
        """The model (or the path) changed after the profile was computed."""
        self.compute_button.setEnabled(True)
        if self.profile is not None:
            self._title("outdated")
            self.state.setText(f"Outdated: {what}. Press Compute for the new one.")

    def start_compute(self):
        """A profile is being computed: the box and Compute wait, the progress bar shows."""
        self.along.setEnabled(False)
        self.compute_button.setEnabled(False)
        self.progress.setRange(0, 0)
        self.progress.show()
        self.state.setText("Computing…")
        self.plot.empty_text = "computing…"
        self.plot.update()

    def set_progress(self, done, total, what=""):
        """Sections done of total (total 0: not known yet, the bar runs busy)."""
        self.progress.setRange(0, total)
        self.progress.setValue(done)
        self.state.setText(f"Computing: {what}" if what else "Computing…")

    def end_compute(self):
        self.progress.hide()
        self.plot.empty_text = EMPTY_TEXT
        self.plot.update()
        self.along.setEnabled(True)

    def set_marker(self, coordinate):
        """Move the marker to the section nearest `coordinate` (m), without signalling."""
        if self.profile is None or not self.profile.sections:
            return
        self._index = int(np.argmin(np.abs(self.profile.coordinates - coordinate)))
        self._redraw()

    @property
    def index(self):
        return self._index

    def hideEvent(self, event):
        super().hideEvent(event)
        self.hidden.emit()

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Wheel and self.profile is not None and self.profile.sections:
            notches = event.angleDelta().y() / 120.0
            step = 10 if event.modifiers() & Qt.ControlModifier else 1
            index = self._index + int(round(notches * step))
            self._index = max(0, min(len(self.profile.sections) - 1, index))
            self._redraw()
            self.section_picked.emit(self._index)
            return True
        return super().eventFilter(watched, event)

    def _redraw(self):
        if self.profile is None or not self.profile.sections:
            self.plot.clear()
            self.readout.setText("")
            return
        x = self.profile.coordinates / MM
        z0 = self.profile.z0
        finite = z0[np.isfinite(z0)]
        top = float(finite.max()) if len(finite) else 2 * GUIDE
        curves = [("Z0", x, z0, COLOR_Z0, False),
                  (f"{GUIDE:g} Ω", x[[0, -1]], np.array([GUIDE, GUIDE]), COLOR_GUIDE, True)]
        s = self.profile.sections[self._index]
        name = "along the path" if self.source == "path" else self.source
        self.plot.set_data(curves, f"{name}, mm", "Z0, Ω",
                           (0.0, min(max(1.15 * top, 1.3 * GUIDE), 500.0)), marker_x=s.coordinate / MM)
        where = f"{name} = {s.coordinate / MM:8.3f} mm"
        if self.source == "path":
            point = self.profile.points[self._index] / MM
            where += " (" + ", ".join(f"{v:.3f}" for v in point) + ")"
        self.readout.setText(f"{where}:  Z0 = {s.z0:.1f} Ω   eps_eff = {s.eps_eff:.3f}" if not s.error
                             else f"{where}:  no Z0 ({s.error})")
