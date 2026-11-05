# parameters_panel.py
#
# The parameter table (Inventor's fx Parameters, CST's parameter list):
# Name | Formula | Value | Comment, in table order, as a tree: a parameter
# with fields (mycenter) is a row with its fields under it (x, y: each its
# own formula and value, read in formulas as mycenter.x); its own row shows
# the fields' values together, "(5, 2)".
# - double click a Name, Formula or Comment cell to edit it (a parameter
#   with fields has no formula of its own; a field has no comment); Value is
#   the result of the last rebuild (mm or deg), or "error" in red with the
#   reason as its tooltip;
# - Add appends a free name (p1, p2...) = 0; "With fields" one with fields
#   (Point 2D: x, y; Point 3D: x, y, z; none); Add Field a field to the
#   selected one; Delete removes the selected rows (one in use is refused
#   by the window with its users named).
# - right click: Color... (a picker) tints the selected parameters' rows
#   (with their fields), Clear Color takes it off: the user marks the
#   inputs, the intermediate ones stay white.
# Rows are known by name: a field's is "mycenter.x". The panel only shows
# and reports: the window applies the edits to the document (undo, rebuild)
# and calls show_parameters() again.

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QHeaderView, QMenu, QPushButton, QStyledItemDelegate, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)

COLUMNS = ("Name", "Formula", "Value", "Comment")
NAME, FORMULA, VALUE, COMMENT = range(4)
ERROR_COLOR = QColor(200, 30, 30)
PRESETS = (("Point 2D (x, y)", ("x", "y")), ("Point 3D (x, y, z)", ("x", "y", "z")), ("No fields yet", ()))
KEY = Qt.UserRole                   # the row's name
EDITABLE = Qt.UserRole + 1          # the columns its editor opens in
HAS_FIELDS = Qt.UserRole + 2        # a parameter with fields' own row


