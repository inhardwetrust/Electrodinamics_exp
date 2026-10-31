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
# file that fails to read is not opened (and so never overwritten). The open
# file changed on disk (by an AI writing it, .claude/skills/mycad) is reloaded
# as one undo step when nothing of the user's is unsaved (poll_disk).
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
#   the radius; Sketch > Polyline draws a wide polyline's center line (its
#   outline follows; widths, ends and the inner points' bends (normal /
#   round / chamfer, a radius) in the properties under the info; a double
#   click on its center line puts a point there);
#   Sketch > Construction: new lines and circles are construction
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
# - Sketch > Project Geometry: a click on a body face projects its edges
#   into the sketch (project_face; view/sketch_tools.py ProjectTool);
#   "As placed" (under the browser, set_placed_view) shows the bodies placed
#   meanwhile, the sketch moved with the body it lies on: faces then project
#   as placed;
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
#
# Claude drives the window through its control channel (view/control.py,
# the client ../customcst/tools/ui.py --app mycad). Locked (set_locked),
# Claude is in control: a yellow banner says so, its button gives the
# control back; the ribbon, the browser and the panels are off and the
# window's shortcuts cleared; the 3D view only turns, pans, zooms and
# hovers (GeometryView.look_only); the disk watch does not reload. The
# commands use the *_quiet methods: no dialog, an error text instead.
# The window's size and the recent files are app settings (app_settings.py,
# MYCAD_SETTINGS for tests).

import dataclasses
import math
import os
import re

import numpy as np

from PySide6.QtCore import QEvent, QPoint, QRect, Qt, QTimer
from PySide6.QtGui import QAction, QBrush, QColor, QGuiApplication, QIcon, QKeySequence, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QColorDialog, QComboBox, QDockWidget, QFileDialog, QFormLayout, QInputDialog, QLineEdit, QLabel, QMainWindow, QMenu, QMessageBox, QPushButton, QTabWidget,
    QSizePolicy, QToolBar, QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from app_settings import AppSettings
from geometry.body import Geometry
from geometry.kernel import extrude as extrude_shape, region_face, revolve as revolve_shape
from geometry.step_io import export_part
from geometry.tessellate import body_from_shape
from model.document import Document
from model.part import Datum, Part, PartError, color_rgb
from model.part_file import EXTENSION, PartFileError
from model.rebuild import rebuild
from model.extrude import Extrude
from model.revolve import Revolve, axis_of, check_side, onto_axis
from model.workplane import WorkPlane
from model.importstep import Import, body_names
from model.transforms import GROUP, Mirror, Move, expand
from model.profiles import find_regions, named_regions, whole_regions
from model.expressions import evaluate, evaluate_parameters
from model.offsets import CAPS, offset_key
from model.sketch import OFFSET_KINDS, Sketch, SketchPolyline, SketchSpline
from model.curves import KINDS, CurveError
from model.traces import BENDS

# The arguments a curve added with the Curve button starts with (then edited in the properties).
CURVE_STARTERS = {
    "parabola": {"focal": "10", "from": "-20", "to": "20"},
    "function": {"x": "20 * t", "y": "5 * sin(360 * t)"},
}

# A bend's one number in the properties: (its label, the default shown when empty).
BEND_PARAMETER = {
    "arc": ("Arc radius", "the width (center line)"),
    "chamfer": ("Setback", "the width (along the center line)"),
    "miter": ("Miter radius", "half the width (a full bevel)"),
}
from view.control import ControlServer
from view.extrude_panel import ExtrudePanel
from view.workplane_panel import WorkPlanePanel
from view.move_panel import MirrorPanel, MovePanel
from view.sketch_tools import (
    ALIGN_PX, CURVE_SNAP_PX, SNAP_PX, ArcTool, CircleTool, LineTool, PasteTool, PointTool, ProjectTool, SplineTool,
    TraceTool, align,
    curve_snap,
    fmt_mm, radius_snap,
)
from view.geometry_view import DEFAULT_COLORS, GeometryView
from view.parameters_panel import COMMENT, FORMULA, NAME, ParametersPanel
from view.ribbon import Ribbon
from view.view_buttons import ViewButtons

MM = 1e-3
DISK_POLL_MS = 700                              # how often the open file is checked for changes on disk
DEFAULT_BODY_COLOR = DEFAULT_COLORS["solid"]
HOVER_ROW = QColor(60, 200, 90, 70)          # the row of the datum hovered in 3D
HINT = ("Middle drag: rotate · Ctrl + middle drag: pan · wheel: zoom · "
        "click: select, Esc: clear")
MEASURE_HINT = "Measure: click: point A, Shift + click: point B, Esc: clear the points"
LINE_HINT = ("Line: click points; click the first point to close, the last one to stop · "
             "Esc: end the chain, Esc again: leave Line")
TRACE_HINT = ("Polyline: click its center line point by point (the width beside the button); click the last "
              "point to stop · Esc: end it, Esc again: leave Polyline")
SPLINE_HINT = ("Spline: click the points it runs through; click the first point to close it (smooth all around), "
               "the last one to stop · Esc: end it, Esc again: leave Spline")
POINT_HINT = "Point: click to put a point · Esc: leave Point"
ARC_HINTS = {
    "center": "Arc from its center: click the center, then its start (the radius), then its end (counter-clockwise) "
              "· the method below left · Esc: drop the clicks, again: leave Arc",
    "three": "Arc through three points: click its start, then its end, then a point on it · the method below left · "
             "Esc: drop the clicks, again: leave Arc"}
CIRCLE_HINT = "Circle: click the center, then a point on the circle · Esc: drop the center, again: leave Circle"
PASTE_HINT = "Paste: click where the copy goes (it hangs on the cursor by its middle) · Esc: cancel"
PROJECT_HINT = ("Project Geometry: click a face of a body: its edges come into the sketch (lines, circles, arcs; "
                "other curves as splines) · Esc: leave")
PICK_PLANE_HINT = "Start 2D Sketch: select a plane or a flat face of a body (in 3D or in the browser), Esc: cancel"
FACE_TOLERANCE = 1e-6           # m and normal components: a body face lies on a feature's face plane
ERROR_COLOR = QColor(200, 30, 30)
EDITING_ROW = QColor(0, 120, 215, 45)        # the sketch being edited
FILE_FILTER = f"myCad part (*{EXTENSION});;All files (*)"
STEP_FILTER = "STEP (*.step *.stp)"
TREE_INDENT_PX = 12             # the browser's indent per level (Qt's default ~20)
EYE_PX = 14                     # the visibility eye in the browser


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


