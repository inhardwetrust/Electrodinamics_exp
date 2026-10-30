# move_panel.py
#
# The Move / Rotate command's panel (a transform of the placement,
# model/transforms.py): a small tool window, not modal (the view stays
# usable for picking the axis).
#   Targets    the bodies / groups it moves (from the selection; clicks on
#              Solid Bodies or in 3D take or drop them: the window); Clear
#              empties the list (applied so, the transform fails)
#   Placed     where a new one goes in the placement (before a group's)
#   Offset     X, Y, Z (formulas, mm)
#   Axis       turned first around it (Pick: a click in the view; Clear: none)
#   Angle      degrees (with an axis)
#   Apply      makes it; Cancel / Esc leaves.
# It only shows and reports (applied(), cancelled(), changed(), pick_axis());
# the window builds the transform and its preview.

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout

from model.expressions import ExpressionError, evaluate

ERROR_STYLE = "color: #c81e1e"


class MovePanel(QDialog):
    applied = Signal()
    cancelled = Signal()
    changed = Signal()
    pick_axis = Signal()
    cleared = Signal()

    def __init__(self, parent, values, targets, offset=("0", "0", "0"), axis=None, axis_label=None, angle="0",
                 title="Move"):
        super().__init__(parent, Qt.Tool)
        self.setWindowTitle(title)
        self.setModal(False)
        self.values = values
        self.axis = axis
        self.offset = [QLineEdit(text) for text in offset]
        for edit, name in zip(self.offset, "XYZ"):
            edit.setToolTip(f"How far along {name} (mm): a number or a formula of the parameters")
        self.axis_label = QLabel()
        self._axis_label = axis_label
        self.pick = QPushButton("Pick")
        self.pick.setAutoDefault(False)
        self.pick.setToolTip("A click in the view takes the axis: a straight line of any sketch, or an origin axis")
        self.pick.clicked.connect(self.pick_axis.emit)
        self.clear = QPushButton("Clear")
        self.clear.setAutoDefault(False)
        self.clear.setToolTip("No turning")
        self.clear.clicked.connect(lambda: self.set_axis(None, None))
        self.angle = QLineEdit(angle)
        self.angle.setToolTip("How far (degrees) around the axis, the right hand way: a number or a formula")
        self.note = QLabel()
        axis_row = QHBoxLayout()
        axis_row.addWidget(self.axis_label, 1)
        axis_row.addWidget(self.pick)
        axis_row.addWidget(self.clear)
        form = self.form = QFormLayout()
        self.targets_label = QLabel()
        self.targets_label.setWordWrap(True)
        self.targets_label.setToolTip("Click bodies or groups under Solid Bodies (or bodies in 3D) to take or drop "
                                      "them: a group takes the place of its bodies, a body of a group drops the group")
        clear_targets = QPushButton("Clear")
        clear_targets.setAutoDefault(False)
        clear_targets.setToolTip("Drop all the targets, to pick them again")
        clear_targets.clicked.connect(self.cleared.emit)
        targets_row = QHBoxLayout()
        targets_row.addWidget(self.targets_label, 1)
        targets_row.addWidget(clear_targets)
        form.addRow("Targets:", targets_row)
        self.placed = QLabel()
        self.placed.setStyleSheet("color: gray")
        form.addRow("", self.placed)
        for edit, name in zip(self.offset, "XYZ"):
            form.addRow(f"Offset {name}:", edit)
        form.addRow("Turn around:", axis_row)
        form.addRow("Angle:", self.angle)
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
        self.set_targets(targets)
        for edit in self.offset + [self.angle]:
            edit.textChanged.connect(self._update)
        self._show_axis()
        self._update()

    def set_targets(self, targets, placed=""):
        """The targets shown (none: a hint), and where a new transform will go ("" for the end)."""
        self.targets_label.setText(", ".join(targets) or "none: click bodies or groups")
        self.targets_label.setStyleSheet("" if targets else "color: gray")
        self.placed.setText(placed)
        self.placed.setVisible(bool(placed))

    def set_axis(self, axis, label):
        """The axis taken (its name in the file, its label), or None: no turning."""
        self.axis, self._axis_label = axis, label
        self._show_axis()
        self._update()

    def _show_axis(self):
        self.axis_label.setText(self._axis_label or "none (Pick one to turn)")
        self.axis_label.setStyleSheet("" if self.axis else "color: gray")
        self.angle.setEnabled(self.axis is not None)
        self.clear.setEnabled(self.axis is not None)

    def set_error(self, text):
        """Why the transform cannot be made (the window's preview found it), or None."""
        self._error = text
        self._update(emit=False)

    def _update(self, *_, emit=True):
        problem = None
        for edit, what in list(zip(self.offset, ("offset X", "offset Y", "offset Z"))) + [(self.angle, "angle")]:
            if edit is self.angle and self.axis is None:
                continue
            try:
                evaluate(edit.text(), self.values)
            except ExpressionError as error:
                problem = f"{what}: {error}"
                break
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


class MirrorPanel(MovePanel):
    """
    The Mirror command's panel: the targets (picked as for Move) and the
    plane each is mirrored in, through its own center (YZ / XZ / XY).
    """

    def __init__(self, parent, values, targets, plane="YZ", title="Mirror"):
        super().__init__(parent, values, targets, title=title)
        from PySide6.QtWidgets import QComboBox

        from model.transforms import MIRROR_PLANES

        for row in range(5, 0, -1):             # Offset X / Y / Z, Turn around, Angle: not a mirror's
            self.form.removeRow(row + 1)
        self.plane = QComboBox()
        self.plane.addItems(list(MIRROR_PLANES))
        self.plane.setCurrentText(plane)
        self.plane.setToolTip("Mirrored in the plane parallel to it through its own center (a group's: of all "
                              "its bodies)")
        self.plane.currentIndexChanged.connect(self._update)
        self.form.insertRow(2, "Plane (through its center):", self.plane)

    def _update(self, *_, emit=True):
        problem = self._error
        self.note.setText(problem or "")
        self.note.setStyleSheet(ERROR_STYLE if problem else "")
        self.apply_button.setEnabled(problem is None)
        if emit:
            self.changed.emit()