class ParametersPanel(QWidget):
    edited = Signal(str, int, str)          # row name ("w", "mycenter", "mycenter.x"), column, new text
    add_requested = Signal()
    add_fields_requested = Signal(list)     # a new parameter with these fields
    add_field_requested = Signal(str)       # a new field in this parameter
    delete_requested = Signal(list)         # row names
    color_requested = Signal(list)          # row names: pick a color for their parameters
    color_cleared = Signal(list)            # row names: their parameters back to no color

    def __init__(self):
        super().__init__()
        self.tree = QTreeWidget()
        self.tree.setColumnCount(len(COLUMNS))
        self.tree.setHeaderLabels(COLUMNS)
        self.tree.setRootIsDecorated(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        self.tree.setItemDelegate(_Delegate(self.tree))
        header = self.tree.header()
        header.setSectionResizeMode(NAME, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(FORMULA, QHeaderView.Stretch)
        header.setSectionResizeMode(VALUE, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(COMMENT, QHeaderView.Stretch)
        self.tree.itemChanged.connect(self._item_changed)
        self.tree.itemSelectionChanged.connect(self._selection_changed)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context_menu)
        self.tree.itemCollapsed.connect(lambda item: self._collapsed.add(item.data(NAME, KEY)))
        self.tree.itemExpanded.connect(lambda item: self._collapsed.discard(item.data(NAME, KEY)))

        add = QPushButton("Add")
        add.setToolTip("A new parameter (p1, p2...) = 0")
        add.clicked.connect(self.add_requested.emit)
        with_fields = QPushButton("With Fields")
        with_fields.setToolTip("A new parameter with fields, read in formulas as p1.x, p1.y...")
        menu = QMenu(with_fields)
        for label, fields in PRESETS:
            menu.addAction(label, lambda fields=fields: self.add_fields_requested.emit(list(fields)))
        with_fields.setMenu(menu)
        self.add_field = QPushButton("Add Field")
        self.add_field.setToolTip("A new field in the selected parameter with fields")
        self.add_field.clicked.connect(self._add_field)
        delete = QPushButton("Delete")
        delete.setToolTip("Delete the selected parameters and fields (only the ones nothing uses)")
        delete.clicked.connect(lambda: self.delete_requested.emit(self.selected_names()))
        buttons = QHBoxLayout()
        for button in (add, with_fields, self.add_field, delete):
            buttons.addWidget(button)
        buttons.addStretch(1)

        layout = QVBoxLayout(self)
        layout.addWidget(self.tree)
        layout.addLayout(buttons)
        self._items = {}                    # row name -> item
        self._collapsed = set()             # parameters with fields the user folded
        self._filling = False
        self._selection_changed()

    def show_parameters(self, parameters, values, errors):
        """parameters: [Parameter]; values / errors: from the rebuild (a field's as "name.field")."""
        self._filling = True
        selected = set(self.selected_names())
        self.tree.clear()
        self._items = {}
        for p in parameters:
            if p.has_fields:
                rows = [(f"{p.name}.{f}", f, text) for f, text in p.fields.items()]
                bad = [errors[r] for r, _, _ in rows if r in errors]
                value = "error" if bad else "(" + ", ".join(f"{values[r]:.6g}" for r, _, _ in rows) + ")"
                item = self._row(None, p.name, p.name, "", value, p.comment, "; ".join(bad), editable=(NAME, COMMENT))
                item.setData(NAME, HAS_FIELDS, True)
                for row, field_, text in rows:
                    self._row(item, row, field_, text, _value(row, values, errors), "", errors.get(row),
                              editable=(NAME, FORMULA))
                item.setExpanded(p.name not in self._collapsed)
                _tint(item, p.color)
                for i in range(item.childCount()):
                    _tint(item.child(i), p.color)
            else:
                item = self._row(None, p.name, p.name, p.formula, _value(p.name, values, errors), p.comment,
                                 errors.get(p.name), editable=(NAME, FORMULA, COMMENT))
                _tint(item, p.color)
        for name in selected & set(self._items):
            self._items[name].setSelected(True)
        self._filling = False
        self._selection_changed()

    def _row(self, parent, key, name, formula, value, comment, error, editable):
        item = QTreeWidgetItem([name, formula, value, comment])
        item.setData(NAME, KEY, key)
        item.setFlags(item.flags() | Qt.ItemIsEditable)
        item.setData(VALUE, Qt.TextAlignmentRole, int(Qt.AlignRight | Qt.AlignVCenter))
        item.setData(NAME, EDITABLE, list(editable))
        if error:
            for column in (FORMULA, VALUE):
                item.setForeground(column, QBrush(ERROR_COLOR))
                item.setToolTip(column, error)
        if parent is None:
            self.tree.addTopLevelItem(item)
        else:
            parent.addChild(item)
        self._items[key] = item
        return item

    def item_of(self, name):
        """The row of a parameter or field ("mycenter.x"), or None."""
        return self._items.get(name)

    def edit(self, name, column=NAME):
        """Opens the editor of a row's cell (a new parameter's name)."""
        item = self._items[name]
        self.tree.setCurrentItem(item, column)
        self.tree.editItem(item, column)

    def selected_names(self):
        return [item.data(NAME, KEY) for item in self.tree.selectedItems()]

    def _owner(self):
        """The parameter with fields the selection is in (its row or a field's), or None."""
        for item in self.tree.selectedItems():
            top = item.parent() or item
            if top.data(NAME, HAS_FIELDS):
                return top.data(NAME, KEY)
        return None

    def context_actions(self, item):
        """[(text, callable)] of the right-click menu on a row (its selection: the row itself if outside it)."""
        if item is None:
            return []
        if not item.isSelected():
            self.tree.clearSelection()
            item.setSelected(True)
        names = self.selected_names()
        actions = [("Color...", lambda: self.color_requested.emit(names))]
        if any(self._items[n].background(NAME).style() != Qt.NoBrush for n in names):
            actions.append(("Clear Color", lambda: self.color_cleared.emit(names)))
        return actions

    def _context_menu(self, pos):
        actions = self.context_actions(self.tree.itemAt(pos))
        if actions:
            menu = QMenu(self.tree)
            for text, call in actions:
                menu.addAction(text, call)
            menu.exec(self.tree.viewport().mapToGlobal(pos))

    def _selection_changed(self):
        self.add_field.setEnabled(self._owner() is not None)

    def _add_field(self):
        owner = self._owner()
        if owner is not None:
            self.add_field_requested.emit(owner)

    def _item_changed(self, item, column):
        if self._filling or column not in item.data(NAME, EDITABLE):
            return
        # After the editor has closed: the window refills this tree in response.
        name, text = item.data(NAME, KEY), item.text(column)
        QTimer.singleShot(0, lambda: self.edited.emit(name, column, text))


class _Delegate(QStyledItemDelegate):
    """Editors only in a row's editable cells (a parameter with fields has no formula, a field no comment)."""

    def createEditor(self, parent, option, index):
        editable = index.siblingAtColumn(NAME).data(EDITABLE) or ()
        return super().createEditor(parent, option, index) if index.column() in editable else None


def _tint(item, color):
    """The row's background in the parameter's color (text dark or light to read on it)."""
    if not color:
        return
    background = QColor(color)
    text = QColor(0, 0, 0) if background.lightnessF() > 0.5 else QColor(255, 255, 255)
    for column in range(len(COLUMNS)):
        item.setBackground(column, QBrush(background))
        if item.foreground(column).color() != ERROR_COLOR:
            item.setForeground(column, QBrush(text))


def _value(name, values, errors):
    return "error" if name in errors else f"{values[name]:.6g}" if name in values else ""
