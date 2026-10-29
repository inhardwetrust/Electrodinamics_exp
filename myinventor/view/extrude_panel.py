# extrude_panel.py
#
# The Extrude command's panel (as Inventor's Extrude dialog, for now only
# what is needed): a small tool window over the main one, not modal, so the
# 3D view stays usable for picking profiles.
#   Profiles   how many are selected (picked in 3D)
#   Distance   a formula of the parameters (mm), its value or the error
#   Apply      makes the extrusion (off until there is a profile and the
#              distance is a number other than 0); Cancel (or Esc, or
#              closing the panel) leaves without one.
# The same panel edits an extrusion (its distance formula filled in, the
# title "Edit Extrusion1"). The panel only shows and reports (applied(distance
# text), cancelled(), and the distance field's textChanged); the window does
# the rest, the preview included.

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout,
)

from model.expressions import ExpressionError, evaluate

ERROR_STYLE = "color: #c81e1e"


class ExtrudePanel(QDialog):
    applied = Signal(str)
    cancelled = Signal()

    def __init__(self, parent, values, distance="10", title="Extrude"):
        """values: the parameters' values, for evaluating the distance formula; title: "Edit Extrusion1"..."""
        super().__init__(parent, Qt.Tool)
        self.setWindowTitle(title)
        self.setModal(False)
        self.values = values
        self.profiles = QLabel()
        self.distance = QLineEdit(distance)
        self.distance.setToolTip("How far along the sketch's normal (mm): a number or a formula of the "
                                 "parameters; negative: the other way")
        self.value = QLabel()
        self.distance.textChanged.connect(self._update)
        form = QFormLayout()
        form.addRow("Profiles:", self.profiles)
        form.addRow("Distance:", self.distance)
        form.addRow("", self.value)
        self.apply_button = QPushButton("Apply")
        self.apply_button.setDefault(True)
        self.apply_button.clicked.connect(self._apply)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.apply_button)
        buttons.addWidget(cancel)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addLayout(buttons)
        self._count = 0
        self._done = False
        self.set_profile_count(0)

    def set_profile_count(self, count):
        self._count = count
        self.profiles.setText(f"{count} selected" if count else "click closed regions in the view")
        self._update()

    def distance_value(self):
        """The distance (mm), or the error text."""
        try:
            value = evaluate(self.distance.text(), self.values)
        except ExpressionError as error:
            return str(error)
        return value if value != 0 else "the distance must not be 0"

    def _update(self):
        value = self.distance_value()
        ok = not isinstance(value, str)
        self.value.setText(f"= {value:.6g} mm" if ok else value)
        self.value.setStyleSheet("" if ok else ERROR_STYLE)
        self.apply_button.setEnabled(ok and self._count > 0)

    def _apply(self):
        if self.apply_button.isEnabled():
            self._done = True
            self.applied.emit(self.distance.text().strip())
            self.close()

    def reject(self):
        """Cancel, Esc in the panel, or closing it."""
        if not self._done:
            self._done = True
            self.cancelled.emit()
        super().reject()

    def closeEvent(self, event):
        if not self._done:
            self._done = True
            self.cancelled.emit()
        super().closeEvent(event)
