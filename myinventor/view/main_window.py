# main_window.py
#
# Qt shell: the ribbon on top (view/ribbon.py: File, 3D Model, Sketch; Undo
# and Redo at its right), the browser on the left (a dock with tabs:
# "Model", the part tree, and "Parameters"), the 3D view in the middle, the
# status bar.
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
#     Sketch1 ...     the features in history order (red: failed, the reason as tooltip)
#     Solid Bodies    (only when the rebuild made bodies)
# - a row's checkbox shows or hides its datum (saved with the part) or body;
# - hovering a row pre-highlights its datum or sketch in 3D (green, a hidden
#   datum too), and hovering one in 3D marks its row (an empty sketch shows
#   as its origin point);
# - selecting a row selects its datum or body in 3D and the other way
#   round; the numbers of the selected item are shown under the tree.
#
# Right click in the browser: New Sketch on a plane; Edit Sketch and Delete
# on a sketch (double click edits it too).
#
# Sketches, as in Inventor:
# - New Sketch on a plane, or 3D Model > Start 2D Sketch: with a plane
#   selected it starts there, else the origin planes are shown and a click on
#   one (in 3D or in the browser) starts it; Esc cancels;
# - sketch mode: the Sketch tab, the camera looking at the plane, the
#   sketch's endless axes; Finish Sketch leaves it (Look At looks again). Undo past
#   the sketch's creation, New and Open leave it too;
# - in sketch mode a click selects a line or a point of the sketch; Sketch >
#   Line draws polylines (view/sketch_tools.py: a click per point, a preview
#   of the next line; Esc ends the chain, a second Esc the tool); every line
#   is one undo step; Sketch > Point puts explicit points (a dot in a
#   gapped cross, as Inventor draws them);
# - several sketch entities can be selected (Shift + click, a box: see
#   view/geometry_view.py); a click on a point takes the points at the same
#   place with it;
# - a left drag moves the selected sketch points and lines (a coordinate
#   given by a formula stays; points at one place take the alignment hints,
#   view/sketch_tools.py align()); one undo step, Esc while dragging puts it
#   back;
# - Delete deletes the selection: a sketch line (with its end points no
#   other line uses) or point (with its lines), or a feature nothing uses.
#
# A click in 3D selects; the Measure button switches to picking points A / B
# (view/geometry_view.py), shown with the distance in the status bar.
#
# Dialogs go through ask_* methods, so the checks can answer them.
# The window's size and the recent files are app settings (app_settings.py,
# MYINVENTOR_SETTINGS for tests).

import math
import os

import numpy as np