def _later(apply):
    """
    Runs `apply` once the event being handled is done. An edit in the
    properties rebuilds them: done inside the signal of one of their widgets
    (a field losing focus to a click on the next one), it deleted the widget
    Qt was still delivering to (an access violation).
    """
    QTimer.singleShot(0, apply)


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
        self._frozen = None         # the full Result when the sketch was opened (its bodies stay shown meanwhile)
        self.construction_mode = False  # new lines and circles are construction ones
        self.clipboard = None       # copied sketch geometry (see copy_selection), or None
        self._trace = None          # the polyline the Polyline tool is drawing
        self._spline = None         # the spline the Spline tool is drawing
        self.arc_method = "center"  # how the Arc tool draws: "center" | "three"
        self.spline_tolerance = "50"    # µm: how far a spline made from a curve may be off it
        self.extrude_panel = None   # the Extrude command's panel while it runs
        self.workplane_panel = None  # the Work Plane command's panel while it runs
        self._workplane = None      # that command: {"edit": name or None, "base": plane name}
        self._plane_purpose = "sketch"  # what a picked plane is for: "sketch" | "workplane"
        self._picking_axis = False    # the view picks an axis (a work plane's, a move's)
        self.move_panel = None      # the Move / Rotate command's panel while it runs
        self._moving = None         # that command: {"edit": name or None, "targets": [...]}
        self._selected_group = None  # the group whose browser row is selected (a Move's target)
        self._unplaced = None       # the Result without the placement (modeling's view of the bodies), cached
        self._modeling_shown = False    # the view shows the bodies unplaced (a modeling command runs)
        self._extruding = None      # the extrusion being edited by it, or None (a new one)
        self._sweep = "extrude"     # the command the panel runs: "extrude" | "revolve"
        self._rolled_back = False   # the view shows the bodies from before the edited extrusion
        self.picking_plane = False  # Start 2D Sketch waits for a plane
        self.locked = False         # Claude is in control (set_locked, view/control.py)
        self.placed_view = False    # sketch mode shows the bodies placed ("As placed"), not as modeling has them
        self._frozen_placed = None  # the full Result with the placement, while a sketch is edited and shown so
        self._shown_cache = {}      # sketch name -> (its output, that output as shown placed)
        self._shortcuts = {}        # while locked: each window action's shortcuts, put back on unlock
        self.view = GeometryView()
        self.view.on_pick = self._on_pick
        self.view.on_select = self._on_view_select
        self.view.on_context = self._view_context_menu
        self.view.on_hover = self._on_view_hover
        self.view.on_escape = self._escape
        self.view.on_delete = self.delete_selected
        self.view.on_drag = self._drag_entity
        self.view.on_insert = self._insert_point
        self._dragged = None        # while dragging: {"circle": name or None, "radius": r0 or None,
        #                              "points": {name: (x0, y0) mm, None where a formula}}
        self.setCentralWidget(self.view.canvas.native)
        self.view_buttons = ViewButtons(self.view)        # standard views over the 3D view

        self._build_lock_bar()
        self._build_ribbon()
        self._build_browser()
        self._set_document(Document())
        if path:
            self.open_file(path)
        if self.app_settings.error:
            self.statusBar().showMessage(f"settings not read: {self.app_settings.error}", 10000)
        # Another program (an AI writing the part, see .claude/skills/mycad) may change the
        # open file: polled, since a file replaced atomically escapes QFileSystemWatcher.
        self._disk_seen = None      # the file stamp already reported and not reloaded
        self.disk_timer = QTimer(self)
        self.disk_timer.setInterval(DISK_POLL_MS)
        self.disk_timer.timeout.connect(self.poll_disk)
        self.disk_timer.start()
        self.control = ControlServer(self)      # Claude's commands (../customcst/tools/ui.py --app mycad)
        if self.control.error:
            self.statusBar().showMessage(f"control channel off: {self.control.error}", 10000)

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

    def _build_lock_bar(self):
        """While Claude is in control (set_locked): a banner over the window (customcst's look)."""
        self.lock_bar = bar = QToolBar("Control")
        bar.setMovable(False)
        bar.setStyleSheet("QToolBar { background: #f2c94c; } QLabel { color: #1a1a1a; font-weight: bold; }")
        self.lock_label = QLabel()
        bar.addWidget(self.lock_label)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        bar.addWidget(spacer)
        self.take_back_button = take = QPushButton("Take control back")
        take.setToolTip("The window is yours again; Claude's next command that changes something is refused")
        take.clicked.connect(lambda: self.set_locked(False))
        bar.addWidget(take)
        self.addToolBar(bar)
        bar.setVisible(False)

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
        group = file.add_group("Import")
        group.add_action(self._action("Import STEP...", self.import_dialog, tip=(
            "The bodies of a STEP file (a connector...) as bodies of the part: the file is copied next to the "
            "part file (<name>.files), names and colors kept")))
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
        self.revolve_action = self._action("Revolve", self.start_revolve,
                                           tip="Revolve closed regions of a sketch around an axis (a straight line "
                                               "of a sketch, or an origin axis)")
        group.add_action(self.revolve_action)
        group = model.add_group("Work Features")
        self.workplane_action = self._action("Work Plane", self.start_work_plane,
                                             tip="A work plane: offset from a plane or flat face, or turned around "
                                                 "an axis in it (select the plane first, or click it next)")
        group.add_action(self.workplane_action)
        group = model.add_group("Placement")
        self.move_action = self._action("Move", lambda: self.start_move(), tip=(
            "Move the selected bodies or group (placement: after all the features, on the bodies as built)"))
        group.add_action(self.move_action)
        self.rotate_action = self._action("Rotate", lambda: self.start_move(rotate=True), tip=(
            "Turn the selected bodies or group around an axis (and move them): the same transform as Move"))
        group.add_action(self.rotate_action)
        self.mirror_action = self._action("Mirror", lambda: self.start_move(mirror=True), tip=(
            "Mirror the selected bodies or group in place: in a plane parallel to YZ, XZ or XY through its own "
            "center (a group's: of all its bodies)"))
        group.add_action(self.mirror_action)
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
        self.arc_action = self._action("Arc", self._set_arc_tool, checkable=True,
                                       tip="Arcs: from the center (center, start, end) or through three points "
                                           "(start, end, a point on it): the method below left")
        group.add_action(self.arc_action)
        self.trace_action = self._action("Polyline", self._set_trace_tool, checkable=True,
                                         tip="A wide polyline: click its center line point by point; each new "
                                             "point gets the width beside it, then its own (Properties)")
        group.add_action(self.trace_action)
        self.spline_action = self._action("Spline", self._set_spline_tool, checkable=True,
                                          tip="An interpolating spline: click the points it runs through; the "
                                              "first point again closes it")
        group.add_action(self.spline_action)
        group.add_widget(QLabel(" Width "))
        self.trace_width = group.add_widget(QLineEdit("1"))
        self.trace_width.setFixedWidth(60)
        self.trace_width.setToolTip("The width (mm, a number or a formula of the parameters) of new polylines")
        self.curve_menu = QMenu(self)
        for kind in KINDS:
            self.curve_menu.addAction(kind.capitalize(), lambda k=kind: self.add_curve(k)).setToolTip(KINDS[kind].DOC)
        self.curve_menu.setToolTipsVisible(True)
        self.curve_action = self._action("Curve", lambda: self.add_curve("parabola"),
                                         tip="A generated curve (a parabola; the arrow: other kinds), set by its "
                                             "arguments in the properties")
        group.add_action(self.curve_action, self.curve_menu)
        self.project_action = self._action(
            "Project Geometry", self._set_project_tool, checkable=True,
            tip="Click faces of bodies: their edges, projected onto the sketch's plane, become sketch geometry "
                "(plain numbers: lines, circles and arcs exactly, other curves as splines within the tolerance)")
        group.add_action(self.project_action)
        self.tool_actions = (self.line_action, self.point_action, self.circle_action, self.arc_action,
                             self.trace_action,
                             self.spline_action, self.project_action)
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
        self.tree.setIndentation(TREE_INDENT_PX)          # a deep tree stays narrow
        # Visibility ticks drawn as an eye (open: shown, struck through: hidden); their state works as a tick's.
        icons = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons").replace(os.sep, "/")
        self.tree.setStyleSheet(
            f"QTreeView::indicator {{ width: {EYE_PX}px; height: {EYE_PX}px; }}"
            f"QTreeView::indicator:checked {{ image: url({icons}/eye.svg); }}"
            f"QTreeView::indicator:unchecked {{ image: url({icons}/eye_off.svg); }}")
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)     # Shift / Ctrl: several bodies
        self.tree.itemSelectionChanged.connect(self._tree_selection_changed)
        self.tree.itemChanged.connect(self._item_changed)
        self.tree.currentItemChanged.connect(lambda item, _: self._item_selected(item))
        self.tree.itemEntered.connect(lambda item, _: self._row_hovered(item))
        self.tree.itemDoubleClicked.connect(lambda item, _: self._row_double_clicked(item))
        rename = QAction("Rename", self.tree)
        rename.setShortcut(QKeySequence(Qt.Key_F2))
        rename.setShortcutContext(Qt.WidgetShortcut)
        rename.triggered.connect(self._rename_current)
        self.tree.addAction(rename)
        self.tree.itemClicked.connect(lambda item, _: self._row_clicked(item))
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._show_context_menu)
        self.tree.viewport().installEventFilter(self)     # Leave: hover off

        self.info = QLabel()
        self.info.setWordWrap(True)
        self.info.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.info.setTextInteractionFlags(Qt.TextSelectableByMouse)

        # Editable properties of the selection (a polyline's widths and ends now).
        self.props = QWidget()
        self.props_form = QFormLayout(self.props)
        self.props_form.setContentsMargins(0, 0, 0, 0)

        # Sketch mode: the bodies as placed (Move, Mirror) instead of as modeling has them.
        self.placed_check = QCheckBox("As placed")
        self.placed_check.setToolTip(
            "While a sketch is edited: show the bodies placed (after Move / Rotate / Mirror), the sketch moved "
            "with the body it lies on; Project Geometry then projects faces as placed")
        self.placed_check.toggled.connect(self.set_placed_view)
        self.placed_check.setVisible(False)

        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.addWidget(self.tree, 3)
        layout.addWidget(self.info, 1)
        layout.addWidget(self.placed_check)
        layout.addWidget(self.props)

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
        self.show_result(self._build(), reset_view)
        broken = [n for n in self.result.feature_errors if n not in before]
        if broken:
            first = broken[0]
            more = f" (and {len(broken) - 1} more)" if len(broken) > 1 else ""
            self.statusBar().showMessage(f"{first} failed: {self.result.feature_errors[first]}{more}", 10000)

    def _build(self):
        """
        The part rebuilt: all of it, or while a sketch is edited only its
        history up to that sketch; the later features' results and the bodies
        stay as they were when the sketch was opened (Finish Sketch rebuilds
        the rest: 3D does not follow every edit of a sketch).
        """
        names = [f.name for f in self.part.features]
        if self.editing is None or self._frozen is None or self.editing not in names:
            return rebuild(self.part, folder=self._folder())
        result = rebuild(self.part, upto=self.editing, folder=self._folder())
        later = set(names[names.index(self.editing) + 1:])
        frozen = self._frozen
        result.outputs.update({n: out for n, out in frozen.outputs.items() if n in later})
        result.feature_errors.update({n: e for n, e in frozen.feature_errors.items() if n in later})
        result.bodies, result.shapes = frozen.bodies, frozen.shapes
        return result

    def _datums(self):
        """The origin's datums and the work planes built (model.part.Datum, as the view draws them)."""
        planes = []
        for feature in self.part.features:
            built = self.result.outputs.get(feature.name) if self.result is not None else None
            if feature.TYPE == "workplane" and built is not None:
                f = built.plane_frame()
                planes.append(Datum(feature.name, "plane", f.origin, f.normal, f.u, f.v, feature["visible"]))
        return list(self.part.origin) + planes

    def _datum(self, name):
        """A datum by name: the origin's or a work plane (KeyError if none)."""
        for d in self._datums():
            if d.name == name:
                return d
        raise KeyError(name)

    def _is_workplane(self, name):
        return any(f.name == name and f.TYPE == "workplane" for f in self.part.features)

    def _folder(self):
        """The part file's folder (imported files live next to it), or None before it is saved."""
        return os.path.dirname(self.doc.path) if self.doc.path else None

    def _modeling_result(self):
        """
        The part as modeling sees it: the bodies as the features built them,
        no placement (the result itself when there is no transform).
        """
        if not self.part.transforms:
            return self.result
        if self._unplaced is None or self._unplaced is self.result:
            self._unplaced = rebuild(self.part, folder=self._folder(), placement=False)
        return self._unplaced

    def _show_modeling(self, on):
        """
        While a modeling command runs (a sketch edited, a plane picked, Extrude,
        Revolve, Work Plane), the bodies unplaced: what sketches and faces
        refer to. Off: placed again.
        """
        if not self.part.transforms or on == self._modeling_shown:
            return
        self._modeling_shown = on
        bodies = self._modeling_result().bodies if on else self.result.bodies
        self.view.set_geometry(Geometry(bodies), reset=False)
        if on:
            self.statusBar().showMessage("Placement off while modeling (the bodies as built)", 5000)

    def _placed_bodies(self):
        """The bodies some transform moves (their faces as shown are not where modeling has them)."""
        return {b for t in self.part.transforms
                for b in getattr(self.result.outputs.get(t.name), "bodies", [])}

    def full_result(self):
        """The Result of the whole part (rebuilt if a sketch being edited holds 3D back): for STEP."""
        return self.result if self.result.upto is None else rebuild(self.part, folder=self._folder())

    def show_result(self, result, reset_view=False):
        self.result = result
        if result.upto is None:
            self._unplaced = None               # the part changed: built anew when asked
        self.view.set_datums(self._datums())
        self._show_sketches(force=True)
        if self.editing is not None and self.editing in result.outputs:
            self.view.set_sketch(self._shown_output(self.editing).frame, self.editing)
        if result.upto is None or reset_view:   # while a sketch is edited, the bodies stay as they were
            self._modeling_shown = False
            self.view.set_geometry(Geometry(result.bodies), reset=reset_view)
        self._fill_tree()
        self.parameters.show_parameters(self.part.parameters, result.values, result.errors)
        if self.editing is not None:
            if self.editing not in result.outputs:     # undone, deleted, or it fails now
                self.finish_sketch(refresh=False)
                if result.upto is not None:
                    self.show_result(rebuild(self.part, folder=self._folder()), reset_view)   # all again
                    return
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

    def poll_disk(self):
        """
        The open file changed on disk: reloaded (one undo step) when nothing
        of the user's is unsaved and no sketch or extrusion is being edited
        (then it waits for that to end). With unsaved edits it is only
        reported (Save asks before overwriting it); a file that does not read
        is reported and the part stays as it is.
        """
        if self.locked or not self.doc.changed_on_disk():
            return                              # locked: Claude reloads by command
        stamp = self.doc.disk_stamp()
        if stamp is None or stamp == self._disk_seen:
            return
        if self.editing is not None or self.extrude_panel is not None or self._dragged is not None:
            return                              # again when that is done
        self._disk_seen = stamp
        if self.doc.modified:
            self.statusBar().showMessage(f"{self.doc.name} changed on disk: not reloaded, you have unsaved "
                                         "edits (Save asks before overwriting it)", 15000)
            return
        try:
            self.doc.reload()
        except PartFileError as error:
            self.statusBar().showMessage(f"{self.doc.name} changed on disk but does not read, kept as it was: "
                                         f"{error}", 15000)
            return
        self._refresh()
        if not self.result.feature_errors:
            self.statusBar().showMessage(f"{self.doc.name} reloaded: changed on disk (Undo goes back)", 10000)

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
        error = self.write_quiet(path)
        if error:
            self.show_error(f"Cannot save the part:\n{error}")
            return False
        return True

    def write_quiet(self, path):
        """Saves to path (and the STEP if STEP on Save is on): the error text, or None."""
        try:
            self._carry_imports(path)
            self.doc.save(path)
        except (OSError, PartFileError) as error:
            return str(error)
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
        return None

    # =====================================================
    # STEP import
    # =====================================================

    def ask_import_path(self):
        start = self._folder() or ""
        path, _ = QFileDialog.getOpenFileName(self, "Import STEP", start, STEP_FILTER)
        return path or None

    def import_dialog(self):
        """File > Import STEP: asks for the file (the part must be saved: the file is copied next to it)."""
        if self.doc.path is None:
            self.show_error("Save the part first: an imported STEP file is copied next to the part file.")
            return None
        path = self.ask_import_path()
        return self.import_step(path) if path else None

    def import_step(self, source):
        """
        A STEP file's bodies into the part: copied into <name>.files next to
        the part file, an Import feature named after it (its bodies
        "<feature>:<name in the file>"); one undo step. Returns its name.
        """
        import shutil

        from geometry.step_io import file_sha256, read_bodies

        self.finish_sketch()
        try:
            read = read_bodies(source)
        except (OSError, ValueError) as error:
            self.show_error(f"Cannot import {os.path.basename(source)}:\n{error}")
            return None
        folder = self._folder()
        files = self.doc.name + ".files"
        os.makedirs(os.path.join(folder, files), exist_ok=True)
        stem, ext = os.path.splitext(os.path.basename(source))
        sha, target, n = file_sha256(source), os.path.join(files, stem + ext), 2
        while os.path.exists(os.path.join(folder, target)) and file_sha256(os.path.join(folder, target)) != sha:
            target, n = os.path.join(files, f"{stem}_{n}{ext}"), n + 1       # another file of that name there
        if not os.path.exists(os.path.join(folder, target)):
            shutil.copyfile(source, os.path.join(folder, target))
        name = re.sub(r"[^A-Za-z0-9_]+", "_", stem).strip("_") or "Import"
        if name[0].isdigit():
            name = "Import_" + name
        name = name if name not in self.part.object_names() else self.part.free_feature_name(name)
        bodies = body_names(name, read)
        taken = set(self.part.body_names())
        bodies = [b if b not in taken else f"{b}_{name}" for b in bodies]
        self.part.add_feature(Import(name, file=target.replace(os.sep, "/"), sha256=sha, bodies=bodies))
        group = name if all(g.name != name for g in self.part.groups) else self.part.free_group_name(name)
        self.part.add_to_group(group, bodies)       # its bodies move, turn, hide together
        self._commit()
        error = self.result.feature_errors.get(name)
        if error:
            self.show_error(f"{name} failed: {error}")
        else:
            self.statusBar().showMessage(f"imported {os.path.basename(source)}: {len(bodies)} bodies "
                                         f"({', '.join(bodies)}), the file copied to {target}", 10000)
        return name

    def reload_import(self, name):
        """Reload: the import takes its file as it is now (its content, and as many bodies as it has)."""
        from geometry.step_io import file_sha256, read_bodies

        feature = self.part.feature(name)
        path = feature.path(self._folder())
        try:
            read = read_bodies(path)
        except (OSError, ValueError) as error:
            self.show_error(f"Cannot reload {feature['file']}:\n{error}")
            return False
        feature.values["sha256"] = file_sha256(path)
        if len(read) != len(feature.bodies):        # bodies came or went: name them anew, the old names first
            fresh = body_names(name, read)
            group = next((self.part.group_of(b) for b in feature.bodies if self.part.group_of(b)), None)
            feature.bodies = feature.bodies[:len(read)] + fresh[len(feature.bodies):]
            if group is not None:                   # the new ones join its group
                self.part.add_to_group(group.name, [b for b in feature.bodies if self.part.group_of(b) is None])
        self._commit()
        self.statusBar().showMessage(f"{name}: reloaded {feature['file']}", 8000)
        return True

    def _carry_imports(self, path):
        """Save As elsewhere or under another name: the imports' files copied to <new name>.files, renamed."""
        import shutil

        old = self._folder()
        new_folder = os.path.dirname(os.path.abspath(path))
        new_files = os.path.basename(path)[:-len(EXTENSION)] + ".files" if path.lower().endswith(EXTENSION) \
            else os.path.splitext(os.path.basename(path))[0] + ".files"
        for feature in self.part.features:
            if feature.TYPE != "import" or old is None:
                continue
            source = feature.path(old)
            target = new_files + "/" + os.path.basename(feature["file"])
            if os.path.normcase(os.path.abspath(source)) == os.path.normcase(os.path.join(new_folder, target)):
                continue
            os.makedirs(os.path.join(new_folder, new_files), exist_ok=True)
            if os.path.isfile(source):
                shutil.copyfile(source, os.path.join(new_folder, target))
            feature.values["file"] = target

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
        return export_part(path, self.part, self.full_result())

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
        self._plane_purpose = "sketch"
        if selected is not None and selected[0] == "datum" and self._datum(selected[1]).kind == "plane":
            self.new_sketch(selected[1])
            return
        if selected is not None and selected[0] == "face" and not self._modeling_shown \
                and selected[1] in self._placed_bodies():
            selected = None                     # a placed body's face as shown: picked again, unplaced
        if selected is not None and selected[0] == "face":
            reference, why = self.face_reference(selected)
            if reference is not None:
                self.new_sketch(reference)
                return
            self.statusBar().showMessage(why, 8000)
        self.picking_plane = True
        self._show_modeling(True)
        planes = [d.name for d in self._datums() if d.kind == "plane"]
        self.view.set_pick_filter({"plane", "face"}, forced=planes)
        self.start_sketch_action.setEnabled(False)
        self.statusBar().showMessage(PICK_PLANE_HINT)

    def _stop_picking_plane(self):
        if self.picking_plane:
            self.picking_plane = False
            self.view.set_pick_filter(None)
            if self.workplane_panel is None:    # (a work plane's panel follows: modeling still)
                self._show_modeling(False)
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
            self._plane_taken(reference)
            return True
        if key[0] != "datum" or self._datum(key[1]).kind != "plane":
            return False
        self._stop_picking_plane()
        self._plane_taken(key[1])
        return True

    def _plane_taken(self, name):
        """A plane picked: a new sketch on it (Start 2D Sketch), or a work plane's base (Work Plane)."""
        if self._plane_purpose == "workplane":
            self._open_work_plane(name)
        else:
            self.new_sketch(name)

    def face_reference(self, key):
        """
        A body face (("face", body, CAD face index)) as a sketch plane by
        meaning: "Extrusion1:end", "Extrusion2:side.L3", "Revolution1:disc.L3"... the first feature
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
        return None, ("this face is not a cap or a wall of an extrusion, a start / end face or a flat ring of a "
                      "revolution, nor a planar face of an import (not supported yet)")

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
        self._frozen = self._modeling_result() if self.result.upto is None else \
            rebuild(self.part, folder=self._folder(), placement=False)
        self._frozen_placed = None
        self._shown_cache = {}
        self._show_modeling(True)               # the bodies as the features built them (no placement)
        if self.placed_view:
            self._show_placed_bodies()          # ... or placed ("As placed")
        self.placed_check.setVisible(True)
        self.view.select(None)
        self._show_sketches(force=True)         # a sketch an extrusion used shows while edited
        self.view.set_sketch(self._shown_output(name).frame, name)
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
        name = self.editing
        for action in self.tool_actions:
            action.setChecked(False)
        self.editing = None
        self._frozen = None
        self._frozen_placed = None
        self._shown_cache = {}
        self.placed_check.setVisible(False)
        self.view.set_sketch(None)
        self._show_sketches(force=True)
        self.ribbon.set_tab_enabled("Sketch", False)
        self.ribbon.show_tab("3D Model")
        self.start_sketch_action.setEnabled(True)
        self._sync_construction()
        if refresh:
            if self.result.upto is not None:
                self._refresh()                 # 3D catches up with the sketch now
            else:
                self._show_modeling(False)      # nothing changed: the placement back
                self._fill_tree()
                self._on_pick(self.view.picks)
            failed = self._failed_users(name) if name in self.result.outputs else []
            if failed:                          # its extrusion broke: the sketch stays selected, in sight
                key = ("feature", name)
                self.view.select(key)
                self._on_view_select(key)
                self._show_sketches()
                self.statusBar().showMessage(
                    f"{failed[0]} failed: {self.result.feature_errors[failed[0]]} — {name} stays shown: "
                    f"edit {failed[0]} (double click) to pick its profiles again, or close the sketch's outline",
                    15000)

    def _look_at_sketch(self):
        frame = self.view.sketch_frame
        if frame is not None:
            self.view.look_at(frame.normal, frame.v)

    def _set_line_tool(self, on):
        self._set_tool(on, self.line_action, lambda: LineTool(
            self.view, self.editing, self._edited_result, self._add_line,
            on_exit=lambda: self.line_action.setChecked(False)))

    def _set_point_tool(self, on):
        self._set_tool(on, self.point_action, lambda: PointTool(
            self.view, self.editing, self._edited_result, self._add_point,
            on_exit=lambda: self.point_action.setChecked(False)))

    def _set_trace_tool(self, on):
        self._trace = None
        self._set_tool(on, self.trace_action, lambda: TraceTool(
            self.view, self.editing, self._edited_result, self._add_trace,
            on_exit=lambda: self.trace_action.setChecked(False)))

    def _add_trace(self, start, end):
        """
        The Polyline tool's callback (anchors, as for Line): the first pair
        makes a polyline with the width of the Width field; a pair that goes
        on from the polyline's last point extends it. One undo step each.
        """
        sketch = self.part.feature(self.editing)

        def point(anchor):
            if anchor[0] == "point":
                return anchor[1]
            x, y = anchor[1]
            return sketch.add_point(fmt_mm(x), fmt_mm(y))

        a, b = point(start), point(end)
        current = next((w for w in sketch.polylines if w.name == self._trace), None)
        if current is not None and current.points[-1] == a:
            sketch.extend_polyline(current.name, b)
        else:
            self._trace = sketch.add_polyline(a, b, self.trace_width.text().strip() or "1")
        self._commit()
        return a, b

    def _set_spline_tool(self, on):
        self._spline = None
        self._set_tool(on, self.spline_action, lambda: SplineTool(
            self.view, self.editing, self._edited_result, self._add_spline,
            on_exit=lambda: self.spline_action.setChecked(False)))

    def _add_spline(self, start, end):
        """
        The Spline tool's callback (anchors, as for Line): the first pair
        makes a spline; a pair that goes on from its last point extends it
        (back to its first point: closed). One undo step each.
        """
        sketch = self.part.feature(self.editing)

        def point(anchor):
            if anchor[0] == "point":
                return anchor[1]
            x, y = anchor[1]
            return sketch.add_point(fmt_mm(x), fmt_mm(y))

        a, b = point(start), point(end)
        current = next((w for w in sketch.splines if w.name == self._spline), None)
        if current is not None and current.points[-1] == a:
            sketch.extend_spline(current.name, b)
        else:
            self._spline = sketch.add_spline(a, b, construction=self.construction_mode)
        self._commit()
        return a, b

    def set_placed_view(self, on):
        """
        "As placed": in sketch mode the bodies as placed (after the
        transforms) or as modeling has them; a sketch on a placed body's face
        is shown moved with that body (its own x, y unchanged).
        """
        self.placed_view = bool(on)
        if self.placed_check.isChecked() != self.placed_view:
            self.placed_check.setChecked(self.placed_view)
            return
        if self.editing is None:
            return
        self._shown_cache = {}
        if self.placed_view:
            self._show_placed_bodies()
        else:
            self._modeling_shown = False
            self.view.set_geometry(Geometry(self._frozen.bodies), reset=False)
            self._modeling_shown = bool(self.part.transforms)
        self._show_sketches(force=True)
        self.view.set_sketch(self._shown_output(self.editing).frame, self.editing)
        self.view.set_hover(None)
        self.statusBar().showMessage("Sketch mode: the bodies as placed (Move, Mirror applied)" if self.placed_view
                                     else "Sketch mode: the bodies as modeling has them (no placement)", 5000)

    def _show_placed_bodies(self):
        if self._frozen_placed is None:
            self._frozen_placed = rebuild(self.part, folder=self._folder())
        self.view.set_geometry(Geometry(self._frozen_placed.bodies), reset=False)
        self._modeling_shown = False

    def _sketch_body(self, name):
        """The body sketch `name` lies on (its plane is a face of a feature making it), or None."""
        plane = self.part.feature(name)["plane"]
        feature_name, _, face = plane.partition(":")
        if not face or feature_name not in {f.name for f in self.part.features}:
            return None
        feature = self.part.feature(feature_name)
        if feature.TYPE == "import":
            match = re.match(r"face\.(\d+)\.", face)
            index = int(match.group(1)) - 1 if match else -1
            return feature.bodies[index] if 0 <= index < len(feature.bodies) else None
        return feature.values.get("body")

    def _shown_output(self, name):
        """
        A sketch's output as shown: as built, or (As placed, in sketch mode)
        with its frame moved by the placement of the body it lies on.
        """
        out = self.result.outputs.get(name)
        if out is None or self.editing is None or not self.placed_view or self._frozen_placed is None:
            return out
        cached = self._shown_cache.get(name)
        if cached is not None and cached[0] is out:
            return cached[1]
        body = self._sketch_body(name)
        trsf = self._frozen_placed.placements.get(body) if body is not None else None
        shown = out
        if trsf is not None:
            from geometry.kernel import placed_frame
            from model.sketch import SketchFrame

            origin, u, v = placed_frame(trsf, out.frame.origin / MM, out.frame.u, out.frame.v)
            frame = SketchFrame(origin * MM, u, v, np.cross(u, v))
            shown = dataclasses.replace(out, frame=frame)
        self._shown_cache[name] = (out, shown)
        return shown

    def _set_project_tool(self, on):
        self._set_tool(on, self.project_action, lambda: ProjectTool(
            self.view, self.editing, self._edited_result, self.project_face,
            on_exit=lambda: self.project_action.setChecked(False)))

    def project_face(self, key):
        """
        Project Geometry: the edges of body face `key` (("face", body, index),
        as the view shows it: unplaced) projected into the edited sketch as
        plain geometry (geometry/project.py, Sketch.add_projection); curves
        that are no line, circle or arc become splines within the spline
        tolerance (µm, as Convert to Spline). One undo step; returns the new
        entities' names.
        """
        from geometry.project import face_of, project_faces
        from model.curves import fit_points
        from model.features import FeatureError

        shown = self._frozen_placed if self.placed_view and self._frozen_placed is not None else self._frozen
        shape = (shown if shown is not None else self.result).shapes.get(key[1])
        if self.editing is None or shape is None:
            return []
        built = self._shown_output(self.editing)       # as shown: placed with its body when the faces are
        frame = built.frame
        try:
            tolerance = float(self.spline_tolerance) * 1e-3
        except ValueError:
            tolerance = 0.05
        items, failed = [], 0
        for item in project_faces([face_of(shape, key[2])], frame.origin / MM, frame.u, frame.v):
            if item[0] != "curve":
                items.append(item)
                continue
            try:
                xy = fit_points(*item[1:], tolerance=tolerance)
            except CurveError:
                failed += 1
                continue
            items.append(("spline", xy, len(xy) > 3 and bool(np.allclose(xy[0], xy[-1], atol=1e-6))))
        sketch = self.part.feature(self.editing)
        try:
            added = sketch.add_projection(items, built.points, self.construction_mode)
        except (FeatureError, PartError) as error:
            self.doc.revert()
            self.statusBar().showMessage(f"face {key[2]} of {key[1]} not projected: {error}", 10000)
            return []
        self._commit()
        kinds = {}
        for name in added:
            kind = "spline" if name.startswith("Spline") else {"L": "line", "C": "circle", "A": "arc"}[name[0]]
            kinds[kind] = kinds.get(kind, 0) + 1
        what = ", ".join(f"{n} {k}{'s' if n > 1 else ''}" for k, n in kinds.items()) or "nothing new"
        self.statusBar().showMessage(f"face {key[2]} of {key[1]} projected: {what}"
                                     + (f"; {failed} curve(s) left out (no spline fits)" if failed else ""), 10000)
        return added

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

    def _set_arc_tool(self, on):
        self._set_tool(on, self.arc_action, lambda: ArcTool(
            self.view, self.editing, self._edited_result, self._add_arc,
            on_exit=lambda: self.arc_action.setChecked(False), method=self.arc_method))
        self._fill_props(None)                  # the method below left

    def set_arc_method(self, method):
        """Arc's method (below left while the tool runs): "center" or "three"; the clicks so far dropped."""
        self.arc_method = method
        tool = self.view.tool
        if isinstance(tool, ArcTool):
            tool.method, tool.anchors = method, []
            tool.line.visible = False
        self._on_pick(self.view.picks)

    def _add_arc(self, method, anchors):
        """
        The Arc tool's callback, one undo step. "center": the center (a point,
        new or existing), the radius and the angles from the start and end
        clicks (12 digits: its ends land on the points clicked); "three": its
        start, end and through points (new or existing).
        """
        from model.sketch import _plain

        sketch = self.part.feature(self.editing)
        built = self.result.outputs[self.editing]

        def point(anchor):
            if anchor[0] == "point":
                return anchor[1]
            return sketch.add_point(fmt_mm(anchor[1][0]), fmt_mm(anchor[1][1]))

        def xy(anchor):
            return built.points[anchor[1]] if anchor[0] == "point" else np.asarray(anchor[1], float)

        if method == "center":
            center = xy(anchors[0])
            start, end = xy(anchors[1]) - center, xy(anchors[2]) - center
            name = sketch.add_arc(kind="center", center=point(anchors[0]), radius=_plain(float(np.linalg.norm(start))),
                                  start_angle=_plain(float(np.degrees(np.arctan2(start[1], start[0])))),
                                  end_angle=_plain(float(np.degrees(np.arctan2(end[1], end[0])))),
                                  construction=self.construction_mode)
        else:
            start, end, through = (point(a) for a in anchors)
            name = sketch.add_arc(kind="three", start=start, end=end, through=through,
                                  construction=self.construction_mode)
        self._commit()
        return name

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
        """Names of the selected lines, circles, generated curves and splines of the edited sketch."""
        if self.editing is None:
            return []
        sketch = self.part.feature(self.editing)
        names = {e.name for e in sketch.lines + sketch.circles + sketch.curves + sketch.splines + sketch.arcs}
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

        def kind(n):
            try:
                return sketch.kind_of(n)
            except KeyError:
                return None             # a generated curve's end point

        kinds = {n: kind(n) for n in names}
        skipped = [n for n in names if kinds[n] in (None, "curve")]
        names = [n for n in names if n not in skipped]
        if not names:
            self.statusBar().showMessage("Copy: generated curves are not copied (yet)", 5000)
            return False
        own = {p.name for p in sketch.points}
        points = [p for n in names for p in sketch.points_of(n)]
        lines = [sketch.line(n) for n in names if kinds[n] == "line"]
        circles = [sketch.circle(n) for n in names if kinds[n] == "circle"]
        # A polyline goes whole (a selected center segment takes its polyline along).
        owners = [n.rpartition(".")[0] if kinds[n] == "segment" else n for n in names
                  if kinds[n] in ("polyline", "segment")]
        polylines = [sketch.polyline(n) for n in dict.fromkeys(owners)]
        points += [p for w in polylines for p in w.points]
        splines = [sketch.spline(n) for n in names if kinds[n] == "spline"]
        clip = {
            "points": {p: {"xy": built.points[p].copy(), "explicit": p in own and sketch.point(p).explicit}
                       for p in dict.fromkeys(points)},
            "lines": [(e.start, e.end, e.construction) for e in lines],
            "circles": [(c.center, built.radii[c.name], c.radius, c.construction) for c in circles],
            "polylines": [(list(w.points), list(w.widths), w.width, w.start, w.end, list(w.bends), list(w.radii))
                          for w in polylines],
            "splines": [(list(w.points), w.construction) for w in splines],
        }
        # The anchor: the middle of the copy's extent (circles included).
        extent = [p["xy"] for p in clip["points"].values()]
        extent += [built.points[c.center] + sign * built.radii[c.name] for c in circles for sign in (-1, 1)]
        extent += [xy for w in polylines if w.name in built.outlines for xy in built.outlines[w.name][1]]
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
        for points, widths, width, start, end, bends, radii in clip.get("polylines", []):
            name = sketch.free_name("W")
            sketch.polylines.append(SketchPolyline(name, [names[p] for p in points], list(widths), width, start, end,
                                                   list(bends), list(radii)))
            new.append(name)
        for points, c in clip.get("splines", []):
            name = sketch.free_name("Spline")
            sketch.splines.append(SketchSpline(name, [names[p] for p in points], c))
            new.append(name)
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
        return self._shown_output(self.editing)

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
            own = {p.name for p in sketch.points}
            points, generated = [], []
            for n in names:
                try:
                    of = sketch.points_of(n)
                except KeyError:                # a curve's end point
                    of = [n]
                points += [p for p in of if p in own]
                generated += [p for p in of if p not in own]
            if not points:                      # only a generated curve or its ends: set by its arguments
                self._dragged = None
                if generated:
                    self.statusBar().showMessage(f"{', '.join(dict.fromkeys(names))}: a generated curve, it stays "
                                                 "(change its arguments in the properties)", 8000)
                return
            self._dragged = {"circle": None, "points": {
                n: (_number(sketch.point(n).x), _number(sketch.point(n).y)) for n in dict.fromkeys(points)}}
            return
        if self._dragged is None:
            return
        if phase == "move":
            sketch = self.part.feature(sketch_name)
            before = self._shown_output(sketch_name)
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
                    mine = ({e.name for e in before.lines if {e.start, e.end} & set(group)}
                            | {c.name for c in before.circles if c.center in group}
                            | {w.name for w in before.splines + before.polylines if set(w.points) & set(group)})
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
            self.show_result(self._build())
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
            self.statusBar().showMessage(f"{', '.join(fixed)}: set by a formula, it stays "
                                         "(change the formula or its parameter)", 8000)

    def delete_selected(self):
        """The Delete key: the selected sketch entities, or the selected feature."""
        key = self.view.selected
        if key is None:
            return
        entities = [k for k in self.view.selection if k[0] == "entity"]
        if entities:
            ends = []                           # a curve's end points: the curve sets them, they are not deleted
            for _, sketch_name, name in entities:
                sketch = self.part.feature(sketch_name)
                owner, _, end = name.rpartition(".")
                if end in ("start", "end") and any(c.name == owner for c in sketch.curves + sketch.arcs):
                    ends.append(name)
                    continue
                try:
                    sketch.kind_of(name)
                except KeyError:
                    continue                    # an earlier delete took it already
                sketch.delete(name)
            if len(ends) == len(entities):
                self.statusBar().showMessage(f"{', '.join(ends)}: the end of a curve, set by it: delete or hide "
                                             "the curve instead", 8000)
                return
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
        does), the edited one, a used one whose browser row is hovered or
        selected (it shows green / blue meanwhile), and a used one whose user
        failed (its body may be gone: the sketch is what is left to see).
        """
        used = self._consumed_sketches()
        outputs = self.result.outputs
        wanted = {self.editing} | {k[1] for k in (self.view.hovered, self.view.selected)
                                   if k is not None and k[0] in ("feature", "entity")}
        wanted |= {name for name in used if self._failed_users(name)}
        if (self.extrude_panel is not None and self._sweep == "revolve") or self._picking_axis:
            wanted |= set(used)                 # an axis may be in any of them
        shown = [(f.name, self._shown_output(f.name)) for f in self.part.features
                 if f.TYPE == "sketch" and f.name in outputs and (f.name not in used or f.name in wanted)]
        # force: after a rebuild (their geometry changed); else only when the set of names changes
        if force or [name for name, _ in shown] != list(self.view._sketch_geometry):
            self.view.set_sketches(shown)

    def _failed_users(self, name):
        """The features using feature `name` that failed (an extrusion whose profile is gone)."""
        errors = self.result.feature_errors if self.result is not None else {}
        return [n for n in self.part.feature_users(name) if n in errors]

    def _consumed_sketches(self):
        """{sketch name: the first feature that uses it} (an extrusion)."""
        used = {}
        for feature in self.part.features:
            if feature.TYPE in ("extrude", "revolve"):
                used.setdefault(feature["sketch"], feature.name)
        return used

    def _selection_targets(self):
        """What Move takes: the group selected in the browser, else the selected bodies."""
        if self._selected_group is not None and any(g.name == self._selected_group for g in self.part.groups):
            return [GROUP + self._selected_group]
        return list(dict.fromkeys(k[1] for k in self.view.selection if k[0] == "body"))

    def start_move(self, edit=None, rotate=False, mirror=False):
        """
        Placement > Move / Rotate / Mirror: a panel for a transform of the
        selected bodies or group (Move: an offset, an axis and an angle;
        Mirror: a plane through its center), a preview of them transformed;
        Apply adds it to the placement (a body of a group before its group).
        edit: a transform to change.
        """
        if self.move_panel is not None:
            self.move_panel.raise_()
            return
        self.finish_sketch()
        if edit is not None:
            t = self.part.transform(edit)
            kind, targets = t.TYPE, list(t.targets)
        else:
            kind, targets = ("mirror" if mirror else "move"), self._selection_targets()
            groups = {g.name for g in self.part.groups}
            targets = [x for x in targets if not x.startswith(GROUP) or x[len(GROUP):] in groups]
        self._moving = {"edit": edit, "targets": targets, "kind": kind}
        title = f"Edit {edit}" if edit else ("Mirror" if mirror else "Rotate" if rotate else "Move")
        if kind == "mirror":
            panel = MirrorPanel(self, self.result.values, targets, plane=t["plane"] if edit else "YZ", title=title)
        else:
            offset, axis = (t["offset"], t["axis"] or None) if edit else (["0", "0", "0"], None)
            angle = t["angle"] if edit else ("90" if rotate else "0")
            panel = MovePanel(self, self.result.values, targets, offset=offset, axis=axis,
                              axis_label=self._work_axis_label(axis) if axis else None, angle=angle, title=title)
            panel.pick_axis.connect(self._pick_move_axis)
        panel.applied.connect(self._apply_move)
        panel.cancelled.connect(self._end_move)
        panel.changed.connect(self._preview_move)
        panel.cleared.connect(lambda: self._set_move_targets([]))
        self.move_panel = panel
        for action in (self.move_action, self.rotate_action, self.mirror_action):
            action.setEnabled(False)
        self._set_move_targets(targets)
        panel.show()
        self.statusBar().showMessage(f"{title}: click bodies or groups under Solid Bodies (or bodies in 3D) to take "
                                     "or drop them · Apply · Esc: cancel")
        if rotate and edit is None:
            self._pick_move_axis()

    def _toggle_move_target(self, key):
        """
        A click on a body or a group while Move runs: takes it or drops it.
        A group takes the place of its bodies; a body of a group drops the
        group. A body of a group placed above an edited transform is refused
        (a body of a group is placed before its group).
        """
        targets = list(self._moving["targets"])
        if key[0] == "group":
            target = GROUP + key[1]
            if target in targets:
                targets.remove(target)
            else:
                members = set(self.part.group(key[1]).bodies)
                targets = [t for t in targets if t not in members] + [target]
        else:
            body = key[1]
            group = self.part.group_of(body)
            if body in targets:
                targets.remove(body)
            else:
                if group is not None:
                    above = self._group_moved_above(group.name)
                    if above is not None:
                        self.statusBar().showMessage(
                            f"{body} is in group {group.name}, placed by {above} above this transform: place a "
                            "body of a group before its group (a new Move goes there by itself)", 10000)
                        self._set_move_targets(targets)
                        return
                    targets = [t for t in targets if t != GROUP + group.name]
                targets.append(body)
        self._set_move_targets(targets)

    def _group_moved_above(self, group):
        """The transform above the one being edited that places `group`, or None (a new one goes before it)."""
        edit = self._moving["edit"]
        if edit is None:
            return None
        for t in self.part.transforms:
            if t.name == edit:
                return None
            if GROUP + group in t.targets:
                return t.name
        return None

    def _set_move_targets(self, targets):
        """The Move's targets: shown in its panel (with where a new one goes), highlighted, previewed."""
        self._moving["targets"] = list(targets)
        placed = ""
        if self._moving["edit"] is None and targets:
            trial = Part.from_dict(self.part.to_dict())
            at = trial.add_transform(Move(trial.free_feature_name("Move"), targets=targets))
            if at < len(self.part.transforms):
                placed = f"goes before {self.part.transforms[at].name} (it places a group of these bodies)"
        self.move_panel.set_targets(targets, placed)
        try:
            bodies = expand(targets, self.part, self.result.shapes)
        except ValueError:
            bodies = []
        self.view.select_keys([("body", b) for b in bodies])
        self._preview_move()

    def _move_transform(self):
        """The transform the panel describes (FeatureError if it cannot be one)."""
        panel, state = self.move_panel, self._moving
        if state.get("kind") == "mirror":
            return Mirror(state["edit"] or self.part.free_feature_name("Mirror"), targets=state["targets"],
                          plane=panel.plane.currentText())
        name = state["edit"] or self.part.free_feature_name("Move")
        return Move(name, targets=state["targets"], offset=[e.text().strip() for e in panel.offset],
                    axis=panel.axis or "", angle=panel.angle.text().strip() if panel.axis else "0")

    def _preview_move(self):
        """The targets as the transform leaves them, see-through; the panel told why if it cannot."""
        panel = self.move_panel
        if panel is None:
            return
        from model.features import FeatureError

        if not self._moving["targets"]:         # nothing to show (applied so, it fails: being set up)
            panel.set_error(None)
            self.view.set_preview(None)
            return
        try:
            transform = self._move_transform()
            trial = Part.from_dict(self.part.to_dict())
            if self._moving["edit"]:
                at = [t.name for t in trial.transforms].index(transform.name)
                trial.transforms[at] = transform
            else:
                at = trial.add_transform(transform)
            trial.transforms = trial.transforms[:at + 1]
            built = rebuild(trial, folder=self._folder(), tessellate=False)
            if transform.name in built.feature_errors:
                raise ValueError(built.feature_errors[transform.name])
            shapes = [built.shapes[b] for b in expand(transform.targets, trial, built.shapes)]
            panel.set_error(None)
        except (ValueError, KeyError, FeatureError) as error:
            panel.set_error(str(error))
            self.view.set_preview(None)
            return
        from OCP.BRep import BRep_Builder
        from OCP.TopoDS import TopoDS_Compound

        compound, builder = TopoDS_Compound(), BRep_Builder()
        builder.MakeCompound(compound)
        for shape in shapes:
            builder.Add(compound, shape)
        self.view.set_preview(body_from_shape("preview", compound))

    def _pick_move_axis(self):
        """Pick: a click in the view takes the axis (a straight sketch line or an origin axis)."""
        self._picking_axis = True
        self.view.set_profile_mode([])
        self.view.set_axis_mode(True)
        self.view.on_axis = self._move_axis_picked
        self._show_sketches()
        self.statusBar().showMessage("Move: click the axis to turn around: a straight line of a sketch or an "
                                     "origin axis")

    def _move_axis_picked(self, key):
        axis = key[1] if key[0] == "datum" else f"{key[1]}:{key[2]}"
        self._end_axis_pick()
        if self.move_panel is not None:
            self.move_panel.set_axis(axis, self._work_axis_label(axis))

    def _apply_move(self):
        from model.features import FeatureError

        try:
            transform = self._move_transform()
        except (ValueError, FeatureError) as error:
            self.show_error(f"Move: {error}")
            return
        edit = self._moving["edit"]
        self._end_move()
        if edit:
            at = [t.name for t in self.part.transforms].index(edit)
            self.part.transforms[at] = transform
        else:
            self.part.add_transform(transform)
        self._commit()
        error = self.result.feature_errors.get(transform.name)
        if error and not transform.targets:     # left to be set up: red, no dialog
            self.statusBar().showMessage(f"{transform.name}: no targets yet (red): Edit it to pick them", 10000)
        elif error:
            self.show_error(f"{transform.name} failed: {error}")

    def _end_move(self):
        panel, self.move_panel = self.move_panel, None
        if panel is not None:
            signals = [panel.applied, panel.cancelled, panel.changed, panel.cleared]
            for signal in signals + ([] if isinstance(panel, MirrorPanel) else [panel.pick_axis]):
                signal.disconnect()
            panel.close()
        self._end_axis_pick()
        self._moving = None
        self.view.select(None)
        self.view.set_preview(None)
        for action in (self.move_action, self.rotate_action, self.mirror_action):
            action.setEnabled(True)

    def reorder_transform(self, name, step):
        """Move Up / Move Down in the placement (refused if a body of a group would come after its group)."""
        try:
            self.part.move_transform(name, step)
        except PartError as error:
            self.statusBar().showMessage(f"{name}: {error}", 8000)
            return False
        self._commit()
        return True

    def delete_transform(self, name):
        self.part.delete_transform(name)
        self._commit()

    def start_work_plane(self, edit=None):
        """
        3D Model > Work Plane: a panel: Offset or Angle from a base (the
        selected plane or flat face, or Pick: a click on one), or Three Points
        (Pick: three clicks); a preview; Apply makes it. edit: a work plane to
        change (the same panel).
        """
        if self.workplane_panel is not None:
            self.workplane_panel.raise_()
            return
        self.finish_sketch()
        if edit is not None:
            self._open_work_plane(self.part.feature(edit)["base"] or None, edit)
            return
        selected = self.view.selected
        if selected is not None and selected[0] == "datum" and self._datum(selected[1]).kind == "plane":
            self._open_work_plane(selected[1])
            return
        if selected is not None and selected[0] == "face" and not self._modeling_shown \
                and selected[1] in self._placed_bodies():
            selected = None                     # a placed body's face as shown: picked again, unplaced
        if selected is not None and selected[0] == "face":
            reference, why = self.face_reference(selected)
            if reference is not None:
                self._open_work_plane(reference)
                return
            self.statusBar().showMessage(why, 8000)
        self._open_work_plane(None)
        self._pick_work_base()                  # nothing selected: the base is clicked next (or Three Points)

    def _open_work_plane(self, base, edit=None):
        if self.workplane_panel is not None:    # a base picked while the panel is open
            self._workplane["base"] = base
            self.workplane_panel.set_base(base)
            return
        feature = self.part.feature(edit) if edit else None
        points = list(feature.points) if feature is not None and feature.points is not None else []
        self._workplane = {"edit": edit, "base": base, "points": points}
        axis = feature["axis"] if feature is not None and feature["axis"] else None
        panel = WorkPlanePanel(self, self.result.values, base,
                               offset=feature["offset"] if feature is not None else "10",
                               axis=axis, axis_label=self._work_axis_label(axis) if axis else None,
                               angle=feature["angle"] if axis else "30",
                               points=[self._point_label(p) for p in points] or None,
                               title=f"Edit {edit}" if edit else "Work Plane")
        panel.applied.connect(self._apply_work_plane)
        panel.cancelled.connect(self._end_work_plane)
        panel.changed.connect(self._preview_work_plane)
        panel.pick_base.connect(self._pick_work_base)
        panel.pick_axis.connect(self._pick_work_axis)
        panel.pick_points.connect(self._pick_work_points)
        self.workplane_panel = panel
        self.workplane_action.setEnabled(False)
        self._show_modeling(True)
        self._preview_work_plane()
        panel.show()
        self.statusBar().showMessage("Work Plane: Offset or Angle from a base, or Three Points (Pick) · Apply · "
                                     "Esc: cancel")

    @staticmethod
    def _work_axis_label(axis):
        owner, sep, name = axis.rpartition(":")
        return f"{name} ({owner})" if sep else axis

    @staticmethod
    def _point_label(point):
        """A work plane's point as shown: "P3 (Sketch1)", "Center Point", "(x, y, z)"."""
        if isinstance(point, list):
            return "(" + ", ".join(point) + ")"
        owner, sep, name = point.rpartition(":")
        return f"{name} ({owner})" if sep else point

    def _work_plane_feature(self):
        """The work plane the panel describes (FeatureError if it cannot be one)."""
        panel, state = self.workplane_panel, self._workplane
        name = state["edit"] or self.part.free_feature_name("WorkPlane")
        visible = self.part.feature(state["edit"])["visible"] if state["edit"] else True
        if panel.is_points():
            return WorkPlane(name, points=state["points"], visible=visible)
        if panel.is_angle():
            return WorkPlane(name, base=state["base"] or "", axis=panel.axis or "",
                             angle=panel.angle.text().strip(), visible=visible)
        return WorkPlane(name, base=state["base"] or "", offset=panel.offset.text().strip(), visible=visible)

    def _preview_work_plane(self):
        """The plane the panel makes, shown (forced) while it runs; the panel told why if it cannot be made."""
        from model.features import FeatureError

        panel = self.workplane_panel
        if panel is None:
            return
        datums = self._datums()
        try:
            feature = self._work_plane_feature()
            trial = Part.from_dict(self.part.to_dict())
            if self._workplane["edit"]:
                index = [f.name for f in trial.features].index(feature.name)
                trial.features[index] = feature
            else:
                trial.add_feature(feature)
            built = rebuild(trial, upto=feature.name, folder=self._folder())
            if feature.name in built.feature_errors:
                raise ValueError(built.feature_errors[feature.name])
            f = built.outputs[feature.name].plane_frame()
            datums = [d for d in datums if d.name != feature.name]
            datums.append(Datum(feature.name, "plane", f.origin, f.normal, f.u, f.v, True))
            panel.set_error(None)
        except (ValueError, KeyError, FeatureError, PartError) as error:
            text = str(error)
            # (what the panel says already: the base or the points still to pick)
            panel.set_error(None if "base plane" in text or "points must be three" in text else text)
        self.view.set_datums(datums)

    def _pick_work_base(self):
        """Pick (Base): a click on a plane, a flat face of a body or another work plane takes it."""
        self._end_axis_pick()
        self._plane_purpose = "workplane"
        self.picking_plane = True
        self.view.set_pick_filter({"plane", "face"}, forced=[d.name for d in self._datums() if d.kind == "plane"])
        self.statusBar().showMessage("Work Plane: click its base, a plane or a flat face of a body (in 3D or in "
                                     "the browser)")

    def _pick_work_axis(self):
        """Pick (Angle): a click in the view takes a straight sketch line or an origin axis."""
        self._stop_picking_plane()
        self._end_axis_pick()
        self._picking_axis = True
        self.view.set_profile_mode([])
        self.view.set_axis_mode(True)
        self.view.on_axis = self._work_axis_picked
        self._show_sketches()
        self.statusBar().showMessage("Work Plane: click a straight line of a sketch or an origin axis")

    def _work_axis_picked(self, key):
        axis = key[1] if key[0] == "datum" else f"{key[1]}:{key[2]}"
        self._end_axis_pick()
        if self.workplane_panel is not None:
            self.workplane_panel.set_axis(axis, self._work_axis_label(axis))

    def _pick_work_points(self):
        """Pick (Three Points): three clicks on sketch points, the origin's center or a body's corners."""
        self._stop_picking_plane()
        self._end_axis_pick()
        self._workplane["points"] = []
        self.workplane_panel.set_points([])
        self._picking_axis = True               # (the view picks: all the sketches shown meanwhile)
        self.view.set_profile_mode([])
        self.view.set_point_mode(True)
        self.view.on_point = self._work_point_picked
        self._show_sketches()
        self.statusBar().showMessage("Work Plane: click three points (sketch points, the origin's center, a body's "
                                     "corners)")

    def _work_point_picked(self, key):
        """A point taken: a sketch point or an origin point by name (followed), a body's corner by its place."""
        from model.sketch import _plain

        if key[0] == "entity":
            point = f"{key[1]}:{key[2]}"
        elif key[0] == "datum":
            point = key[1]
        else:
            point = [_plain(c) for c in key[1]]
        points = self._workplane["points"] + [point]
        self._workplane["points"] = points
        if len(points) == 3:
            self.view.on_point = None
            self._end_axis_pick()
        if self.workplane_panel is not None:
            self.workplane_panel.set_points([self._point_label(p) for p in points])

    def _end_axis_pick(self):
        if self._picking_axis:
            self._picking_axis = False
            self.view.set_profile_mode(None)
            self.view.on_axis = None
            self.view.on_point = None
            self._show_sketches()

    def _apply_work_plane(self):
        from model.features import FeatureError

        try:
            feature = self._work_plane_feature()
        except (ValueError, KeyError, FeatureError) as error:
            self.show_error(f"Work Plane: {error}")
            return
        edit = self._workplane["edit"]
        self._end_work_plane()
        if edit:
            index = [f.name for f in self.part.features].index(edit)
            self.part.features[index] = feature
        else:
            self.part.add_feature(feature)
        self._commit()
        error = self.result.feature_errors.get(feature.name)
        if error:
            self.show_error(f"{feature.name} failed: {error}")

    def _end_work_plane(self):
        panel, self.workplane_panel = self.workplane_panel, None
        if panel is not None:
            for signal in (panel.applied, panel.cancelled, panel.changed, panel.pick_base, panel.pick_axis,
                           panel.pick_points):
                signal.disconnect()
            panel.close()
        self._stop_picking_plane()
        self._end_axis_pick()
        self._workplane = None
        self.workplane_action.setEnabled(True)
        if self.editing is None:
            self._show_modeling(False)
        self.view.set_datums(self._datums())

    def set_work_plane_visible(self, name, on):
        """A work plane shown or hidden (its tick); one undo step."""
        feature = self.part.feature(name)
        if feature["visible"] != on:
            feature.values["visible"] = on
            self._commit()

    def _work_plane_info(self, name):
        feature = self.part.feature(name)
        how = (f"turned {feature['angle']} deg around {feature['axis']}" if feature["axis"]
               else f"offset {feature['offset']} mm")
        lines = [f"<b>{name}</b> (work plane)", f"from {feature['base']}, {how}"]
        if name in self.result.outputs:
            d = self._datum(name)
            lines += [f"through {_fmt_point(d.origin)} mm", f"normal {_fmt_vector(d.direction)}",
                      "New Sketch (right click) puts a sketch on it"]
        error = self.result.feature_errors.get(name)
        if error:
            lines.append(f"<span style='color:#c81e1e'>error: {error}</span>")
        return "<br>".join(lines)

    def start_revolve(self, edit=None):
        """3D Model > Revolve: as Extrude, with an axis to pick (Axis) and an angle (see start_extrude)."""
        self.start_extrude(edit, sweep="revolve")

    def start_extrude(self, edit=None, sweep=None):
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
        self._sweep = feature.TYPE if feature is not None else (sweep or "extrude")
        verb = "Revolve" if self._sweep == "revolve" else "Extrude"
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
            regions = find_regions(built)          # the pieces where things cross: what is picked
            if feature is not None:                 # ... and the whole loops the extrusion names (files, AI)
                keys = {r.key for r in regions}
                regions += [r for r in whole_regions(built) if r.key in feature.profiles and r.key not in keys]
            for region in regions:
                fill = body_from_shape("profile", region_face(built.frame, region))
                items.append((("profile", name, region.key), built.frame, region, fill.vertices / MM, fill.triangles))
        if not items:
            where = f"{feature['sketch']}" if feature is not None else "a sketch"
            self.show_error(f"{verb} needs a closed region in {where} (lines meeting at their ends, or a circle).")
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
            before = rebuild(rolled, folder=self._folder(), placement=False).bodies
            self.view.set_geometry(Geometry(before), reset=False)
            self._rolled_back = True
            amount = feature["distance"] if self._sweep == "extrude" else feature["angle"]
            axis_key = self._axis_key(feature) if self._sweep == "revolve" else None
            panel = ExtrudePanel(self, self.result.values, bodies=[b.name for b in before],
                                 distance=amount, operation=feature["operation"], body=feature["body"],
                                 title=f"Edit {edit}", kind=self._sweep,
                                 axis=self._axis_label(axis_key) if axis_key else None)
            self.view.set_profile_selection([("profile", feature["sketch"], key) for key in feature.profiles])
            if axis_key is not None:
                self.view.set_axis_key(axis_key)
        else:
            self._show_modeling(True)           # the bodies as built: what it joins to, cuts from
            panel = ExtrudePanel(self, self.result.values, bodies=[b.name for b in self.result.bodies],
                                 distance="10" if self._sweep == "extrude" else "360", kind=self._sweep,
                                 title=verb)
            if len(items) == 1:                 # the only profile: taken at once (as Inventor does)
                self.view.set_profile_selection([items[0][0]])
        panel.applied.connect(self._apply_extrude)
        panel.cancelled.connect(self._end_extrude)
        panel.cleared.connect(self._clear_profiles)
        panel.changed.connect(self._update_preview)
        panel.selecting.connect(lambda which: self.view.set_axis_mode(which == "axis"))
        self.view.on_axis = self._axis_picked
        self.extrude_panel = panel
        self._show_sketches()
        self._profiles_picked(self.view.profile_selection)
        panel.show()
        self.extrude_action.setEnabled(False)
        self.revolve_action.setEnabled(False)
        what = " · Axis: click a straight line or an origin axis" if self._sweep == "revolve" else ""
        self.statusBar().showMessage(f"{'Edit ' + edit if edit else verb}: click closed regions to take or "
                                     f"drop them{what} · Apply · Esc: cancel")

    def _axis_key(self, feature):
        """The view's key of a revolution's axis: ("entity", sketch, line) or ("datum", name)."""
        owner, sep, name = feature["axis"].rpartition(":")
        if sep:
            return ("entity", owner, name)
        if any(d.name == name for d in self.part.origin):
            return ("datum", name)
        return ("entity", feature["sketch"], name)

    @staticmethod
    def _axis_label(key):
        return f"{key[2]} ({key[1]})" if key[0] == "entity" else key[1]

    def _axis_text(self, key, sketch):
        """An axis key as a revolution names it: "L3" (its own sketch), "Sketch2:L3", or "Y Axis"."""
        if key[0] == "datum":
            return key[1]
        return key[2] if key[1] == sketch else f"{key[1]}:{key[2]}"

    def _axis_picked(self, key):
        """Revolve's axis taken in the view: shown, and clicks take profiles again."""
        if self.extrude_panel is None:
            return
        self.extrude_panel.set_axis(self._axis_label(key))
        self.extrude_panel.select("profiles")
        self._update_preview()

    def _clear_profiles(self):
        """The panel's Clear: no profile selected, the preview gone; pick them again."""
        self.view.set_profile_selection([])
        self._profiles_picked([])

    def _profiles_picked(self, keys):
        """Profiles come from one sketch: a pick in another sketch starts over with it."""
        if keys:
            sketch = keys[-1][1]
            if any(k[1] != sketch for k in keys):
                self.view.set_profile_selection([keys[-1]])
        if self.extrude_panel is not None:
            self.extrude_panel.set_profile_count(len(self.view.profile_selection))
            if self._sweep == "revolve" and self.view.profile_selection and self.view.axis_key is None:
                self.extrude_panel.select("axis")       # profiles taken: now the axis
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
        regions = named_regions(built)
        try:
            used = [regions[k[2]] for k in keys]
            if self._sweep == "revolve":
                if self.view.axis_key is None:
                    self.view.set_preview(None)
                    return
                axis = axis_of(built, self._axis_text(self.view.axis_key, keys[0][1]), self.part, self.result.outputs)
                check_side(built, used, axis)
                shape = revolve_shape(built.frame, onto_axis(built, used, axis), axis[0], axis[1], distance)
            else:
                shape = extrude_shape(built.frame, used, distance)
        except ValueError as error:             # the axis crosses a profile, is not in the plane...
            self.view.set_preview(None)
            self.statusBar().showMessage(f"{self._sweep.capitalize()}: {error}", 8000)
            return
        except (RuntimeError, KeyError):
            self.view.set_preview(None)
            return
        self.view.set_preview(body_from_shape("preview", shape), cut=panel.operation_key() == "cut")

    def _apply_extrude(self, distance):
        keys = list(self.view.profile_selection)
        panel, edit, sweep, axis_key = self.extrude_panel, self._extruding, self._sweep, self.view.axis_key
        operation, target = panel.operation_key(), panel.body_name()
        self._end_extrude()
        if sweep == "revolve" and axis_key is None:
            return
        if not keys:
            return
        profiles = [k[2] for k in keys]
        sketch = keys[0][1]
        amount = {"distance": distance} if sweep == "extrude" else {"angle": distance,
                                                                       "axis": self._axis_text(axis_key, sketch)}
        if edit:
            feature = self.part.feature(edit)
            if operation == "new":              # keeps its own name if it made a body already
                target = feature["body"] if feature["operation"] == "new" else self.part.free_body_name()
            feature.profiles = [tuple(sorted(p)) for p in profiles]
            feature.values.update(operation=operation, body=target, **amount)
        else:
            kind, stem = (Extrude, "Extrusion") if sweep == "extrude" else (Revolve, "Revolution")
            feature = kind(self.part.free_feature_name(stem), profiles=profiles, sketch=sketch,
                           operation=operation, body=target if operation != "new" else self.part.free_body_name(),
                           **amount)
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
            panel.selecting.disconnect()
            panel.close()
        self.view.set_profile_mode(None)
        self.view.on_profiles = None
        self.view.on_axis = None
        self.view.set_preview(None)
        if self._rolled_back:                   # the whole model again
            self._rolled_back = False
            self._modeling_shown = False
            self.view.set_geometry(Geometry(self.result.bodies), reset=False)
        self._show_modeling(False)
        self._extruding = None
        self.extrude_action.setEnabled(True)
        self.revolve_action.setEnabled(True)
        self._show_sketches()                   # the sketches shown for an axis hide again
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
        if key[0] == "entity":                  # a generated curve's row
            sketch_name, curve = key[1], key[2]
            visible = self.part.feature(sketch_name).gen_curve(curve).visible
            return [("Hide" if visible else "Show", lambda: self.set_curve_visible(sketch_name, curve, not visible)),
                    ("Convert to Spline", lambda: self.curve_to_spline(sketch_name, curve, keep=False)),
                    ("Copy as Spline", lambda: self.curve_to_spline(sketch_name, curve, keep=True))]
        kind, name = key
        if kind == "body":
            bodies = self._selected_bodies()
            bodies = bodies if name in bodies else [name]       # the selection, if the row is in it
            actions = [("Rename...", lambda: self.rename_body_dialog(name))] if len(bodies) == 1 else []
            actions.append(("Group", self._group_menu(bodies)))
            if any(self.part.group_of(b) is not None for b in bodies):
                actions.append(("Remove from Group", lambda: self.remove_from_group(bodies)))
            return actions
        if kind == "group":
            return [("Rename...", lambda: self.rename_group_dialog(name)), ("Ungroup", lambda: self.ungroup(name))]
        if kind == "transform":
            return [("Edit", lambda: self.start_move(edit=name)), ("Move Up", lambda: self.reorder_transform(name, -1)),
                    ("Move Down", lambda: self.reorder_transform(name, 1)),
                    ("Delete", lambda: self.delete_transform(name))]
        if kind == "body_color":
            return [("Pick a color...", lambda: self.pick_body_color(name)),
                    ("Default color", lambda: self.set_body_color(name, None))]
        if kind == "datum" and self._is_workplane(name):
            built = name in self.result.outputs
            return ([("New Sketch", lambda: self.new_sketch(name))] if built else []) + [
                ("Edit Work Plane", lambda: self.start_work_plane(edit=name)),
                ("Delete", lambda: self.delete_feature(name))]
        if kind == "datum" and self.part.datum(name).kind == "plane":
            return [("New Sketch", lambda: self.new_sketch(name))]
        if kind == "feature" and self.part.feature(name).TYPE == "sketch":
            actions = [] if name == self.editing else [("Edit Sketch", lambda: self.edit_sketch(name))]
            return actions + [("Delete", lambda: self.delete_feature(name))]
        if kind == "feature" and self.part.feature(name).TYPE == "extrude":
            return [("Edit Extrusion", lambda: self.start_extrude(edit=name)),
                    ("Delete", lambda: self.delete_feature(name))]
        if kind == "feature" and self.part.feature(name).TYPE == "import":
            return [("Reload", lambda: self.reload_import(name)), ("Delete", lambda: self.delete_feature(name))]
        if kind == "feature" and self.part.feature(name).TYPE == "revolve":
            return [("Edit Revolution", lambda: self.start_extrude(edit=name)),
                    ("Delete", lambda: self.delete_feature(name))]
        return []

    def view_context_actions(self, key):
        """
        [(text, callable)] of the 3D view's right-click menu for `key` (what is
        under the cursor): a flat face or a plane: New Sketch on it, a Work
        Plane from it (a placed body's face: picked again, unplaced).
        """
        if key is None:
            return []
        if key[0] == "datum":
            try:
                if self._datum(key[1]).kind != "plane":
                    return []
            except KeyError:
                return []
            name = key[1]
            return [("New Sketch", lambda: self.new_sketch(name)), ("Work Plane", lambda: self._open_work_plane(name))]
        if key[0] != "face":
            return []
        if key[1] in self._placed_bodies() and not self._modeling_shown:
            return [("New Sketch (pick the face as built)...", self.start_sketch)]
        reference, why = self.face_reference(key)
        if reference is None:
            self.statusBar().showMessage(why, 8000)
            return []
        return [("New Sketch", lambda: self.new_sketch(reference)),
                ("Work Plane", lambda: self._open_work_plane(reference))]

    def _view_context_menu(self, key, pos):
        """A right click in 3D: what is under the cursor selected, its menu."""
        actions = self.view_context_actions(key)
        if not actions:
            return
        self.view.select(key)
        self._on_view_select(key)
        menu = QMenu(self)
        for text, slot in actions:
            menu.addAction(text).triggered.connect(lambda _=False, f=slot: _later(f))
        native = self.view.canvas.native
        menu.exec(native.mapToGlobal(QPoint(int(pos[0]), int(pos[1]))))

    def _show_context_menu(self, pos):
        actions = self.context_actions(self.tree.itemAt(pos))
        if not actions:
            return
        menu = QMenu(self)
        for text, slot in actions:
            if isinstance(slot, list):          # a submenu: [(text, callable)]
                sub = menu.addMenu(text)
                for subtext, subslot in slot:
                    sub.addAction(subtext).triggered.connect(lambda _=False, f=subslot: f())
            else:
                menu.addAction(text).triggered.connect(lambda _=False, f=slot: f())
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def _row_clicked(self, item):
        key = self._key_of(item)
        if key is not None and key[0] in ("body", "group") and self.move_panel is not None:
            self._toggle_move_target(key)       # Move runs: the row takes or drops a target
            return
        if key is not None and key[0] == "body_color":
            self.pick_body_color(key[1])

    def ask_text(self, title, label, text):
        """A line of text from a small dialog, or None if cancelled."""
        value, ok = QInputDialog.getText(self, title, label, QLineEdit.Normal, text)
        return value if ok else None

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

    def _group_members(self, name):
        """The built bodies of group `name`."""
        built = {b.name for b in self.result.bodies}
        try:
            return [b for b in self.part.group(name).bodies if b in built]
        except KeyError:
            return []

    def _group_menu(self, bodies):
        """[(text, callable)]: the existing groups, and a new one (named Group1...), for these bodies."""
        rows = [(g.name, lambda g=g.name: self.group_bodies(g, bodies)) for g in self.part.groups]
        return rows + [("New Group", lambda: self.group_bodies(self.part.free_group_name(), bodies))]

    def group_dialog(self, body):
        """Group... (a body's menu): asks which group (an existing one or a new name) it goes into."""
        current = self.part.group_of(body)
        names = ", ".join(g.name for g in self.part.groups)
        label = f"Group of {body} (an existing group{': ' + names if names else ''}, or a new name):"
        answer = self.ask_text(f"Group {body}", label, current.name if current else self.part.free_group_name())
        if answer is not None and answer.strip():
            self.group_bodies(answer.strip(), [body])

    def group_bodies(self, name, bodies):
        """Bodies into group `name` (made if new; out of their old group); one undo step."""
        try:
            self.part.add_to_group(name, bodies)
        except PartError as error:
            self.statusBar().showMessage(f"Group: {error}", 8000)
            return False
        self._commit()
        row = self._rows.get(("group", name))
        if row is not None:                     # the group's row current: F2 renames it
            self.tree.setCurrentItem(row)
        self.statusBar().showMessage(f"{', '.join(bodies)} in group {name} (F2: rename it)", 8000)
        return True

    def remove_from_group(self, bodies):
        self.part.remove_from_group([bodies] if isinstance(bodies, str) else list(bodies))
        self._commit()

    def rename_group_dialog(self, name):
        new = self.ask_text(f"Rename {name}", "New name of the group:", name)
        if new is None:
            return
        try:
            self.part.rename_group(name, new)
        except PartError as error:
            self.statusBar().showMessage(f"Rename {name}: {error}", 8000)
            return
        if self._selected_group == name:        # still the selected one, by its new name
            self._selected_group = new.strip()
        self._commit()

    def ungroup(self, name):
        """The group goes; its bodies stay (one undo step)."""
        self.part.ungroup(name)
        self._commit()

    def rename_body_dialog(self, name):
        """Rename (the body's right-click menu, double click, F2): asks for the new name."""
        new = self.ask_text(f"Rename {name}", "New name of the body:", name)
        if new is not None:
            self.rename_body(name, new)

    def rename_body(self, old, new):
        """
        A body renamed everywhere (the features making and changing it, its
        color, STEP on the next save); one undo step. A bad or taken name: the
        status bar says why, nothing changes.
        """
        try:
            self.part.rename_body(old, new)
        except PartError as error:
            self.statusBar().showMessage(f"Rename {old}: {error}", 8000)
            return False
        new = new.strip()
        if new == old:
            return True
        hidden = not self.view._visible.get(old, True)
        selected = self.view.selected == ("body", old)
        self._commit()
        if hidden:                              # it stays hidden under its new name
            self.view.set_visible(new, False)
            self._fill_tree()
        if selected:
            self.view.select(("body", new))
            self._on_view_select(("body", new))
        self.statusBar().showMessage(f"{old} renamed {new} (in every feature using it)", 5000)
        return True

    def _rename_current(self):
        """F2 in the browser: rename the body on the current row."""
        key = self._key_of(self.tree.currentItem())
        if key is not None and key[0] == "body":
            self.rename_body_dialog(key[1])
        elif key is not None and key[0] == "group":
            self.rename_group_dialog(key[1])

    def _row_double_clicked(self, item):
        key = self._key_of(item)
        if key is not None and key[0] == "body":
            _later(lambda: self.rename_body_dialog(key[1]))
            return
        if key is not None and key[0] == "group":
            _later(lambda: self.rename_group_dialog(key[1]))
            return
        if key is not None and key[0] == "transform":
            _later(lambda: self.start_move(edit=key[1]))
            return
        if key is not None and key[0] == "datum" and self._is_workplane(key[1]):
            _later(lambda: self.start_work_plane(edit=key[1]))
            return
        if key is None or key[0] != "feature":
            return
        kind = self.part.feature(key[1]).TYPE
        if kind == "sketch":
            self.edit_sketch(key[1])
        elif kind in ("extrude", "revolve"):
            self.start_extrude(edit=key[1])

    # =====================================================
    # Control by Claude (view/control.py): locked, the user only looks
    # =====================================================

    def set_locked(self, locked, message=""):
        """
        Locked: the ribbon, the browser and the panels are off, the window's
        shortcuts cleared (a disabled ribbon leaves them working), the 3D
        view only for looking; a banner says who is in control, its button
        gives the control back to the user.
        """
        if locked:
            self.lock_label.setText(f"  Claude is in control: {message}" if message else "  Claude is in control")
        if locked == self.locked:
            return
        self.locked = locked
        if locked:
            self._shortcuts = {action: action.shortcuts() for action in self.actions()}
            for action in self._shortcuts:
                action.setShortcuts([])
        else:
            for action, shortcuts in self._shortcuts.items():
                action.setShortcuts(shortcuts)
            self._shortcuts = {}
        for widget in (self.ribbon, self.side_tabs):
            widget.setEnabled(not locked)
        self.view.look_only = locked
        self.lock_bar.setVisible(locked)

    def open_panel(self):
        """The command panel open (extrude, revolve, workplane, move, mirror), or None."""
        if self.extrude_panel is not None:
            return self._sweep
        if self.workplane_panel is not None:
            return "workplane"
        if self.move_panel is not None:
            return type(self.move_panel).__name__.replace("Panel", "").lower()
        return None

    def user_busy(self):
        """What the user is in the middle of (lock waits), or "" if nothing."""
        if self.editing is not None:
            return f"editing the sketch {self.editing}"
        panel = self.open_panel()
        if panel:
            return f"the {panel} panel is open"
        if self.picking_plane or self._picking_axis:
            return "picking in 3D"
        if self._dragged is not None or self.view._drag is not None or self.view._box is not None:
            return "dragging"
        if QApplication.activeModalWidget() is not None or QApplication.activePopupWidget() is not None:
            return "a dialog or a menu is open"
        if self.tree.state() == QAbstractItemView.EditingState \
                or self.parameters.table.state() == QAbstractItemView.EditingState:
            return "editing a field"
        return ""

    def open_quiet(self, path):
        """Opens a part without dialogs: the error text (the window as it was), or None."""
        if self.doc.modified:
            return "the open part has unsaved edits: save them first"
        try:
            doc = Document.open(path)
        except PartFileError as error:
            return f"does not read: {error}"
        self._set_document(doc)
        self._remember(doc.path)
        return None

    def reload_quiet(self):
        """The open file again (one undo step), the camera kept: the error text, or None."""
        if self.doc.path is None:
            return "the part has no file"
        try:
            self.doc.reload()
        except PartFileError as error:
            return f"does not read, the part kept as it was: {error}"
        self._disk_seen = None
        self._refresh()
        return None

    def save_quiet(self):
        """Save without dialogs: the error text, or None."""
        if self.doc.path is None:
            return "the part has no file yet (open one; the user saves a new part once)"
        if self.doc.changed_on_disk():
            return ("the file changed on disk since it was read: reload it first (that drops the edits "
                    "made since), or write the file and reload")
        return self.write_quiet(self.doc.path)

    def set_parameters_quiet(self, formulas):
        """
        Parameter formulas {name: text} as one undo step, then rebuilt. A
        name that is not a parameter, or a formula that does not evaluate:
        the error text, nothing changed.
        """
        names = [p.name for p in self.part.parameters]
        missing = [name for name in formulas if name not in names]
        if missing:
            return f"no parameter named {missing}; parameters: {names}"
        before = evaluate_parameters([(p.name, p.formula) for p in self.part.parameters])[1]
        for name, formula in formulas.items():
            self.part.parameter(name).formula = formula
        errors = evaluate_parameters([(p.name, p.formula) for p in self.part.parameters])[1]
        broken = {name: error for name, error in errors.items() if name in formulas or name not in before}
        if broken:
            self.doc.revert()
            return "; ".join(f"{name}: {error}" for name, error in broken.items()) + " (nothing changed)"
        self._commit()
        return None

    def select_row(self, key):
        """Selects a browser row as a click does (its highlight); False if there is none."""
        row = self._rows.get(key)
        if row is None:
            return False
        self.tree.clearSelection()
        self.tree.setCurrentItem(row)
        self.tree.scrollToItem(row)
        return True

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
        current = self._key_of(self.tree.currentItem())     # a feature's row stays current (its highlight)
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
            if feature.TYPE == "workplane":        # a datum: a tick for showing it, picked like the origin's
                key = ("datum", feature.name)
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(0, Qt.Checked if feature["visible"] else Qt.Unchecked)
            item.setText(0, feature.name)
            item.setData(0, Qt.UserRole, key)
            if feature.TYPE == "workplane":
                how = (f"turned {feature['angle']} deg around {feature['axis']}" if feature["axis"]
                       else f"offset {feature['offset']} mm")
                tip = f"work plane: {feature['base']} {how}\nTick: shown in 3D. Double click: edit"
            elif feature.TYPE == "sketch":
                tip = f"sketch on {feature['plane']}"
            elif feature.TYPE == "extrude":
                what = {"new": "into", "join": "joined to", "cut": "cut from", "intersect": "intersected with"}
                tip = (f"extrusion of {feature['sketch']} by {feature['distance']} mm "
                       f"{what[feature['operation']]} {feature['body']}")
            elif feature.TYPE == "import":
                tip = f"import of {feature['file']}: {', '.join(feature.bodies)}\nRight click: Reload"
            elif feature.TYPE == "revolve":
                what = {"new": "into", "join": "joined to", "cut": "cut from", "intersect": "intersected with"}
                tip = (f"revolution of {feature['sketch']} by {feature['angle']} deg around {feature['axis']} "
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
            if feature.TYPE == "sketch":        # its generated curves, one row each (tick: shown)
                for c in feature.curves:
                    row_key = ("entity", feature.name, c.name)
                    self._rows[row_key] = _checkable(
                        item, c.name, row_key, f"{c.kind} curve: {KINDS[c.kind].DOC}\nTick: shown (hidden: not drawn, "
                        "not in profiles). Select: its arguments. Right click: Convert / Copy as Spline.", c.visible)
        if self.part.transforms:                # the placement, after the features
            placement = _group_item(root, "Placement")
            placement.setToolTip(0, "Transforms of the bodies as built, in order (a body of a group before "
                                    "its group). Right click: Edit, Move Up / Down, Delete")
            for t in self.part.transforms:
                key = ("transform", t.name)
                row = QTreeWidgetItem([t.name])
                row.setData(0, Qt.UserRole, key)
                tip = f"{t.TYPE} {', '.join(t.targets)}: {t.describe()}"
                if t.name in errors:
                    row.setForeground(0, QBrush(ERROR_COLOR))
                    tip += f"\nerror: {errors[t.name]}"
                row.setToolTip(0, tip)
                placement.addChild(row)
                self._rows[key] = row
        bodies = self.result.bodies if self.result is not None else []
        if bodies:
            folder = _group_item(None, f"Solid Bodies ({len(bodies)})")
            root.insertChild(0, folder)         # first, as in Inventor
            built = {b.name for b in bodies}
            parents = {}                        # body name -> its group's row
            for g in self.part.groups:          # each group a folder of its bodies (those built)
                members = [b for b in g.bodies if b in built]
                if not members:
                    continue
                key = ("group", g.name)
                shown = any(self.view._visible.get(b, True) for b in members)
                row = _checkable(folder, g.name, key, f"group of {len(members)} bodies. Tick: all shown. "
                                 "Select: all selected. Double click: rename. Right click: Rename, Ungroup",
                                 shown)
                font = row.font(0)
                font.setItalic(True)
                row.setFont(0, font)
                self._rows[key] = row
                parents.update({b: row for b in members})
            for body in bodies:
                key = ("body", body.name)
                parent = parents.get(body.name, folder)
                row = _checkable(parent, body.name, key, "Tick: shown in 3D. Select: selected. "
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
        for key, row in self._rows.items():     # groups open
            if key[0] == "group":
                row.setExpanded(True)
        for feature in self.part.features:      # sketches with curves: open, the curves' rows in sight
            if feature.TYPE == "sketch" and feature.curves:
                self._rows[("feature", feature.name)].setExpanded(True)
        group_row = self._rows.get(("group", self._selected_group))
        selected = group_row if group_row is not None else self._rows.get(self.view.selected)
        feature_row = self._rows.get(current) if current is not None and current[0] == "feature" else None
        if selected is None and feature_row is not None and self.view.selected is None:
            selected = feature_row              # (the view drops a non-sketch feature's key on a rebuild)
        if selected is not None:
            self.tree.setCurrentItem(selected)
        elif self.view.selected is not None and self.view.selected[0] != "entity":
            self.view.select(None)              # gone (sketch entities are not in the tree)
        if group_row is None:                   # that group is gone (ungrouped, renamed, emptied)
            self._selected_group = None
        self.tree.blockSignals(False)
        self._show_info(current if selected is feature_row and feature_row is not None else self.view.selected)

    @staticmethod
    def _key_of(item):
        return item.data(0, Qt.UserRole) if item is not None else None

    def _item_changed(self, item, column):
        key = self._key_of(item)
        if key is None:
            return
        on = item.checkState(0) == Qt.Checked
        if key[0] == "group":                   # all its bodies (as a body's tick: the view only)
            self.tree.blockSignals(True)
            for b in self._group_members(key[1]):
                self.view.set_visible(b, on)
                if ("body", b) in self._rows:
                    self._rows[("body", b)].setCheckState(0, Qt.Checked if on else Qt.Unchecked)
            self.tree.blockSignals(False)
            return
        if key[0] == "entity":                  # a curve's row: after this signal (the tree is rebuilt)
            QTimer.singleShot(0, lambda: self.set_curve_visible(key[1], key[2], on))
        elif key[0] == "datum" and self._is_workplane(key[1]):
            name = key[1]
            _later(lambda: self.set_work_plane_visible(name, on))
        elif key[0] == "datum":
            self.part.datum(key[1]).visible = on          # saved with the part, one undo step
            self.view.set_datum_visible(key[1], on)
            self._commit(refresh=False)
        else:
            self.view.set_visible(key[1], on)

    def _selected_bodies(self):
        """The bodies whose rows are selected in the browser (Shift / Ctrl: several)."""
        keys = [self._key_of(item) for item in self.tree.selectedItems()]
        return [k[1] for k in keys if k is not None and k[0] == "body"]

    def _tree_selection_changed(self):
        """Several body rows selected (Shift / Ctrl): all of them selected in 3D."""
        bodies = self._selected_bodies()
        if len(bodies) < 2 or self.move_panel is not None:
            return
        self._selected_group = None
        self.view.select_keys([("body", b) for b in bodies])
        self.info.setText(f"<b>{len(bodies)} bodies selected</b><br>{', '.join(bodies)}<br>"
                          "Right click: Group (an existing group or a new one)")

    def _item_selected(self, item):
        if len(self._selected_bodies()) > 1:
            self._tree_selection_changed()
            return
        key = self._key_of(item)
        self._selected_group = key[1] if key is not None and key[0] == "group" else None
        if key is not None and key[0] == "group":
            self.view.select_keys([("body", b) for b in self._group_members(key[1])])
            self._show_info(key)
            return
        if key is not None and key[0] == "transform":
            moved = self.result.outputs.get(key[1])
            self.view.select_keys([("body", b) for b in (moved.bodies if moved is not None else [])])
            self._show_info(key)
            return
        if self._plane_picked(key):
            return
        self.view.select(key)
        self._show_sketches()                   # a used sketch shows while its row is selected
        self._show_info(key)

    def _row_hovered(self, item):
        key = self._key_of(item)
        self.view.set_hover(key if key is not None and key[0] in ("datum", "feature", "entity") else None)
        self._show_sketches()                   # ... or hovered

    def _on_view_select(self, key):
        """A click in 3D selected `key` (or nothing): follow it in the tree."""
        if self.picking_plane:
            self._plane_picked(key)
            return
        if self.move_panel is not None and key is not None and key[0] in ("body", "face"):
            self._toggle_move_target(("body", key[1]))      # Move runs: a click on a body (a face) takes or drops it
            return
        self._selected_group = None
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
        if len(selection) > 1 and all(k[0] == "body" for k in selection):     # Shift + clicks on bodies
            self.tree.blockSignals(True)
            self.tree.clearSelection()
            for k in selection:
                if k in self._rows:
                    self._rows[k].setSelected(True)
            self.tree.blockSignals(False)
        if len(selection) > 1:
            names = ", ".join(k[-1] for k in selection)
            self.info.setText(f"<b>{len(selection)} selected</b><br>{names}")
            self._fill_props(key)               # what applies to all of them (Deparametrize)
        else:
            self._show_info(key)

    def _on_view_hover(self, key):
        """The mouse moved onto (or off) a datum in 3D: mark its row."""
        if self._hover_row is not None:
            self._hover_row.setBackground(0, QBrush())
        self._hover_row = self._rows.get(key)
        if self._hover_row is not None:
            self._hover_row.setBackground(0, HOVER_ROW)

    def _feature_highlight(self, name):
        """
        (Body, cut) of what feature `name` made, placed as its bodies are
        (an extrusion's or a revolution's swept profiles, an import's bodies
        as read), or None (a sketch, a work plane: shown otherwise).
        """
        from geometry import kernel
        from OCP.BRep import BRep_Builder
        from OCP.TopoDS import TopoDS_Compound

        feature = self.part.feature(name)
        out = self.result.outputs.get(name)
        if out is None or self.result.upto is not None:
            return None
        if feature.TYPE in ("extrude", "revolve"):
            made, cut = [(out.body, out.shape)], feature["operation"] == "cut"
        elif feature.TYPE == "import":
            made, cut = list(zip(out.bodies, out.shapes)), False
        else:
            return None
        placements = {} if self._modeling_shown else self.result.placements
        compound, builder = TopoDS_Compound(), BRep_Builder()
        builder.MakeCompound(compound)
        for body, shape in made:
            builder.Add(compound, kernel.placed(shape, placements[body]) if body in placements else shape)
        return body_from_shape(f"{name} made", compound), cut

    def _show_info(self, key):
        highlight = self._feature_highlight(key[1]) if key is not None and key[0] == "feature" else None
        self.view.set_highlight(*(highlight or (None,)))
        self._fill_props(key)
        if key is None:
            self.info.setText("")
        elif key[0] == "face":
            self.info.setText(self._face_info(key))
        elif key[0] == "transform":
            t = self.part.transform(key[1])
            lines = [f"<b>{t.name}</b> ({t.TYPE}, placement)", f"of {', '.join(t.targets)}", t.describe()]
            error = self.result.feature_errors.get(t.name)
            if error:
                lines.append(f"<span style='color:#c81e1e'>error: {error}</span>")
            self.info.setText("<br>".join(lines))
        elif key[0] == "group":
            members = self._group_members(key[1])
            volume = sum(b.volume for b in self.result.bodies if b.name in members and b.kind == "solid")
            self.info.setText(f"<b>{key[1]}</b> (group)<br>{len(members)} bodies: {', '.join(members)}"
                              f"<br>volume: {volume / MM ** 3:.6g} mm³")
        elif key[0] == "datum" and self._is_workplane(key[1]):
            self.info.setText(self._work_plane_info(key[1]))
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
            elif feature.TYPE == "import":
                lines += [f"of {feature['file']}", f"{len(feature.bodies)} bodies: {', '.join(feature.bodies)}"]
            elif feature.TYPE == "revolve":
                verb = {"new": "makes", "join": "joins", "cut": "cuts", "intersect": "intersects"}[feature["operation"]]
                lines += [f"of {feature['sketch']}: {len(feature.profiles)} profile(s)",
                          f"around {feature['axis']}", f"angle = {feature['angle']} deg", f"{verb} {feature['body']}"]
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

    # =====================================================
    # Properties (under the info)
    # =====================================================

    def _fill_props(self, key):
        """
        Editable fields of the selection: a polyline (or one of its center
        segments): its default width, its start and end; a point on
        polylines: its width on each. Each change is one undo step.
        """
        while self.props_form.rowCount():
            self.props_form.removeRow(0)
        self._add_deparametrize(key)
        if key is None and isinstance(self.view.tool, ArcTool):     # the Arc tool's method, below left
            combo = QComboBox()
            combo.addItem("From the center", "center")
            combo.addItem("Through three points", "three")
            combo.setCurrentIndex(0 if self.arc_method == "center" else 1)
            combo.setToolTip("Center: the center, its start (the radius), its end · Three points: its start, "
                             "its end, a point on it")
            combo.currentIndexChanged.connect(lambda i, c=combo: self.set_arc_method(c.itemData(i)))
            self.props_form.addRow(QLabel("<b>Arc</b>"))
            self.props_form.addRow("Method:", combo)
            return
        if key is not None and key[0] == "entity" and len(self.view.selection) > 1:
            self._add_offset_rows(key[1], [k[2] for k in self.view.selection if k[0] == "entity" and k[1] == key[1]])
        if key is None or key[0] != "entity" or len(self.view.selection) != 1:
            return
        sketch_name, name = key[1], key[2]
        sketch = self.part.feature(sketch_name)
        try:
            kind = sketch.kind_of(name)
        except KeyError:
            return
        if kind == "curve":                     # a generator: its declared arguments, as formulas
            curve = sketch.gen_curve(name)
            declared = KINDS[curve.kind]
            title = QLabel(f"<b>{curve.name}</b>: {curve.kind}")
            title.setToolTip(declared.DOC)
            self.props_form.addRow(title)
            for a in declared.ARGS:
                field = self._formula_field(
                    curve.args[a.name], lambda text, c=curve.name, a=a: self._set_curve_arg(sketch_name, c, a, text),
                    empty=a.default is not None)
                field.setToolTip(a.doc + (f" (empty: {a.default})" if a.default is not None else ""))
                if a.default is not None:
                    field.setPlaceholderText(a.default)
                self.props_form.addRow(f"{a.name}:", field)
            self._add_spline_buttons(sketch_name, curve.name)
            self._add_offset_rows(sketch_name, [name])
            return
        if kind == "arc":
            arc = sketch.arc(name)
            self.props_form.addRow(QLabel(f"<b>{arc.name}</b>: arc " + ("from its center" if arc.kind == "center"
                                                                      else "through three points")))
            if arc.kind == "center":
                for label, attr, unit in (("Radius:", "radius", "mm"), ("From:", "start_angle", "degrees"),
                                          ("To:", "end_angle", "degrees")):
                    field = self._formula_field(getattr(arc, attr),
                                                lambda text, attr=attr: self._set_arc(sketch_name, name, attr, text))
                    field.setToolTip(f"{unit}: a number or a formula of the parameters" + (
                        " (counter-clockwise from the sketch's x)" if unit == "degrees" else ""))
                    self.props_form.addRow(label, field)
            self._add_offset_rows(sketch_name, [name])
            return
        if kind in ("polyline", "segment"):
            w = sketch.polyline(name.rpartition(".")[0] if kind == "segment" else name)
            self.props_form.addRow(QLabel(f"<b>{w.name}</b>"))
            self.props_form.addRow("Width (new points):", self._formula_field(
                w.width, lambda text, w=w.name: self._set_polyline(sketch_name, w, width=text)))
            for label, end in (("Start:", "start"), ("End:", "end")):
                combo = QComboBox()
                combo.addItems(["flat", "round"])
                combo.setCurrentText(getattr(w, end))
                combo.currentTextChanged.connect(
                    lambda text, w=w.name, end=end: _later(lambda: self._set_polyline(sketch_name, w, **{end: text})))
                self.props_form.addRow(label, combo)
        elif kind == "point":
            for w, i in sketch.polyline_widths(name):
                self.props_form.addRow(f"Width on {w.name}:", self._formula_field(
                    w.widths[i], lambda text, w=w.name, i=i: self._set_polyline(sketch_name, w, at=(i, text))))
                if 0 < i < len(w.points) - 1:   # an inner point: its bend
                    combo = QComboBox()
                    combo.addItems(list(BENDS))
                    combo.setCurrentText(w.bends[i])
                    combo.setToolTip("normal: a sharp corner\n"
                                     "arc: the center line turns along an arc, both sides concentric, the width stays\n"
                                     "chamfer: the center line is cut straight, both sides cut, the width stays\n"
                                     "miter: only the outer corner is cut (the circle of the radius around the point)")
                    combo.currentTextChanged.connect(
                        lambda text, w=w.name, i=i: _later(lambda: self._set_polyline(sketch_name, w, bend=(i, text))))
                    self.props_form.addRow(f"Bend on {w.name}:", combo)
                    if w.bends[i] in BEND_PARAMETER:
                        label, default = BEND_PARAMETER[w.bends[i]]
                        radius = self._formula_field(
                            w.radii[i],
                            lambda text, w=w.name, i=i: self._set_polyline(sketch_name, w, radius=(i, text)),
                            empty=True)
                        radius.setPlaceholderText(default)
                        self.props_form.addRow(f"{label} on {w.name}:", radius)
        self._add_offset_rows(sketch_name, [name])

    def _set_arc(self, sketch_name, name, attr, text):
        """An arc's radius or angle changed in the properties; one undo step."""
        setattr(self.part.feature(sketch_name).arc(name), attr, text)
        self._commit()

    def _add_offset_rows(self, sketch_name, names):
        """
        The Offset of the selected lines, circles, splines and curves: one
        with an offset: its fields (left, right, the caps of an open one) and
        Remove Offset; else Offset / Remove Offset buttons for them all.
        """
        sketch = self.part.feature(sketch_name)

        def able(n):
            try:
                return sketch.kind_of(n) in OFFSET_KINDS and not sketch.curve(n).construction
            except (KeyError, StopIteration):
                return False

        names = [n for n in names if able(n)]
        have = [n for n in names if sketch.offset(n) is not None]
        free = [n for n in names if n not in have]
        if len(names) == 1 and have:
            name, offset = names[0], sketch.offset(names[0])
            title = QLabel("<b>Offset</b>")
            title.setToolTip("A band along it: left / right of it, from its start to its end (a circle: left is "
                             "inside); a profile of its own")
            self.props_form.addRow(title)
            for side in ("left", "right"):
                field = self._formula_field(getattr(offset, side),
                                            lambda text, side=side: self._set_offset(sketch_name, name, side, text),
                                            empty=True)
                field.setPlaceholderText("0")
                self.props_form.addRow(f"Offset {side}:", field)
            built = self.result.outputs[sketch_name].shapes.get(name) if sketch_name in self.result.outputs else None
            closed = sketch.kind_of(name) == "circle" or (built is not None and built.closed)
            if not closed:
                for label, end in (("Start cap:", "start"), ("End cap:", "end")):
                    combo = QComboBox()
                    combo.addItems(list(CAPS))
                    combo.setCurrentText(getattr(offset, end))
                    combo.currentTextChanged.connect(
                        lambda text, end=end: _later(lambda: self._set_offset(sketch_name, name, end, text)))
                    self.props_form.addRow(label, combo)
            remove = QPushButton("Remove Offset")
            remove.clicked.connect(lambda: _later(lambda: self.remove_offsets(sketch_name, [name])))
            self.props_form.addRow(remove)
            return
        if free:
            button = QPushButton("Offset" if len(names) == 1 else f"Offset ({len(free)})")
            button.setToolTip("A band along it (a ring around a closed one): its own profile; set its sides and "
                              "caps here then")
            button.clicked.connect(lambda: _later(lambda: self.add_offsets(sketch_name, free)))
            self.props_form.addRow(button)
        if have:
            button = QPushButton(f"Remove Offset ({len(have)})")
            button.clicked.connect(lambda: _later(lambda: self.remove_offsets(sketch_name, have)))
            self.props_form.addRow(button)

    def add_offsets(self, sketch_name, names):
        """An Offset (1 mm to the left) of each of these entities; one undo step."""
        sketch = self.part.feature(sketch_name)
        for name in names:
            if sketch.offset(name) is None:
                sketch.add_offset(name)
        self._commit()
        self.statusBar().showMessage(f"Offset of {', '.join(names)}: 1 mm to the left (its fields in the "
                                     "properties)", 8000)

    def remove_offsets(self, sketch_name, names):
        """These entities' offsets removed; one undo step."""
        sketch = self.part.feature(sketch_name)
        for name in names:
            sketch.remove_offset(name)
        self._commit()

    def _set_offset(self, sketch_name, name, key, text):
        """An offset's left / right (empty: 0) or cap changed; one undo step."""
        offset = self.part.feature(sketch_name).offset(name)
        setattr(offset, key, text or "0")
        self._commit()

    def add_curve(self, kind):
        """A generated curve of `kind` in the edited sketch, with starter arguments, selected: one undo step."""
        if self.editing is None:
            self.statusBar().showMessage("Curve: edit a sketch first", 5000)
            return None
        sketch = self.part.feature(self.editing)
        name = sketch.add_curve(kind, **CURVE_STARTERS.get(kind, {}))
        self._commit()
        self.view.select_keys([("entity", self.editing, name)])
        self._on_view_select(self.view.selected)
        return name

    def _add_spline_buttons(self, sketch_name, name):
        """Under a curve's arguments: the tolerance, Convert to Spline and Copy as Spline."""
        tolerance = QLineEdit(self.spline_tolerance)
        tolerance.setToolTip("How far (µm) the spline made from the curve may be off it")
        tolerance.editingFinished.connect(lambda: setattr(self, "spline_tolerance", tolerance.text().strip()))
        self.props_form.addRow("Tolerance, µm:", tolerance)
        convert = QPushButton("Convert to Spline")
        convert.setToolTip("Replace the curve by a spline through points on it (plain numbers: drag them); "
                           "what used its ends uses the spline's")
        convert.clicked.connect(lambda: _later(lambda: self.curve_to_spline(sketch_name, name, keep=False)))
        copy = QPushButton("Copy as Spline")
        copy.setToolTip("The same spline, and the curve stays, hidden (tick its browser row to show it)")
        copy.clicked.connect(lambda: _later(lambda: self.curve_to_spline(sketch_name, name, keep=True)))
        self.props_form.addRow(convert)
        self.props_form.addRow(copy)

    def set_curve_visible(self, sketch_name, name, on):
        """A generated curve shown or hidden (not drawn, not in profiles); one undo step."""
        curve = self.part.feature(sketch_name).gen_curve(name)
        if curve.visible == on:
            return
        curve.visible = on
        self._commit()

    def curve_to_spline(self, sketch_name, name, keep):
        """
        A spline through points on generated curve `name`, within the
        tolerance (µm, the field under its arguments); keep: the curve stays,
        hidden (else it goes). Lines on its ends and extrusions of profiles
        with it move to the spline. One undo step; the spline is selected.
        """
        built = self.result.outputs.get(sketch_name)
        shape = built.shapes.get(name) if built is not None else None
        if shape is None:
            self.statusBar().showMessage(f"{name}: not built (see its error), no spline made", 8000)
            return None
        try:
            tolerance = float(self.spline_tolerance) * 1e-3
        except ValueError:
            tolerance = 0.0
        if not tolerance > 0:
            self.show_error(f"The tolerance must be a number of µm > 0, not {self.spline_tolerance!r}")
            return None
        sketch = self.part.feature(sketch_name)
        try:
            spline = sketch.curve_to_spline(name, shape, tolerance, keep)
        except CurveError as error:
            self.doc.revert()
            self.show_error(f"{name}: no spline: {error}")
            return None
        self.part.replace_in_profiles(sketch_name, name, spline)
        for inner in (False, True):             # its offset went with it
            self.part.replace_in_profiles(sketch_name, offset_key(name, inner), offset_key(spline, inner))
        self._commit()
        count = len(dict.fromkeys(self.part.feature(sketch_name).spline(spline).points))
        self.statusBar().showMessage(f"{spline}: {count} points on {name}, within {self.spline_tolerance} µm"
                                     + (f"; {name} hidden" if keep else f"; {name} removed"), 8000)
        key = ("entity", sketch_name, spline)
        self.view.select_keys([key])
        self._on_view_select(key)
        return spline

    def _set_curve_arg(self, sketch_name, name, arg, text):
        """A generated curve's argument changed in the properties: one undo step (empty: its default)."""
        self.part.feature(sketch_name).gen_curve(name).args[arg.name] = text or arg.default
        self._commit()

    def _deparametrize_target(self, key):
        """(sketch name, entity names or None for the whole sketch) the selection stands for, or None."""
        if key is None:
            return None
        if key[0] == "entity":
            names = [k[2] for k in self.view.selection if k[0] == "entity" and k[1] == key[1]]
            return key[1], names
        if key[0] == "feature":
            try:
                feature = self.part.feature(key[1])
            except KeyError:
                return None
            return (key[1], None) if feature.TYPE == "sketch" else None
        return None

    def _add_deparametrize(self, key):
        """A Deparametrize button when the selection has values set by formulas."""
        target = self._deparametrize_target(key)
        if target is None:
            return
        sketch_name, names = target
        try:
            slots = self.part.feature(sketch_name).formula_slots(names)
        except KeyError:
            return
        if not slots:
            return
        button = QPushButton(f"Deparametrize ({len(slots)})")
        what = "the whole sketch" if names is None else "the selection"
        labels = [label for label, _, _ in slots]
        button.setToolTip(f"Replace the formulas of {what} by their values now: the geometry stays, it no "
                          "longer follows the parameters, and it can be dragged.\n"
                          + ", ".join(labels[:12]) + (f" and {len(labels) - 12} more" if len(labels) > 12 else ""))
        button.clicked.connect(lambda: _later(lambda: self.deparametrize(sketch_name, names)))
        self.props_form.addRow(button)

    def deparametrize(self, sketch_name, names=None):
        """The formulas of these entities of a sketch (all if None) become numbers; one undo step."""
        sketch = self.part.feature(sketch_name)
        done, failed = sketch.deparametrize(self.result.values, names)
        if not done:
            if failed:
                self.statusBar().showMessage(f"not deparametrized: {next(iter(failed.items()))}", 8000)
            return
        self._commit()
        note = f"; {len(failed)} formula(s) with an error left as they are" if failed else ""
        self.statusBar().showMessage(f"deparametrized {len(done)} value(s) of {sketch_name}: now plain numbers, "
                                     f"draggable (Undo goes back){note}", 8000)
        self._show_info(self.view.selected)

    def _formula_field(self, text, apply, empty=False):
        """A formula field applying on Enter / leaving it, when changed (empty allowed only if `empty`)."""
        field = QLineEdit(text)
        field.setToolTip("mm: a number or a formula of the parameters (Enter applies)")
        def finished():
            value = field.text().strip()
            if value != text and (empty or value):
                _later(lambda: apply(value))

        field.editingFinished.connect(finished)
        return field

    def _set_polyline(self, sketch_name, name, width=None, start=None, end=None, at=None, bend=None, radius=None):
        polyline = self.part.feature(sketch_name).polyline(name)
        if width is not None:
            polyline.width = width or "1"
        if start is not None:
            polyline.start = start
        if end is not None:
            polyline.end = end
        if at is not None:
            polyline.widths[at[0]] = at[1] or polyline.width
        if bend is not None:
            polyline.bends[bend[0]] = bend[1]
            polyline.radii[bend[0]] = ""        # its meaning changes with the bend: back to the default
        if radius is not None:
            polyline.radii[radius[0]] = radius[1]
        self._commit()

    def _insert_point(self, key, xy):
        """
        A double click on a polyline's center segment: a point put there
        (on the segment), its width as the line between its neighbours
        has it (their formula if they share one); one undo step, selected.
        """
        _, sketch_name, name = key
        sketch = self.part.feature(sketch_name)
        try:
            if sketch.kind_of(name) != "segment":
                return
        except KeyError:
            return
        owner, _, index = name.rpartition(".")
        w, k = sketch.polyline(owner), int(index)
        built = self.result.outputs[sketch_name]
        a, b = built.points[w.points[k - 1]], built.points[w.points[k]]
        t = float(np.clip(np.dot(xy - a, b - a) / max(float(np.dot(b - a, b - a)), 1e-18), 0.0, 1.0))
        if t <= 1e-6 or t >= 1 - 1e-6:
            return                              # on a point already
        at = a + t * (b - a)
        if w.widths[k - 1] == w.widths[k]:
            width = w.widths[k]
        else:
            w0, w1 = (evaluate(text, self.result.values) for text in (w.widths[k - 1], w.widths[k]))
            width = fmt_mm(w0 + t * (w1 - w0))
        point = sketch.add_point(fmt_mm(at[0]), fmt_mm(at[1]))
        sketch.insert_into_polyline(owner, k, point, width)
        self._commit()
        self.view.select_keys([("entity", sketch_name, point)])
        self._on_view_select(self.view.selected)

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
        owner, _, end = name.rpartition(".")
        if end in ("start", "end") and any(c.name == owner for c in sketch.curves + sketch.arcs):
            return (f"<b>{name}</b> (the {end} of curve {owner})<br>{where(name) if name in points else 'not built'}"
                    "<br>set by the curve: lines may use it")
        if sketch.kind_of(name) == "curve":
            curve = sketch.gen_curve(name)
            built = self.result.outputs[sketch_name].shapes.get(name)
            args = "<br>".join(f"{a} = {text}" for a, text in curve.args.items())
            ends = (f"<br>from {where(name + '.start')}<br>to {where(name + '.end')}<br>"
                    f"{len(built.edges)} exact edge(s)" if built is not None else "<br>not built (see the error)")
            hidden = "" if curve.visible else " (hidden: not drawn, not in profiles)"
            return (f"<b>{name}</b> ({curve.kind} curve of {sketch_name}){hidden}<br>{KINDS[curve.kind].DOC}<br>"
                    f"{args}{ends}<br>generated: change it by its arguments (properties)")
        if sketch.kind_of(name) == "spline":
            w = sketch.spline(name)
            built = self.result.outputs[sketch_name].shapes.get(name)
            closed = len(w.points) > 3 and w.points[0] == w.points[-1]
            shape = (f"<br>length {float(np.linalg.norm(np.diff(built.points, axis=0), axis=1).sum()):.6g} mm, "
                     f"a B-spline of {len(built.edges[0][2]) - 1} span(s)" if built is not None
                     else "<br>not built (see the error)")
            return (f"<b>{name}</b> ({'closed ' if closed else ''}spline of {sketch_name})<br>through "
                    f"{len(w.points) - closed} points: {', '.join(w.points)}{shape}"
                    "<br>drag its points to bend it")
        if sketch.kind_of(name) == "arc":
            arc = sketch.arc(name)
            built = self.result.outputs[sketch_name].shapes.get(name)
            if arc.kind == "center":
                text = (f"<b>{name}</b> (arc of {sketch_name}, from its center)<br>center {where(arc.center)}<br>"
                        f"radius = {arc.radius} mm, from {arc.start_angle} to {arc.end_angle} deg<br>its ends "
                        f"{name}.start / {name}.end (lines may use them)")
            else:
                text = (f"<b>{name}</b> (arc of {sketch_name}, through three points)<br>from {where(arc.start)}<br>"
                        f"through {where(arc.through)}<br>to {where(arc.end)}<br>drag its points to bend it")
            if built is not None:
                length = float(np.linalg.norm(np.diff(built.points, axis=0), axis=1).sum())
                text += f"<br>length {length:.6g} mm"
            return text
        if sketch.kind_of(name) in ("polyline", "segment"):
            owner = name.rpartition(".")[0] if sketch.kind_of(name) == "segment" else name
            w = sketch.polyline(owner)
            what = f"segment {name.rpartition('.')[2]} of polyline {owner}" if owner != name else "polyline"
            return (f"<b>{name}</b> ({what} of {sketch_name})<br>{len(w.points)} points: {', '.join(w.points)}"
                    f"<br>widths {', '.join(w.widths)} mm<br>ends: {w.start} / {w.end}"
                    "<br>Delete on a segment splits it, on a point joins it")
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
            hints = {LineTool: LINE_HINT, PointTool: POINT_HINT, CircleTool: CIRCLE_HINT, PasteTool: PASTE_HINT,
                     ArcTool: ARC_HINTS[self.arc_method],
                     TraceTool: TRACE_HINT, SplineTool: SPLINE_HINT, ProjectTool: PROJECT_HINT}
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
        self.control.close()
        normal = self.normalGeometry() if self.isMaximized() else self.geometry()
        self.app_settings.window = {
            "x": normal.x(), "y": normal.y(), "width": normal.width(), "height": normal.height(),
            "maximized": self.isMaximized(),
        }
        self.app_settings.save()            # an error here must not keep the window open
        super().closeEvent(event)
