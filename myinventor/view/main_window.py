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
# Files: New, Open, Recent, Save (Ctrl+S), Save As (`name.part.toml`);
# Export (Ctrl+E) writes the bodies to a STEP file (names, colors); with
# STEP on Save checked (an app setting), every Save also writes name.step
# next to the part file, overwriting it without a question (for customcst).
# Leaving a modified part (New, Open, close) asks to save it; a file changed on disk by
# another program since it was opened is not overwritten without asking. A
# file that fails to read is not opened (and so never overwritten).
#
# The browser, as in Inventor:
#   <part name>       the file's name, or Part1
#     Origin          YZ / XZ / XY Plane, X / Y / Z Axis, Center Point (hidden at first)
#     Sketch1 ...     the features in history order (red: failed, the reason as tooltip);
#                     a sketch an extrusion used sits under it (and is hidden in 3D)
#     Solid Bodies    (only when the rebuild made bodies); open a body for
#                     its properties: Color (a swatch; click: Qt's color dialog,
#                     right click: Default color), saved with the part
# - a row's checkbox shows or hides its datum (saved with the part) or body;
# - hovering a row pre-highlights its datum or sketch in 3D (green, a hidden
#   datum or a sketch an extrusion used too, as long as the mouse is there;
#   selected: blue, as long as it is selected), and hovering one in 3D marks
#   its row (an empty sketch shows as its origin point);
# - a feature that cannot be built is red, the reason as its tooltip and in
#   the info; an edit that breaks one says so in the status bar;
# - selecting a row selects its datum or body in 3D and the other way
#   round; the numbers of the selected item are shown under the tree.
#
# Right click in the browser: New Sketch on a plane; Edit Sketch and Delete
# on a sketch (double click edits it too).
#
# Sketches, as in Inventor:
# - New Sketch on a plane, or 3D Model > Start 2D Sketch: with a plane or a
#   flat body face selected it starts there, else the origin planes are shown
#   and a click on one, or on a flat face (in 3D or in the browser), starts
#   it; Esc cancels. A face is named by meaning (face_reference:
#   "Extrusion1:end", "Extrusion1:side.L3"), so the sketch follows its
#   feature's sizes;
# - sketch mode: the Sketch tab, the camera looking at the plane, the
#   sketch's endless axes; Finish Sketch leaves it (Look At looks again). Undo past
#   the sketch's creation, New and Open leave it too;
# - in sketch mode a click selects a line or a point of the sketch; Sketch >
#   Line draws polylines (view/sketch_tools.py: a click per point, a preview
#   of the next line; Esc ends the chain, a second Esc the tool); every line
#   is one undo step; Sketch > Point puts explicit points (a dot in a
#   gapped cross, as Inventor draws them); Sketch > Circle: the center, then
#   the radius; Sketch > Construction: new lines and circles are construction
#   ones (dashed), or, with lines or circles selected, shows and sets theirs
#   (on only if all are; a click sets them all);
# - several sketch entities can be selected (Shift + click, a box: see
#   view/geometry_view.py); a click on a point takes the points at the same
#   place with it;
# - a left drag moves the selected sketch points and lines (a coordinate
#   given by a formula stays; points at one place take the alignment hints,
#   view/sketch_tools.py align()); one undo step, Esc while dragging puts it
#   back;
# - Ctrl+C / Ctrl+X / Ctrl+V (Sketch > Clipboard) copy, cut and paste the
#   selected sketch geometry; a paste hangs on the cursor until a click puts
#   it (view/sketch_tools.py PasteTool);
# - Delete deletes the selection: a sketch line (with its end points no
#   other line uses) or point (with its lines), or a feature nothing uses.
#
# 3D Model > Extrude (view/extrude_panel.py): closed regions of the sketches
# are picked in 3D (hover: green, click: take or drop, blue), the panel takes
# the distance and the operation (New solid: Solid1, ...; Join / Cut /
# Intersect a body); Apply adds the Extrusion, one undo step; Cancel or Esc
# leaves. A see-through preview shows what it would add (amber) or cut (red).
# Double click (or Edit Extrusion) on an extrusion opens the same panel on
# it, the model rolled back to just before it; Apply changes it.
#
# A click in 3D selects; the Measure button switches to picking points A / B
# (view/geometry_view.py), shown with the distance in the status bar.
#
# Dialogs go through ask_* methods, so the checks can answer them.
# The window's size and the recent files are app settings (app_settings.py,
# MYCAD_SETTINGS for tests).

import math
import os

import numpy as np

from PySide6.QtCore import QEvent, QRect, Qt
from PySide6.QtGui import QAction, QBrush, QColor, QGuiApplication, QIcon, QKeySequence, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QColorDialog, QDockWidget, QFileDialog, QLabel, QMainWindow, QMenu, QMessageBox, QTabWidget,
    QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from app_settings import AppSettings
from geometry.body import Geometry
from geometry.kernel import extrude as extrude_shape, region_face
from geometry.step_io import write_step
from geometry.tessellate import body_from_shape
from model.document import Document
from model.part import Part, PartError, color_rgb
from model.part_file import EXTENSION, PartFileError
from model.rebuild import rebuild
from model.extrude import Extrude
from model.profiles import find_regions
from model.sketch import Sketch
from view.extrude_panel import ExtrudePanel
from view.sketch_tools import (
    ALIGN_PX, CURVE_SNAP_PX, SNAP_PX, CircleTool, PasteTool, PointTool, PolylineTool, align, curve_snap,
    fmt_mm, radius_snap,
)
from view.geometry_view import DEFAULT_COLORS, GeometryView
from view.parameters_panel import COMMENT, FORMULA, NAME, ParametersPanel
from view.ribbon import Ribbon
from view.view_buttons import ViewButtons

