# profile_window.py
#
# The Z0 profile along an axis (solver/line_profile.py) as a plot: Z0 of
# every grid cross-section, a 50 Ohm guide line, and a marker that follows
# the mesh plane of the 3D view. The mouse wheel over the plot moves the
# marker (one section per notch, ten with Ctrl) and asks the main window
# to move the plane there (coordinate_picked), so the 3D view shows the
# section whose Z0 is read.

import numpy as np
from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from view.plot import LinePlot

MM = 1e-3
GUIDE = 50.0            # Ohm: the guide line
COLOR_Z0, COLOR_GUIDE = 0, 6


class ProfileWindow(QWidget):
    coordinate_picked = Signal(float)       # m along the profile's axis

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Tool)
        self.setWindowTitle("Z0 profile")
        self.resize(820, 460)
        self.profile = None
        self.axis_name = ""
        self._index = 0

        self.plot = LinePlot()
        self.plot.installEventFilter(self)
        self.plot.setToolTip("Mouse wheel: move the marker and the 3D plane (Ctrl: 10 sections)")
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
        layout.addWidget(self.plot, 1)
        layout.addWidget(self.readout)
        layout.addWidget(self.info)

    def set_profile(self, profile, axis_name, title="", accuracy=""):
        self.profile = profile
        self.axis_name = axis_name
        self.setWindowTitle(f"Z0 profile along {axis_name.upper()}" + (f" - {title}" if title else ""))
        z0 = profile.z0
        missing = int(np.count_nonzero(np.isnan(z0)))
        self.info.setText(
            "Z0 of a uniform line with each grid cross-section (quasi-TEM, as the FDTD grid sees it): "
            "exact on uniform stretches, indicative on tapers and steps; not the input impedance. "
            + (f"<b>At this mesh Z0 of narrow lines is {accuracy}</b> (see Mesh in the tree). " if accuracy else "")
            + (f"Gaps: {missing} section{'s' * (missing > 1)} without a single signal conductor."
               if missing else ""))
        self._index = min(self._index, max(len(profile.sections) - 1, 0))
        self._redraw()

    def mark_outdated(self):
        """The model changed after the profile was computed."""
        if self.profile is not None:
            self.setWindowTitle(f"Z0 profile along {self.axis_name.upper()} - outdated (model changed, "
                                "compute it again)")

    def set_marker(self, coordinate):
        """Move the marker to the section nearest `coordinate` (m), without signalling."""
        if self.profile is None or not self.profile.sections:
            return
        self._index = int(np.argmin(np.abs(self.profile.coordinates - coordinate)))
        self._redraw()

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Wheel and self.profile is not None and self.profile.sections:
            notches = event.angleDelta().y() / 120.0
            step = 10 if event.modifiers() & Qt.ControlModifier else 1
            index = self._index + int(round(notches * step))
            self._index = max(0, min(len(self.profile.sections) - 1, index))
            self._redraw()
            self.coordinate_picked.emit(self.profile.sections[self._index].coordinate)
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
        self.plot.set_data(curves, f"{self.axis_name}, mm", "Z0, Ω",
                           (0.0, min(max(1.15 * top, 1.3 * GUIDE), 500.0)), marker_x=s.coordinate / MM)
        where = f"{self.axis_name} = {s.coordinate / MM:8.3f} mm"
        self.readout.setText(f"{where}:  Z0 = {s.z0:.1f} Ω   eps_eff = {s.eps_eff:.3f}" if not s.error
                             else f"{where}:  no Z0 ({s.error})")
