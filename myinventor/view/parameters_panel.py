# parameters_panel.py
#
# The parameter table (Inventor's fx Parameters, CST's parameter list):
# Name | Formula | Value | Comment, in table order.
# - double click a Name, Formula or Comment cell to edit it; Value is the
#   result of the last rebuild (mm or deg), or "error" in red with the
#   reason as its tooltip;
# - Add appends a free name (p1, p2...) = 0; Delete removes the selected
#   rows (a parameter in use is refused by the window with its users named).
# The panel only shows and reports: the window applies the edits to the
# document (undo, rebuild) and calls show() again.

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QHeaderView, QPushButton, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

COLUMNS = ("Name", "Formula", "Value", "Comment")
NAME, FORMULA, VALUE, COMMENT = range(4)
ERROR_COLOR = QColor(200, 30, 30)


class ParametersPanel(QWidget):
    edited = Signal(str, int, str)          # parameter name, column, new text
    add_requested = Signal()
    delete_requested = Signal(list)         # parameter names

    def __init__(self):
        super().__init__()
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(NAME, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(FORMULA, QHeaderView.Stretch)
        header.setSectionResizeMode(VALUE, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(COMMENT, QHeaderView.Stretch)
        self.table.itemChanged.connect(self._item_changed)

        add = QPushButton("Add")
        add.setToolTip("A new parameter (p1, p2...) = 0")
        add.clicked.connect(self.add_requested.emit)
        delete = QPushButton("Delete")
        delete.setToolTip("Delete the selected parameters (only the ones nothing uses)")
        delete.clicked.connect(lambda: self.delete_requested.emit(self.selected_names()))
        buttons = QHBoxLayout()
        buttons.addWidget(add)
        buttons.addWidget(delete)
        buttons.addStretch(1)

        layout = QVBoxLayout(self)
        layout.addWidget(self.table)
        layout.addLayout(buttons)
        self._names = []
        self._filling = False

    def show_parameters(self, parameters, values, errors):
        """parameters: [Parameter]; values / errors: from the rebuild."""
        self._filling = True
        self.table.setRowCount(len(parameters))
        self._names = [p.name for p in parameters]
        for row, p in enumerate(parameters):
            error = errors.get(p.name)
            value = "error" if error else f"{values[p.name]:.6g}" if p.name in values else ""
            for column, text in ((NAME, p.name), (FORMULA, p.formula), (VALUE, value), (COMMENT, p.comment)):
                item = QTableWidgetItem(text)
                if column == VALUE:
                    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if error and column in (FORMULA, VALUE):
                    item.setForeground(QBrush(ERROR_COLOR))
                    item.setToolTip(error)
                self.table.setItem(row, column, item)
        self._filling = False

    def row_of(self, name):
        return self._names.index(name)

    def selected_names(self):
        rows = sorted({index.row() for index in self.table.selectionModel().selectedRows()})
        return [self._names[r] for r in rows]

    def _item_changed(self, item):
        if self._filling or item.column() == VALUE:
            return
        # After the editor has closed: the window refills this table in response.
        name, column, text = self._names[item.row()], item.column(), item.text()
        QTimer.singleShot(0, lambda: self.edited.emit(name, column, text))