MM = 1e-3
DEFAULT_BODY_COLOR = DEFAULT_COLORS["solid"]
HOVER_ROW = QColor(60, 200, 90, 70)          # the row of the datum hovered in 3D
HINT = ("Middle drag: rotate · Ctrl + middle drag: pan · wheel: zoom · "
        "click: select, Esc: clear")
MEASURE_HINT = "Measure: click: point A, Shift + click: point B, Esc: clear the points"
LINE_HINT = ("Line: click points; click the first point to close, the last one to stop · "
             "Esc: end the chain, Esc again: leave Line")
POINT_HINT = "Point: click to put a point · Esc: leave Point"
CIRCLE_HINT = "Circle: click the center, then a point on the circle · Esc: drop the center, again: leave Circle"
PASTE_HINT = "Paste: click where the copy goes (it hangs on the cursor by its middle) · Esc: cancel"
PICK_PLANE_HINT = "Start 2D Sketch: select a plane or a flat face of a body (in 3D or in the browser), Esc: cancel"
FACE_TOLERANCE = 1e-6           # m and normal components: a body face lies on a feature's face plane
ERROR_COLOR = QColor(200, 30, 30)
EDITING_ROW = QColor(0, 120, 215, 45)        # the sketch being edited
FILE_FILTER = f"myCad part (*{EXTENSION});;All files (*)"
STEP_FILTER = "STEP (*.step *.stp)"


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


