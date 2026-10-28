# main_window.py
#
# Qt shell: open a STEP file, list its bodies, look at them in 3D, assign
# object types (right click) and edit their parameters in the tree; edit the
# project settings (Simulation, Mesh); build the compute grid and show its
# lines on a plane, like CST's mesh view.
#
# Everything is saved at once to the project file next to the STEP file
# (project/project_file.py). Thin layer: menus and editors are generated from
# project/object_types.py and project/settings.py.

import os
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import numpy as np
from PySide6.QtCore import QRect, Qt, QTimer
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDockWidget, QFileDialog, QHBoxLayout,
    QApplication, QHeaderView, QLabel, QProgressBar, QPushButton, QToolButton,
    QMainWindow, QMenu, QMessageBox, QSlider, QTableWidget, QTableWidgetItem, QTabWidget,
    QToolBar, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from app_settings import AppSettings
from geometry.holes import find_holes, group_by_diameter, via_body
from geometry.kicad_step import compare_bodies, prepare_kicad_step
from geometry.step_io import read_step
from discretize.materials import discretize
from mesh.rectilinear import ACCURATE_CELLS_PER_FEATURE, build_grid, z0_error_percent
from project.materials import BUILTIN, CUSTOM, MATERIAL_PARAMS
from project.object_types import OBJECT_TYPES
from project.project_file import UP_AXES, Z0PATH_PARAMS, Project, ProjectError
from project.settings import SECTIONS
from view.geometry_view import GeometryView
from view.profile_window import ProfileWindow
from view.remote_panel import RemotePanel, RemoteRunner
from view.results_panel import ResultsPanel
from view.tdr_window import TdrWindow
from view.view_buttons import ViewButtons
from project.settings import EXCITE_ALL, NOT_IN_FINGERPRINT, accuracy_db, farfield_frequencies
from solver.result_file import (delete_result, fingerprint, format_duration, list_results,
                                rename_result, results_dir, save_result)
from view.sim_runner import SimulationRunner
from solver.simulation import parameters, port_names
from solver.simulation import problem as simulation_problem
from solver.line_profile import CONVERGED, impedance_profile, line_impedance, pec_nets, z0_convergence
from solver.path_profile import Path, path_profile
from solver.profile_file import load_profile, profile_key, save_profile

MM = 1e-3
AXES = "xyz"
MESH_PROGRESS_DELAY = 0.3       # s: the mesh progress bar appears after this
REMOTE_POLL = 15000             # ms between status reads of a run going on the server
SLIDER_STEPS = 1000             # plane positions over the model when it is not on the grid lines
TREE_SECTIONS = ("simulation", "mesh")  # the settings sections, in the tree's order (before Model)
TREE_GROUPS = ("model", "bodies", "section", "monitors", "results")   # rows _tree_item looks into


class _Cancelled(Exception):
    """A background discretization made stale by a newer edit."""


def _fmt_mm(values):
    return " x ".join(f"{v / MM:.3g}" for v in values)


def _fmt_point(values):
    """A point in mm: (x, y, z), to the micrometer, trailing zeros dropped."""
    def one(v):
        text = f"{v / MM:.3f}".rstrip("0").rstrip(".")
        return "0" if text in ("-0", "") else text
    return "(" + ", ".join(one(v) for v in values) + ")"


def _fmt_value(value):
    if isinstance(value, bool):
        return "on" if value else "off"
    if value == "all":                      # the excite setting
        return "all ports"
    return value if isinstance(value, str) else f"{value:.6g}"


def _group_item(parent, text, role, tooltip="", owner=None):
    """A bold tree row that holds others (Model, Bodies, Holes, Mesh, Monitors...)."""
    item = QTreeWidgetItem([text])
    item.setData(0, Qt.UserRole, (role, owner, None))   # before it joins the tree (itemChanged)
    font = item.font(0)
    font.setBold(True)
    item.setFont(0, font)
    if tooltip:
        item.setToolTip(0, tooltip)
    if parent is not None:
        parent.addChild(item)
    return item