from PySide6.QtCore import QEvent, QRect, Qt
from PySide6.QtGui import QAction, QBrush, QColor, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (
    QCheckBox, QDockWidget, QFileDialog, QLabel, QMainWindow, QMenu, QMessageBox, QTabWidget,
    QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from app_settings import AppSettings
from geometry.body import Geometry
from model.document import Document
from model.part import PartError
from model.part_file import EXTENSION, PartFileError
from model.rebuild import rebuild
from model.sketch import Sketch
from view.sketch_tools import ALIGN_PX, PointTool, PolylineTool, align, fmt_mm
from view.geometry_view import GeometryView
from view.parameters_panel import COMMENT, FORMULA, NAME, ParametersPanel
from view.ribbon import Ribbon
from view.view_buttons import ViewButtons

MM = 1e-3
HOVER_ROW = QColor(60, 200, 90, 70)          # the row of the datum hovered in 3D
HINT = ("Middle drag: rotate · Ctrl + middle drag: pan · wheel: zoom · "
        "click: select, Esc: clear")
MEASURE_HINT = "Measure: click: point A, Shift + click: point B, Esc: clear the points"
LINE_HINT = ("Line: click points; click the first point to close, the last one to stop · "
             "Esc: end the chain, Esc again: leave Line")
POINT_HINT = "Point: click to put a point · Esc: leave Point"
PICK_PLANE_HINT = "Start 2D Sketch: select a plane (in 3D or in the browser), Esc: cancel"
ERROR_COLOR = QColor(200, 30, 30)
EDITING_ROW = QColor(0, 120, 215, 45)        # the sketch being edited
FILE_FILTER = f"myinventor part (*{EXTENSION});;All files (*)"


def _fmt_mm(value):
    """A length in m -> mm text, to the micrometer, trailing zeros dropped."""
    text = f"{value / MM:.3f}".rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def _fmt_point(values):
    return "(" + ", ".join(_fmt_mm(v) for v in values) + ")"


def _fmt_vector(values):
    return "(" + ", ".join(f"{v + 0.0:.6g}" for v in values) + ")"     # + 0.0: no "-0"


def _number(text):
    """The value of a formula that is a plain number (a point that may be dragged), else None."""
    try:
        value = float(text)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


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
        self.editing = None         # name of the sketch in sketch mode, or None
        self.picking_plane = False  # Start 2D Sketch waits for a plane
        self.view = GeometryView()
        self.view.on_pick = self._on_pick
        self.view.on_select = self._on_view_select
        self.view.on_hover = self._on_view_hover
        self.view.on_escape = self._escape
        self.view.on_delete = self.delete_selected
        self.view.on_drag = self._drag_entity
        self._dragged = None        # {point name: (x0, y0) mm, None where a formula} while dragging
        self.setCentralWidget(self.view.canvas.native)
        self.view_buttons = ViewButtons(self.view)        # standard views over the 3D view

        self._build_ribbon()
        self._build_browser()
        self._set_document(Document())
        if path:
            self.open_file(path)
        if self.app_settings.error:
            self.statusBar().showMessage(f"settings not read: {self.app_settings.error}", 10000)

    # =====================================================
    # Layout
    # =====================================================

    def _action(self, text, slot, shortcut=None, tip="", checkable=False):
        """A window-wide action (its shortcut works anywhere in the window)."""
        action = QAction(text, self)
        action.setCheckable(checkable)
        if checkable:
            action.toggled.connect(slot)
        else:
            action.triggered.connect(lambda _=False: slot())
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
            tip = f"{tip or text} ({action.shortcut().toString()})"
        action.setToolTip(tip or text)
        self.addAction(action)
        return action

    def _build_ribbon(self):
        self.ribbon = Ribbon()
        self.setMenuWidget(self.ribbon)

        file = self.ribbon.add_tab("File")
        group = file.add_group("File")
        group.add_action(self._action("New", self.new_part, QKeySequence.New, "A new part"))
        group.add_action(self._action("Open...", self.open_dialog, QKeySequence.Open, "Open a part file"))
        self.recent_menu = QMenu(self)
        self.recent_menu.aboutToShow.connect(self._fill_recent_menu)
        recent = QAction("Recent", self)
        recent.setMenu(self.recent_menu)
        group.add_action(recent).setPopupMode(QToolButton.InstantPopup)
        group = file.add_group("Save")
        self.save_action = self._action("Save", self.save, QKeySequence.Save, "Save the part")
        group.add_action(self.save_action)
        group.add_action(self._action("Save As...", self.save_as, QKeySequence.SaveAs,
                                      "Save the part to another file"))

        model = self.ribbon.add_tab("3D Model")
        group = model.add_group("Sketch")
        self.start_sketch_action = self._action(
            "Start 2D Sketch", self.start_sketch, tip="A new sketch on the selected plane, or on the plane "
                                                       "you click next")
        group.add_action(self.start_sketch_action)
        group = model.add_group("Inspect")
        self.measure_action = self._action("Measure", self._set_measure, checkable=True,
                                           tip="Measure: click picks point A, Shift + click point B")
        group.add_action(self.measure_action)
        group = model.add_group("View")
        group.add_action(self._action("Reset view", self.view.reset_view, tip="The isometric view of the model"))
        self.edges_check = group.add_widget(QCheckBox("Edges"))
        self.edges_check.setChecked(True)
        self.edges_check.toggled.connect(self.view.set_show_edges)
        self.xray_check = group.add_widget(QCheckBox("X-ray"))
        self.xray_check.toggled.connect(self.view.set_xray)
        self.box_check = group.add_widget(QCheckBox("Bounding box"))
        self.box_check.toggled.connect(self.view.set_show_box)

        sketch = self.ribbon.add_tab("Sketch")
        group = sketch.add_group("Create")
        self.line_action = self._action("Line", self._set_line_tool, checkable=True,
                                        tip="Draw lines: a click per point; Esc ends the chain, "
                                            "a second Esc the tool")
        group.add_action(self.line_action)
        self.point_action = self._action("Point", self._set_point_tool, checkable=True,
                                         tip="Put points: a click per point; Esc leaves the tool")
        group.add_action(self.point_action)
        group = sketch.add_group("View")
        group.add_action(self._action("Look At", self._look_at_sketch, tip="Look at the sketch's plane again"))
        group = sketch.add_group("Exit")
        self.finish_sketch_action = self._action("Finish Sketch", self.finish_sketch,
                                                 tip="Leave the sketch")
        group.add_action(self.finish_sketch_action)
        self.ribbon.set_tab_enabled("Sketch", False)

        self.undo_action = self._action("Undo", self.undo, QKeySequence.Undo)
        self.redo_action = self._action("Redo", self.redo, "Ctrl+Y")
        self.ribbon.add_quick(self.undo_action)
        self.ribbon.add_quick(self.redo_action)
        self.ribbon.show_tab("3D Model")

    def _build_browser(self):
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setMouseTracking(True)                  # itemEntered: hover
        self.tree.itemChanged.connect(self._item_changed)
        self.tree.currentItemChanged.connect(lambda item, _: self._item_selected(item))
        self.tree.itemEntered.connect(lambda item, _: self._row_hovered(item))
        self.tree.itemDoubleClicked.connect(lambda item, _: self._row_double_clicked(item))
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._show_context_menu)
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
        self._stop_picking_plane()
        self.finish_sketch(refresh=False)
        self.doc = doc
        self.view.set_picks([None, None])
        self._refresh(reset_view=True)

    def _refresh(self, reset_view=False):
        """Rebuild doc.part and show everything again (after an edit, undo, open)."""
        self.show_result(rebuild(self.part), reset_view)

    def show_result(self, result, reset_view=False):
        self.result = result
        self.view.set_datums(self.part.origin)
        self.view.set_sketches([(f.name, result.outputs[f.name]) for f in self.part.features
                                if f.TYPE == "sketch" and f.name in result.outputs])
        if self.editing is not None and self.editing in result.outputs:
            self.view.set_sketch(result.outputs[self.editing].frame, self.editing)
        self.view.set_geometry(Geometry(result.bodies), reset=reset_view)
        self._fill_tree()
        self.parameters.show_parameters(self.part.parameters, result.values, result.errors)
        if self.editing is not None:
            if self.editing not in result.outputs:     # undone, deleted, or it fails now
                self.finish_sketch(refresh=False)
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

    # =====================================================
    # Sketches
    # =====================================================

    def start_sketch(self):
        """3D Model > Start 2D Sketch: on the selected plane, or wait for a click on one."""
        selected = self.view.selected
        if selected is not None and selected[0] == "datum" and self.part.datum(selected[1]).kind == "plane":
            self.new_sketch(selected[1])
            return
        self.picking_plane = True
        planes = [d.name for d in self.part.origin if d.kind == "plane"]
        self.view.set_pick_filter({"plane"}, forced=planes)
        self.start_sketch_action.setEnabled(False)
        self.statusBar().showMessage(PICK_PLANE_HINT)

    def _stop_picking_plane(self):
        if self.picking_plane:
            self.picking_plane = False
            self.view.set_pick_filter(None)
            self.start_sketch_action.setEnabled(self.editing is None)
            self._on_pick(self.view.picks)

    def _plane_picked(self, key):
        """While picking a plane: True if `key` was one (the sketch starts on it)."""
        if not self.picking_plane or key is None or key[0] != "datum" \
                or self.part.datum(key[1]).kind != "plane":
            return False
        self._stop_picking_plane()
        self.new_sketch(key[1])
        return True

    def _escape(self):
        """Esc in the 3D view: cancels picking a plane (True: used)."""
        if self.picking_plane:
            self._stop_picking_plane()
            return True
        return False

    def new_sketch(self, plane):
        """A new sketch on a plane (a datum's name), then sketch mode."""
        sketch = Sketch(self.part.free_feature_name("Sketch"), plane=plane)
        self.part.add_feature(sketch)
        self._commit()
        self.edit_sketch(sketch.name)
        return sketch.name

    def edit_sketch(self, name):
        """Sketch mode on an existing sketch."""
        self._stop_picking_plane()
        built = self.result.outputs.get(name)
        if built is None:
            self.show_error(f"{name} cannot be edited: {self.result.feature_errors.get(name, 'not built')}")
            return
        self.line_action.setChecked(False)
        self.point_action.setChecked(False)
        self.editing = name
        self.view.select(None)
        self.view.set_sketch(built.frame, name)
        self._look_at_sketch()
        self.ribbon.set_tab_enabled("Sketch", True)
        self.ribbon.show_tab("Sketch")
        self.start_sketch_action.setEnabled(False)
        self._fill_tree()
        self.statusBar().showMessage(f"{name} on {self.part.feature(name)['plane']}: Finish Sketch to leave it")

    def finish_sketch(self, refresh=True):
        if self.editing is None:
            return
        self.line_action.setChecked(False)
        self.point_action.setChecked(False)
        self.editing = None
        self.view.set_sketch(None)
        self.ribbon.set_tab_enabled("Sketch", False)
        self.ribbon.show_tab("3D Model")
        self.start_sketch_action.setEnabled(True)
        if refresh:
            self._fill_tree()
            self._on_pick(self.view.picks)

    def _look_at_sketch(self):
        frame = self.view.sketch_frame
        if frame is not None:
            self.view.look_at(frame.normal, frame.v)

    def _set_line_tool(self, on):
        self._set_tool(on, self.line_action, lambda: PolylineTool(
            self.view, self.editing, self._edited_result, self._add_line,
            on_exit=lambda: self.line_action.setChecked(False)))

    def _set_point_tool(self, on):
        self._set_tool(on, self.point_action, lambda: PointTool(
            self.view, self.editing, self._edited_result, self._add_point,
            on_exit=lambda: self.point_action.setChecked(False)))

    def _set_tool(self, on, action, make):
        """One drawing tool at a time: turning one on turns the other off."""
        if on and self.editing is not None:
            for other in (self.line_action, self.point_action):
                if other is not action and other.isChecked():
                    other.blockSignals(True)
                    other.setChecked(False)
                    other.blockSignals(False)
            self.view.select(None)
            self._on_view_select(None)
            self.view.set_tool(make())
        elif not any(a.isChecked() for a in (self.line_action, self.point_action)):
            self.view.set_tool(None)
        self._on_pick(self.view.picks)

    def _add_point(self, xy):
        """The Point tool's callback: an explicit point at (x, y) mm; one undo step."""
        sketch = self.part.feature(self.editing)
        name = sketch.add_point(fmt_mm(xy[0]), fmt_mm(xy[1]), explicit=True)
        self._commit()
        return name

    def _edited_result(self):
        return self.result.outputs[self.editing]

    def _add_line(self, start, end):
        """The Line tool's callback: anchors -> points (new ones as numbers) and a line; one undo step."""
        sketch = self.part.feature(self.editing)

        def point(anchor):
            if anchor[0] == "point":
                return anchor[1]
            x, y = anchor[1]
            return sketch.add_point(fmt_mm(x), fmt_mm(y))

        a, b = point(start), point(end)
        sketch.add_line(a, b)
        self._commit()
        return a, b

    def _drag_entity(self, keys, moved, phase):
        """
        A left drag on sketch points and lines (GeometryView.on_drag, keys:
        the selected entities): every point of them follows by `moved` (mm)
        from where it was; a coordinate that is a formula stays (the
        parametric model wins). When the points all sit at one place (one
        point, or a coincident group) they take the alignment hints. One undo
        step on release; Esc puts everything back.
        """
        sketch_name = keys[0][1] if keys else self.editing
        if phase == "start":
            sketch = self.part.feature(sketch_name)
            names = []
            for _, _, name in keys:
                if any(p.name == name for p in sketch.points):
                    names.append(name)
                else:
                    line = sketch.line(name)
                    names += [line.start, line.end]
            self._dragged = {n: (_number(sketch.point(n).x), _number(sketch.point(n).y))
                             for n in dict.fromkeys(names)}
            return
        if self._dragged is None:
            return
        if phase == "move":
            sketch = self.part.feature(sketch_name)
            before = self.result.outputs[sketch_name]
            group = list(self._dragged)
            first = group[0]
            offset, guides = np.asarray(moved, float), []
            if all(np.allclose(before.points[n], before.points[first], atol=1e-6) for n in group):
                x0, y0 = self._dragged[first]
                fixed = (x0 is None, y0 is None)
                target = np.where(fixed, before.points[first], np.array([x0 or 0.0, y0 or 0.0]) + offset)
                aligned, guides = align(target, before.points, ALIGN_PX * self.view._mm_per_px(),
                                        exclude=set(group), fixed=fixed)
                offset = offset + (aligned - target)
            for n, (x0, y0) in self._dragged.items():
                point = sketch.point(n)
                if x0 is not None:
                    point.x = fmt_mm(x0 + offset[0])
                if y0 is not None:
                    point.y = fmt_mm(y0 + offset[1])
            self.show_result(rebuild(self.part))
            self.view.set_guides(before.frame, guides)
            self._say_fixed()
            return
        self.view.set_guides(None, [])
        if phase == "end":
            self._commit()
            self._say_fixed()
        else:                                   # cancel
            self.doc.revert()
            self._refresh()
        self._dragged = None

    def _say_fixed(self):
        fixed = [f"{n}.{axis}" for n, xy in self._dragged.items() for axis, v in zip("xy", xy) if v is None]
        if fixed:
            self.statusBar().showMessage(f"{', '.join(fixed)}: a formula, it stays", 5000)

    def delete_selected(self):
        """The Delete key: the selected sketch entities, or the selected feature."""
        key = self.view.selected
        if key is None:
            return
        entities = [k for k in self.view.selection if k[0] == "entity"]
        if entities:
            for _, sketch_name, name in entities:
                sketch = self.part.feature(sketch_name)
                if any(p.name == name for p in sketch.points) or any(line.name == name for line in sketch.lines):
                    sketch.delete(name)         # an earlier delete may have taken it already
            self.view.select(None)
            self._commit()
            self._show_info(None)
        elif key[0] == "feature":
            self.delete_feature(key[1])

    def delete_feature(self, name):
        try:
            self.part.delete_feature(name)
        except PartError as error:
            self.doc.revert()
            self.show_error(f"Cannot delete: {error}")
            return
        self._commit()

    def context_actions(self, item):
        """[(text, callable)] of the browser's right-click menu for a row."""
        key = self._key_of(item)
        if key is None:
            return []
        kind, name = key
        if kind == "datum" and self.part.datum(name).kind == "plane":
            return [("New Sketch", lambda: self.new_sketch(name))]
        if kind == "feature" and self.part.feature(name).TYPE == "sketch":
            actions = [] if name == self.editing else [("Edit Sketch", lambda: self.edit_sketch(name))]
            return actions + [("Delete", lambda: self.delete_feature(name))]
        return []

    def _show_context_menu(self, pos):
        actions = self.context_actions(self.tree.itemAt(pos))
        if not actions:
            return
        menu = QMenu(self)
        for text, slot in actions:
            menu.addAction(text).triggered.connect(lambda _=False, f=slot: f())
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def _row_double_clicked(self, item):
        key = self._key_of(item)
        if key is not None and key[0] == "feature" and self.part.feature(key[1]).TYPE == "sketch":
            self.edit_sketch(key[1])

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
        errors = self.result.feature_errors if self.result is not None else {}
        for feature in self.part.features:
            key = ("feature", feature.name)
            item = QTreeWidgetItem([feature.name])
            item.setData(0, Qt.UserRole, key)
            tip = f"{feature.TYPE} on {feature['plane']}" if feature.TYPE == "sketch" else feature.TYPE
            if feature.name in errors:
                item.setForeground(0, QBrush(ERROR_COLOR))
                tip = f"{tip}\nerror: {errors[feature.name]}"
            if feature.name == self.editing:
                item.setBackground(0, EDITING_ROW)
                font = item.font(0)
                font.setBold(True)
                item.setFont(0, font)
                tip += "\n(being edited)"
            item.setToolTip(0, tip)
            root.addChild(item)
            self._rows[key] = item
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
        elif self.view.selected is not None and self.view.selected[0] != "entity":
            self.view.select(None)              # gone (sketch entities are not in the tree)
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
        if self._plane_picked(key):
            return
        self.view.select(key)
        self._show_info(key)

    def _row_hovered(self, item):
        key = self._key_of(item)
        self.view.set_hover(key if key is not None and key[0] in ("datum", "feature") else None)

    def _on_view_select(self, key):
        """A click in 3D selected `key` (or nothing): follow it in the tree."""
        if self.picking_plane:
            self._plane_picked(key)
            return
        self.tree.blockSignals(True)
        if key is None or key not in self._rows:
            self.tree.setCurrentItem(None)
            self.tree.clearSelection()
        else:
            self.tree.setCurrentItem(self._rows[key])
        self.tree.blockSignals(False)
        selection = self.view.selection
        if len(selection) > 1:
            names = ", ".join(k[-1] for k in selection)
            self.info.setText(f"<b>{len(selection)} selected</b><br>{names}")
        else:
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
        elif key[0] == "entity":
            self.info.setText(self._entity_info(key[1], key[2]))
        elif key[0] == "feature":
            feature = self.part.feature(key[1])
            lines = [f"<b>{feature.name}</b> ({feature.TYPE})"]
            if feature.TYPE == "sketch":
                lines.append(f"on {feature['plane']}")
            error = self.result.feature_errors.get(feature.name)
            if error:
                lines.append(f"<span style='color:#c81e1e'>error: {error}</span>")
            self.info.setText("<br>".join(lines))
        else:
            body = next(b for b in self.result.bodies if b.name == key[1])
            self.info.setText(self._body_info(body))

    def _entity_info(self, sketch_name, name):
        sketch = self.part.feature(sketch_name)
        points = self.result.outputs[sketch_name].points

        def where(p):
            x, y = points[p]
            return f"{p} ({x:.6g}, {y:.6g})"

        if any(p.name == name for p in sketch.points):
            p = sketch.point(name)
            kind = "sketch point" if p.explicit else "point"
            return f"<b>{name}</b> ({kind} of {sketch_name})<br>x = {p.x}, y = {p.y} mm<br>{where(name)}"
        line = sketch.line(name)
        length = float(((points[line.end] - points[line.start]) ** 2).sum() ** 0.5)
        return (f"<b>{name}</b> (line of {sketch_name})<br>from {where(line.start)}<br>to {where(line.end)}"
                f"<br>length {length:.6g} mm")

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
        hint = MEASURE_HINT if self.view.measure else PICK_PLANE_HINT if self.picking_plane else HINT
        if self.editing is not None and not self.view.measure:
            tool = self.view.tool
            hint = f"{self.editing}: " + (LINE_HINT if isinstance(tool, PolylineTool) else POINT_HINT
                                          if tool is not None else "Finish Sketch to leave it · " + HINT)
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
