# workplane_panel.py
#
# The Work Plane command's panel: a small tool window over the main one (not
# modal: the view stays usable for picking the base, the axis, the points).
#   Kind       Offset (along the base's normal), Angle (around an axis in the
#              base), or Three Points
#   Base       the plane it starts from (Offset, Angle); Pick: a click on a
#              plane, a flat face of a body or another work plane
#   Offset     a formula (mm)
#   Axis       Angle: the axis taken (Pick: a click on a line or an axis)
#   Angle      a formula (degrees)
#   Points     Three Points: the points taken (Pick: three clicks on sketch
#              points, the origin's center or a body's corners)
#   Apply      makes it (off until the values do); Cancel / Esc leaves.
# The panel only shows and reports (applied(), cancelled(), changed() on any
# edit, pick_base(), pick_axis(), pick_points()); the window picks and
# builds the plane, its preview included.

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout,
)

from model.expressions import ExpressionError, evaluate

ERROR_STYLE = "color: #c81e1e"
KINDS = ("Offset", "Angle", "Three Points")
ROWS = {"Offset": ("base", "offset"), "Angle": ("base", "axis", "angle"), "Three Points": ("points",)}


class WorkPlanePanel(QDialog):
    applied = Signal()
    cancelled = Signal()
    changed = Signal()
    pick_base = Signal()
    pick_axis = Signal()
    pick_points = Signal()

    def __init__(self, parent, values, base, offset="0", axis=None, axis_label=None, angle="30", points=None,
                 title="Work Plane"):
        """values: the parameters' values; axis: the axis's name (Angle) or None; points: their labels (3)."""
        super().__init__(parent, Qt.Tool)
        self.setWindowTitle(title)
        self.setModal(False)
        self.values = values
        self.base, self.axis = base, axis
        self.points = list(points or [])
        self.kind = QComboBox()
        self.kind.addItems(list(KINDS))
        self.kind.setCurrentText("Three Points" if points else "Angle" if axis else "Offset")
        self.base_label = QLabel()
        self.offset = QLineEdit(offset)
        self.offset.setToolTip("How far (mm) along the base's normal: a number or a formula of the parameters")
        self.axis_label = QLabel(axis_label or "")
        self.angle = QLineEdit(angle)
        self.angle.setToolTip("How far (degrees) around the axis from the base: a number or a formula")
        self.points_label = QLabel()
        self.points_label.setWordWrap(True)
        self.note = QLabel()
        form = QFormLayout()
        self.form = form
        self._rows = {}
        form.addRow("Kind:", self.kind)
        for key, label, widget, signal, tip in (
                ("base", "Base:", self.base_label, self.pick_base,
                 "A click in the view takes the base: a plane, a flat face of a body, another work plane"),
                ("offset", "Offset:", self.offset, None, None),
                ("axis", "Axis:", self.axis_label, self.pick_axis,
                 "A click in the view takes the axis: a straight line of any sketch, or an origin axis"),
                ("angle", "Angle:", self.angle, None, None),
                ("points", "Points:", self.points_label, self.pick_points,
                 "Three clicks in the view take the points: sketch points (the plane follows them), the origin's "
                 "center, a body's corners (their place now)")):
            if signal is None:
                form.addRow(label, widget)
            else:
                row = QHBoxLayout()
                row.addWidget(widget, 1)
                pick = QPushButton("Pick")
                pick.setAutoDefault(False)
                pick.setToolTip(tip)
                pick.clicked.connect(signal.emit)
                row.addWidget(pick)
                form.addRow(label, row)
            self._rows[key] = form.rowCount() - 1
        form.addRow("", self.note)
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
        self._error = None
        for edit in (self.offset, self.angle):
            edit.textChanged.connect(self._update)
        self.kind.currentIndexChanged.connect(self._update)
        self._update()

    def is_angle(self):
        return self.kind.currentText() == "Angle"

    def is_points(self):
        return self.kind.currentText() == "Three Points"

    def set_base(self, base):
        self.base = base
        self._update()

    def set_axis(self, axis, label):
        """The axis taken (its name in the file and its label)."""
        self.axis = axis
        self.axis_label.setText(label)
        self._update()

    def set_points(self, labels):
        """The points taken so far (their labels: up to three)."""
        self.points = list(labels)
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
        kind = self.kind.currentText()
        for key, row in self._rows.items():
            self.form.setRowVisible(row, key in ROWS[kind])
        self.base_label.setText(self.base or "Pick a plane or a flat face")
        self.base_label.setStyleSheet("" if self.base else "color: gray")
        self.axis_label.setStyleSheet("" if self.axis else "color: gray")
        if not self.axis:
            self.axis_label.setText("Pick an axis")
        self.points_label.setText(", ".join(self.points) + (f" ({3 - len(self.points)} more)" if
                                                            len(self.points) < 3 else "")
                                  if self.points else "Pick three points")
        self.points_label.setStyleSheet("" if len(self.points) == 3 else "color: gray")
        if kind == "Three Points":
            problem = None if len(self.points) == 3 else "pick three points"
        elif kind == "Angle":
            value = self._value(self.angle, "angle")
            problem = value if isinstance(value, str) else (
                "pick the base" if not self.base else None if self.axis else "pick the axis")
        else:
            value = self._value(self.offset, "offset")
            problem = value if isinstance(value, str) else (None if self.base else "pick the base")
        problem = problem or self._error
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