class MainWindow(QMainWindow):
    def __init__(self, path=None):
        super().__init__()
        self.resize(1400, 900)

        self.geometry = None
        self.project = None
        self.ports = {}             # {port body name: PortGeometry}
        self.elements = {}          # {lumped element body name: PortGeometry}
        self._port_cache = {}       # ports' and elements' geometry of this geometry (Project.ports)
        self.waveports = {}         # {waveguide port body name: WavePortGeometry}
        self.holes = []             # geometry.holes.Hole: open through holes of the model
        self.via_bodies = []        # PEC cylinders of the holes assigned as vias
        self.mesh = None            # MeshResult
        self.materials = None       # MaterialGrid (solver's view)
        self._nets = None           # (materials, pec_nets of it) for the Z0 tools
        self._profiles = None       # (materials, {axis or "path": Profile}): Z0 profiles of this mesh
        self._mesh_coordinate = None    # where the mesh plane is (m)
        self.profile_window = None      # Z0 profile (ProfileWindow), made on first use
        self.tdr_window = None          # TDR of the shown runs (TdrWindow), made on first use
        self._mesh_pool = ThreadPoolExecutor(max_workers=1)    # background discretization
        self._mesh_job = None
        self._center_slider = False
        self._mesh_timer = QTimer(self)
        self._mesh_timer.setInterval(100)
        self._mesh_timer.timeout.connect(self._poll_discretize)
        # The display goes to the project's [view] a second after the last
        # change (a slider drag is one save), and before the file is left.
        self._view_timer = QTimer(self)
        self._view_timer.setSingleShot(True)
        self._view_timer.setInterval(1000)
        self._view_timer.timeout.connect(self._save_view)
        self._restoring = False
        self._project_mtime = None      # of the project file as last read or written here
        self.view = GeometryView()
        self.view.view.camera.on_change = self._view_changed
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.addTab(self.view.canvas.native, "3D View")
        self.view_buttons = ViewButtons(self.view)        # standard views over the 3D view
        self.setCentralWidget(self.tabs)

        self.runner = SimulationRunner(self)
        self.runner.progress.connect(self._sim_progress)
        self.runner.finished.connect(self._sim_finished)
        self.runner.failed.connect(self._sim_failed)
        self.remote = RemoteRunner(self)        # push / pull / check, off the GUI thread
        self.remote.progress.connect(lambda text: self.statusBar().showMessage(text))
        self.remote.finished.connect(self._remote_finished)
        self.remote.failed.connect(self._remote_failed)
        self._remote_operation = None
        self.remote_watch = RemoteRunner(self)  # the open model's server run, read quietly
        self.remote_watch.finished.connect(self._remote_watched)
        self.remote_watch.failed.connect(self._remote_watch_failed)
        self._remote_watch_target = None        # (model, server) of the status being read
        self._launch_fingerprint = None
        self._remote_state = None               # its last status (run.py's status file), None: no run
        self._remote_timer = QTimer(self)
        self._remote_timer.setInterval(REMOTE_POLL)
        self._remote_timer.timeout.connect(self._remote_poll)
        self.runs = []              # SimulationResult: all saved runs of the model, oldest first
        self.shown = set()          # paths of the runs ticked in Project > Results
        self.app_settings = AppSettings.load()

        self._build_toolbar()
        self._build_tree_panel()
        self._build_results_panel()

        self.setWindowTitle("customcst")
        if self.app_settings.error:
            self.statusBar().showMessage(f"settings not read (defaults used): {self.app_settings.error}", 10000)
        if path:
            self.load(path)
        else:
            self.statusBar().showMessage("Open a STEP file (Open STEP or Recent)")

    # =====================================================
    # UI
    # =====================================================

    def _build_toolbar(self):
        bar = QToolBar("Main")
        bar.setMovable(False)
        self.addToolBar(bar)

        bar.addAction("Open STEP...", self._open_dialog)
        kicad = bar.addAction("Import KiCad...", self._import_kicad)
        kicad.setToolTip("A KiCad board STEP -> <name>_prep.stp in our form (one body per copper "
                         "layer, pads cut to the copper, vias as one body), then opened")
        self.recent_menu = QMenu(self)
        self.recent_menu.aboutToShow.connect(self._fill_recent_menu)
        recent = QToolButton()
        recent.setText("Recent")
        recent.setMenu(self.recent_menu)
        recent.setPopupMode(QToolButton.InstantPopup)
        bar.addWidget(recent)
        bar.addAction("Reload", self._reload)      # STEP + project file
        bar.addAction("Reset view", self.view.reset_view)
        bar.addSeparator()

        self.simulate_action = bar.addAction("▶ Simulate", self._simulate)
        self.stop_action = bar.addAction("■ Stop", self.runner.stop)
        self.stop_action.setEnabled(False)
        bar.addSeparator()

        self.edges_check = QCheckBox("Edges")
        self.edges_check.setChecked(True)
        self.edges_check.toggled.connect(self.view.set_show_edges)
        bar.addWidget(self.edges_check)

        self.xray_check = QCheckBox("X-ray")
        self.xray_check.toggled.connect(self.view.set_xray)
        bar.addWidget(self.xray_check)

        self.planes_check = QCheckBox("Ref. planes")
        self.planes_check.setChecked(True)
        self.planes_check.setToolTip("Reference planes of the waveguide ports (dark blue): the port plane "
                                     "moved by the shift in the Results tab, along the port's axis")
        self.planes_check.toggled.connect(self.view.set_show_reference_planes)
        bar.addWidget(self.planes_check)
        bar.addSeparator()

        bar.addWidget(QLabel(" Up: "))
        self.up_combo = QComboBox()
        self.up_combo.addItems([a.upper() for a in UP_AXES])
        self.up_combo.setToolTip("Vertical axis of the CAD model (stored in the project)")
        self.up_combo.currentTextChanged.connect(self._up_changed)
        bar.addWidget(self.up_combo)

        # A second row (one row did not fit): the plane, Z0 and the Z0 path.
        self.addToolBarBreak()
        bar = QToolBar("Plane")
        bar.setMovable(False)
        self.addToolBar(bar)

        # Mesh view: grid lines on one plane normal to an axis, at a grid line.
        self.mesh_check = QCheckBox("Mesh")
        self.mesh_check.toggled.connect(lambda _: self._grid_plane_toggled())
        bar.addWidget(self.mesh_check)

        self.materials_check = QCheckBox("Materials")
        self.materials_check.setToolTip("Edges on the mesh plane as the solver sees them")
        self.materials_check.toggled.connect(lambda _: self._grid_plane_toggled())
        bar.addWidget(self.materials_check)

        self.section_check = QCheckBox("Section")
        self.section_check.setToolTip("Cut the model by the plane (axis and position on the right) "
                                      "to check the geometry inside")
        self.section_check.toggled.connect(lambda _: self._show_mesh_plane())
        bar.addWidget(self.section_check)
        self.section_flip = QToolButton()
        self.section_flip.setText("⇅")
        self.section_flip.setCheckable(True)
        self.section_flip.setToolTip("Section: keep the other half")
        self.section_flip.toggled.connect(lambda _: self._show_mesh_plane())
        bar.addWidget(self.section_flip)

        bar.addWidget(QLabel(" plane ⊥ "))
        self.mesh_axis = QComboBox()
        self.mesh_axis.addItems([a.upper() for a in AXES])
        self.mesh_axis.currentIndexChanged.connect(self._mesh_axis_changed)
        bar.addWidget(self.mesh_axis)

        self.mesh_slider = QSlider(Qt.Horizontal)
        self.mesh_slider.setMinimumWidth(220)
        self.mesh_slider.valueChanged.connect(self._mesh_slider_moved)
        self.mesh_slider.sliderReleased.connect(self._show_section)        # full detail after a drag
        bar.addWidget(self.mesh_slider)

        self.mesh_label = QLabel()
        self.mesh_label.setMinimumWidth(90)
        bar.addWidget(self.mesh_label)

        # Line impedance: Z0 of the cross-section at the plane, or along its axis.
        self.z0_button = QToolButton()
        self.z0_button.setText("Z₀")
        self.z0_button.setToolTip("Z0 of the line at the plane (its grid cross-section); "
                                  "the arrow: Z0 profile along the plane's axis")
        self.z0_button.setPopupMode(QToolButton.MenuButtonPopup)
        self.z0_button.clicked.connect(self._z0_here)
        z0_menu = QMenu(self)
        z0_menu.addAction("Z₀ at the plane", self._z0_here)
        z0_menu.addAction("Z₀ profile…", self._z0_profile)
        z0_menu.addAction("Z₀ convergence at the plane…", self._z0_convergence)
        z0_menu.addAction("TDR of the shown runs…", self._tdr)
        self.z0_button.setMenu(z0_menu)
        bar.addWidget(self.z0_button)

        # The Z0 path ([z0path]): drawn and edited in the 3D view while checked.
        self.path_check = QCheckBox("Z₀ path")
        self.path_check.setToolTip(
            "Draw the path for the Z0 profile along it (Z₀ ▸ Z₀ profile…, along Path), in the plane "
            "of Project ▸ Z0 path: click adds a point, Ctrl + drag moves one, double click deletes "
            "one, Shift + click on a segment inserts one. Measuring is off meanwhile.")
        self.path_check.toggled.connect(self._path_mode_toggled)
        bar.addWidget(self.path_check)

        for switch in self._view_switches().values():
            switch.toggled.connect(lambda _: self._view_changed())
        self.mesh_axis.currentIndexChanged.connect(lambda _: self._view_changed())

        self.legend = QLabel(
            "  <span style='color:#ccc;background:#222'>&nbsp;"
            "<b style='color:#fff21a'>━ PEC</b>&nbsp; "
            "<b style='color:#ff33e6'>━ port</b>&nbsp; "
            "<b style='color:#ffffff'>━ lumped</b>&nbsp; "
            "<b style='color:#4df2ff'>━ dielectric</b> (brighter = higher εr)&nbsp;</span>"
        )
        self.legend.setVisible(False)
        self.statusBar().addPermanentWidget(self.legend)   # the toolbar is full

        # The points picked in the 3D view and their distance (a click sets A,
        # Shift + click B; a double click on nothing or Esc clears).
        self.pick_label = QLabel()
        self.pick_label.setTextFormat(Qt.RichText)
        self.pick_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.statusBar().addPermanentWidget(self.pick_label)
        self.view.on_pick = self._picked
        self.view.on_path_changed = self._path_edited

        # Simulation progress: the field energy of the running port, full at
        # its peak, empty at the accuracy level below it (where the run stops).
        self._end_db = -40.0
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, int(-10 * self._end_db))
        self.progress_bar.setFixedWidth(220)
        self.progress_bar.setMaximumHeight(16)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setStyleSheet(
            "QProgressBar { border: 1px solid #999; border-radius: 3px; background: #eee; "
            "text-align: center; font-size: 11px; }"
            "QProgressBar::chunk { background-color: #3cb043; border-radius: 2px; }")
        self.progress_bar.setVisible(False)
        self.statusBar().addPermanentWidget(self.progress_bar)

        # Mesh progress: the steps of the discretization (bodies), shown when
        # a rebuild takes longer than MESH_PROGRESS_DELAY.
        self.mesh_bar = QProgressBar()
        self.mesh_bar.setFixedWidth(220)
        self.mesh_bar.setMaximumHeight(16)
        self.mesh_bar.setTextVisible(True)
        self.mesh_bar.setStyleSheet(
            "QProgressBar { border: 1px solid #999; border-radius: 3px; background: #eee; "
            "text-align: center; font-size: 11px; }"
            "QProgressBar::chunk { background-color: #3c8dd0; border-radius: 2px; }")
        self.mesh_bar.setVisible(False)
        self.statusBar().addPermanentWidget(self.mesh_bar)

        # The open model's run on the server (Remote): its progress from run.py's status file.
        self._remote_bar = QProgressBar()
        self._remote_bar.setFixedWidth(380)
        self._remote_bar.setMaximumHeight(16)
        self._remote_bar.setTextVisible(True)
        self._remote_bar.setStyleSheet(
            "QProgressBar { border: 1px solid #999; border-radius: 3px; background: #eee; "
            "text-align: center; font-size: 11px; }"
            "QProgressBar::chunk { background-color: #8a5cc8; border-radius: 2px; }")
        self._remote_bar.setVisible(False)
        self.statusBar().addPermanentWidget(self._remote_bar)

    def _build_tree_panel(self):
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Name", "Kind / value", "Size, mm"])
        self.tree.setEditTriggers(QAbstractItemView.NoEditTriggers)   # values: double click
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context_menu)
        self.tree.itemChanged.connect(self._item_changed)
        self.tree.itemDoubleClicked.connect(self._item_double_clicked)
        self.tree.currentItemChanged.connect(self._current_changed)

        hint = QLabel("Right click a body to assign a type; double click a value to edit it.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray")

        self.info = QLabel()
        self.info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.info.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.info.setWordWrap(True)

        # The console runner's machine (the Remote tab): the model there, its runs back.
        self.push_button = QPushButton("Push")
        self.push_button.setEnabled(False)      # until the model can run (_update_remote_buttons)
        self.push_button.clicked.connect(lambda: self._remote_start("push"))
        self.fetch_button = QPushButton("Fetch")
        self.fetch_button.setToolTip("Ask the server how this model's run goes; take its finished runs "
                                     "(and its model files, if they differ: asked)")
        self.fetch_button.clicked.connect(lambda: self._remote_start("fetch"))
        self.run_remote_button = QPushButton("▶ Run on server")
        self.run_remote_button.setEnabled(False)
        self.run_remote_button.clicked.connect(lambda: self._remote_start("launch"))
        self.stop_remote_button = QPushButton("■ Stop")
        self.stop_remote_button.setToolTip("Stop this model's run on the server (nothing is saved)")
        self.stop_remote_button.setEnabled(False)
        self.stop_remote_button.clicked.connect(lambda: self._remote_start("stop"))
        remote_buttons = QHBoxLayout()
        for button in (self.push_button, self.fetch_button, self.run_remote_button, self.stop_remote_button):
            remote_buttons.addWidget(button)
        remote_buttons.addStretch(1)

        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.addWidget(self.tree, 3)
        layout.addLayout(remote_buttons)
        layout.addWidget(hint)
        layout.addWidget(self.info, 1)

        self.remote_panel = RemotePanel()
        self.remote_panel.check_requested.connect(lambda: self._remote_start("check"))

        # Tabs of the left panel: the model tree, the material library, the remote machine.
        self.side_tabs = QTabWidget()
        self.side_tabs.setDocumentMode(True)
        self.side_tabs.addTab(panel, "Project")
        self.side_tabs.addTab(self._build_materials_panel(), "Materials")
        self.side_tabs.addTab(self.remote_panel, "Remote")

        dock = QDockWidget()
        dock.setTitleBarWidget(QWidget())       # the tabs are the title
        dock.setFeatures(QDockWidget.DockWidgetMovable)
        dock.setWidget(self.side_tabs)
        dock.setMinimumWidth(400)
        self.addDockWidget(Qt.LeftDockWidgetArea, dock)

    def _build_materials_panel(self):
        dielectric = OBJECT_TYPES["dielectric"]
        headers = ["Name"] + [
            dielectric.param(p).label + (f", {dielectric.param(p).unit}" if dielectric.param(p).unit else "")
            for p in MATERIAL_PARAMS
        ] + ["Source", "Used by"]
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.SingleSelection)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        table.horizontalHeader().setStretchLastSection(True)
        table.itemChanged.connect(self._material_cell_changed)
        table.itemSelectionChanged.connect(self._material_buttons)
        self.materials_table = table

        self.material_new = QPushButton("New")
        self.material_new.setToolTip("A new preset with the dielectric defaults (εr 1)")
        self.material_new.clicked.connect(self._material_add)
        self.material_copy = QPushButton("Duplicate")
        self.material_copy.clicked.connect(self._material_duplicate)
        self.material_delete = QPushButton("Delete")
        self.material_delete.setToolTip("Only presets that no body uses; built-in ones stay")
        self.material_delete.clicked.connect(self._material_remove)
        self.material_reset = QPushButton("Reset built-in")
        self.material_reset.setToolTip("Back to the program's values (bodies follow)")
        self.material_reset.clicked.connect(self._material_reset_values)
        buttons = QHBoxLayout()
        for button in (self.material_new, self.material_copy, self.material_delete, self.material_reset):
            buttons.addWidget(button)
        buttons.addStretch(1)

        hint = QLabel(
            "Dielectric presets of this model, saved in its .toml. Double click a value to "
            "edit it (every body using the preset follows) or a name to rename it. Built-in "
            "presets come from the program: they can be edited and reset, not renamed or "
            "deleted. Assign a preset with a dielectric's Material row in the Project tab."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray")

        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.addLayout(buttons)
        layout.addWidget(table, 1)
        layout.addWidget(hint)
        self._material_buttons()
        return panel

    def _fill_materials(self, select=None):
        """(Re)builds the library table; `select`: the preset to select."""
        table = self.materials_table
        if select is None:
            select = self._selected_material()
        library = self.project.materials if self.project else {}
        editable = self.project is not None

        table.blockSignals(True)
        table.setRowCount(len(library))
        for row, (name, values) in enumerate(library.items()):
            builtin = name in BUILTIN
            name_item = QTableWidgetItem(name)
            name_item.setData(Qt.UserRole, name)
            if builtin or not editable:
                name_item.setFlags(name_item.flags() & ~Qt.ItemIsEditable)
            table.setItem(row, 0, name_item)

            for column, param in enumerate(MATERIAL_PARAMS, start=1):
                item = QTableWidgetItem(_fmt_value(values[param]))
                item.setData(Qt.UserRole, (name, param))
                item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if not editable:
                    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                table.setItem(row, column, item)

            source = ("built-in" if builtin and BUILTIN[name] == values else
                      "built-in, edited" if builtin else "model")
            for column, text in ((len(MATERIAL_PARAMS) + 1, source),
                                 (len(MATERIAL_PARAMS) + 2, ", ".join(self.project.material_users(name)) or "—")):
                item = QTableWidgetItem(text)
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                item.setForeground(QColor("gray"))
                table.setItem(row, column, item)

            if name == select:
                table.selectRow(row)
        table.blockSignals(False)
        self._material_buttons()

    def _selected_material(self):
        rows = self.materials_table.selectionModel().selectedRows() if self.materials_table.selectionModel() else []
        if not rows:
            return None
        item = self.materials_table.item(rows[0].row(), 0)
        return item.data(Qt.UserRole) if item else None

    def _material_buttons(self):
        name = self._selected_material()
        editable = self.project is not None
        self.material_new.setEnabled(editable)
        self.material_copy.setEnabled(editable and name is not None)
        self.material_delete.setEnabled(editable and name is not None and name not in BUILTIN
                                        and not self.project.material_users(name))
        self.material_reset.setEnabled(editable and name in BUILTIN
                                       and self.project.materials[name] != BUILTIN[name])

    def _material_edit(self, action, select=None):
        """Runs a library edit; on success saves and refreshes everything that depends on it."""
        try:
            result = action()
        except (ProjectError, ValueError) as error:
            self.statusBar().showMessage(str(error), 6000)
            self._fill_materials()          # revert the shown cell
            return
        self._after_edit()
        self._fill_materials(select=result if select is None else select)

    def _material_add(self):
        self._material_edit(lambda: self.project.add_material(self.project.unique_material_name("New material")))

    def _material_duplicate(self):
        name = self._selected_material()
        if name is not None:
            self._material_edit(lambda: self.project.duplicate_material(name))

    def _material_remove(self):
        name = self._selected_material()
        if name is not None:
            self._material_edit(lambda: self.project.delete_material(name), select="")

    def _material_reset_values(self):
        name = self._selected_material()
        if name is not None:
            self._material_edit(lambda: self.project.reset_material(name), select=name)

    def _material_cell_changed(self, item):
        data = item.data(Qt.UserRole)
        text = item.text().strip()
        if isinstance(data, str):                               # the name column
            self._material_edit(lambda: self.project.rename_material(data, text))
        elif data is not None:
            name, param = data
            self._material_edit(
                lambda: self.project.set_material_value(name, param, text.replace(",", ".")), select=name)

    def _build_results_panel(self):
        self.results = ResultsPanel()
        self.results.shifts_changed.connect(self._show_reference_planes)
        self.results.tdr_requested.connect(self._tdr)
        self.tabs.addTab(self.results, "Results")

    def _picked(self, picks):
        """Status bar: the picked points A and B, and the distance between them."""
        parts = [f"{name}: {p.kind} of {p.body} {_fmt_point(p.point * MM)}"
                 for name, p in zip("AB", picks) if p is not None]
        if all(p is not None for p in picks):
            d = picks[1].point - picks[0].point
            parts.insert(0, f"<b>|AB| = {np.linalg.norm(d):.4f} mm</b> "
                            f"(dx {d[0]:.4f}, dy {d[1]:.4f}, dz {d[2]:.4f})")
        self.pick_label.setText("&nbsp;&nbsp;".join(parts) + ("&nbsp;mm&nbsp;" if parts else ""))

    def _show_reference_planes(self):
        """A square on each valid waveguide port's plane, moved by its Results shift."""
        planes = []
        for name, port in self.waveports.items():
            if not port.ok:
                continue
            center = np.array(port.center, dtype=float)
            center[port.axis] = port.plane + port.direction * self.results.shifts.get(name, 0.0) * MM
            if port.region is not None:
                half = tuple(0.5 * (b - a) for a, b in port.region)
            else:
                half = (1.5 * port.radius,) * 2
            planes.append((port.axis, center, half))
        self.view.set_reference_planes(planes)

    # =====================================================
    # Simulation
    # =====================================================

    def _simulation_problem(self):
        """Why the model cannot be simulated now, or None."""
        if self.project is None:
            return "the project file has errors"
        if self.mesh is not None and self.mesh.ok and self.materials is None and self.mesh_busy():
            return "the mesh is still being built"
        return simulation_problem(self.project, self.mesh, self.materials)

    def _confirm_shorts(self, title="Simulate", question="Simulate anyway?"):
        """Ports or elements shorted by metal on this mesh: usually a mesh too coarse, ask."""
        text = ("On this mesh, metal joins what should be apart:\n\n"
                + "\n\n".join(f"• {s}" for s in self.materials.shorts)
                + "\n\nUnless the short is meant (a shorted stub, a PIFA's short pin), the result "
                  "will be wrong. " + question)
        answer = QMessageBox.warning(self, title, text, QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        return answer == QMessageBox.Yes

    def _port_list(self):
        """Names of the valid ports in S-matrix order (lumped, then waveguide)."""
        return port_names(self.materials) if self.materials is not None else []

    def _simulate(self):
        problem = self._simulation_problem()
        if problem:
            QMessageBox.warning(self, "Simulate", f"Cannot simulate: {problem}")
            return
        if self.materials.shorts and not self._confirm_shorts():
            return
        sim = self.project.settings["simulation"]
        self._run_fingerprint = fingerprint(self.project)      # the inputs of this run
        self._run_params = parameters(self.project, self.mesh, self.materials, self.via_bodies)
        self.simulate_action.setEnabled(False)
        self.stop_action.setEnabled(True)
        self.results.set_message("running...")
        excite = sim["excite"]
        self._port_names = self._port_list() if excite == EXCITE_ALL else [excite]
        self._n_ports = len(self._port_names)
        self._end_db = accuracy_db(self.project.settings)
        self.progress_bar.setRange(0, int(-10 * self._end_db))
        self.progress_bar.setValue(self.progress_bar.maximum())
        self.progress_bar.setFormat("starting...")
        self.progress_bar.setVisible(True)
        self.runner.start(self.materials, sim["f_min"], sim["f_max"], sim["trim_pulse"], self._end_db,
                          None if excite == EXCITE_ALL else [excite],
                          farfield=farfield_frequencies(self.project.settings))

    def _sim_progress(self, port, t, level, pulse_duration):
        self.statusBar().showMessage(
            f"simulating, {port} excited: t = {t * 1e9:.2f} ns, energy {level:.1f} dB "
            f"(stops at {self._end_db:.0f} dB)")
        index = self._port_names.index(port) + 1 if port in self._port_names else 0
        drop = min(max(-level, 0.0), -self._end_db)
        self.progress_bar.setValue(int(round(10 * (-self._end_db - drop))))
        if t < pulse_duration:          # still feeding: the energy cannot decay yet
            state = f"pulse {100 * t / pulse_duration:.0f} %"
        else:
            state = f"{level:.0f} dB → {self._end_db:.0f}"
        self.progress_bar.setFormat(f"port {index}/{self._n_ports}   {state}")

    def _sim_done(self):
        self.simulate_action.setEnabled(True)
        self.stop_action.setEnabled(False)
        self.progress_bar.setVisible(False)

    def _sim_finished(self, result):
        self._sim_done()
        if result is None:
            self.statusBar().showMessage("simulation stopped", 4000)
            self.results.set_message("stopped")
            return
        result.fingerprint = self._run_fingerprint
        result.params = self._run_params
        try:
            save_result(self.geometry.source, result)
            self.statusBar().showMessage(
                f"simulation finished: {result.name}, {format_duration(result.elapsed)}", 6000)
        except OSError as error:
            result.name = result.name or "unsaved run"
            self.statusBar().showMessage(f"simulation finished, NOT saved: {error}", 10000)
        self.runs.append(result)
        self.shown.add(result.path or id(result))
        self._show_runs()
        self.tabs.setCurrentWidget(self.results)       # show what was computed

    def _sim_failed(self, message):
        self._sim_done()
        QMessageBox.critical(self, "Simulation failed", message)
        self.results.set_message(message)

    # =====================================================
    # Loading
    # =====================================================

    def _open_dialog(self):
        start = os.path.dirname(self.geometry.source) if self.geometry else ""
        path, _ = QFileDialog.getOpenFileName(self, "Open STEP", start, "STEP (*.step *.stp)")
        if path:
            self.load(path)

    def _import_kicad(self):
        """KiCad STEP -> <name>_prep.stp next to it (geometry/kicad_step.py), then opened."""
        start = os.path.dirname(self.geometry.source) if self.geometry else ""
        src, _ = QFileDialog.getOpenFileName(self, "Import KiCad STEP", start, "STEP (*.step *.stp)")
        if not src:
            return
        dst = os.path.splitext(src)[0] + "_prep.stp"
        if os.path.exists(dst):
            answer = QMessageBox.question(
                self, "Import KiCad",
                f"{os.path.basename(dst)} exists. Overwrite it?\n\n"
                "Its project file (.toml) is kept: bodies with the same names keep their types, "
                "the types of bodies no longer in the board are removed.")
            if answer != QMessageBox.Yes:
                return
        QGuiApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            old = None
            if os.path.exists(dst):
                try:
                    old = read_step(dst)
                except Exception:           # an unreadable old file: nothing to compare
                    old = None
            report = prepare_kicad_step(src, dst)
        except Exception as error:          # a file we cannot convert: say why
            QGuiApplication.restoreOverrideCursor()
            QMessageBox.warning(self, "Import KiCad", f"Cannot convert {os.path.basename(src)}:\n{error}")
            return
        QGuiApplication.restoreOverrideCursor()
        self.load(dst)
        lines = report.lines + [f"warning: {w}" for w in report.warnings]
        if old is not None and self.geometry is not None:
            new, _, changed = compare_bodies(old, self.geometry)
            if changed:
                lines.append("\nchanged under the same name, CHECK THE TYPE (a model part may have "
                             "been renumbered): " + ", ".join(changed))
            if new:
                lines.append("new, without type: " + ", ".join(new))
        # A re-import drops the types of bodies no longer in the board (a
        # plain Open STEP keeps them, in case a CAD edit brings them back).
        gone = self.project.orphans(self.geometry) if self.project is not None else []
        if gone:
            for name in gone:
                self.project.remove_type(name)
            self._save()
            self._show_summary()
            lines.append("gone, removed from the project file: " + ", ".join(gone))
        text = "\n".join(lines)
        QMessageBox.information(self, "Import KiCad", f"Written {os.path.basename(dst)}:\n\n{text}")

    # =====================================================
    # Window size and state (app settings)
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
        # QMainWindow.geometry: self.geometry is the STEP model here, it hides the Qt method.
        normal = self.normalGeometry() if self.isMaximized() else QMainWindow.geometry(self)
        self.app_settings.window = {
            "x": normal.x(), "y": normal.y(), "width": normal.width(), "height": normal.height(),
            "maximized": self.isMaximized(),
        }
        self.app_settings.save()            # an error here must not keep the window open
        self._save_view()
        self._cancel_discretize()           # the worker stops at its next step
        self._remote_timer.stop()
        self.remote.wait()                  # a transfer cannot be cut off half way
        self.remote_watch.wait()
        self._mesh_pool.shutdown(wait=False, cancel_futures=True)
        super().closeEvent(event)

    def _remember(self, path):
        """The model goes to the top of the recent files (app settings)."""
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
            action.triggered.connect(lambda _=False, p=path: self.load(p))
        menu.addSeparator()
        menu.addAction("Remove missing", self._forget_missing)
        menu.addAction("Clear list", self._clear_recent)

    def _forget_missing(self):
        for path in list(self.app_settings.recent_files):
            if not os.path.isfile(path):
                self.app_settings.remove_recent(path)
        self.app_settings.save()

    def _clear_recent(self):
        self.app_settings.recent_files = []
        self.app_settings.save()

    def _reload(self):
        if self.geometry:
            self.load(self.geometry.source)

    def load(self, path):
        self._save_view()               # the display of the file being left (or reloaded)
        try:
            geometry = read_step(path)
        except Exception as error:      # show any kernel/file error, keep the old model
            QMessageBox.critical(self, "STEP import", f"{path}\n\n{error}")
            return

        try:
            project = Project.load(path)
        except ProjectError as error:
            # Never overwrite a file we could not read: everything stays
            # read-only until the user fixes it and presses Reload.
            QMessageBox.warning(
                self, "Project file",
                f"{error}\n\nThe project is read-only until the file is fixed (then Reload).",
            )
            project = None

        self.geometry = geometry
        self._port_cache = {}
        self.project = project
        if project is not None and project.z0path is None:
            project.z0path = project.default_z0path(geometry)     # saved with the next save
        self._project_mtime = self._file_mtime(project.path) if project else None
        self.holes = find_holes(geometry)
        self.view.highlight_holes([])
        self._mesh_coordinate = None
        self.view.set_geometry(geometry)

        up = project.up if project else "z"
        self.up_combo.blockSignals(True)
        self.up_combo.setCurrentText(up.upper())
        self.up_combo.blockSignals(False)
        self.view.set_up(up)

        view = project.view if project else {}
        self.mesh_axis.blockSignals(True)
        self.mesh_axis.setCurrentIndex(AXES.index(view.get("plane", up)))  # horizontal plane by default
        self.mesh_axis.blockSignals(False)
        self._restore_switches(view)
        if "at" in view:
            self._mesh_coordinate = view["at"] * MM

        if all(key in view for key in ("camera_center", "camera_distance", "camera_rotation")):
            self.view.view.camera.set_state(view["camera_center"], view["camera_distance"],
                                            view["camera_rotation"])

        self._refresh_model(center_slider="at" not in view)
        self._show_path()
        self._fill_tree()
        self._restore_hidden(view.get("hidden", []))
        self._view_timer.stop()         # the camera reset above is no change of the user's
        self._fill_materials()
        self._load_runs(path)
        self._remember(path)
        self._remote_from_record()              # its runs on a server, as recorded here (no connection)

        lo, hi = geometry.bounds
        self.setWindowTitle(f"customcst - {os.path.basename(path)}  ({_fmt_mm(hi - lo)} mm)")

    # =====================================================
    # The last display, kept in the project's [view]
    # =====================================================

    def _view_switches(self):
        """{[view] key: its toolbar check box or button}."""
        return {"edges": self.edges_check, "xray": self.xray_check, "ref_planes": self.planes_check,
                "mesh": self.mesh_check, "materials": self.materials_check,
                "section": self.section_check, "flip": self.section_flip}

    def _body_items(self):
        bodies = self._tree_item("bodies", None)
        if bodies is None:
            return []
        return [bodies.child(i) for i in range(bodies.childCount())]

    def _view_state(self):
        state = {key: switch.isChecked() for key, switch in self._view_switches().items()}
        state["plane"] = AXES[self.mesh_axis.currentIndex()]
        if self._mesh_coordinate is not None:
            state["at"] = round(float(self._mesh_coordinate) / MM, 4)
        state["hidden"] = [item.data(0, Qt.UserRole)[1] for item in self._body_items()
                           if item.checkState(0) != Qt.Checked]
        camera = self.view.view.camera.state()
        state["camera_center"] = [round(v, 4) for v in camera["center"]]
        state["camera_distance"] = round(camera["distance"], 4)
        state["camera_rotation"] = [round(v, 6) for v in camera["rotation"]]
        return state

    def _view_changed(self):
        if self._restoring or self.project is None or self.geometry is None:
            return
        self._view_timer.start()        # restarted by every camera move: one save after it stops

    def _save_view(self):
        """
        Writes the display to the project file if it changed (quietly: no
        status message). Never over a file edited outside since it was
        read: that edit would be lost for a view change (Reload takes it).
        """
        self._view_timer.stop()
        if self.project is None or self.geometry is None:
            return
        state = self._view_state()
        if state == self.project.view:
            return
        self.project.view = state
        name = os.path.basename(self.project.path)
        if self._file_mtime(self.project.path) != self._project_mtime:
            self.statusBar().showMessage(f"{name} changed on disk: the view is not saved (Reload reads it)", 10000)
            return
        try:
            self.project.save()
        except OSError as error:
            self.statusBar().showMessage(f"view not saved to {name}: {error}", 10000)
            return
        self._project_mtime = self._file_mtime(self.project.path)

    @staticmethod
    def _file_mtime(path):
        try:
            return os.stat(path).st_mtime_ns
        except OSError:
            return None

    def _restore_switches(self, view):
        """
        The toolbar from [view] (defaults for missing keys), without the
        toggled handlers: the 3D view gets the three display switches here,
        the plane ones are read when the plane is shown (_mesh_ready).
        """
        defaults = {"edges": True, "ref_planes": True}
        for key, switch in self._view_switches().items():
            switch.blockSignals(True)
            switch.setChecked(view.get(key, defaults.get(key, False)))
            switch.blockSignals(False)
        self.view.set_show_edges(self.edges_check.isChecked())
        self.view.set_xray(self.xray_check.isChecked())
        self.view.set_show_reference_planes(self.planes_check.isChecked())

    def _restore_hidden(self, names):
        self._restoring = True
        try:
            for item in self._body_items():
                if item.data(0, Qt.UserRole)[1] in names:
                    item.setCheckState(0, Qt.Unchecked)     # -> _item_changed hides it
        finally:
            self._restoring = False

    # =====================================================
    # Derived model: ports, colors, mesh (recomputed after every edit)
    # =====================================================

    def _refresh_model(self, center_slider=False):
        """
        Ports, vias, the grid (fast), then the discretization in the
        background (_start_discretize): self.materials is None until it is
        done, then _mesh_ready() updates the plane and the tree.
        """
        self.ports = self.project.ports(self.geometry, self._port_cache) if self.project else {}
        self.elements = self.project.elements(self.geometry, self._port_cache) if self.project else {}
        self.waveports = self.project.waveports(self.geometry) if self.project else {}
        self.view.set_ports(list(self.ports.values()), list(self.elements.values()))
        self._show_reference_planes()

        types = {n: a.type for n, a in self.project.assignments.items()} if self.project else {}
        self.view.set_type_colors(types)

        self.via_bodies = [via_body(h) for h in self.project.vias(self.holes)] if self.project else []
        self.view.set_vias(self.via_bodies)

        columns = {**self.ports, **self.elements}       # bodies that need a line on their axis
        self.mesh = (build_grid(self.geometry, self.project, columns, self.waveports, self.via_bodies)
                     if self.project else None)
        self.materials = None
        self._nets = self._profiles = None              # of the old mesh: free them
        self._center_slider = self._center_slider or center_slider
        if self.mesh is not None and self.mesh.ok:
            self._start_discretize()
            self._update_remote_buttons()                         # off: "the mesh is still being built"
        else:
            self._cancel_discretize()
            self._mesh_ready()

    # Background discretization: one worker thread, no Qt objects in it.
    # The newest job wins: an edit while one runs cancels it (it stops at
    # its next step) and starts another; a timer polls the job's progress
    # and takes the result in the GUI thread. No nested event loop: running
    # discretize in the GUI thread with processEvents crashed the app when
    # the tree's editor was deleted under its own commit.

    def _start_discretize(self):
        self._cancel_discretize()
        job = SimpleNamespace(progress=(0, 1, ""), cancelled=False, start=time.perf_counter(),
                              mesh=self.mesh, geometry=self.geometry)
        args = (self.mesh, self.geometry, self.project, self.ports, self.elements, self.waveports,
                self.via_bodies)

        def progress(done, total, what):
            if job.cancelled:
                raise _Cancelled()
            job.progress = (done, total, what)

        def run():
            return discretize(*args, progress=progress)

        job.future = self._mesh_pool.submit(run)
        self._mesh_job = job
        self._mesh_timer.start()

    def _cancel_discretize(self):
        if self._mesh_job is not None:
            self._mesh_job.cancelled = True
            self._mesh_job = None
        self._mesh_timer.stop()
        self.mesh_bar.setVisible(False)

    def _poll_discretize(self):
        job = self._mesh_job
        if job is None:
            self._mesh_timer.stop()
            return
        elapsed = time.perf_counter() - job.start
        cells = job.mesh.grid.cell_count / 1e6
        if not job.future.done():
            if elapsed > MESH_PROGRESS_DELAY:
                done, total, what = job.progress
                self.mesh_bar.setRange(0, total)
                self.mesh_bar.setValue(done)
                self.mesh_bar.setFormat(f"mesh {done}/{total}: {what}")
                self.mesh_bar.setVisible(True)
                self.statusBar().showMessage(f"Mesh: {cells:.2f} M cells, discretizing {what} ({done}/{total})")
            return
        if self.tree.state() == QAbstractItemView.EditingState:
            return                      # the tree is rebuilt below: not under an open editor
        self._mesh_job = None
        self._mesh_timer.stop()
        self.mesh_bar.setVisible(False)
        try:
            self.materials = job.future.result()
        except Exception as error:      # show it, keep the window alive
            self.materials = None
            self.statusBar().showMessage(f"Mesh: discretization failed: {error}", 15000)
        else:
            if elapsed > MESH_PROGRESS_DELAY:
                self.statusBar().showMessage(f"Mesh: {cells:.2f} M cells in {elapsed:.1f} s", 5000)
        self._mesh_ready()
        if self.geometry is not None:
            self._refresh_rows()

    def mesh_busy(self):
        return self._mesh_job is not None

    def wait_for_mesh(self, timeout=600.0):
        """For scripts and tests: process events until the background discretization is done."""
        end = time.perf_counter() + timeout
        while self.mesh_busy() and time.perf_counter() < end:
            QApplication.processEvents()
            time.sleep(0.01)

    def _mesh_ready(self):
        self._update_remote_buttons()
        self._configure_mesh_slider(center=self._center_slider)
        self._center_slider = False
        self._show_mesh_plane()
        if self.profile_window is not None and self.profile_window.isVisible():
            self.profile_window.mark_outdated()
        self._update_tdr()

    def _on_grid(self):
        """The plane is on the grid lines: the mesh or materials plane is shown."""
        return (self.mesh is not None and self.mesh.ok
                and (self.mesh_check.isChecked() or self.materials_check.isChecked()))

    def _slider_positions(self):
        """
        Plane coordinates of the slider: the grid lines while the mesh or
        materials plane is shown, else fine even steps (a smooth section;
        the grid's lines are up to millimeters apart in the air).
        """
        axis = self.mesh_axis.currentIndex()
        if self._on_grid():
            return self.mesh.grid.lines[axis]
        if self.mesh is not None and self.mesh.ok:
            lines = self.mesh.grid.lines[axis]
            lo, hi = lines[0], lines[-1]
        else:
            lo, hi = (b[axis] for b in self.geometry.bounds)
        return np.linspace(lo, hi, SLIDER_STEPS + 1)

    def _grid_index(self):
        """The grid line nearest the plane (for the mesh, materials and Z0)."""
        lines = self.mesh.grid.lines[self.mesh_axis.currentIndex()]
        return int(np.argmin(np.abs(lines - self._mesh_coordinate)))

    def _grid_plane_toggled(self):
        # The slider's steps change (grid lines or fine steps); the plane
        # goes to the nearest one.
        self._configure_mesh_slider()
        if self._on_grid():
            self._mesh_coordinate = float(self._slider_positions()[self.mesh_slider.value()])
        self._show_mesh_plane()

    def _configure_mesh_slider(self, center=False):
        if self.geometry is None:
            self.mesh_slider.setEnabled(False)
            return

        # The plane keeps its coordinate when the grid changes (the line
        # index would point somewhere else); new axis or first mesh: center.
        # Without a mesh (no types yet) the slider still moves the section.
        axis = self.mesh_axis.currentIndex()
        lines = self._slider_positions()
        if center or self._mesh_coordinate is None:
            lo, hi = self.geometry.bounds
            self._mesh_coordinate = 0.5 * (lo + hi)[axis]

        self.mesh_slider.blockSignals(True)
        self.mesh_slider.setEnabled(True)
        self.mesh_slider.setRange(0, len(lines) - 1)
        self.mesh_slider.setValue(int(np.argmin(np.abs(lines - self._mesh_coordinate))))
        self.mesh_slider.blockSignals(False)

    def _mesh_slider_moved(self, index):
        if self.geometry is None:
            return
        self._mesh_coordinate = float(self._slider_positions()[index])
        self._view_changed()
        window = self.profile_window
        if (self.mesh is not None and self.mesh.ok and window is not None and window.profile is not None
                and window.profile.axis == self.mesh_axis.currentIndex()):
            window.set_marker(self._mesh_coordinate)
        self._show_mesh_plane()

    def _mesh_axis_changed(self, _index):
        self._configure_mesh_slider(center=True)
        self._show_mesh_plane()

    def _show_mesh_plane(self):
        self._show_grid_plane()
        self._show_section()            # after: it may write the plane's label too

    def _show_grid_plane(self):
        # "Mesh" and "Materials" each show the plane; either alone works.
        ok = self.mesh is not None and self.mesh.ok
        show_mesh, show_materials = self.mesh_check.isChecked(), self.materials_check.isChecked()
        self.view.set_ghost(ok and show_materials)
        self.legend.setVisible(ok and show_materials)
        if not (ok and (show_mesh or show_materials)):
            self.view.set_mesh_plane(None)
            self.view.set_material_plane(None)
            self.view.set_domain(None)
            self.mesh_label.setText("")
            if (show_mesh or show_materials) and self.mesh is not None and not ok:
                self.statusBar().showMessage(f"no mesh: {self.mesh.error}", 5000)
            return

        axis = self.mesh_axis.currentIndex()
        index = self._grid_index()
        coordinate = self.mesh.grid.lines[axis][index]
        self.view.set_mesh_plane(self.mesh.grid if show_mesh else None, axis, coordinate)
        if show_materials and self.materials is not None:
            self.view.set_material_plane(self.materials, axis, index)
        else:
            self.view.set_material_plane(None)
        self.view.set_domain(self.mesh.grid.bounds)
        self.mesh_label.setText(f" {AXES[axis]} = {coordinate / MM:.3f} mm")

    # =====================================================
    # Line impedance (solver/line_profile.py)
    # =====================================================

    def _z0_ready(self):
        if self.materials is None or self.geometry is None:
            self.statusBar().showMessage("Z0: no mesh yet", 5000)
            return False
        return True

    def _pec_nets(self):
        """The 3D nets of the current materials (found once per mesh)."""
        if self._nets is None or self._nets[0] is not self.materials:
            self._nets = (self.materials, pec_nets(self.materials))
        return self._nets[1]

    def _z0_here(self):
        if not self._z0_ready():
            return
        axis = self.mesh_axis.currentIndex()
        s = line_impedance(self.materials, axis, self._grid_index(), self.geometry.bounds, self._pec_nets())
        where = f"{AXES[axis]} = {s.coordinate / MM:.3f} mm"
        note = self._z0_note()
        self.statusBar().showMessage(
            f"Z0 at {where}: {s.z0:.1f} Ω, eps_eff {s.eps_eff:.3f}" + (f"  (narrow lines: {note})" if note else "")
            if not s.error else f"Z0 at {where}: {s.error}", 15000)

    def _z0_profile(self):
        """
        Open the profile window: along the path in path mode, else along
        the mesh plane's axis (the window's along box switches): the kept
        profile of this mesh, or Compute to make one.
        """
        if not self._z0_ready():
            return
        if self.profile_window is None:
            self.profile_window = ProfileWindow(self)
            self.profile_window.section_picked.connect(self._profile_picked)
            self.profile_window.compute_requested.connect(self._z0_profile_compute)
            self.profile_window.source_changed.connect(self._show_source)
            self.profile_window.hidden.connect(self._show_path)
        source = "path" if self.path_check.isChecked() else AXES[self.mesh_axis.currentIndex()]
        self.profile_window.show()
        self.profile_window.raise_()
        self._show_source(source)

    def _kept_profiles(self):
        """
        {axis or "path": Profile} of the current materials (cleared with the
        mesh; the path's also when it changes). A new mesh starts with the
        profiles saved for it (solver/profile_file.py: the same model,
        types and settings, and path).
        """
        if self._profiles is None or self._profiles[0] is not self.materials:
            self._profiles = (self.materials, self._saved_profiles())
        return self._profiles[1]

    def _saved_profiles(self):
        if self.project is None or self.materials is None:
            return {}
        try:
            model = fingerprint(self.project)
        except OSError:
            return {}
        kept = {}
        for source in ("x", "y", "z", "path"):
            key = profile_key(self.project, source, self.project.z0path, model)
            profile = load_profile(self.project.step_path, source, key)
            if profile is not None:
                kept[self._profile_key(source)] = profile
        return kept

    @staticmethod
    def _profile_key(source):
        return "path" if source == "path" else AXES.index(source)

    def _show_source(self, source):
        """The profile window shows `source`: its kept profile, or the Compute state."""
        if self.materials is None:
            self.profile_window.set_empty(source, "No mesh yet: wait for the discretization.")
        else:
            profile = self._kept_profiles().get(self._profile_key(source))
            if profile is not None:
                self._show_profile(profile)
            elif source == "path" and self._z0path() is None:
                self.profile_window.set_empty(source, "No path: check Z₀ path on the toolbar and click "
                                                      "at least two points in the 3D view.")
            else:
                self.profile_window.set_empty(source)
        self._show_path()

    def _z0_profile_compute(self, source):
        if not self._z0_ready():
            return
        path = self._z0path() if source == "path" else None
        if source == "path" and path is None:
            self._show_source(source)
            return
        where = "the path" if path is not None else source

        def progress(done, total, what=""):
            counted = f"section {done} / {total}" if total else ""
            self.statusBar().showMessage(f"Z0 profile along {where}: "
                                         + ", ".join(t for t in (counted, what) if t))
            self.profile_window.set_progress(done, total, what)
            QApplication.processEvents()

        QApplication.setOverrideCursor(Qt.WaitCursor)
        self.profile_window.start_compute()
        QApplication.processEvents()
        try:
            if path is not None:
                profile = path_profile(self.geometry, self.project, path, self.via_bodies,
                                       self._pec_nets(), progress)
            else:
                profile = impedance_profile(self.materials, AXES.index(source), self.geometry.bounds,
                                            progress, self._pec_nets())
        finally:
            QApplication.restoreOverrideCursor()
            self.profile_window.end_compute()
        self._kept_profiles()[self._profile_key(source)] = profile
        saved = ""
        try:
            save_profile(self.project.step_path, source, profile,
                         profile_key(self.project, source, self.project.z0path))
        except OSError as error:
            saved = f" (not saved: {error})"
        self.statusBar().showMessage(f"Z0 profile along {where}: {len(profile.sections)} sections{saved}", 5000)
        self._show_profile(profile)
        self._update_tdr()

    def _show_profile(self, profile):
        window = self.profile_window
        source = "path" if profile.axis < 0 else AXES[profile.axis]
        window.set_profile(profile, source, accuracy=self._z0_note())
        if profile.axis >= 0:
            if self._mesh_coordinate is not None:
                window.set_marker(self._mesh_coordinate)
        elif profile.sections:
            self._profile_picked(window.index)
        self._show_path()

    def _z0_convergence(self):
        """Z0 at the plane on finer and finer grids of its own (the 3D grid is not touched)."""
        if self.mesh is None or not self.mesh.ok or self.geometry is None:
            self.statusBar().showMessage("Z0 convergence: no mesh yet", 5000)
            return
        axis = self.mesh_axis.currentIndex()
        coordinate = float(self.mesh.grid.lines[axis][self._grid_index()])
        where = f"{AXES[axis]} = {coordinate / MM:.3f} mm"

        def progress(done, total):
            self.statusBar().showMessage(f"Z0 convergence at {where}: level {done} done")
            QApplication.processEvents()

        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            result = z0_convergence(self.geometry, self.project, axis, coordinate, self.mesh.min_step,
                                    self.project.settings["mesh"]["cells_per_feature"],
                                    self.waveports, self.via_bodies, progress=progress,
                                    nets=self._pec_nets() if self.materials is not None else None)
        finally:
            QApplication.restoreOverrideCursor()
        self.statusBar().clearMessage()

        rows = "".join(
            f"<tr><td align=right>{lv.cells_per_feature:g}</td><td align=right>{lv.min_step / MM:.4f}</td>"
            f"<td align=right>{lv.nodes:,}</td>"
            + (f"<td align=right>{lv.section.z0:.2f}</td><td align=right>{lv.section.eps_eff:.3f}</td>"
               if not lv.section.error else f"<td colspan=2>{lv.section.error}</td>") + "</tr>"
            for lv in result.levels)
        estimate = result.estimate
        if estimate is None:
            verdict = "No Z0 here (see the table)."
        else:
            z, spread = estimate
            state = ("converged" if result.converged
                     else f"<b>not converged</b> (more than {CONVERGED:.0%} between the last two)")
            verdict = f"Z0 = <b>{z:.1f} ± {spread:.1f} Ω</b>, {state}."
            own = self.project.settings["mesh"]["cells_per_feature"]
            same = [lv for lv in result.levels if lv.cells_per_feature == own and not lv.section.error]
            if same:
                verdict += (f" The 3D grid now (cells per feature {own:g}) sees the line as "
                            f"{same[0].section.z0:.1f} Ω.")
        QMessageBox.information(
            self, "Z0 convergence",
            f"<p>Z0 at {where} on grids of its own (only this plane, the 3D grid is unchanged):</p>"
            "<table cellspacing=0 cellpadding=3 border=1>"
            "<tr><th>cells per feature</th><th>min step, mm</th><th>nodes</th><th>Z0, Ω</th>"
            "<th>eps_eff</th></tr>" + rows + "</table>"
            f"<p>{verdict}</p>")

    def _profile_picked(self, index):
        """
        The profile's marker moved: along an axis, put the plane there (and
        make it visible); along the path, mark the section across it.
        """
        self._mark_section(self.profile_window.profile, index)

    def _mark_section(self, profile, index):
        """A profile's section in 3D: the mesh plane there (along an axis), the section window (along the path)."""
        axis = profile.axis
        if axis < 0:
            self.view.set_path_section(profile.points[index], profile.across[index])
            return
        coordinate = profile.sections[index].coordinate
        if self.mesh is None or not self.mesh.ok:
            return
        if self.mesh_axis.currentIndex() != axis:
            self.mesh_axis.setCurrentIndex(axis)
        if not (self.mesh_check.isChecked() or self.materials_check.isChecked()
                or self.section_check.isChecked()):
            self.materials_check.setChecked(True)
        self.mesh_slider.setValue(int(np.argmin(np.abs(self._slider_positions() - coordinate))))

    # =====================================================
    # TDR (solver/tdr.py, view/tdr_window.py)
    # =====================================================

    def _tdr(self):
        """The TDR window: the runs shown in Results, on a kept Z0 profile of this mesh."""
        if self.tdr_window is None:
            self.tdr_window = TdrWindow(self)
            self.tdr_window.section_picked.connect(self._mark_section)
            self.tdr_window.profile_changed.connect(self._show_path)
            self.tdr_window.hidden.connect(self._show_path)
            self.tdr_window.profile_requested.connect(self._tdr_profile)
        self.tdr_window.show()
        self.tdr_window.raise_()
        self._update_tdr()

    def _update_tdr(self):
        window = self.tdr_window
        if window is None or not window.isVisible():
            return
        profiles = self._kept_profiles() if self.materials is not None else {}
        window.path_drawn = self._z0path() is not None
        window.set_data(self.results.runs, profiles, self.waveports)

    def _tdr_profile(self, source):
        """The TDR window asks for a profile: open the profile window on `source` and compute it."""
        if not self._z0_ready():
            return
        self._z0_profile()
        self._show_source(source)
        self._z0_profile_compute(source)

    # =====================================================
    # Z0 path (project [z0path], solver/path_profile.py)
    # =====================================================

    def _z0path(self):
        """The project's path as a solver Path (m), or None without two points."""
        z0path = self.project.z0path if self.project is not None else None
        if z0path is None or len(z0path["points"]) < 2:
            return None
        return Path(UP_AXES.index(z0path["axis"]), z0path["at"] * MM,
                    np.asarray(z0path["points"], dtype=float) * MM, z0path["half_width"] * MM)

    def _path_on_profile(self):
        window = self.profile_window
        return window is not None and window.isVisible() and window.source == "path"

    def _path_on_tdr(self):
        window = self.tdr_window
        return window is not None and window.isVisible() and window.on_path

    def _show_path(self):
        """The path in the 3D view: while drawing it, or while the profile or the TDR window reads along it."""
        z0path = self.project.z0path if self.project is not None else None
        if z0path is None:
            return
        self.view.set_path(UP_AXES.index(z0path["axis"]), z0path["at"] * MM,
                           np.asarray(z0path["points"], dtype=float).reshape(-1, 2) * MM,
                           shown=self._path_on_profile() or self._path_on_tdr(),
                           half_width=z0path["half_width"] * MM)
        window = self.profile_window
        if not (self._path_on_profile() and window.profile is not None and window.profile.sections
                or self._path_on_tdr()):
            self.view.set_path_section(None)

    def _path_mode_toggled(self, on):
        if on and self.project is None:
            self.statusBar().showMessage("Z0 path: the project file has errors (fix it, then Reload)", 5000)
        self.view.set_path_mode(on and self.project is not None)
        if on:
            self.view.set_picks([None, None])
            self.statusBar().showMessage(
                "Z0 path: click adds a point, Ctrl + drag moves one, double click deletes one, "
                "Shift + click on a segment inserts one", 10000)
        self._show_path()

    def _path_edited(self, points):
        """The path was edited in the 3D view: save it; its profile is outdated."""
        if self.project is None:
            return
        self.project.set_z0path_points(np.asarray(points) / MM)
        self._z0path_changed()

    def _z0path_changed(self):
        self._save()
        self._fill_z0path()
        if self._profiles is not None:
            self._profiles[1].pop("path", None)
        self._update_tdr()
        if self._path_on_profile() and self.profile_window.profile is not None:
            self.profile_window.mark_outdated("the path changed")
            self.view.set_path_section(None)
        self._show_path()

    def _fill_z0path(self):
        """The Z0 path section of the tree: its plane, the section width and the points (rebuilt in place)."""
        model = self._tree_item("model", None)
        old = self._tree_item("z0path", None)
        index = 0                                   # the first row of Model
        if old is not None:
            index = model.indexOfChild(old)
            model.takeChild(index)
        if self.project is None or self.project.z0path is None:
            return
        self.tree.blockSignals(True)
        z0path = self.project.z0path
        section = _group_item(None, "Z0 path", "z0path",
                              "The path of the Z0 profile along it: draw it with Z₀ path on the toolbar")
        model.insertChild(index, section)
        for param in Z0PATH_PARAMS:
            value = z0path[param.name]
            row = QTreeWidgetItem(section, [param.label, _fmt_value(value), param.unit])
            row.setData(0, Qt.UserRole, ("z0path_param", "z0path", param.name))
            row.setFlags(row.flags() | Qt.ItemIsEditable)
            row.setToolTip(0, param.tooltip)
            row.setToolTip(1, "Double click to edit")
        points = np.asarray(z0path["points"], dtype=float).reshape(-1, 2)
        length = float(np.linalg.norm(np.diff(points, axis=0), axis=1).sum()) if len(points) > 1 else 0.0
        u, v = [a for a in AXES if a != z0path["axis"]]
        row = QTreeWidgetItem(section, ["points", f"{len(points)}, length {length:.3f}", "mm"])
        row.setData(0, Qt.UserRole, ("info", "z0path", None))
        row.setForeground(1, QColor("#2a2"))
        row.setToolTip(1, f"({u}, {v}) mm: " + ", ".join(f"({p[0]:g}, {p[1]:g})" for p in points))
        note = self._note_row(section, "z0path", "half width ≥ half the widest part (connector: its legs)",
                              "#888")
        note.setToolTip(0, "Each cross-section spans the half width to either side of the path. What its "
                           "side cuts counts as ground, but a ground beyond it is left out and Z0 reads "
                           "high (an SMA launch with 2 mm: 54.5 Ω for 49.2). Not wider than needed: a "
                           "neighboring line inside it would be a second conductor.")
        section.setExpanded(True)
        self.tree.blockSignals(False)

    def _show_section(self):
        """Section view at the plane of the slider (grid lines, or steps over the model without a mesh)."""
        if not self.section_check.isChecked() or self.geometry is None:
            self.view.set_section(None)
            return
        axis = self.mesh_axis.currentIndex()
        coordinate = self._mesh_coordinate
        if coordinate is None:
            lo, hi = self.geometry.bounds
            coordinate = 0.5 * (lo + hi)[axis]
        self.view.set_section(axis, coordinate, keep_below=not self.section_flip.isChecked(),
                              quick=self.mesh_slider.isSliderDown())
        self.mesh_label.setText(f" {AXES[axis]} = {coordinate / MM:.3f} mm")

    # =====================================================
    # Tree
    #   Simulation        section row: settings, then
    #     Monitors        what a run collects (the ports' S for now)
    #     Results         one row per run
    #   Mesh              section row: settings, then mesh statistics
    #   Model             what the STEP is and how it is read (last: long)
    #     Z0 path         the path of the Z0 profile
    #     Holes           open through holes, grouped by diameter
    #     Bodies
    #       body row      name (visibility checkbox) | kind | size
    #         type row    "Type" | Dielectric
    #           param row "εr" | 4.3 (editable) | unit
    #           note row  read-only explanation
    # Every item carries (role, owner, param) in UserRole; owner is a body
    # name for body rows and a section name for section rows.
    # =====================================================

    def _fill_tree(self):
        self.tree.blockSignals(True)
        self.tree.clear()

        if self.project is not None:
            for name in TREE_SECTIONS:
                item = _group_item(None, SECTIONS[name].label, "section", owner=name)
                self.tree.addTopLevelItem(item)
                self._fill_section(item)

        self.tree.blockSignals(True)
        model = _group_item(None, "Model", "model",
                            "The STEP's bodies and their types, its through holes, the Z0 path")
        self.tree.addTopLevelItem(model)
        bodies = _group_item(model, "Bodies", "bodies", "The STEP's bodies: right click one to set its type")
        bodies.setText(1, f"{len(self.geometry.bodies)}")
        for body in self.geometry.bodies:
            lo, hi = body.bounds
            item = QTreeWidgetItem([body.name, body.kind, _fmt_mm(hi - lo)])
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(0, Qt.Checked)
            item.setData(0, Qt.UserRole, ("body", body.name, None))
            bodies.addChild(item)
            self._fill_type(item)
        model.setExpanded(True)
        bodies.setExpanded(True)

        self.tree.blockSignals(False)
        self._fill_z0path()
        self.tree.blockSignals(True)
        self._fill_holes()

        self.tree.blockSignals(False)
        self._resize_columns()
        self._show_summary()

    def _fill_holes(self):
        """
        The Holes section: open through holes, grouped by diameter, with their
        via assignment. Rebuilt in place after an edit (expanded groups kept).
        """
        model = self._tree_item("model", None)
        old = self._tree_item("holes", None)
        index, expanded = model.indexOfChild(self._tree_item("bodies", None)), set()   # before Bodies
        if old is not None:
            index = model.indexOfChild(old)
            expanded = {old.child(i).data(0, Qt.UserRole)[1] for i in range(old.childCount())
                        if old.child(i).isExpanded()}
            model.takeChild(index)
        if not self.holes:
            return

        n_vias = len(self.project.vias(self.holes)) if self.project else 0
        section = _group_item(None, "Holes", "holes",
                              "Open through holes found in the STEP. Select to highlight them, "
                              "right click a diameter or a hole to make it a via.")
        section.setText(1, f"{len(self.holes)} through, {n_vias} via")
        model.insertChild(index, section)

        gold = QColor("#c8961e")
        for diameter, holes in group_by_diameter(self.holes).items():
            kinds = [self.project.hole_rule(h) if self.project else ("none", "") for h in holes]
            vias = sum(kind == "via" for kind, _ in kinds)
            text = f"× {len(holes)}" + (f", {vias} via" if vias else "")
            group = QTreeWidgetItem(section, [f"⌀ {diameter / MM:.3f}", text, "mm"])
            group.setData(0, Qt.UserRole, ("hole_group", diameter, None))
            if vias:
                group.setForeground(1, gold)
            for hole, (kind, source) in zip(holes, kinds):
                label = hole.id + ("  via" if kind == "via" else "") + ("  *" if source == "single" else "")
                row = QTreeWidgetItem(group, [label, _fmt_point(hole.center), ", ".join(hole.bodies)])
                row.setData(0, Qt.UserRole, ("hole", hole.id, None))
                row.setToolTip(0, "* = its own rule, not the diameter's")
                row.setToolTip(1, "center, mm")
                if kind == "via":
                    row.setForeground(0, gold)
            group.setExpanded(diameter in expanded or (old is None and len(holes) <= 5))
        section.setExpanded(True)

    def _hole_menu(self, item, role, key):
        """Right click on a diameter group or a hole: make it a via, or not."""
        menu = QMenu(self)
        if role == "hole_group":
            holes = group_by_diameter(self.holes).get(key, [])
            drill = key / MM
            current = next((k for d, k in self.project.hole_diameters.items() if abs(d - drill) < 1e-4), None)
            for kind, text in (("via", f"Via: all ⌀ {drill:g} mm ({len(holes)})"),
                               ("none", f"Not via: all ⌀ {drill:g} mm")):
                action = menu.addAction(text)
                action.setCheckable(True)
                action.setChecked(current == kind)
                action.triggered.connect(lambda _=False, k=kind: self._set_hole_rule(
                    lambda: self.project.set_hole_diameter(drill, k)))
            remove = menu.addAction("Remove the diameter rule")
            remove.setEnabled(current is not None)
            remove.triggered.connect(lambda: self._set_hole_rule(lambda: self.project.set_hole_diameter(drill, None)))
        elif role == "hole":
            hole = next(h for h in self.holes if h.id == key)
            kind, source = self.project.hole_rule(hole)
            center = hole.center / MM
            for k, text in (("via", "Via"), ("none", "Not via")):
                action = menu.addAction(text)
                action.setCheckable(True)
                action.setChecked(source == "single" and kind == k)
                action.triggered.connect(lambda _=False, k=k: self._set_hole_rule(
                    lambda: self.project.set_hole_single(center, k)))
            back = menu.addAction(f"Use the ⌀ {hole.diameter / MM:g} mm rule")
            back.setEnabled(source == "single")
            back.triggered.connect(lambda: self._set_hole_rule(lambda: self.project.set_hole_single(center, None)))
        else:
            return
        menu.exec(self.tree.viewport().mapToGlobal(self.tree.visualItemRect(item).bottomLeft()))

    def _set_hole_rule(self, action):
        try:
            action()
        except ProjectError as error:
            self.statusBar().showMessage(str(error), 5000)
            return
        self._after_edit()

    def _selected_holes(self, item):
        """The holes a tree row stands for (section, group or one hole), or None."""
        role, key, _ = item.data(0, Qt.UserRole)
        if role == "holes":
            return list(self.holes)
        if role == "hole_group":
            return group_by_diameter(self.holes).get(key, [])
        if role == "hole":
            return [h for h in self.holes if h.id == key]
        return None

    def _show_holes_info(self, holes):
        if len(holes) == 1:
            h = holes[0]
            axis = "xyz"[h.axis_index] if h.axis_index is not None else _fmt_point(h.axis * MM)
            lines = [f"<b>{h.id}</b>: through hole ⌀ {h.diameter / MM:.3f} mm",
                     f"center: {_fmt_point(h.center)} mm",
                     f"from {_fmt_point(h.start)} to {_fmt_point(h.end)} mm (axis {axis})",
                     f"length: {h.length / MM:.4g} mm",
                     f"through: {', '.join(h.bodies)}"]
        else:
            diameters = sorted({round(h.diameter / MM, 3) for h in holes})
            lines = [f"<b>{len(holes)} holes</b>, ⌀ {', '.join(f'{d:g}' for d in diameters)} mm"]
        self.info.setText("<br>".join(lines))

    def _fill_section(self, item):
        self.tree.blockSignals(True)
        item.takeChildren()
        name = item.data(0, Qt.UserRole)[1]
        section = SECTIONS[name]

        for param in section.params:
            self._setting_row(item, name, param)

        if section.note:
            self._note_row(item, name, section.note, "gray")

        if name == "mesh":
            self._mesh_rows(item)
        if name == "simulation":
            _group_item(item, "Monitors", "monitors",
                        "What a run collects besides the field itself")
            _group_item(item, "Results", "results")

        item.setExpanded(True)
        self.tree.blockSignals(False)
        if name == "simulation":
            self._fill_monitors()
            self._fill_results()

    def _setting_row(self, parent, name, param):
        """A row of a [name] setting: editable value, or a check box for a switch."""
        value = self.project.settings[name][param.name]
        row = QTreeWidgetItem([param.label, _fmt_value(value), param.unit])
        row.setData(0, Qt.UserRole, ("setting", name, param.name))  # before it joins the tree (itemChanged)
        parent.addChild(row)
        if param.boolean:                       # a check box, toggled with a click
            row.setFlags(row.flags() | Qt.ItemIsUserCheckable)
            row.setCheckState(1, Qt.Checked if value else Qt.Unchecked)
        else:
            row.setFlags(row.flags() | Qt.ItemIsEditable)
            row.setToolTip(1, "Double click to edit")
        if param.tooltip:
            row.setToolTip(0, param.tooltip)
            row.setToolTip(1, param.tooltip)
        return row

    def _fill_monitors(self):
        """Simulation > Monitors: what every run records (the ports' S; the far field if set)."""
        item = self._tree_item("monitors", None)
        if item is None:
            return
        self.tree.blockSignals(True)
        item.takeChildren()
        ports = self._port_list()
        row = QTreeWidgetItem(item, ["Ports (S)", ", ".join(ports) if ports else "no ports", ""])
        row.setData(0, Qt.UserRole, ("info", "monitors", None))
        row.setForeground(1, QColor("#2a2" if ports else "#c80"))
        row.setToolTip(0, "Every run records the voltage and current at each port: the S-parameters "
                          "(and the TDR from them)")

        far = _group_item(item, "Far field", "farfield", SECTIONS["farfield"].params[0].tooltip)
        for param in SECTIONS["farfield"].params:
            self._setting_row(far, "farfield", param)
        self._note_row(far, "farfield", "empty: off; D, G and efficiency under Results > the run", "gray")
        far.setExpanded(True)
        item.setExpanded(True)
        self.tree.blockSignals(False)

    def _z0_note(self):
        """'' or a short note on how low Z0 of narrow lines comes out at this mesh."""
        cells = self.project.settings["mesh"]["cells_per_feature"] if self.project else 0
        if not cells or cells >= ACCURATE_CELLS_PER_FEATURE:
            return ""
        return f"~{z0_error_percent(cells):.0f} % low at cells per feature {cells:g}"

    def _z0_accuracy_rows(self, item):
        note = self._z0_note()
        if not note:
            return
        advice = (
            "A staircased conductor looks wider near its edges, where the field is strongest: "
            "the Z0 of narrow lines (traces and gaps of a few cells) comes out low, about 7 % at "
            "4 cells per feature, 3 % at 6, 1.5 % at 8, 1 % at 12 (a 0.3 mm GCPW over 0.2 mm FR4). "
            "Variants compare fine at this mesh. For absolute Z0: cells per feature >= 8 and min step "
            "<= (narrowest trace or gap) / 8; the min step bounds the cells too. Check a line's Z0 "
            "with Z₀ (toolbar) at such a mesh first: the mesh builds in seconds, the 3D run is the "
            "long part (cells and time steps both grow).")
        for text in (f"⚠ narrow lines: Z0 {note}",
                     f"   for absolute Z0: cells per feature ≥ {ACCURATE_CELLS_PER_FEATURE}, "
                     f"min step ≤ narrowest gap / {ACCURATE_CELLS_PER_FEATURE}"):
            self._note_row(item, "mesh", text, "#c80").setToolTip(0, advice)

    def _mesh_rows(self, item):
        mesh = self.mesh
        if mesh is None:
            return
        if not mesh.ok:
            self._note_row(item, "mesh", f"✗ {mesh.error}", "#d33")
            return

        grid = mesh.grid
        steps = np.concatenate([grid.steps(a) for a in range(3)])
        rows = (
            ("cells", f"{grid.cell_count:,}".replace(",", " "), ""),
            ("nx × ny × nz", " × ".join(str(n) for n in grid.shape), ""),
            ("step min / max", f"{steps.min() / MM:.3g} / {steps.max() / MM:.3g}", "mm"),
            ("dt (Courant)", f"{grid.time_step() * 1e12:.3g}", "ps"),
        )
        for label, value, unit in rows:
            row = QTreeWidgetItem(item, [label, value, unit])
            row.setData(0, Qt.UserRole, ("info", "mesh", None))
            row.setForeground(1, QColor("#2a2"))

        count = sum(len(merged) for merged in mesh.merged)
        if count:
            # Collapsed: with curved bodies (vias) there can be dozens.
            group = QTreeWidgetItem(item, [f"merged lines: {count}", "", ""])
            group.setData(0, Qt.UserRole, ("info", "mesh", None))
            group.setForeground(0, QColor("#888"))
            group.setToolTip(0, "Fixpoints closer than the min step, joined into one grid line")
            for axis, merged in enumerate(mesh.merged):
                for kept, dropped in merged:
                    text = ", ".join(f"{d / MM:.4g}" for d in dropped)
                    self._note_row(group, "mesh", f"{AXES[axis]}: {text} → {kept / MM:.4g} mm", "#888")
            group.setExpanded(False)

        if mesh.slanted_faces:
            finest = min(h for regions in mesh.slanted for _, _, h in regions)
            row = self._note_row(item, "mesh", f"slanted metal: {mesh.slanted_faces} faces, narrowest "
                                 f"{mesh.slanted_feature / MM:.3g} mm → step ≤ {finest / MM:.3g} mm", "#888")
            row.setToolTip(0, "Slanted metal faces put no grid lines; the step around them follows "
                              "the gap or width there (Mesh > refine slanted metal)")

        for warning in mesh.warnings:
            self._note_row(item, "mesh", f"⚠ {warning}", "#c80")
        self._z0_accuracy_rows(item)

        m = self.materials
        if m is not None:
            pec_edges = sum(int(a.sum()) for a in m.pec)
            row = QTreeWidgetItem(item, ["PEC edges", f"{pec_edges:,}".replace(",", " "), ""])
            row.setData(0, Qt.UserRole, ("info", "mesh", None))
            row.setForeground(1, QColor("#2a2"))
            for port in m.ports:
                row = QTreeWidgetItem(item, [
                    f"{port.name} edges",
                    f"{len(port.edges)} along {AXES[port.axis]}, {port.length / MM:.3f}", "mm",
                ])
                row.setData(0, Qt.UserRole, ("info", "mesh", None))
                row.setForeground(1, QColor("#2a2"))
            for element in m.elements:
                axes = np.unique(element.axes)
                along = AXES[axes[0]] if len(axes) == 1 else \
                    " + ".join(f"{np.count_nonzero(element.axes == a)} {AXES[a]}" for a in axes) + " (staircase)"
                row = QTreeWidgetItem(item, [
                    f"{element.name} edges",
                    (f"{np.count_nonzero(element.column == 0)} along {along}" if len(axes) == 1 else along)
                    + (f" x {element.columns} columns" if element.columns > 1 else "")
                    + f", {element.length / MM:.3f}", "mm",
                ])
                row.setData(0, Qt.UserRole, ("info", "mesh", None))
                row.setForeground(1, QColor("#2a2"))
            for port in m.waveports:
                row = QTreeWidgetItem(item, [
                    f"{port.name} line",
                    f"Z0 {port.mode.z0:.2f} Ω, εeff {port.mode.eps_eff:.3f}", "",
                ])
                row.setData(0, Qt.UserRole, ("info", "mesh", None))
                row.setForeground(1, QColor("#2a2"))
            for error in m.errors:
                self._note_row(item, "mesh", f"✗ {error}", "#d33")
            for short in m.shorts:
                self._note_row(item, "mesh", f"⚠ short: {short}", "#d33")
            for warning in m.warnings:
                self._note_row(item, "mesh", f"⚠ {warning}", "#c80")

    def _fill_type(self, body_item):
        """(Re)builds the type subtree of one body row."""
        self.tree.blockSignals(True)
        body_item.takeChildren()

        name = body_item.data(0, Qt.UserRole)[1]
        assignment = self.project.get(name) if self.project else None

        if assignment is not None:
            object_type = assignment.object_type

            type_item = QTreeWidgetItem(body_item, ["Type", object_type.label])
            type_item.setData(0, Qt.UserRole, ("type", name, None))
            font = type_item.font(1)
            font.setBold(True)
            type_item.setFont(1, font)

            if assignment.material is not None:
                row = QTreeWidgetItem(type_item, ["Material", assignment.material, ""])
                row.setData(0, Qt.UserRole, ("material", name, None))
                row.setToolTip(1, "Double click to choose a preset (Materials tab)")
                font = row.font(1)
                font.setBold(True)
                row.setFont(1, font)

            preset = assignment.material not in (None, CUSTOM)
            for param in object_type.params:
                row = QTreeWidgetItem(type_item, [
                    param.label, _fmt_value(assignment.values[param.name]), param.unit,
                ])
                row.setData(0, Qt.UserRole, ("param", name, param.name))
                row.setFlags(row.flags() | Qt.ItemIsEditable)
                row.setToolTip(1, "Double click to edit")
                if preset and param.name in MATERIAL_PARAMS:
                    row.setForeground(1, QColor("gray"))
                    row.setToolTip(1, f"From {assignment.material}; editing makes it {CUSTOM}")

            if object_type.note:
                self._note_row(type_item, name, object_type.note, "gray")

            if assignment.type == "port":
                self._port_rows(type_item, name)
            elif assignment.type == "lumped":
                self._element_rows(type_item, name)
            elif assignment.type == "waveport":
                self._waveport_rows(type_item, name)

            body_item.setExpanded(True)
            type_item.setExpanded(True)

        self.tree.blockSignals(False)

    def _resize_columns(self):
        for column in range(3):
            self.tree.resizeColumnToContents(column)

    def _note_row(self, parent, owner, text, color):
        note = QTreeWidgetItem(parent, [text])
        note.setData(0, Qt.UserRole, ("note", owner, None))
        note.setForeground(0, QColor(color))
        note.setFirstColumnSpanned(True)
        return note

    def _port_rows(self, type_item, name):
        port = self.ports.get(name)
        if port is None:
            return

        if not port.ok:
            self._note_row(type_item, name, f"✗ {port.error}", "#d33")
            return

        for label, value in (
            ("− end", port.minus_body),
            ("+ end", port.plus_body),
            ("gap", f"{port.gap_length / MM:.3f}"),
        ):
            row = QTreeWidgetItem(type_item, [label, value, "mm" if label == "gap" else ""])
            row.setData(0, Qt.UserRole, ("info", name, None))
            row.setForeground(1, QColor("#2a2"))

    def _element_rows(self, type_item, name):
        element = self.elements.get(name)
        if element is None:
            return
        if not element.ok:
            self._note_row(type_item, name, f"✗ {element.error}", "#d33")
            return
        for label, value, unit in (
            ("ends", f"{element.minus_body} — {element.plus_body}", ""),
            ("gap", f"{element.gap_length / MM:.3f}", "mm"),
        ):
            row = QTreeWidgetItem(type_item, [label, value, unit])
            row.setData(0, Qt.UserRole, ("info", name, None))
            row.setForeground(1, QColor("#2a2"))

    def _waveport_rows(self, type_item, name):
        port = self.waveports.get(name)
        if port is None:
            return
        if not port.ok:
            self._note_row(type_item, name, f"✗ {port.error}", "#d33")
            return
        rows = [
            ("plane", f"{AXES[port.axis]} = {port.plane / MM:.4g}", "mm"),
            ("line runs to", f"{'+' if port.direction > 0 else '−'}{AXES[port.axis]}", ""),
        ]
        if port.region is not None:
            (u0, u1), (v0, v1) = port.region
            u, v = [a for a in range(3) if a != port.axis]
            rows.append(("rectangle", f"{AXES[u]} {(u1 - u0) / MM:.4g} × {AXES[v]} {(v1 - v0) / MM:.4g}", "mm"))
        else:
            rows.append(("⌀", f"{2 * port.radius / MM:.4g}", "mm"))
        discrete = [w for w in (self.materials.waveports if self.materials else []) if w.name == name]
        if discrete:
            mode = discrete[0].mode
            rows += [("Z0 (line)", f"{mode.z0:.2f}", "Ω"), ("εeff", f"{mode.eps_eff:.3f}", "")]
        for label, value, unit in rows:
            row = QTreeWidgetItem(type_item, [label, value, unit])
            row.setData(0, Qt.UserRole, ("info", name, None))
            row.setForeground(1, QColor("#2a2"))

    # =====================================================
    # Remote machine (the console runner's)
    # =====================================================

    # The buttons under the Project tree and the Remote tab drive one
    # operation at a time (self.remote): check, push, fetch (status + take
    # the finished runs), launch (push + start run.py), stop. Nothing
    # connects by itself when a model is opened: what was started from here
    # is in remote/record.py (pending rows in Results, Stop), Fetch asks the
    # server. Only after Run on server (or a Fetch that finds the run going)
    # a second runner (self.remote_watch) follows it every REMOTE_POLL ms
    # while the window is open, quietly: the status bar shows its progress
    # and a finished run is fetched by itself.

    def _remote_start(self, operation):
        if self.remote.running:
            return
        step = self.project.step_path if self.project else None
        if operation != "check" and step is None:
            self.statusBar().showMessage("no model to " + operation, 5000)
            return
        if operation in ("push", "launch"):
            # only what would run here goes there: built, checked, shorts confirmed
            title = "Push" if operation == "push" else "Run on the server"
            problem = self._simulation_problem()
            if problem:
                self.statusBar().showMessage(f"{title}: cannot run yet: {problem}", 10000)
                self._update_remote_buttons()
                return
            if self.materials.shorts and not self._confirm_shorts(
                    title, "Send it anyway? (run.py stops on shorts unless --allow-shorts)"):
                return
            self._save()                        # the .toml as the window shows it
            self._launch_fingerprint = fingerprint(self.project)    # the model as sent
        if operation == "stop":
            answer = QMessageBox.question(self, "Stop the server's run",
                                          "Stop the run on the server? Nothing of it is saved.")
            if answer != QMessageBox.Yes:
                return
        self.remote_panel.save()
        config = self.remote_panel.config()
        self._remote_operation = (operation, step, config.target)
        self.remote_panel.show_status(f"{operation}: connecting to {config.target}...")
        self.statusBar().showMessage(f"{operation}: connecting to {config.target}...")
        self.remote.start(operation, config, step)
        self._update_remote_buttons()

    def _remote_active(self):
        """The open model's run on the server is going (as last seen)."""
        state = self._remote_state
        return state is not None and state.get("state") in ("meshing", "running")

    def _update_remote_buttons(self):
        """Push / Run only a model that would run here; Stop while its server run goes."""
        busy = self.remote.running
        problem = None if busy else self._simulation_problem()
        active = self._remote_active()
        self.push_button.setEnabled(not busy and problem is None)
        self.push_button.setToolTip(
            "Copy this model's STEP and .toml to the remote's models/ (replacing them there)"
            if problem is None else f"Push is off until the model can run: {problem}")
        self.run_remote_button.setEnabled(not busy and problem is None and not active)
        self.run_remote_button.setToolTip(
            "Push this model and run it on the server (one run at a time there)"
            if problem is None and not active else
            "its run on the server is going" if active else f"off until the model can run: {problem}")
        self.stop_remote_button.setEnabled(not busy and active)
        self.fetch_button.setEnabled(not busy and self.project is not None)
        self.remote_panel.check_button.setEnabled(not busy)

    def _remote_finished(self, operation, value):
        _, step, server = self._remote_operation
        if operation == "check":
            text = f"connected: {value}"
        elif operation == "push":
            text = f"pushed {os.path.basename(step)} and its .toml to {value}"
        elif operation == "launch":
            from remote import record
            text = f"running on the server: {os.path.basename(step)} (pid {value.get('pid')})"
            try:
                record.add_launch(step, server, value, self._launch_fingerprint)
            except OSError as error:
                text += f" (not recorded here: {error})"
            self._remote_status_seen(step, value, follow=True)
        elif operation == "stop":
            text = f"stopping the server's run (pid {value}): it ends at its next energy check"
            self._remote_timer.start()
            QTimer.singleShot(3000, self._remote_poll)
        else:                                   # fetch
            parts = [self._status_text(value.status), self._pulled(step, value.pulled)]
            self._remote_status_seen(step, value.status, follow=True, fetched=True)
            text = "; ".join(p for p in parts if p)
        self._update_remote_buttons()
        self.remote_panel.show_status(text)
        self.statusBar().showMessage(text.splitlines()[0], 10000)

    @staticmethod
    def _status_text(status):
        if status is None:
            return "this model never ran on the server"
        state = status.get("state")
        if status.get("busy") and state not in ("meshing", "running"):
            busy = status["busy"]
            prefix = f"the server is busy with {busy.get('model')} (pid {busy.get('pid')}); "
        else:
            prefix = ""
        return prefix + f"server: {state}" + (f" ({status.get('message')})" if status.get("message") else "")

    def _pulled(self, step, pulled):
        """Takes a pull in (remote/sync.py Pulled); returns the status text."""
        parts = [f"took {len(pulled.runs)} run(s)" if pulled.runs else "no new runs on the server"]
        parts += [f"left on the server (no run file): {e}" for e in pulled.errors]
        if self.project is None or self.project.step_path != step:
            return "; ".join(parts)                 # another model opened meanwhile
        ticked = set(self.shown)
        if pulled.model:
            names = ", ".join(os.path.basename(p) for p in pulled.model)
            answer = QMessageBox.question(
                self, "Fetch",
                f"The server's {names} differ from yours.\n\nTake the server's? "
                "Yours are replaced (changes made here since the push are lost).")
            if answer == QMessageBox.Yes:
                from remote.sync import write_local
                try:
                    write_local(pulled.model)
                except OSError as error:
                    QMessageBox.critical(self, "Fetch", f"not written: {error}")
                else:
                    parts.append(f"took the server's {names}")
                    self.load(step)                 # the model as on the server, its runs too
            else:
                parts.append(f"kept yours ({names} differ on the server)")
        # like a finished run: the ticks stay, the taken runs join them, Results shows them
        self._load_runs(step)
        paths = {r.path for r in self.runs}
        self.shown = (ticked & paths) | {p for p in paths if os.path.basename(p) in pulled.runs}
        self._show_runs()
        if pulled.runs:
            self.tabs.setCurrentWidget(self.results)
        return "; ".join(parts)

    def _remote_failed(self, message, unknown):
        operation = self._remote_operation[0]
        self._update_remote_buttons()
        if unknown is not None:
            from remote.sync import fingerprint as key_fingerprint, trust_host
            answer = QMessageBox.question(
                self, "Remote host",
                f"{unknown.host} is not known yet. Its key:\n\n{unknown.key.get_name()} "
                f"{key_fingerprint(unknown.key)}\n\nTrust it? (Compare with the server's: "
                "ssh-keygen -lf /etc/ssh/ssh_host_*_key.pub)")
            if answer == QMessageBox.Yes:
                try:
                    trust_host(unknown.host, unknown.key)
                except OSError as error:
                    message = f"host key not saved: {error}"
                else:
                    self._remote_start(operation)
                    return
        self.remote_panel.show_status(message, error=True)
        self.statusBar().showMessage(f"{operation} failed: {message.splitlines()[0] if message else ''}", 10000)
        if operation == "launch":
            QMessageBox.warning(self, "Run on the server", message)

    # The server run as recorded here, and followed after Run / Fetch.

    def _remote_from_record(self):
        """After a model is opened: its last launch as recorded here (no connection)."""
        from remote import record
        self._remote_timer.stop()
        self._remote_bar.setVisible(False)
        self._remote_state = None
        if self.project is not None:
            server = self.remote_panel.config().target
            launches = [launch for launch in record.pending(self.project.step_path) if launch.get("server") == server]
            if launches:
                self._remote_state = dict(launches[-1])
        self._update_remote_buttons()

    def _remote_poll(self):
        if self.remote_watch.running or self.project is None:
            return
        config = self.remote_panel.config()
        if config.complete:
            self._remote_watch_target = (self.project.step_path, config.target)
            self.remote_watch.start("status", config, self.project.step_path)

    def _remote_watched(self, operation, value):
        from remote import record
        step, server = self._remote_watch_target
        try:
            record.note_status(step, server, value, self._progress_text(value) if value else "")
        except OSError:
            pass
        self._remote_status_seen(step, value, follow=self._remote_timer.isActive())

    def _remote_watch_failed(self, message, unknown):
        if self._remote_active():
            self._remote_bar.setFormat("server: no answer, retrying")    # the timer goes on
        self.remote_panel.show_status(f"status: {message}", error=True)

    def _remote_status_seen(self, step, status, follow=False, fetched=False):
        """
        A status read from the server: shown, recorded in the pending rows;
        followed while it goes (`follow`); a finished run not taken yet is
        fetched (unless this was a fetch).
        """
        if self.project is None or self.project.step_path != step:
            return                                  # another model opened meanwhile
        from remote import record
        was_active = self._remote_active()
        self._remote_state = status
        state = status.get("state") if status else None
        if state in ("meshing", "running"):
            if follow:
                self._remote_timer.start()
                self._show_remote_progress(status)
        else:
            self._remote_timer.stop()
            self._remote_bar.setVisible(False)
            server = self.remote_panel.config().target
            if state == "done" and not fetched:
                result = status.get("result")
                if result and not record.was_pulled(step, server, result):
                    self.remote_panel.show_status(f"the server's run is done: {result}, fetching it")
                    if not self.remote.running:
                        self._remote_start("fetch")
                    else:
                        QTimer.singleShot(5000, self._remote_poll)  # fetch when the button is free
            elif state in ("failed", "died", "stopped"):
                tail = "\n".join((status.get("log") or "").splitlines()[-8:])
                text = f"the server's run {state}: {status.get('message', '')}" + (f"\n\n{tail}" if tail else "")
                self.remote_panel.show_status(text, error=state != "stopped")
                if was_active and not fetched:      # it ended while we followed it: say so
                    self.statusBar().showMessage(f"the server's run {state}: {status.get('message', '')}", 15000)
                    if state != "stopped":
                        QMessageBox.warning(self, "The server's run", text)
        self._fill_results()                        # the pending row's state
        self._update_remote_buttons()

    @staticmethod
    def _progress_text(status):
        ports = status.get("ports") or []
        port = status.get("port")
        if status.get("state") == "meshing" or port is None:
            return status.get("message") or "meshing"
        energy, end_db = status.get("energy_db"), status.get("end_db") or -40.0
        where = (f"pulse {status.get('pulse_pct', 0):.0f} %" if energy is None
                 else f"energy {energy:.1f} dB -> {end_db:.0f}")
        return (f"{port} ({status.get('port_index') or 0}/{len(ports)}), {where}, "
                f"{format_duration(status.get('elapsed', 0))}")

    def _show_remote_progress(self, status):
        ports = status.get("ports") or []
        index = status.get("port_index") or 0
        value = 0
        if status.get("state") == "running" and status.get("port") is not None:
            energy, end_db = status.get("energy_db"), status.get("end_db") or -40.0
            fraction = (0.5 * status.get("pulse_pct", 0) / 100 if energy is None
                        else 0.5 + 0.5 * min(max(-energy, 0.0), -end_db) / -end_db)
            value = int(round(1000 * (index - 1 + fraction) / max(len(ports), 1)))
        text = "server: " + self._progress_text(status)
        self._remote_bar.setRange(0, 1000)
        self._remote_bar.setValue(value)
        self._remote_bar.setFormat(text)
        self._remote_bar.setToolTip(text)
        self._remote_bar.setVisible(True)
        self.remote_panel.show_status(text)

    def _pending_rows(self):
        """(stamp, launch) of this model's launches not taken yet (record.py)."""
        if self.project is None:
            return []
        from remote import record
        return [(launch.get("stamp", ""), launch) for launch in record.pending(self.project.step_path)]

    def _pending_menu(self, item, stamp):
        launch = next((l for _, l in self._pending_rows() if l.get("stamp") == stamp), None)
        if launch is None:
            return
        menu = QMenu(self)
        active = launch.get("state") in ("meshing", "running")
        fetch = menu.addAction("Fetch (ask the server)", lambda: self._remote_start("fetch"))
        fetch.setEnabled(not self.remote.running)
        forget = menu.addAction("Forget it here", lambda: self._forget_launch(stamp))
        if active:
            forget.setToolTip("it is going on the server: Stop it first, or Fetch")
            forget.setEnabled(False)
        menu.exec(self.tree.viewport().mapToGlobal(self.tree.visualItemRect(item).center()))

    def _forget_launch(self, stamp):
        from remote import record
        try:
            record.forget(self.project.step_path, stamp)
        except OSError as error:
            self.statusBar().showMessage(f"not forgotten: {error}", 10000)
        self._remote_from_record()
        self._fill_results()

    # =====================================================
    # Saved results
    # =====================================================

    def _load_runs(self, step_path):
        """The runs saved next to the STEP file; the newest one is shown."""
        try:
            self.runs, errors = list_results(step_path)
        except OSError as error:
            self.runs, errors = [], [str(error)]
        if errors:
            self.statusBar().showMessage("results not read: " + "; ".join(errors), 10000)
        self.shown = {self.runs[-1].path} if self.runs else set()
        self._fill_port_lines(self.runs)
        self.results.default_dir = os.path.dirname(step_path)
        self._show_runs()

    def _fill_port_lines(self, runs):
        """
        Runs saved before eps_eff was stored: take it from the current port
        of the same name (a port's own line rarely changes with the rest of
        the model; an outdated run is marked as such anyway).
        """
        if self.materials is None or not runs:
            return
        lines = {w.name: w.mode.eps_eff for w in self.materials.waveports}
        for run in runs:
            if all(np.isnan(e) for e in run.eps_eff):
                run.eps_eff = [lines.get(name, float("nan")) for name in run.ports]

    def _show_runs(self):
        """Results tab and the Results list after a change of the runs or of their ticks."""
        shown = [r for r in self.runs if (r.path or id(r)) in self.shown]
        self.results.set_results(shown)
        if not self.runs:
            self.results.set_message("no results yet: ▶ Simulate")
        elif not shown:
            self.results.set_message("no run ticked in Project > Simulation > Results")
        self._update_result_status()
        self._fill_results()
        self._update_tdr()

    def _is_current(self, run):
        return self.project is not None and run.fingerprint == self._current_fingerprint()

    def _current_fingerprint(self):
        return fingerprint(self.project) if self.project is not None else None

    def _update_result_status(self):
        """Current / outdated line under the plot, for the newest shown run."""
        run = self.results.primary
        if run is None:
            self.results.set_status("")
            return
        if self._is_current(run):
            self.results.set_status(f"{run.name}: matches the current model and settings", "#2a2")
        else:
            self.results.set_status(
                f"⚠ {run.name}: OUTDATED: the STEP file, the body types or the settings changed "
                f"since this run (▶ Simulate to update)", "#c80")

    def _results_item(self):
        return self._tree_item("results", None)

    def _fill_results(self):
        """Project > Simulation > Results: one row per run (tick = shown), its parameters inside."""
        item = self._results_item()
        if item is None:
            return
        self.tree.blockSignals(True)
        expanded = {item.child(i).data(0, Qt.UserRole)[1] for i in range(item.childCount())
                    if item.child(i).isExpanded()}
        item.takeChildren()
        pending = self._pending_rows()
        item.setText(1, f"{len(self.runs)} run" + ("s" if len(self.runs) != 1 else "")
                     + (f", {len(pending)} on the server" if pending else ""))
        current = self._current_fingerprint()
        primary = self.results.primary
        rows = [(os.path.basename(run.path or "")[:15], 1, run) for run in self.runs]
        rows += [(stamp, 0, launch) for stamp, launch in pending]
        for _, kind, run in sorted(rows, key=lambda row: (row[0], row[1]), reverse=True):  # newest on top
            if kind == 0:
                self._pending_row(item, run, current)
                continue
            key = run.path or id(run)
            ok = current is not None and run.fingerprint == current
            row = QTreeWidgetItem(item, [run.name, run.saved, "current" if ok else "outdated"])
            row.setData(0, Qt.UserRole, ("run", key, None))
            row.setFlags(row.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsEditable)
            row.setCheckState(0, Qt.Checked if key in self.shown else Qt.Unchecked)
            row.setForeground(2, QColor("#2a2" if ok else "#c80"))
            row.setToolTip(0, "Tick to show it in Results (the newest ticked run in full color). "
                              "Right click: rename, show only this, delete.")
            if run is primary:
                font = row.font(0)
                font.setBold(True)
                row.setFont(0, font)
            for label, value in self._run_rows(run):
                info = QTreeWidgetItem(row, [label, value, ""])
                info.setData(0, Qt.UserRole, ("run_info", key, None))
                info.setForeground(1, QColor("#666"))
            row.setExpanded(key in expanded)
        item.setExpanded(True)
        self.tree.blockSignals(False)

    def _pending_row(self, parent, launch, current):
        """A run started on a server from here, not taken yet (remote/record.py)."""
        state = launch.get("state") or "?"
        ok = current is not None and launch.get("fingerprint") == current
        row = QTreeWidgetItem(parent, ["⏳ on the server", launch.get("started", ""),
                                       state + ("" if ok else ", outdated")])
        row.setData(0, Qt.UserRole, ("pending", launch.get("stamp"), None))
        row.setForeground(0, QColor("#8a5cc8"))
        row.setForeground(2, QColor("#8a5cc8" if state in ("meshing", "running", "done") else "#c22"))
        row.setToolTip(0, "Started on the server from here. Fetch asks the server and takes it when it is "
                          "done; right click: fetch, forget.")
        for label, value in (("server", f"{launch.get('server')}, pid {launch.get('pid')}"),
                             ("state", state + (f": {launch['message']}" if launch.get("message") else "")),
                             ("progress", launch.get("progress") or ""), ("last seen", launch.get("seen") or ""),
                             ("result", launch.get("result") or "")):
            if value:
                info = QTreeWidgetItem(row, [label, value, ""])
                info.setData(0, Qt.UserRole, ("run_info", launch.get("stamp"), None))
                info.setForeground(1, QColor("#666"))

    @staticmethod
    def _run_rows(run):
        p = run.params
        took = [("time", format_duration(run.elapsed))] if format_duration(run.elapsed) else []
        if not p:
            return [("parameters", "not recorded (an older run)")] + took + [("run", line) for line in run.info]
        sim, mesh = p.get("simulation", {}), p.get("mesh", {})
        rows = [
            ("band", f"{sim.get('f_min', '?'):g}–{sim.get('f_max', '?'):g} GHz, padding {sim.get('padding', 0):g} mm"
                     + (", pulse tails trimmed" if sim.get("trim_pulse") else "")
                     + f", accuracy {sim.get('accuracy', '-40')} dB"),
            ("mesh", f"cells/λ {mesh.get('cells_per_wavelength', 0):g}, per feature {mesh.get('cells_per_feature', 0):g}, "
                     f"ratio {mesh.get('max_ratio', 0):g}, min step {mesh.get('min_step', 0):g} mm"),
            ("grid", p.get("grid", "")),
        ]
        rows += [(f"  {name}", text) for name, text in p.get("dielectrics", {}).items()]
        rows += [("elements", "; ".join(p["elements"]) or "none")] if "elements" in p else []
        rows += [("vias", str(p.get("vias", 0))), ("ports", ", ".join(p.get("ports", []))),
                 ("excited", p.get("excite", "all ports"))]
        rows += took + [("run", line) for line in run.info]
        return rows

    def _run_by_key(self, key):
        return next((r for r in self.runs if (r.path or id(r)) == key), None)

    def _run_menu(self, item, key):
        run = self._run_by_key(key)
        if run is None:
            return
        menu = QMenu(self)
        menu.addAction("Rename", lambda: self.tree.editItem(item, 0))
        menu.addAction("Show only this", lambda: self._set_shown({key}))
        menu.addAction("Show all", lambda: self._set_shown({r.path or id(r) for r in self.runs}))
        menu.addSeparator()
        menu.addAction("Delete…", lambda: self._delete_run(run))
        menu.exec(self.tree.viewport().mapToGlobal(self.tree.visualItemRect(item).bottomLeft()))

    def _set_shown(self, keys):
        self.shown = set(keys)
        QTimer.singleShot(0, self._show_runs)       # never rebuild the tree inside its own signal

    def _delete_run(self, run):
        answer = QMessageBox.question(
            self, "Delete result",
            f"Delete the result \"{run.name}\" ({run.saved})?\n\n{run.path}\n\nThis cannot be undone.")
        if answer != QMessageBox.Yes:
            return
        try:
            delete_result(run)
        except OSError as error:
            QMessageBox.critical(self, "Delete result", str(error))
            return
        self.runs.remove(run)
        self.shown.discard(run.path or id(run))
        self.shown = {k for k in self.shown if self._run_by_key(k) is not None}
        QTimer.singleShot(0, self._show_runs)

    def _run_changed(self, item, key):
        """A run row changed: its tick (shown or not) or its name (renamed in place)."""
        run = self._run_by_key(key)
        if run is None:
            return
        ticked = item.checkState(0) == Qt.Checked
        if ticked != (key in self.shown):
            self._set_shown(self.shown | {key} if ticked else self.shown - {key})
            return
        name = item.text(0).strip()
        if name and name != run.name:
            try:
                rename_result(run, name)
            except (OSError, ValueError) as error:
                self.statusBar().showMessage(f"rename: {error}", 6000)
        QTimer.singleShot(0, self._show_runs)

    def _tree_item(self, role, owner):
        """
        A row by its (role, owner): a top-level section or group, or a row in
        a group (Model, Bodies, Simulation's Monitors and Results).
        """
        rows = [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())]
        while rows:
            item = rows.pop(0)
            data = item.data(0, Qt.UserRole)
            if data[:2] == (role, owner):
                return item
            if data[0] in TREE_GROUPS:
                rows += [item.child(i) for i in range(item.childCount())]
        return None

    def _body_row(self, item):
        """The body row a tree row belongs to (itself or an ancestor), or None."""
        while item is not None and item.data(0, Qt.UserRole)[0] != "body":
            item = item.parent()
        return item

    def _after_edit(self):
        """Save, recompute ports and mesh, refresh the dependent tree rows."""
        self._save()
        self._refresh_model()
        self._refresh_rows()

    def _refresh_rows(self):
        """The tree rows that depend on the ports and the mesh."""
        for body in self.geometry.bodies:
            self._fill_type(self._tree_item("body", body.name))
        self._fill_section(self._tree_item("section", "mesh"))
        self._fill_holes()
        self._fill_monitors()                   # the ports may have changed
        self._resize_columns()
        self._show_summary()
        self._fill_materials()
        self._update_result_status()
        self._fill_results()                    # current / outdated marks

    # =====================================================
    # Editing
    # =====================================================

    def _up_changed(self, text):
        axis = text.lower()
        self.view.set_up(axis)

        if self.project is not None and self.project.up != axis:
            self.project.set_up(axis)
            self._save()

        self.mesh_axis.setCurrentIndex(AXES.index(axis))   # horizontal mesh plane

    def _context_menu(self, pos):
        item = self.tree.itemAt(pos)
        if item is None:
            return

        if self.project is None:
            self.statusBar().showMessage("Project file has errors: fix it, then Reload", 5000)
            return

        role, name, _ = item.data(0, Qt.UserRole)
        if role in ("hole_group", "hole"):
            self._hole_menu(item, role, name)
            return
        if role == "run":
            self._run_menu(item, name)
            return
        if role == "pending":
            self._pending_menu(item, name)
            return
        if self._body_row(item) is None:
            return

        body = self.geometry[name]
        current = self.project.get(name)

        menu = QMenu(self)
        type_menu = menu.addMenu("Change type" if current else "Set type")

        for object_type in OBJECT_TYPES.values():
            action = type_menu.addAction(object_type.label)
            action.setCheckable(True)
            action.setChecked(current is not None and current.type == object_type.name)

            if body.kind not in object_type.allowed_kinds:
                action.setEnabled(False)
                action.setText(f"{object_type.label}  (not for a {body.kind})")

            action.triggered.connect(lambda _=False, t=object_type.name: self._set_type(name, t))

        remove = menu.addAction("Remove type")
        remove.setEnabled(current is not None)
        remove.triggered.connect(lambda: self._set_type(name, None))

        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def _set_type(self, name, type_name):
        try:
            if type_name is None:
                self.project.remove_type(name)
            else:
                self.project.set_type(self.geometry[name], type_name)
        except ProjectError as error:
            self.statusBar().showMessage(str(error), 5000)
            return

        self._after_edit()

    def _item_double_clicked(self, item, _column):
        role, owner, name = item.data(0, Qt.UserRole)
        if role == "material" and self.project is not None:
            self._material_menu(item, owner)
            return
        if role == "run":
            self.tree.editItem(item, 0)             # rename in place
            return
        if role not in ("param", "setting", "z0path_param") or self.project is None:
            return

        if role == "z0path_param":
            param = next(p for p in Z0PATH_PARAMS if p.name == name)
        else:
            param = (self.project.get(owner).object_type.param(name) if role == "param"
                     else SECTIONS[owner].param(name))
        if param.boolean:
            return                                  # the check box is the editor
        if role == "setting" and name == "excite":
            self._excite_menu(item)
            return
        if not param.choices:
            self.tree.editItem(item, 1)
            return

        # A choice: pick it from a menu at the row (the tree has no combo editor).
        menu = QMenu(self)
        for choice in param.choices:
            action = menu.addAction(choice)
            action.setCheckable(True)
            action.setChecked(item.text(1) == choice)
            action.triggered.connect(lambda _=False, c=choice: item.setText(1, c))
        rect = self.tree.visualItemRect(item)
        menu.exec(self.tree.viewport().mapToGlobal(rect.bottomLeft()))

    def _excite_menu(self, item):
        """Simulation > excite: all ports, or one of the model's ports."""
        current = self.project.settings["simulation"]["excite"]
        menu = QMenu(self)
        for value, text in [(EXCITE_ALL, "all ports (full S-matrix)")] + \
                [(n, f"{n} only") for n in self._port_list()]:
            action = menu.addAction(text)
            action.setCheckable(True)
            action.setChecked(value == current)
            action.triggered.connect(lambda _=False, v=value: self._set_excite(v))
        if current != EXCITE_ALL and current not in self._port_list():
            menu.addSeparator()
            menu.addAction(f"{current} (not a valid port now)").setEnabled(False)
        rect = self.tree.visualItemRect(item)
        menu.exec(self.tree.viewport().mapToGlobal(rect.bottomLeft()))

    def _set_excite(self, value):
        self.project.set_setting("simulation", "excite", value)
        self._fill_section(self._tree_item("section", "simulation"))
        self._after_edit()

    def _material_menu(self, item, body_name):
        """Pick a dielectric's material: a preset of the library, or its own values."""
        current = self.project.get(body_name).material
        menu = QMenu(self)
        for material in list(self.project.materials) + [CUSTOM]:
            if material == CUSTOM:
                menu.addSeparator()
            action = menu.addAction(material)
            action.setCheckable(True)
            action.setChecked(material == current)
            action.triggered.connect(lambda _=False, m=material: self._set_material(body_name, m))
        rect = self.tree.visualItemRect(item)
        menu.exec(self.tree.viewport().mapToGlobal(rect.bottomLeft()))

    def _set_material(self, body_name, material):
        try:
            self.project.set_material(body_name, material)
        except ProjectError as error:
            self.statusBar().showMessage(str(error), 5000)
            return
        self._after_edit()

    def _item_changed(self, item, column):
        role, owner, param = item.data(0, Qt.UserRole)

        if role == "body" and column == 0:
            self.view.set_visible(owner, item.checkState(0) == Qt.Checked)
            self._view_changed()
            return

        if role == "run" and column == 0:
            self._run_changed(item, owner)
            return

        if role == "z0path_param" and column == 1:
            old = dict(self.project.z0path)
            try:
                self.project.set_z0path_value(param, item.text(1).strip().replace(",", "."))
            except ValueError as error:
                self.statusBar().showMessage(str(error), 5000)
            if self.project.z0path != old:
                # Rebuilds the rows: not under the editor's own commit.
                QTimer.singleShot(0, self._z0path_changed)
            else:
                self.tree.blockSignals(True)
                item.setText(1, _fmt_value(self.project.z0path[param]))
                self.tree.blockSignals(False)
            return

        if role not in ("param", "setting") or column != 1:
            return

        if role == "setting" and SECTIONS[owner].param(param).boolean:
            checked = item.checkState(1) == Qt.Checked
            if checked != self.project.settings[owner][param]:
                self.project.set_setting(owner, param, checked)
                self.tree.blockSignals(True)
                item.setText(1, _fmt_value(checked))
                self.tree.blockSignals(False)
                self._after_edit()
            return

        text = item.text(1).strip().replace(",", ".")
        try:
            if role == "param":
                self.project.set_value(owner, param, text)
            else:
                self.project.set_setting(owner, param, text)
        except ValueError as error:
            self.statusBar().showMessage(str(error), 5000)
            changed = False
        else:
            changed = True

        # Show the stored value: reverts an invalid edit, normalizes a valid one.
        stored = (self.project.get(owner).values if role == "param" else self.project.settings[owner])[param]
        self.tree.blockSignals(True)
        item.setText(1, _fmt_value(stored))
        self.tree.blockSignals(False)

        if changed and role == "setting" and owner in NOT_IN_FINGERPRINT:
            self._save()                        # no new mesh: it changes no material, no port
            QTimer.singleShot(0, self._fill_monitors)
        elif changed:
            self._after_edit()

    def _save(self):
        self.project.view = self._view_state()      # the display goes along
        self._view_timer.stop()
        try:
            self.project.save()
        except OSError as error:
            QMessageBox.critical(self, "Project file", f"cannot save {self.project.path}\n\n{error}")
            return
        self._project_mtime = self._file_mtime(self.project.path)

        self.statusBar().showMessage(f"saved {os.path.basename(self.project.path)}", 2000)

    # =====================================================
    # Info panel
    # =====================================================

    def _show_summary(self):
        lines = [f"{len(self.geometry.bodies)} bodies from {os.path.basename(self.geometry.source)}"]

        if self.project is None:
            lines.append("<span style='color:#c33'>project file has errors: read-only</span>")
        else:
            untyped = [b.name for b in self.geometry.bodies if self.project.get(b.name) is None]
            if untyped:
                lines.append(f"without type: {', '.join(untyped)}")

            orphans = self.project.orphans(self.geometry)
            if orphans:
                lines.append(
                    "<span style='color:#c80'>in the project file but not in the STEP "
                    f"(kept): {', '.join(orphans)}</span>"
                )

        self.info.setText("<br>".join(lines))

    def _current_changed(self, item, _previous):
        if item is None:
            self.view.select(None)
            self.view.highlight_holes([])
            return

        holes = self._selected_holes(item)
        self.view.highlight_holes(holes or [])
        if holes is not None:
            self.view.select(None)
            self._show_holes_info(holes)
            return

        body_row = self._body_row(item)
        if body_row is None:
            self.view.select(None)
            return
        name = body_row.data(0, Qt.UserRole)[1]

        self.view.select(name)
        body = self.geometry[name]
        lo, hi = body.bounds
        lines = [
            f"<b>{body.name}</b> ({body.kind})",
            f"min: {_fmt_mm(lo)} mm",
            f"max: {_fmt_mm(hi)} mm",
            f"area: {body.area / MM ** 2:.4g} mm²",
        ]
        if body.kind == "solid":
            lines.append(f"volume: {body.volume / MM ** 3:.4g} mm³")
        lines.append(f"triangles: {len(body.triangles)}")
        self.info.setText("<br>".join(lines))
