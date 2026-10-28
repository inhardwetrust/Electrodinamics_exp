# main_window.py
#
# Qt shell: the browser on the left (a dock with tabs: "Model", the part
# tree, and "Parameters"), the 3D view in the middle, a toolbar, the status
# bar.
#
# The document (model/document.py) is the part being edited and its file.
# Every edit goes: change doc.part -> doc.commit() (one undo step) ->
# rebuild (model/rebuild.py) -> show the result. Undo / redo replace
# doc.part, so nothing here keeps a part object across an edit.
#
# Files: New, Open, Recent, Save, Save As (`name.part.toml`). Leaving a
# modified part (New, Open, close) asks to save it; a file changed on disk by
# another program since it was opened is not overwritten without asking. A
# file that fails to read is not opened (and so never overwritten).
#
# The browser, as in Inventor:
#   <part name>       the file's name, or Part1
#     Origin          YZ / XZ / XY Plane, X / Y / Z Axis, Center Point (hidden at first)
#     Solid Bodies    (only when the rebuild made bodies)
# - a row's checkbox shows or hides its datum (saved with the part) or body;
# - hovering a row pre-highlights its datum in 3D (green, even when hidden),
#   and hovering a datum in 3D marks its row;
# - selecting a row selects its datum or body in 3D and the other way
#   round; the numbers of the selected item are shown under the tree.
#
# A click in 3D selects; the Measure button switches to picking points A / B
# (view/geometry_view.py), shown with the distance in the status bar.
#
# Dialogs go through ask_* methods, so the checks can answer them.
# The window's size and the recent files are app settings (app_settings.py,
# MYINVENTOR_SETTINGS for tests).

import os

