# view_buttons.py
#
# Standard views over the 3D view's top right corner (a child of the
# canvas widget, so they sit on the picture, not in a toolbar), laid out
# as an unfolded cube like CAD view cubes:
#   Iso     Top     Back
#   Left    Front   Right
#   Ortho   Bottom
# all of one width.
# A view resets the camera to the model as Reset view does and looks along
# an axis (GeometryView.look_from); Iso is Reset view; Ortho toggles the
# perspective off. The buttons take no keyboard focus: Space keeps panning
# the view. (Copied from customcst/view/view_buttons.py.)

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QGridLayout, QToolButton, QWidget

MARGIN = 8              # px from the canvas corner
STYLE = """
QToolButton { color: #d8dce2; background: rgba(40, 44, 52, 170); border: 1px solid rgba(120, 128, 140, 120);
              border-radius: 3px; padding: 1px 4px; font-size: 8pt; min-width: 34px; }
QToolButton:hover { background: rgba(70, 76, 88, 220); }
QToolButton:checked { background: rgba(0, 120, 200, 200); }
"""
LAYOUT = (("Top", 0, 1), ("Back", 0, 2), ("Left", 1, 0), ("Front", 1, 1), ("Right", 1, 2), ("Bottom", 2, 1))


class ViewButtons(QWidget):
    def __init__(self, view):
        canvas = view.canvas.native
        super().__init__(canvas)
        self.view = view
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setStyleSheet(STYLE)
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(2)
        self.buttons = {}
        for name, row, column in LAYOUT:
            button = self._button(name, f"Look from the {name.lower()} (camera reset to the model)")
            button.clicked.connect(lambda _=False, n=name: self.view.look_from(n))
            grid.addWidget(button, row, column)
        iso = self._button("Iso", "Reset view: the isometric view with the up axis vertical")
        iso.clicked.connect(self.view.reset_view)
        ortho = self._button("Ortho", "Orthographic projection (no perspective): flat views")
        ortho.setCheckable(True)
        ortho.setChecked(self.view.orthographic)
        ortho.toggled.connect(self.view.set_orthographic)
        grid.addWidget(iso, 0, 0)
        grid.addWidget(ortho, 2, 0)
        width = max(b.sizeHint().width() for b in self.buttons.values())
        for button in self.buttons.values():
            button.setFixedWidth(width)
        self.adjustSize()
        canvas.installEventFilter(self)
        self._place()
        self.show()

    def _button(self, name, tip):
        button = QToolButton(self)
        button.setText(name)
        button.setToolTip(tip)
        button.setFocusPolicy(Qt.NoFocus)
        self.buttons[name] = button
        return button

    def _place(self):
        parent = self.parentWidget()
        self.move(parent.width() - self.width() - MARGIN, MARGIN)
        self.raise_()

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Resize:
            self._place()
        return False
