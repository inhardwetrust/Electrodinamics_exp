# workplane_panel.py
#
# The Work Plane command's panel: a small tool window over the main one, not
# modal (the view stays usable for picking the axis).
#   Base       the plane it starts from (picked before the panel opens)
#   Kind       Offset (along the base's normal) or Angle (around an axis)
#   Offset     a formula (mm)
#   Axis       Angle: the axis taken (Pick: a click in the view takes one)
#   Angle      a formula (degrees)
#   Apply      makes it (off until the values do); Cancel / Esc leaves.
# The panel only shows and reports (applied(), cancelled(), changed() on any
# edit, pick_axis()); the window builds the plane, its preview included.

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout,
)

from model.expressions import ExpressionError, evaluate

ERROR_STYLE = "color: #c81e1e"


class WorkPlanePanel(QDialog):
    applied = Signal()
    cancelled = Signal()
    changed = Signal()
    pick_axis = Signal()

    def __init__(self, parent, values, base, offset="0", axis=None, axis_label=None, angle="30",
                 title="Work Plane"):
        """values: the parameters' values; axis: the axis's name (Angle) or None (Offset)."""
        super().__init__(parent, Qt.Tool)
        self.setWindowTitle(title)
        self.setModal(False)
        self.values = values
        self.axis = axis
        self.kind = QComboBox()
        self.kind.addItems(["Offset", "Angle"])
        self.kind.setCurrentText("Angle" if axis else "Offset")
        self.offset = QLineEdit(offset)
        self.offset.setToolTip("How far (mm) along the base's normal: a number or a formula of the parameters")
        self.axis_label = QLabel(axis_label or "")
        self.pick = QPushButton("Pick")
        self.pick.setAutoDefault(False)
        self.pick.setToolTip("A click in the view takes the axis: a straight line of any sketch, or an origin axis")
        self.pick.clicked.connect(self.pick_axis.emit)
        self.angle = QLineEdit(angle)
        self.angle.setToolTip("How far (degrees) around the axis from the base: a number or a formula")
        self.note = QLabel()
        axis_row = QHBoxLayout()
        axis_row.addWidget(self.axis_label, 1)
        axis_row.addWidget(self.pick)
        form = QFormLayout()
        form.addRow("Base:", QLabel(base))
        form.addRow("Kind:", self.kind)
        form.addRow("Offset:", self.offset)
        form.addRow("Axis:", axis_row)
        form.addRow("Angle:", self.angle)
        form.addRow("", self.note)
        self.form = form
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
        self._done = False
        for edit in (self.offset, self.angle):
            edit.textChanged.connect(self._update)
        self.kind.currentIndexChanged.connect(self._update)
        self._update()

    def is_angle(self):
        return self.kind.currentText() == "Angle"

    def set_axis(self, axis, label):
        """The axis taken (its name in the file and its label)."""
        self.axis = axis
        self.axis_label.setText(label)
        self._update()

    def set_error(self, text):
        """Why the plane cannot be made (the window's preview found it), or None."""
        self._error = text
        self._update(emit=False)

    def _value(self, edit, what):
        try:
            return evaluate(edit.text(), self.values)
        except ExpressionError as error:
            return f"{what}: {error}"

    def _update(self, *_, emit=True):
        angle = self.is_angle()
        self.form.setRowVisible(2, not angle)
        self.form.setRowVisible(3, angle)
        self.form.setRowVisible(4, angle)
        if angle:
            value = self._value(self.angle, "angle")
            if not self.axis:
                self.axis_label.setText("Pick an axis")
                self.axis_label.setStyleSheet("color: gray")
            else:
                self.axis_label.setStyleSheet("")
            problem = value if isinstance(value, str) else (None if self.axis else "pick the axis")
        else:
            value = self._value(self.offset, "offset")
            problem = value if isinstance(value, str) else None
        problem = problem or getattr(self, "_error", None)
        self.note.setText(problem or "")
        self.note.setStyleSheet(ERROR_STYLE if problem else "")
        self.apply_button.setEnabled(problem is None)
        if emit:
            self.changed.emit()

    def _apply(self):
        if self.apply_button.isEnabled():
            self._done = True
            self.applied.emit()
            self.close()

    def reject(self):
        if not self._done:
            self._done = True
            self.cancelled.emit()
        super().reject()

    def closeEvent(self, event):
        if not self._done:
            self._done = True
            self.cancelled.emit()
        super().closeEvent(event)