from PySide6.QtCore import QEvent, QRect, Qt
from PySide6.QtGui import QBrush, QColor, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (
    QCheckBox, QDockWidget, QFileDialog, QLabel, QMainWindow, QMenu, QMessageBox, QTabWidget,
    QToolBar, QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from app_settings import AppSettings
from geometry.body import Geometry
from model.document import Document
from model.part import PartError
from model.part_file import EXTENSION, PartFileError
from model.rebuild import rebuild
from view.geometry_view import GeometryView
from view.parameters_panel import COMMENT, FORMULA, NAME, ParametersPanel
from view.view_buttons import ViewButtons

MM = 1e-3
HOVER_ROW = QColor(60, 200, 90, 70)          # the row of the datum hovered in 3D
HINT = ("Left drag: rotate · Shift / Space + drag: pan · wheel: zoom · "
        "click: select, Esc: clear")
MEASURE_HINT = "Measure: click: point A, Shift + click: point B, Esc: clear the points"
FILE_FILTER = f"myinventor part (*{EXTENSION});;All files (*)"


def _fmt_mm(value):
    """A length in m -> mm text, to the micrometer, trailing zeros dropped."""
    text = f"{value / MM:.3f}".rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def _fmt_point(values):
    return "(" + ", ".join(_fmt_mm(v) for v in values) + ")"


def _fmt_vector(values):
    return "(" + ", ".join(f"{v + 0.0:.6g}" for v in values) + ")"     # + 0.0: no "-0"


def _group_item(parent, text):
    """A bold tree row that holds others (the part, Origin, Solid Bodies)."""
    item = QTreeWidgetItem([text])
    font = item.font(0)
    font.setBold(True)
    item.setFont(0, font)
    if parent is not None:
        parent.addChild(item)
    return item


def _checkable(parent, text, key, tooltip, checked=True):
    item = QTreeWidgetItem([text])
    item.setData(0, Qt.UserRole, key)
    item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
    item.setCheckState(0, Qt.Checked if checked else Qt.Unchecked)
    item.setToolTip(0, tooltip)
    parent.addChild(item)
    return item


class MainWindow(QMainWindow):
    def __init__(self, path=None):
        super().__init__()
        self.resize(1400, 900)
        self.app_settings = AppSettings.load()

        self.doc = Document()
        self.result = None          # model.rebuild.Result of doc.part
        self._rows = {}             # key -> tree row
        self._hover_row = None
        self.view = GeometryView()
        self.view.on_pick = self._on_pick
        self.view.on_select = self._on_view_select
        self.view.on_hover = self._on_view_hover
        self.setCentralWidget(self.view.canvas.native)
        self.view_buttons = ViewButtons(self.view)        # standard views over the 3D view

        self._build_toolbar()
        self._build_browser()
        self._set_document(Document())
        if path:
            self.open_file(path)
        if self.app_settings.error:
            self.statusBar().showMessage(f"settings not read: {self.app_settings.error}", 10000)

    # =====================================================
    # Layout
    # =====================================================

    def _build_toolbar(self):
        bar = QToolBar("Main")
        bar.setMovable(False)
        self.addToolBar(bar)

        def action(text, slot, shortcut=None, tip=""):
            a = bar.addAction(text)
            a.triggered.connect(lambda _=False: slot())
            if shortcut:
                a.setShortcut(QKeySequence(shortcut))
            a.setToolTip(f"{tip or text} ({QKeySequence(shortcut).toString()})" if shortcut else tip or text)
            return a

        action("New", self.new_part, QKeySequence.New, "A new part")
        action("Open...", self.open_dialog, QKeySequence.Open, "Open a part file")
        self.recent_menu = QMenu(self)
        self.recent_menu.aboutToShow.connect(self._fill_recent_menu)
        recent = QToolButton()
        recent.setText("Recent")
        recent.setMenu(self.recent_menu)
        recent.setPopupMode(QToolButton.InstantPopup)
        bar.addWidget(recent)
        action("Save", self.save, QKeySequence.Save, "Save the part")
        action("Save As...", self.save_as, QKeySequence.SaveAs, "Save the part to another file")
        bar.addSeparator()
        self.undo_action = action("Undo", self.undo, QKeySequence.Undo)
        self.redo_action = action("Redo", self.redo, "Ctrl+Y")
        bar.addSeparator()

        bar.addAction("Reset view", self.view.reset_view)
        self.measure_action = bar.addAction("Measure")
        self.measure_action.setCheckable(True)
        self.measure_action.toggled.connect(self._set_measure)
        self.measure_action.setToolTip("Measure: click picks point A, Shift + click point B")
        bar.addSeparator()

        self.edges_check = QCheckBox("Edges")
        self.edges_check.setChecked(True)
        self.edges_check.toggled.connect(self.view.set_show_edges)
        bar.addWidget(self.edges_check)

        self.xray_check = QCheckBox("X-ray")
        self.xray_check.toggled.connect(self.view.set_xray)
        bar.addWidget(self.xray_check)

        self.box_check = QCheckBox("Bounding box")
        self.box_check.toggled.connect(self.view.set_show_box)
        bar.addWidget(self.box_check)

    def _build_browser(self):
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setMouseTracking(True)                  # itemEntered: hover
        self.tree.itemChanged.connect(self._item_changed)
        self.tree.currentItemChanged.connect(lambda item, _: self._item_selected(item))
        self.tree.itemEntered.connect(lambda item, _: self._row_hovered(item))
        self.tree.viewport().installEventFilter(self)     # Leave: hover off

        self.info = QLabel()
        self.info.setWordWrap(True)
        self.info.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.info.setTextInteractionFlags(Qt.TextSelectableByMouse)

        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.addWidget(self.tree, 3)
        layout.addWidget(self.info, 1)

        self.parameters = ParametersPanel()
        self.parameters.edited.connect(self._parameter_edited)
        self.parameters.add_requested.connect(self._add_parameter)
        self.parameters.delete_requested.connect(self._delete_parameters)

        self.side_tabs = QTabWidget()
        self.side_tabs.setDocumentMode(True)
        self.side_tabs.addTab(panel, "Model")
        self.side_tabs.addTab(self.parameters, "Parameters")

        dock = QDockWidget()
        dock.setTitleBarWidget(QWidget())       # the tabs are the title
        dock.setFeatures(QDockWidget.DockWidgetMovable)
        dock.setWidget(self.side_tabs)
        dock.setMinimumWidth(340)
        self.addDockWidget(Qt.LeftDockWidgetArea, dock)

    def eventFilter(self, watched, event):
        if watched is self.tree.viewport() and event.type() == QEvent.Leave:
            self._row_hovered(None)
        return False

    # =====================================================
    # Document
    # =====================================================

    @property
    def part(self):
        return self.doc.part

    def _set_document(self, doc):
        self.doc = doc
        self.view.set_picks([None, None])
        self._refresh(reset_view=True)

    def _refresh(self, reset_view=False):
        """Rebuild doc.part and show everything again (after an edit, undo, open)."""
        self.show_result(rebuild(self.part), reset_view)

    def show_result(self, result, reset_view=False):
        self.result = result
        self.view.set_datums(self.part.origin)
        self.view.set_geometry(Geometry(result.bodies), reset=reset_view)
        self._fill_tree()
        self.parameters.show_parameters(self.part.parameters, result.values, result.errors)
        self._update_title()
        self._on_pick(self.view.picks)

    def _commit(self, refresh=True):
        """An edit of doc.part is done: one undo step, then rebuild and show."""
        self.doc.commit()
        if refresh:
            self._refresh()
        else:
            self._update_title()

    def _update_title(self):
        self.setWindowTitle(f"{self.doc.name}{'*' if self.doc.modified else ''} — myinventor")
        self.undo_action.setEnabled(self.doc.can_undo)
        self.redo_action.setEnabled(self.doc.can_redo)

    def undo(self):
        if self.doc.undo():
            self._refresh()

    def redo(self):
        if self.doc.redo():
            self._refresh()

    # =====================================================
    # Files
    # =====================================================

    def ask_save_changes(self):
        """'save', 'discard' or 'cancel' for a modified part about to be left."""
        answer = QMessageBox.question(
            self, "myinventor", f"Save the changes to {self.doc.name}?",
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel, QMessageBox.Save)
        return {QMessageBox.Save: "save", QMessageBox.Discard: "discard"}.get(answer, "cancel")

    def ask_open_path(self):
        start = os.path.dirname(self.doc.path) if self.doc.path else ""
        path, _ = QFileDialog.getOpenFileName(self, "Open part", start, FILE_FILTER)
        return path or None

    def ask_save_path(self):
        start = self.doc.path or self.doc.name + EXTENSION
        path, _ = QFileDialog.getSaveFileName(self, "Save part", start, FILE_FILTER)
        if path and not path.lower().endswith(EXTENSION):
            path = os.path.splitext(path)[0] + EXTENSION if path.lower().endswith(".toml") else path + EXTENSION
        return path or None

    def ask_overwrite_changed(self):
        answer = QMessageBox.question(
            self, "myinventor", f"{self.doc.path}\nchanged on disk since it was opened. Overwrite it?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        return answer == QMessageBox.Yes

    def show_error(self, text):
        QMessageBox.critical(self, "myinventor", text)

    def _may_leave(self):
        """True if the current part may be replaced or closed (saved or discarded)."""
        if not self.doc.modified:
            return True
        answer = self.ask_save_changes()
        if answer == "save":
            return self.save()
        return answer == "discard"

    def new_part(self):
        if self._may_leave():
            self._set_document(Document())

    def open_dialog(self):
        if not self._may_leave():
            return
        path = self.ask_open_path()
        if path:
            self.open_file(path, ask=False)

    def open_file(self, path, ask=True):
        """Opens a part file; on an error the current part stays. Returns True if opened."""
        if ask and not self._may_leave():
            return False
        try:
            doc = Document.open(path)
        except PartFileError as error:
            self.show_error(f"Cannot open the part:\n{error}")
            return False
        self._set_document(doc)
        self._remember(doc.path)
        return True

    def save(self):
        if self.doc.path is None:
            return self.save_as()
        if self.doc.changed_on_disk() and not self.ask_overwrite_changed():
            return False
        return self._write(self.doc.path)

    def save_as(self):
        path = self.ask_save_path()
        return self._write(path) if path else False

    def _write(self, path):
        try:
            self.doc.save(path)
        except (OSError, PartFileError) as error:
            self.show_error(f"Cannot save the part:\n{error}")
            return False
        self._remember(self.doc.path)
        self._fill_tree()                   # the part's name comes from the file
        self._update_title()
        self.statusBar().showMessage(f"saved {self.doc.path}", 5000)
        return True

    def _remember(self, path):
        self.app_settings.add_recent(path)
        error = self.app_settings.save()
        if error:
            self.statusBar().showMessage(f"settings not saved: {error}", 10000)

    def _fill_recent_menu(self):
        menu = self.recent_menu
        menu.clear()
        files = self.app_settings.recent_files
        if not files:
            menu.addAction("(no recent files)").setEnabled(False)
            return
        for path in files:
            exists = os.path.isfile(path)
            text = f"{os.path.basename(path)}   —   {os.path.dirname(path)}"
            action = menu.addAction(text if exists else f"{text}   (missing)")
            action.setToolTip(path)
            action.setEnabled(exists)
            action.triggered.connect(lambda _=False, p=path: self.open_file(p))
        menu.addSeparator()
        menu.addAction("Remove missing", self._forget_missing)
        menu.addAction("Clear list", self._clear_recent)

    def _forget_missing(self):
        for path in [p for p in self.app_settings.recent_files if not os.path.isfile(p)]:
            self.app_settings.remove_recent(path)
        self.app_settings.save()

    def _clear_recent(self):
        self.app_settings.recent_files = []
        self.app_settings.save()

    # =====================================================
    # Parameters
    # =====================================================

    def _parameter_edited(self, name, column, text):
        try:
            if column == NAME:
                self.part.rename_parameter(name, text.strip())
            elif column == FORMULA:
                self.part.parameter(name).formula = text.strip()
            elif column == COMMENT:
                self.part.parameter(name).comment = text
        except PartError as error:
            self.doc.revert()
            self._refresh()                     # puts the old text back in the cell
            self.statusBar().showMessage(str(error), 10000)
            return
        self._commit()

    def _add_parameter(self):
        name = self.part.add_parameter()
        self._commit()
        row = self.parameters.row_of(name)
        self.parameters.table.setCurrentCell(row, NAME)
        self.parameters.table.editItem(self.parameters.table.item(row, NAME))

    def _delete_parameters(self, names):
        try:
            for name in names:
                self.part.delete_parameter(name)
        except PartError as error:
            self.doc.revert()
            self.show_error(f"Cannot delete: {error}")
            return
        self._commit()

    # =====================================================
    # Browser
    # =====================================================

    def _fill_tree(self):
        self.tree.blockSignals(True)
        self.tree.clear()
        self._rows = {}
        self._hover_row = None
        root = _group_item(None, self.doc.name)
        self.tree.addTopLevelItem(root)
        origin = _group_item(root, "Origin")
        for datum in self.part.origin:
            key = ("datum", datum.name)
            self._rows[key] = _checkable(origin, datum.name, key,
                                         "Tick: shown in 3D. Hover: highlighted. Select: selected.",
                                         datum.visible)
        bodies = self.result.bodies if self.result is not None else []
        if bodies:
            group = _group_item(root, f"Solid Bodies ({len(bodies)})")
            for body in bodies:
                key = ("body", body.name)
                self._rows[key] = _checkable(group, body.name, key, "Tick: shown in 3D. Select: selected.",
                                             self.view._visible.get(body.name, True))
        root.setExpanded(True)
        for i in range(root.childCount()):
            root.child(i).setExpanded(True)
        selected = self._rows.get(self.view.selected)
        if selected is not None:
            self.tree.setCurrentItem(selected)
        else:
            self.view.select(None)
        self.tree.blockSignals(False)
        self._show_info(self.view.selected)

    @staticmethod
    def _key_of(item):
        return item.data(0, Qt.UserRole) if item is not None else None

    def _item_changed(self, item, column):
        key = self._key_of(item)
        if key is None:
            return
        on = item.checkState(0) == Qt.Checked
        if key[0] == "datum":
            self.part.datum(key[1]).visible = on          # saved with the part, one undo step
            self.view.set_datum_visible(key[1], on)
            self._commit(refresh=False)
        else:
            self.view.set_visible(key[1], on)

    def _item_selected(self, item):
        key = self._key_of(item)
        self.view.select(key)
        self._show_info(key)

    def _row_hovered(self, item):
        key = self._key_of(item)
        self.view.set_hover(key if key is not None and key[0] == "datum" else None)

    def _on_view_select(self, key):
        """A click in 3D selected `key` (or nothing): follow it in the tree."""
        self.tree.blockSignals(True)
        if key is None:
            self.tree.setCurrentItem(None)
            self.tree.clearSelection()
        else:
            self.tree.setCurrentItem(self._rows[key])
        self.tree.blockSignals(False)
        self._show_info(key)

    def _on_view_hover(self, key):
        """The mouse moved onto (or off) a datum in 3D: mark its row."""
        if self._hover_row is not None:
            self._hover_row.setBackground(0, QBrush())
        self._hover_row = self._rows.get(key)
        if self._hover_row is not None:
            self._hover_row.setBackground(0, HOVER_ROW)

    def _show_info(self, key):
        if key is None:
            self.info.setText("")
        elif key[0] == "datum":
            self.info.setText(self._datum_info(self.part.datum(key[1])))
        else:
            body = next(b for b in self.result.bodies if b.name == key[1])
            self.info.setText(self._body_info(body))

    @staticmethod
    def _datum_info(datum):
        lines = [f"<b>{datum.name}</b> (work {datum.kind}, origin)"]
        if datum.kind == "plane":
            lines += [f"through {_fmt_point(datum.origin)} mm", f"normal {_fmt_vector(datum.direction)}",
                      f"in-plane axes u {_fmt_vector(datum.u)}, v {_fmt_vector(datum.v)}"]
        elif datum.kind == "axis":
            lines += [f"through {_fmt_point(datum.origin)} mm", f"direction {_fmt_vector(datum.direction)}"]
        else:
            lines.append(f"at {_fmt_point(datum.origin)} mm")
        return "<br>".join(lines)

    @staticmethod
    def _body_info(body):
        lo, hi = body.bounds
        lines = [
            f"<b>{body.name}</b> ({body.kind})",
            f"size: {' x '.join(_fmt_mm(v) for v in hi - lo)} mm",
            f"from {_fmt_point(lo)} to {_fmt_point(hi)} mm",
            f"faces: {body.face_count}, edges (segments): {len(body.edges)}, vertices: {len(body.corners)}",
        ]
        if body.kind == "solid":
            lines.append(f"volume: {body.volume / MM ** 3:.6g} mm³")
        lines.append(f"area: {body.area / MM ** 2:.6g} mm²")
        return "<br>".join(lines)

    # =====================================================
    # Measure
    # =====================================================

    def _set_measure(self, on):
        self.view.set_measure(on)
        self._on_pick(self.view.picks)

    def _on_pick(self, picks):
        parts = []
        for label, pick in zip("AB", picks):
            if pick is not None:
                where = f"face {pick.face}" if pick.kind == "face" else pick.kind
                parts.append(f"{label}: {where} of {pick.body} {_fmt_point(pick.point * MM)} mm")
        a, b = picks
        if a is not None and b is not None:
            d = (b.point - a.point) * MM
            parts.append(f"|AB| = {_fmt_mm(float((d ** 2).sum()) ** 0.5)} mm "
                         f"(dx {_fmt_mm(d[0])}, dy {_fmt_mm(d[1])}, dz {_fmt_mm(d[2])})")
        hint = MEASURE_HINT if self.view.measure else HINT
        self.statusBar().showMessage("   ".join(parts) if parts else hint)

    # =====================================================
    # Window state
    # =====================================================

    def show_restored(self):
        """Shows the window with the size, position and maximized state it was closed with."""
        saved = self.app_settings.window
        if saved is None:
            self.show()
            return
        rect = QRect(saved["x"], saved["y"], saved["width"], saved["height"])
        if QGuiApplication.screenAt(rect.center()) is None:
            # That monitor is gone: same size (if it fits), centered on the primary one.
            available = QGuiApplication.primaryScreen().availableGeometry()
            rect.setSize(rect.size().boundedTo(available.size()))
            rect.moveCenter(available.center())
        self.setGeometry(rect)
        if saved["maximized"]:
            self.showMaximized()
        else:
            self.show()

    def closeEvent(self, event):
        if not self._may_leave():
            event.ignore()
            return
        normal = self.normalGeometry() if self.isMaximized() else self.geometry()
        self.app_settings.window = {
            "x": normal.x(), "y": normal.y(), "width": normal.width(), "height": normal.height(),
            "maximized": self.isMaximized(),
        }
        self.app_settings.save()            # an error here must not keep the window open
        super().closeEvent(event)
