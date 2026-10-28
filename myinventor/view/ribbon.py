# ribbon.py
#
# Inventor-style ribbon: tabs across the top (File, 3D Model, Sketch), each a
# row of groups; a group is a row of buttons with its title under them.
# Buttons are QToolButtons showing a QAction (text only for now), so the
# same action keeps its shortcut, enabled state and check state everywhere.
# A corner widget at the right of the tab bar holds the quick access buttons
# (Undo, Redo).

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QSizePolicy, QTabWidget, QToolButton, QVBoxLayout, QWidget,
)

STYLE = """
QToolButton { padding: 4px 8px; }
QLabel#group { color: #707070; font-size: 8pt; }
"""


class RibbonGroup(QWidget):
    def __init__(self, title):
        super().__init__()
        self.row = QHBoxLayout()
        self.row.setSpacing(2)
        label = QLabel(title)
        label.setObjectName("group")
        label.setAlignment(Qt.AlignHCenter)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 0)
        layout.setSpacing(1)
        layout.addLayout(self.row, 1)
        layout.addWidget(label)
        self.buttons = {}

    def add_action(self, action, menu=None):
        button = QToolButton()
        button.setDefaultAction(action)
        button.setToolButtonStyle(Qt.ToolButtonTextOnly)
        button.setAutoRaise(True)
        button.setFocusPolicy(Qt.NoFocus)
        if menu is not None:
            button.setMenu(menu)
            button.setPopupMode(QToolButton.MenuButtonPopup)
        self.row.addWidget(button)
        self.buttons[action.text()] = button
        return button

    def add_widget(self, widget):
        self.row.addWidget(widget)
        return widget


class RibbonTab(QWidget):
    def __init__(self):
        super().__init__()
        self.layout_ = QHBoxLayout(self)
        self.layout_.setContentsMargins(2, 2, 2, 2)
        self.layout_.setSpacing(0)
        self.layout_.addStretch(1)
        self.groups = {}

    def add_group(self, title):
        if self.groups:
            line = QFrame()
            line.setFrameShape(QFrame.VLine)
            line.setFrameShadow(QFrame.Sunken)
            self.layout_.insertWidget(self.layout_.count() - 1, line)
        group = RibbonGroup(title)
        self.layout_.insertWidget(self.layout_.count() - 1, group)
        self.groups[title] = group
        return group


class Ribbon(QTabWidget):
    def __init__(self):
        super().__init__()
        self.setDocumentMode(True)
        self.setStyleSheet(STYLE)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.tabs = {}
        self.quick = QWidget()
        self._quick_row = QHBoxLayout(self.quick)
        self._quick_row.setContentsMargins(0, 0, 4, 0)
        self.setCornerWidget(self.quick, Qt.TopRightCorner)

    def add_tab(self, title):
        tab = RibbonTab()
        self.addTab(tab, title)
        self.tabs[title] = tab
        return tab

    def add_quick(self, action):
        button = QToolButton()
        button.setDefaultAction(action)
        button.setAutoRaise(True)
        button.setFocusPolicy(Qt.NoFocus)
        self._quick_row.addWidget(button)
        return button

    def show_tab(self, title):
        self.setCurrentWidget(self.tabs[title])

    def set_tab_enabled(self, title, on):
        self.setTabEnabled(self.indexOf(self.tabs[title]), on)

    def current_title(self):
        return self.tabText(self.currentIndex())
