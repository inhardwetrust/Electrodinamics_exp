# presets_panel.py
#
# The Presets tab (left panel): the model's mesh and monitors presets
# (project/presets.py), one table each. A row is a preset: built-in ones
# first (grey, read-only: Duplicate one to change it), then the model's
# own files in model.presets/<kind>/. Double click a value to edit it, a
# name to rename the file. Editing a preset changes no project: Apply (or
# the tree's right click on Mesh / Far field) copies it in.

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QPushButton, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from project import presets

TITLES = {"mesh": "Mesh", "monitors": "Monitors"}


def _fmt(value):
    if isinstance(value, bool):
        return "on" if value else "off"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


class PresetsPanel(QWidget):
    apply_requested = Signal(str, str)      # kind, preset name
    message = Signal(str)                   # for the status bar

    def __init__(self, parent=None):
        super().__init__(parent)
        self.step_path = None
        self.tables, self.buttons = {}, {}
        layout = QVBoxLayout(self)
        for kind in presets.KINDS:
            layout.addWidget(QLabel(f"<b>{TITLES[kind]} presets</b>"))
            layout.addLayout(self._build_buttons(kind))
            layout.addWidget(self._build_table(kind), 1)
        hint = QLabel(
            "Settings that do not depend on the model, one file each in <i>model</i>.presets/mesh/ "
            "and .../monitors/ (copy a file into another model's folder to take it along). Double "
            "click a value to edit, a name to rename. A preset is a template: Apply copies it into "
            "the project; editing it later changes no project until it is applied again. Monitors: "
            "the far-field frequencies as % from the far field's f0 (0 = the band's center).")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray")
        layout.addWidget(hint)
        self.set_model(None)

    # ---------------------------------------------------------------- building

    def _build_table(self, kind):
        params = presets.PARAMS[kind]
        headers = ["Name"] + [p.label + (f", {p.unit}" if p.unit else "") for p in params] + ["Source"]
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.SingleSelection)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        table.horizontalHeader().setStretchLastSection(True)
        table.itemChanged.connect(lambda item, k=kind: self._cell_changed(k, item))
        table.itemSelectionChanged.connect(lambda k=kind: self._update_buttons(k))
        self.tables[kind] = table
        return table

    def _build_buttons(self, kind):
        row = QHBoxLayout()
        buttons = {
            "new": QPushButton("Save current"),
            "duplicate": QPushButton("Duplicate"),
            "delete": QPushButton("Delete"),
            "apply": QPushButton("Apply"),
        }
        buttons["new"].setToolTip("A new preset of this model from the project's current settings")
        buttons["apply"].setToolTip("Copy the selected preset into the project")
        buttons["new"].clicked.connect(lambda _=False, k=kind: self.save_current(k))
        buttons["duplicate"].clicked.connect(lambda _=False, k=kind: self._duplicate(k))
        buttons["delete"].clicked.connect(lambda _=False, k=kind: self._delete(k))
        buttons["apply"].clicked.connect(lambda _=False, k=kind: self._apply(k))
        for button in buttons.values():
            row.addWidget(button)
        row.addStretch(1)
        self.buttons[kind] = buttons
        return row

    # ---------------------------------------------------------------- state

    def set_model(self, step_path, project=None):
        """The open model (None: nothing to edit); refills both tables."""
        self.step_path = step_path
        self.project = project
        self.refresh()

    def refresh(self, select=None):
        """Re-reads the preset files (another program may have copied one in)."""
        for kind in presets.KINDS:
            self._fill(kind, select.get(kind) if select else None)

    def _fill(self, kind, select=None):
        table = self.tables[kind]
        if select is None:
            select = self.selected(kind)
        rows = presets.all_presets(self.step_path, kind) if self.step_path else []
        errors = presets.load_presets(self.step_path, kind)[1] if self.step_path else []
        params = presets.PARAMS[kind]
        table.blockSignals(True)
        table.setRowCount(len(rows))
        for r, (name, values, source) in enumerate(rows):
            own = source == "model"
            cells = [name] + [_fmt(values[p.name]) for p in params] + [source]
            for c, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setData(Qt.UserRole, (name, None if c == 0 else
                                           params[c - 1].name if c <= len(params) else "source"))
                if not own or c > len(params):
                    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                    item.setForeground(QColor("gray"))
                if 0 < c <= len(params):
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                table.setItem(r, c, item)
            if name == select:
                table.selectRow(r)
        table.blockSignals(False)
        self._update_buttons(kind)
        if errors:
            self.message.emit("presets not read: " + "; ".join(errors))

    def selected(self, kind):
        """(name, source) of the selected row, or None."""
        table = self.tables[kind]
        rows = table.selectionModel().selectedRows() if table.selectionModel() else []
        if not rows:
            return None
        item = table.item(rows[0].row(), 0)
        return item.data(Qt.UserRole)[0] if item else None

    def _source(self, kind, name):
        for n, _, source in presets.all_presets(self.step_path, kind):
            if n == name:
                return source
        return None

    def _update_buttons(self, kind):
        name = self.selected(kind)
        has_model = self.step_path is not None and self.project is not None
        b = self.buttons[kind]
        b["new"].setEnabled(has_model)
        b["duplicate"].setEnabled(has_model and name is not None)
        b["delete"].setEnabled(has_model and name is not None and self._source(kind, name) == "model")
        b["apply"].setEnabled(has_model and name is not None)

    # ---------------------------------------------------------------- edits

    def _run(self, kind, action, select=None):
        """A file edit: an error goes to the status bar and the table shows the files again."""
        try:
            result = action()
        except (presets.PresetError, OSError) as error:
            self.message.emit(str(error))
            self._fill(kind)
            return None
        self._fill(kind, select if select is not None else result)
        return result

    def save_current(self, kind):
        """A new model preset from the project's settings; its name cell opens for typing."""
        if self.project is None:
            return None

        def make():
            values = presets.from_project(self.project, kind)
            name = presets.unique_name(self.step_path, kind, f"My {kind}")
            presets.save_preset(self.step_path, kind, name, values)
            return name

        name = self._run(kind, make)
        if name is not None:
            self.edit_name(kind, name)
        return name

    def edit_name(self, kind, name):
        table = self.tables[kind]
        for r in range(table.rowCount()):
            if table.item(r, 0).data(Qt.UserRole)[0] == name:
                table.setFocus()
                table.selectRow(r)
                table.editItem(table.item(r, 0))

    def _duplicate(self, kind):
        name = self.selected(kind)
        values = dict(next(v for n, v, _ in presets.all_presets(self.step_path, kind) if n == name))

        def make():
            new = presets.unique_name(self.step_path, kind, f"{name} copy")
            presets.save_preset(self.step_path, kind, new, values)
            return new
        self._run(kind, make)

    def _delete(self, kind):
        name = self.selected(kind)
        self._run(kind, lambda: presets.delete_preset(self.step_path, kind, name), select="")

    def _apply(self, kind):
        name = self.selected(kind)
        if name is not None:
            self.apply_requested.emit(kind, name)

    def _cell_changed(self, kind, item):
        name, param = item.data(Qt.UserRole)
        text = item.text().strip()
        if param is None:
            self._run(kind, lambda: presets.rename_preset(self.step_path, kind, name, text))
        else:
            if not any(p.name == param and p.numbers for p in presets.PARAMS[kind]):
                text = text.replace(",", ".")       # a decimal comma
            self._run(kind, lambda: (presets.set_value(self.step_path, kind, name, param, text), name)[1])