def _swatch(rgb, size=14):
    """A small square of a color (0..1 RGB), for a property row's icon."""
    pixmap = QPixmap(size, size)
    pixmap.fill(QColor.fromRgbF(*rgb))
    return QIcon(pixmap)


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
        self.construction_mode = False  # new lines and circles are construction ones
        self.clipboard = None       # copied sketch geometry (see copy_selection), or None
        self.extrude_panel = None   # the Extrude command's panel while it runs
        self._extruding = None      # the extrusion being edited by it, or None (a new one)
        self._rolled_back = False   # the view shows the bodies from before the edited extrusion
        self.picking_plane = False  # Start 2D Sketch waits for a plane
        self.view = GeometryView()
        self.view.on_pick = self._on_pick
        self.view.on_select = self._on_view_select
        self.view.on_hover = self._on_view_hover
        self.view.on_escape = self._escape
        self.view.on_delete = self.delete_selected
        self.view.on_drag = self._drag_entity
        self._dragged = None        # while dragging: {"circle": name or None, "radius": r0 or None,
        #                              "points": {name: (x0, y0) mm, None where a formula}}
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
        group = file.add_group("Export")
        group.add_action(self._action("Export...", self.export_dialog, "Ctrl+E",
                                      "Export the bodies to a STEP file (names and colors kept)"))
        self.step_on_save_action = self._action(
            "STEP on Save", self._set_step_on_save, checkable=True,
            tip="Save also writes the bodies to name.step next to the part file (overwritten without asking): "
                "customcst can re-read it at once")
        self.step_on_save_action.setChecked(self.app_settings.step_on_save)
        group.add_action(self.step_on_save_action)

        model = self.ribbon.add_tab("3D Model")
        group = model.add_group("Sketch")
        self.start_sketch_action = self._action(
            "Start 2D Sketch", self.start_sketch, tip="A new sketch on the selected plane, or on the plane "
                                                       "you click next")
        group.add_action(self.start_sketch_action)
        group = model.add_group("Create")
        self.extrude_action = self._action("Extrude", self.start_extrude,
                                           tip="Extrude closed regions of a sketch into a new body")
        group.add_action(self.extrude_action)
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
        self.circle_action = self._action("Circle", self._set_circle_tool, checkable=True,
                                          tip="Circles: click the center, then a point on the circle; "
                                              "Esc drops the center, again leaves the tool")
        group.add_action(self.circle_action)
        self.tool_actions = (self.line_action, self.point_action, self.circle_action)
        group = sketch.add_group("Format")
        self.construction_action = self._action(
            "Construction", self._construction_toggled, checkable=True,
            tip="Construction lines and circles (dashed). With lines or circles selected: theirs (on only if "
                "all are); a click sets them all. With none: for the new ones")
        group.add_action(self.construction_action)
        group = sketch.add_group("Clipboard")
        self.copy_action = self._action("Copy", self.copy_selection, QKeySequence.Copy,
                                         "Copy the selected sketch geometry")
        self.cut_action = self._action("Cut", self.cut_selection, QKeySequence.Cut,
                                        "Copy the selected sketch geometry and delete it")
        self.paste_action = self._action("Paste", self.start_paste, QKeySequence.Paste,
                                          "The copied geometry follows the cursor; a click puts it there")
        for action in (self.copy_action, self.cut_action, self.paste_action):
            group.add_action(action)
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
        self.tree.itemClicked.connect(lambda item, _: self._row_clicked(item))
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
        """
        Rebuild doc.part and show everything again (after an edit, undo,
        open). Features this rebuild broke are named in the status bar (they
        are red in the browser, the reason in their tooltip).
        """
        before = set(self.result.feature_errors) if self.result is not None else set()
        self.show_result(rebuild(self.part), reset_view)
        broken = [n for n in self.result.feature_errors if n not in before]
        if broken:
            first = broken[0]
            more = f" (and {len(broken) - 1} more)" if len(broken) > 1 else ""
            self.statusBar().showMessage(f"{first} failed: {self.result.feature_errors[first]}{more}", 10000)

    def show_result(self, result, reset_view=False):
        self.result = result
        self.view.set_datums(self.part.origin)
        self._show_sketches(force=True)
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
        self._sync_construction()

    def _commit(self, refresh=True):
        """An edit of doc.part is done: one undo step, then rebuild and show."""
        self.doc.commit()
        if refresh:
            self._refresh()
        else:
            self._update_title()

    def _update_title(self):
        self.setWindowTitle(f"{self.doc.name}{'*' if self.doc.modified else ''} — myCad")
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
            self, "myCad", f"Save the changes to {self.doc.name}?",
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
            self, "myCad", f"{self.doc.path}\nchanged on disk since it was opened. Overwrite it?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        return answer == QMessageBox.Yes

    def show_error(self, text):
        QMessageBox.critical(self, "myCad", text)

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
        message = f"saved {self.doc.path}"
        if self.app_settings.step_on_save:
            step = self.step_path()
            try:
                skipped = self.export_step(step)
                message += f" and {os.path.basename(step)}" + (f" ({skipped})" if skipped else "")
            except (OSError, ValueError) as error:
                message += f"; the STEP was not written: {error}"
        self.statusBar().showMessage(message, 10000)
        return True

    # =====================================================
    # STEP export
    # =====================================================

    def step_path(self):
        """name.part.toml -> name.step next to it (where STEP on Save writes)."""
        return os.path.join(os.path.dirname(self.doc.path), self.doc.name + ".step")

    def ask_export_path(self):
        start = self.step_path() if self.doc.path else self.doc.name + ".step"
        path, _ = QFileDialog.getSaveFileName(self, "Export STEP", start, STEP_FILTER)
        if path and not path.lower().endswith((".step", ".stp")):
            path += ".step"
        return path or None

    def export_dialog(self):
        """File > Export: the bodies to a STEP file the dialog names."""
        path = self.ask_export_path()
        if not path:
            return False
        try:
            skipped = self.export_step(path)
        except (OSError, ValueError) as error:
            self.show_error(f"Cannot export the STEP file:\n{error}")
            return False
        self.statusBar().showMessage(f"exported {path}" + (f" ({skipped})" if skipped else ""), 10000)
        return True

    def export_step(self, path):
        """
        The bodies as the rebuild left them, with their names and colors, to
        a STEP file (overwritten). Returns a note on what was left out (failed
        features), or "". ValueError if there is no body.
        """
        shapes = self.result.shapes
        if not shapes:
            raise ValueError("the part has no body yet")
        write_step(path, [(name, shape, color_rgb(self.part.body_color(name)) if self.part.body_color(name)
                           else None) for name, shape in shapes.items()])
        failed = list(self.result.feature_errors)
        return f"without the failed {', '.join(failed)}" if failed else ""

    def _set_step_on_save(self, on):
        self.app_settings.step_on_save = bool(on)
        error = self.app_settings.save()
        if error:
            self.statusBar().showMessage(f"settings not saved: {error}", 10000)

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
        """
        3D Model > Start 2D Sketch: on the selected plane or flat body face,
        or wait for a click on one.
        """
        selected = self.view.selected
        if selected is not None and selected[0] == "datum" and self.part.datum(selected[1]).kind == "plane":
            self.new_sketch(selected[1])
            return
        if selected is not None and selected[0] == "face":
            reference, why = self.face_reference(selected)
            if reference is not None:
                self.new_sketch(reference)
                return
            self.statusBar().showMessage(why, 8000)
        self.picking_plane = True
        planes = [d.name for d in self.part.origin if d.kind == "plane"]
        self.view.set_pick_filter({"plane", "face"}, forced=planes)
        self.start_sketch_action.setEnabled(False)
        self.statusBar().showMessage(PICK_PLANE_HINT)

    def _stop_picking_plane(self):
        if self.picking_plane:
            self.picking_plane = False
            self.view.set_pick_filter(None)
            self.start_sketch_action.setEnabled(self.editing is None)
            self._on_pick(self.view.picks)

    def _plane_picked(self, key):
        """While picking a plane: True if `key` was one, or a flat face of a feature (the sketch starts on it)."""
        if not self.picking_plane or key is None:
            return False
        if key[0] == "face":
            reference, why = self.face_reference(key)
            if reference is None:
                self.statusBar().showMessage(why, 8000)
                return False
            self._stop_picking_plane()
            self.new_sketch(reference)
            return True
        if key[0] != "datum" or self.part.datum(key[1]).kind != "plane":
            return False
        self._stop_picking_plane()
        self.new_sketch(key[1])
        return True

    def face_reference(self, key):
        """
        A body face (("face", body, CAD face index)) as a sketch plane by
        meaning: "Extrusion1:end", "Extrusion2:side.L3"... the first feature
        (in history order) whose named face lies on that face's plane with the
        same outward normal. Returns (reference, None) or (None, why not).
        """
        triangles = self.view.face_triangles(key) * MM
        if not len(triangles):
            return None, "no such face"
        normals = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
        normals /= np.linalg.norm(normals, axis=1, keepdims=True)
        normal = normals[0]
        points = triangles.reshape(-1, 3)
        if not np.allclose(normals, normal, atol=FACE_TOLERANCE) \
                or np.abs((points - points[0]) @ normal).max() > FACE_TOLERANCE:
            return None, "a sketch needs a flat face (this one is curved)"
        for feature in self.part.features:
            built = self.result.outputs.get(feature.name)
            if not hasattr(built, "face_planes"):
                continue
            for role, (origin, outward) in built.face_planes().items():
                if np.allclose(outward, normal, atol=FACE_TOLERANCE) \
                        and abs(float(np.dot(points[0] - origin, outward))) < FACE_TOLERANCE:
                    return f"{feature.name}:{role}", None
        return None, "this face is not a cap or a wall of an extrusion (not supported yet)"

    def _escape(self):
        """Esc in the 3D view: cancels the Extrude command or picking a plane (True: used)."""
        if self.extrude_panel is not None:
            self.extrude_panel.reject()
            return True
        if self.picking_plane:
            self._stop_picking_plane()
            return True
        return False

    def new_sketch(self, plane):
        """A new sketch on a plane (a datum's name, or a feature's face: "Extrusion1:end"), then sketch mode."""
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
        for action in self.tool_actions:
            action.setChecked(False)
        self.editing = name
        self.view.select(None)
        self._show_sketches(force=True)         # a sketch an extrusion used shows while edited
        self.view.set_sketch(built.frame, name)
        self._look_at_sketch()
        self.ribbon.set_tab_enabled("Sketch", True)
        self.ribbon.show_tab("Sketch")
        self.start_sketch_action.setEnabled(False)
        self._fill_tree()
        self._sync_construction()               # Paste, Construction: they depend on the edited sketch
        self.statusBar().showMessage(f"{name} on {self.part.feature(name)['plane']}: Finish Sketch to leave it")

    def finish_sketch(self, refresh=True):
        if self.editing is None:
            return
        for action in self.tool_actions:
            action.setChecked(False)
        self.editing = None
        self.view.set_sketch(None)
        self._show_sketches(force=True)
        self.ribbon.set_tab_enabled("Sketch", False)
        self.ribbon.show_tab("3D Model")
        self.start_sketch_action.setEnabled(True)
        self._sync_construction()
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

    def _set_circle_tool(self, on):
        self._set_tool(on, self.circle_action, lambda: CircleTool(
            self.view, self.editing, self._edited_result, self._add_circle,
            on_exit=lambda: self.circle_action.setChecked(False)))

    def _set_tool(self, on, action, make):
        """One drawing tool at a time: turning one on turns the others off."""
        if on and self.editing is not None:
            for other in self.tool_actions:
                if other is not action and other.isChecked():
                    other.blockSignals(True)
                    other.setChecked(False)
                    other.blockSignals(False)
            self.view.select(None)
            self._on_view_select(None)
            self.view.set_tool(make())
        elif not any(a.isChecked() for a in self.tool_actions):
            self.view.set_tool(None)
        self._on_pick(self.view.picks)

    def _add_circle(self, center, radius):
        """The Circle tool's callback: the center (a new point, or an existing one) and the circle; one undo step."""
        sketch = self.part.feature(self.editing)
        if center[0] == "point":
            name = center[1]
        else:
            name = sketch.add_point(fmt_mm(center[1][0]), fmt_mm(center[1][1]))
        circle = sketch.add_circle(name, fmt_mm(radius), construction=self.construction_mode)
        self._commit()
        return circle

    def _selected_curves(self):
        """Names of the selected lines and circles of the edited sketch."""
        if self.editing is None:
            return []
        sketch = self.part.feature(self.editing)
        names = {e.name for e in sketch.lines + sketch.circles}
        return [k[2] for k in self.view.selection if k[0] == "entity" and k[1] == self.editing and k[2] in names]

    def _sync_construction(self):
        """
        The Construction button shows the selected curves' type (on only if
        all are), else the mode; Copy / Cut / Paste are on when they can act.
        """
        entities = bool(self._selected_entities())
        self.copy_action.setEnabled(entities)
        self.cut_action.setEnabled(entities)
        self.paste_action.setEnabled(self.editing is not None and self.clipboard is not None)
        curves = self._selected_curves()
        if curves:
            sketch = self.part.feature(self.editing)
            on = all(sketch.curve(n).construction for n in curves)
        else:
            on = self.construction_mode
        self.construction_action.blockSignals(True)
        self.construction_action.setChecked(on)
        self.construction_action.blockSignals(False)

    def _construction_toggled(self, on):
        curves = self._selected_curves()
        if curves:                              # the selection takes it (one undo step)
            sketch = self.part.feature(self.editing)
            for n in curves:
                sketch.curve(n).construction = on
            self._commit()
        else:                                   # the mode for the new ones
            self.construction_mode = on
        self._sync_construction()

    # =====================================================
    # Clipboard
    # =====================================================

    def _selected_entities(self):
        """Names of the selected entities of the edited sketch."""
        if self.editing is None:
            return []
        return [k[2] for k in self.view.selection if k[0] == "entity" and k[1] == self.editing]

    def copy_selection(self):
        """
        Ctrl+C: the selected lines, circles and points with the points they
        need. Points are copied as values (the copy goes where it is put);
        a radius keeps its formula (moving does not change it).
        """
        names = self._selected_entities()
        if not names:
            self.statusBar().showMessage("Copy: select sketch lines, circles or points first", 5000)
            return False
        sketch = self.part.feature(self.editing)
        built = self.result.outputs[self.editing]
        kinds = {n: sketch.kind_of(n) for n in names}
        points = [p for n in names for p in sketch.points_of(n)]
        lines = [sketch.line(n) for n in names if kinds[n] == "line"]
        circles = [sketch.circle(n) for n in names if kinds[n] == "circle"]
        clip = {
            "points": {p: {"xy": built.points[p].copy(), "explicit": sketch.point(p).explicit}
                       for p in dict.fromkeys(points)},
            "lines": [(e.start, e.end, e.construction) for e in lines],
            "circles": [(c.center, built.radii[c.name], c.radius, c.construction) for c in circles],
        }
        # The anchor: the middle of the copy's extent (circles included).
        extent = [p["xy"] for p in clip["points"].values()]
        extent += [built.points[c.center] + sign * built.radii[c.name] for c in circles for sign in (-1, 1)]
        extent = np.array(extent)
        clip["anchor"] = 0.5 * (extent.min(axis=0) + extent.max(axis=0))
        self.clipboard = clip
        self.statusBar().showMessage(f"copied {len(names)}: {', '.join(names)}", 5000)
        self._sync_construction()
        return True

    def cut_selection(self):
        """Ctrl+X: copy, then delete the selection (one undo step)."""
        if self.copy_selection():
            self.delete_selected()

    def start_paste(self):
        """Ctrl+V: the copy hangs on the cursor (PasteTool); a click puts it."""
        if self.editing is None or self.clipboard is None:
            self.statusBar().showMessage("Paste: copy sketch geometry first (in sketch mode)", 5000)
            return
        for action in self.tool_actions:
            action.setChecked(False)
        self.view.select(None)
        self._on_view_select(None)
        self.view.set_tool(PasteTool(self.view, self.editing, self._edited_result, self.clipboard, self._paste,
                                     on_exit=self._end_paste))
        self._on_pick(self.view.picks)

    def _end_paste(self):
        self.view.set_tool(None)
        self._on_pick(self.view.picks)

    def _paste(self, offset):
        """The copy moved by `offset` (mm) into the edited sketch; one undo step; the pasted is selected."""
        sketch = self.part.feature(self.editing)
        clip = self.clipboard
        names = {}
        for old, p in clip["points"].items():
            x, y = p["xy"] + offset
            names[old] = sketch.add_point(fmt_mm(x), fmt_mm(y), explicit=p["explicit"])
        new = list(names.values())
        new += [sketch.add_line(names[a], names[b], construction=c) for a, b, c in clip["lines"]]
        new += [sketch.add_circle(names[center], formula, construction=c)
                for center, _, formula, c in clip["circles"]]
        self._commit()
        self.view.select_keys([("entity", self.editing, n) for n in new])
        self._on_view_select(self.view.selected)

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
        sketch.add_line(a, b, construction=self.construction_mode)
        self._commit()
        return a, b

    def _drag_entity(self, keys, moved, phase):
        """
        A left drag on sketch entities (GeometryView.on_drag, keys: the
        selected ones), as in Inventor:
        - one circle alone, dragged by its rim: its radius follows the cursor
          (the distance from the center), or goes through a point that comes
          within SNAP_PX of the circle (radius_snap; a green dot marks it);
        - else every point of them (a line's ends, a circle's center) follows
          by `moved` (mm) from where it was; when they all sit at one place
          (one point, a coincident group) they land on a line or circle
          nearby (not their own), else take the alignment hints.
        A coordinate or radius that is a formula stays (the parametric model
        wins). One undo step on release; Esc puts everything back.
        """
        sketch_name = keys[0][1] if keys else self.editing
        if phase == "start":
            sketch = self.part.feature(sketch_name)
            names = [k[2] for k in keys]
            if len(names) == 1 and sketch.kind_of(names[0]) == "circle":
                circle = sketch.circle(names[0])
                self._dragged = {"circle": circle.name, "radius": _number(circle.radius), "points": {}}
                return
            points = [p for n in names for p in sketch.points_of(n)]
            self._dragged = {"circle": None, "points": {
                n: (_number(sketch.point(n).x), _number(sketch.point(n).y)) for n in dict.fromkeys(points)}}
            return
        if self._dragged is None:
            return
        if phase == "move":
            sketch = self.part.feature(sketch_name)
            before = self.result.outputs[sketch_name]
            guides, spot = [], None
            if self._dragged["circle"] is not None:
                circle = sketch.circle(self._dragged["circle"])
                if self._dragged["radius"] is not None:
                    cursor = self.view.drag_start + moved
                    center = before.points[circle.center]
                    radius = float(np.linalg.norm(cursor - center))
                    others = {n: p for n, p in before.points.items() if not np.allclose(p, center, atol=1e-6)}
                    through = radius_snap(center, radius, others, SNAP_PX * self.view._mm_per_px())
                    if through is not None:     # the circle through a point nearby
                        radius, spot = through
                    circle.radius = fmt_mm(max(radius, 1e-3))
            else:
                dragged = self._dragged["points"]
                group = list(dragged)
                first = group[0]
                offset = np.asarray(moved, float)
                if all(np.allclose(before.points[n], before.points[first], atol=1e-6) for n in group):
                    x0, y0 = dragged[first]
                    fixed = (x0 is None, y0 is None)
                    target = np.where(fixed, before.points[first], np.array([x0 or 0.0, y0 or 0.0]) + offset)
                    mine = {e.name for e in before.lines if {e.start, e.end} & set(group)}                         | {c.name for c in before.circles if c.center in group}
                    on_curve = None if any(fixed) else curve_snap(
                        target, before, CURVE_SNAP_PX * self.view._mm_per_px(), exclude=mine)
                    if on_curve is not None:    # onto another line or circle
                        aligned = on_curve[0]
                    else:
                        aligned, guides = align(target, before.points, ALIGN_PX * self.view._mm_per_px(),
                                                exclude=set(group), fixed=fixed)
                    offset = offset + (aligned - target)
                for n, (x0, y0) in dragged.items():
                    point = sketch.point(n)
                    if x0 is not None:
                        point.x = fmt_mm(x0 + offset[0])
                    if y0 is not None:
                        point.y = fmt_mm(y0 + offset[1])
            self.show_result(rebuild(self.part))
            self.view.set_guides(before.frame, guides)
            self.view.set_snap_marker(before.frame, spot)
            self._say_fixed()
            return
        self.view.set_guides(None, [])
        self.view.set_snap_marker(None, None)
        if phase == "end":
            self._commit()
            self._say_fixed()
        else:                                   # cancel
            self.doc.revert()
            self._refresh()
        self._dragged = None

    def _say_fixed(self):
        dragged = self._dragged
        fixed = [f"{n}.{axis}" for n, xy in dragged["points"].items() for axis, v in zip("xy", xy) if v is None]
        if dragged["circle"] is not None and dragged["radius"] is None:
            fixed.append(f"{dragged['circle']}.radius")
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
                try:
                    sketch.kind_of(name)
                except KeyError:
                    continue                    # an earlier delete took it already
                sketch.delete(name)
            self.view.select(None)
            self._commit()
            self._show_info(None)
        elif key[0] == "feature":
            self.delete_feature(key[1])

    # =====================================================
    # Extrude
    # =====================================================

    def _show_sketches(self, force=False):
        """
        The sketches drawn in 3D: those no extrusion used (hidden, as Inventor
        does), the edited one, and a used one whose browser row is hovered or
        selected (it shows green / blue meanwhile).
        """
        used = self._consumed_sketches()
        outputs = self.result.outputs
        wanted = {self.editing} | {k[1] for k in (self.view.hovered, self.view.selected)
                                   if k is not None and k[0] == "feature"}
        shown = [(f.name, outputs[f.name]) for f in self.part.features
                 if f.TYPE == "sketch" and f.name in outputs and (f.name not in used or f.name in wanted)]
        # force: after a rebuild (their geometry changed); else only when the set of names changes
        if force or [name for name, _ in shown] != list(self.view._sketch_geometry):
            self.view.set_sketches(shown)

    def _consumed_sketches(self):
        """{sketch name: the first feature that uses it} (an extrusion)."""
        used = {}
        for feature in self.part.features:
            if feature.TYPE == "extrude":
                used.setdefault(feature["sketch"], feature.name)
        return used

    def start_extrude(self, edit=None):
        """
        3D Model > Extrude: the panel opens and the closed regions of the
        (visible) sketches can be picked in 3D; Apply makes the extrusion.
        edit: the name of an extrusion to change instead (double click, Edit
        Extrusion): the panel opens with its values, its profiles taken,
        regions of its own sketch only; the model rolls back to just before
        it meanwhile (as Inventor does), so a cut shows the body uncut.
        While the command runs, a preview shows what it would add or take.
        """
        if self.extrude_panel is not None:
            self.extrude_panel.raise_()
            return
        self._stop_picking_plane()
        self.finish_sketch()
        feature = self.part.feature(edit) if edit else None
        if feature is not None:
            sketches = [feature["sketch"]]
        else:
            used = self._consumed_sketches()
            sketches = [f.name for f in self.part.features if f.TYPE == "sketch" and f.name not in used]
        items = []
        for name in sketches:
            built = self.result.outputs.get(name)
            if built is None:
                continue
            for region in find_regions(built):
                fill = body_from_shape("profile", region_face(built.frame, region))
                items.append((("profile", name, region.key), built.frame, region, fill.vertices / MM, fill.triangles))
        if not items:
            where = f"{feature['sketch']}" if feature is not None else "a sketch"
            self.show_error(f"Extrude needs a closed region in {where} (lines meeting at their ends, or a circle).")
            return
        self.view.select(None)
        self._on_view_select(None)
        self.view.set_profile_mode(items)
        self.view.on_profiles = self._profiles_picked
        self._extruding = edit
        if feature is not None:
            # Roll back: the bodies as they were just before this feature.
            rolled = Part.from_dict(self.part.to_dict())
            rolled.features = rolled.features[:[f.name for f in rolled.features].index(edit)]
            before = rebuild(rolled).bodies
            self.view.set_geometry(Geometry(before), reset=False)
            self._rolled_back = True
            panel = ExtrudePanel(self, self.result.values, bodies=[b.name for b in before],
                                 distance=feature["distance"], operation=feature["operation"],
                                 body=feature["body"], title=f"Edit {edit}")
            self.view.set_profile_selection([("profile", feature["sketch"], key) for key in feature.profiles])
        else:
            panel = ExtrudePanel(self, self.result.values, bodies=[b.name for b in self.result.bodies])
            if len(items) == 1:                 # the only profile: taken at once (as Inventor does)
                self.view.set_profile_selection([items[0][0]])
        panel.applied.connect(self._apply_extrude)
        panel.cancelled.connect(self._end_extrude)
        panel.changed.connect(self._update_preview)
        self.extrude_panel = panel
        self._profiles_picked(self.view.profile_selection)
        panel.show()
        self.extrude_action.setEnabled(False)
        self.statusBar().showMessage(f"{'Edit ' + edit if edit else 'Extrude'}: click closed regions to take or "
                                     "drop them · Apply · Esc: cancel")

    def _profiles_picked(self, keys):
        """Profiles come from one sketch: a pick in another sketch starts over with it."""
        if keys:
            sketch = keys[-1][1]
            if any(k[1] != sketch for k in keys):
                self.view.set_profile_selection([keys[-1]])
        if self.extrude_panel is not None:
            self.extrude_panel.set_profile_count(len(self.view.profile_selection))
        self._update_preview()

    def _update_preview(self):
        """
        What the command would add (amber) or take away (red, a cut): the
        swept profiles, see-through; none while it cannot make them.
        """
        panel, keys = self.extrude_panel, self.view.profile_selection
        distance = panel.distance_value() if panel is not None else None
        if not keys or not isinstance(distance, float):
            self.view.set_preview(None)
            return
        built = self.result.outputs[keys[0][1]]
        regions = {r.key: r for r in find_regions(built)}
        try:
            shape = extrude_shape(built.frame, [regions[k[2]] for k in keys], distance)
        except (ValueError, RuntimeError, KeyError):
            self.view.set_preview(None)
            return
        self.view.set_preview(body_from_shape("preview", shape), cut=panel.operation_key() == "cut")

    def _apply_extrude(self, distance):
        keys = list(self.view.profile_selection)
        panel, edit = self.extrude_panel, self._extruding
        operation, target = panel.operation_key(), panel.body_name()
        self._end_extrude()
        if not keys:
            return
        profiles = [k[2] for k in keys]
        if edit:
            feature = self.part.feature(edit)
            if operation == "new":              # keeps its own name if it made a body already
                target = feature["body"] if feature["operation"] == "new" else self.part.free_body_name()
            feature.profiles = [tuple(sorted(p)) for p in profiles]
            feature.values.update(distance=distance, operation=operation, body=target)
        else:
            feature = Extrude(self.part.free_feature_name("Extrusion"), profiles=profiles, sketch=keys[0][1],
                              distance=distance, operation=operation,
                              body=target if operation != "new" else self.part.free_body_name())
            self.part.add_feature(feature)
        self._commit()
        error = self.result.feature_errors.get(feature.name)
        if error:
            self.show_error(f"{feature.name} failed: {error}")

    def _end_extrude(self):
        """The command ends (Apply, Cancel, Esc, the panel closed)."""
        panel, self.extrude_panel = self.extrude_panel, None
        if panel is not None:
            panel.applied.disconnect()
            panel.cancelled.disconnect()
            panel.changed.disconnect()
            panel.close()
        self.view.set_profile_mode(None)
        self.view.on_profiles = None
        self.view.set_preview(None)
        if self._rolled_back:                   # the whole model again
            self._rolled_back = False
            self.view.set_geometry(Geometry(self.result.bodies), reset=False)
        self._extruding = None
        self.extrude_action.setEnabled(True)
        self._on_pick(self.view.picks)

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
        if kind == "body_color":
            return [("Pick a color...", lambda: self.pick_body_color(name)),
                    ("Default color", lambda: self.set_body_color(name, None))]
        if kind == "datum" and self.part.datum(name).kind == "plane":
            return [("New Sketch", lambda: self.new_sketch(name))]
        if kind == "feature" and self.part.feature(name).TYPE == "sketch":
            actions = [] if name == self.editing else [("Edit Sketch", lambda: self.edit_sketch(name))]
            return actions + [("Delete", lambda: self.delete_feature(name))]
        if kind == "feature" and self.part.feature(name).TYPE == "extrude":
            return [("Edit Extrusion", lambda: self.start_extrude(edit=name)),
                    ("Delete", lambda: self.delete_feature(name))]
        return []

    def _show_context_menu(self, pos):
        actions = self.context_actions(self.tree.itemAt(pos))
        if not actions:
            return
        menu = QMenu(self)
        for text, slot in actions:
            menu.addAction(text).triggered.connect(lambda _=False, f=slot: f())
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def _row_clicked(self, item):
        key = self._key_of(item)
        if key is not None and key[0] == "body_color":
            self.pick_body_color(key[1])

    def ask_color(self, initial, title):
        """A color from Qt's color dialog (palette and picker), or None if cancelled."""
        color = QColorDialog.getColor(initial, self, title)
        return color if color.isValid() else None

    def pick_body_color(self, name):
        """The body's Color row was clicked: pick a color for it."""
        current = self.part.body_color(name)
        body = next((b for b in self.result.bodies if b.name == name), None)
        rgb = color_rgb(current) if current else (body.color if body is not None and body.color else
                                                    DEFAULT_BODY_COLOR)
        color = self.ask_color(QColor.fromRgbF(*rgb), f"Color of {name}")
        if color is not None:
            self.set_body_color(name, color.name())

    def set_body_color(self, name, color):
        """'#rrggbb' or None (the default); one undo step."""
        self.part.set_body_color(name, color)
        self._commit()

    def _row_double_clicked(self, item):
        key = self._key_of(item)
        if key is None or key[0] != "feature":
            return
        kind = self.part.feature(key[1]).TYPE
        if kind == "sketch":
            self.edit_sketch(key[1])
        elif kind == "extrude":
            self.start_extrude(edit=key[1])

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
        used = self._consumed_sketches()
        for feature in self.part.features:
            key = ("feature", feature.name)
            # A row may exist already: made for the sketch it took, which comes earlier in the history.
            item = self._rows[key] if key in self._rows else QTreeWidgetItem()
            item.setText(0, feature.name)
            item.setData(0, Qt.UserRole, key)
            if feature.TYPE == "sketch":
                tip = f"sketch on {feature['plane']}"
            elif feature.TYPE == "extrude":
                what = {"new": "into", "join": "joined to", "cut": "cut from", "intersect": "intersected with"}
                tip = (f"extrusion of {feature['sketch']} by {feature['distance']} mm "
                       f"{what[feature['operation']]} {feature['body']}")
            else:
                tip = feature.TYPE
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
            # A sketch an extrusion used sits under that extrusion (as in Inventor's browser).
            consumer = used.get(feature.name)
            parent = root if consumer is None else self._rows.setdefault(("feature", consumer), QTreeWidgetItem())
            parent.addChild(item)
            self._rows[key] = item
        bodies = self.result.bodies if self.result is not None else []
        if bodies:
            group = _group_item(None, f"Solid Bodies ({len(bodies)})")
            root.insertChild(0, group)          # first, as in Inventor
            for body in bodies:
                key = ("body", body.name)
                row = _checkable(group, body.name, key, "Tick: shown in 3D. Select: selected. "
                                 "Open it for its properties.", self.view._visible.get(body.name, True))
                self._rows[key] = row
                # Its properties, one row each (the color now; a material later).
                color = self.part.body_color(body.name)
                prop = QTreeWidgetItem(["Color" if color else "Color (default)"])
                prop.setData(0, Qt.UserRole, ("body_color", body.name))
                prop.setIcon(0, _swatch(color_rgb(color) if color else (body.color or DEFAULT_BODY_COLOR)))
                prop.setToolTip(0, f"{color or 'the default color'}: click to pick another "
                                   "(right click: Default color)")
                row.addChild(prop)
                self._rows[("body_color", body.name)] = prop
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
        self._show_sketches()                   # a used sketch shows while its row is selected
        self._show_info(key)

    def _row_hovered(self, item):
        key = self._key_of(item)
        self.view.set_hover(key if key is not None and key[0] in ("datum", "feature") else None)
        self._show_sketches()                   # ... or hovered

    def _on_view_select(self, key):
        """A click in 3D selected `key` (or nothing): follow it in the tree."""
        if self.picking_plane:
            self._plane_picked(key)
            return
        self._sync_construction()
        self._show_sketches()
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
        elif key[0] == "face":
            self.info.setText(self._face_info(key))
        elif key[0] == "datum":
            self.info.setText(self._datum_info(self.part.datum(key[1])))
        elif key[0] == "entity":
            self.info.setText(self._entity_info(key[1], key[2]))
        elif key[0] == "feature":
            feature = self.part.feature(key[1])
            lines = [f"<b>{feature.name}</b> ({feature.TYPE})"]
            if feature.TYPE == "sketch":
                lines.append(f"on {feature['plane']}")
            elif feature.TYPE == "extrude":
                verb = {"new": "makes", "join": "joins", "cut": "cuts", "intersect": "intersects"}[feature["operation"]]
                lines += [f"of {feature['sketch']}: {len(feature.profiles)} profile(s)",
                          f"distance = {feature['distance']} mm", f"{verb} {feature['body']}"]
                body = next((b for b in self.result.bodies if b.name == feature["body"]), None)
                if body is not None and feature.name in self.result.outputs:
                    lines.append(f"{body.name} now: {body.volume / MM ** 3:.6g} mm³")
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
        if sketch.kind_of(name) == "circle":
            circle = sketch.circle(name)
            radius = self.result.outputs[sketch_name].radii[name]
            return (f"<b>{name}</b> (circle of {sketch_name})<br>center {where(circle.center)}"
                    f"<br>radius = {circle.radius} mm ({radius:.6g})<br>diameter {2 * radius:.6g} mm")
        line = sketch.line(name)
        length = float(((points[line.end] - points[line.start]) ** 2).sum() ** 0.5)
        return (f"<b>{name}</b> (line of {sketch_name})<br>from {where(line.start)}<br>to {where(line.end)}"
                f"<br>length {length:.6g} mm")

    def _face_info(self, key):
        _, body, index = key
        triangles = self.view.face_triangles(key) * MM
        normals = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
        area = 0.5 * float(np.linalg.norm(normals, axis=1).sum())
        lines = [f"<b>{body}</b>, face {index}", f"area {area / MM ** 2:.6g} mm²"]
        reference, why = self.face_reference(key)
        if reference is not None:
            unit = normals[0] / np.linalg.norm(normals[0])
            lines += [f"flat, normal {_fmt_vector(np.round(unit, 6))}", f"named {reference}",
                      "Start 2D Sketch puts a sketch on it"]
        else:
            lines.append(why)
        return "<br>".join(lines)

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
            hints = {PolylineTool: LINE_HINT, PointTool: POINT_HINT, CircleTool: CIRCLE_HINT, PasteTool: PASTE_HINT}
            hint = f"{self.editing}: " + (hints[type(tool)] if tool is not None
                                          else "Finish Sketch to leave it · " + HINT)
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
