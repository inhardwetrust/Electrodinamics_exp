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
from project.project_file import UP_AXES, Project, ProjectError
from project.settings import SECTIONS
from view.geometry_view import GeometryView
from view.profile_window import ProfileWindow
from view.results_panel import ResultsPanel
from project.settings import EXCITE_ALL, accuracy_db
from solver.result_file import (delete_result, fingerprint, format_duration, list_results,
                                rename_result, save_result)
from view.sim_runner import SimulationRunner
from solver.line_profile import impedance_profile, line_impedance

MM = 1e-3
AXES = "xyz"
MESH_PROGRESS_DELAY = 0.3       # s: the mesh progress bar appears after this
SLIDER_STEPS = 1000             # plane positions over the model when it is not on the grid lines


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


class MainWindow(QMainWindow):
    def __init__(self, path=None):
        super().__init__()
        self.resize(1400, 900)

        self.geometry = None
        self.project = None
        self.ports = {}             # {port body name: PortGeometry}
        self.elements = {}          # {lumped element body name: PortGeometry}
        self.waveports = {}         # {waveguide port body name: WavePortGeometry}
        self.holes = []             # geometry.holes.Hole: open through holes of the model
        self.via_bodies = []        # PEC cylinders of the holes assigned as vias
        self.mesh = None            # MeshResult
        self.materials = None       # MaterialGrid (solver's view)
        self._mesh_coordinate = None    # where the mesh plane is (m)
        self.profile_window = None      # Z0 profile (ProfileWindow), made on first use
        self._mesh_pool = ThreadPoolExecutor(max_workers=1)    # background discretization
        self._mesh_job = None
        self._center_slider = False
        self._mesh_timer = QTimer(self)
        self._mesh_timer.setInterval(100)
        self._mesh_timer.timeout.connect(self._poll_discretize)
        self.view = GeometryView()
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.addTab(self.view.canvas.native, "3D View")
        self.setCentralWidget(self.tabs)

        self.runner = SimulationRunner(self)
        self.runner.progress.connect(self._sim_progress)
        self.runner.finished.connect(self._sim_finished)
        self.runner.failed.connect(self._sim_failed)
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

        edges = QCheckBox("Edges")
        edges.setChecked(True)
        edges.toggled.connect(self.view.set_show_edges)
        bar.addWidget(edges)

        xray = QCheckBox("X-ray")
        xray.toggled.connect(self.view.set_xray)
        bar.addWidget(xray)

        planes = QCheckBox("Ref. planes")
        planes.setChecked(True)
        planes.setToolTip("Reference planes of the waveguide ports (dark blue): the port plane "
                          "moved by the shift in the Results tab, along the port's axis")
        planes.toggled.connect(self.view.set_show_reference_planes)
        bar.addWidget(planes)
        bar.addSeparator()

        bar.addWidget(QLabel(" Up: "))
        self.up_combo = QComboBox()
        self.up_combo.addItems([a.upper() for a in UP_AXES])
        self.up_combo.setToolTip("Vertical axis of the CAD model (stored in the project)")
        self.up_combo.currentTextChanged.connect(self._up_changed)
        bar.addWidget(self.up_combo)
        bar.addSeparator()

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
        z0_menu.addAction("Z₀ profile along the axis…", self._z0_profile)
        self.z0_button.setMenu(z0_menu)
        bar.addWidget(self.z0_button)

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

        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.addWidget(self.tree, 3)
        layout.addWidget(hint)
        layout.addWidget(self.info, 1)

        # Tabs of the left panel: the model tree and the material library.
        self.side_tabs = QTabWidget()
        self.side_tabs.setDocumentMode(True)
        self.side_tabs.addTab(panel, "Project")
        self.side_tabs.addTab(self._build_materials_panel(), "Materials")

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
        if self.mesh is None or not self.mesh.ok:
            return f"no mesh ({self.mesh.error if self.mesh else 'no project'})"
        if self.materials is None:
            return "the mesh is still being built" if self.mesh_busy() else "no materials"
        if self.materials.errors:
            return self.materials.errors[0]
        if not self.materials.ports and not self.materials.waveports:
            return "no valid port"
        excite = self.project.settings["simulation"]["excite"]
        if excite != EXCITE_ALL and excite not in self._port_list():
            return (f"the excited port {excite!r} is not a valid port of the model "
                    f"(Simulation > excite: choose another one, or all ports)")
        return None

    def _confirm_shorts(self):
        """Ports or elements shorted by metal on this mesh: usually a mesh too coarse, ask."""
        text = ("On this mesh, metal joins what should be apart:\n\n"
                + "\n\n".join(f"• {s}" for s in self.materials.shorts)
                + "\n\nUnless the short is meant (a shorted stub, a PIFA's short pin), the result "
                  "will be wrong. Simulate anyway?")
        answer = QMessageBox.warning(self, "Simulate", text, QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        return answer == QMessageBox.Yes

    def _port_list(self):
        """Names of the valid ports in S-matrix order (lumped, then waveguide)."""
        if self.materials is None:
            return []
        return [p.name for p in self.materials.ports] + [w.name for w in self.materials.waveports]

    def _simulate(self):
        problem = self._simulation_problem()
        if problem:
            QMessageBox.warning(self, "Simulate", f"Cannot simulate: {problem}")
            return
        if self.materials.shorts and not self._confirm_shorts():
            return
        sim = self.project.settings["simulation"]
        self._run_fingerprint = fingerprint(self.project)      # the inputs of this run
        self._run_params = self._parameters_snapshot()
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
                          None if excite == EXCITE_ALL else [excite])

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

    def _parameters_snapshot(self):
        """What this run is computed with, for the Results list (display only)."""
        grid = self.mesh.grid
        steps = np.concatenate([grid.steps(a) for a in range(3)])
        dielectrics = {}
        for name, a in sorted(self.project.assignments.items()):
            if a.type == "dielectric":
                v = a.values
                dielectrics[name] = (f"{a.material}: " if a.material else "") + \
                    f"εr {v['eps_r']:g}, tan δ {v['tan_d']:g}"
        ports = [f"{p.name} {p.resistance:.4g} Ω" for p in self.materials.ports] + \
                [f"{w.name} {w.resistance:.4g} Ω (line)" for w in self.materials.waveports]
        elements = [f"{e.name}: {e.topology} " + ", ".join(
                        [f"R {e.r:g} Ω"] * (e.r > 0) + [f"L {e.l * 1e9:g} nH"] * (e.l > 0)
                        + [f"C {e.c * 1e12:g} pF"] * (e.c > 0)) for e in self.materials.elements]
        excite = self.project.settings["simulation"]["excite"]
        return {
            "excite": "all ports" if excite == EXCITE_ALL else excite,
            "elements": elements,
            "simulation": dict(self.project.settings["simulation"]),
            "mesh": dict(self.project.settings["mesh"]),
            "grid": f"{' × '.join(str(n) for n in grid.shape)} = {grid.cell_count:,}".replace(",", " ")
                    + f" cells, step {steps.min() / MM:.3g}–{steps.max() / MM:.3g} mm",
            "dielectrics": dielectrics,
            "vias": len(self.via_bodies),
            "ports": ports,
        }

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
        self._cancel_discretize()           # the worker stops at its next step
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
        self.project = project
        self.holes = find_holes(geometry)
        self.view.highlight_holes([])
        self._mesh_coordinate = None
        self.view.set_geometry(geometry)

        up = project.up if project else "z"
        self.up_combo.blockSignals(True)
        self.up_combo.setCurrentText(up.upper())
        self.up_combo.blockSignals(False)
        self.view.set_up(up)

        self.mesh_axis.blockSignals(True)
        self.mesh_axis.setCurrentIndex(AXES.index(up))      # horizontal plane by default
        self.mesh_axis.blockSignals(False)

        self._refresh_model(center_slider=True)
        self._fill_tree()
        self._fill_materials()
        self._load_runs(path)
        self._remember(path)

        lo, hi = geometry.bounds
        self.setWindowTitle(f"customcst - {os.path.basename(path)}  ({_fmt_mm(hi - lo)} mm)")

    # =====================================================
    # Derived model: ports, colors, mesh (recomputed after every edit)
    # =====================================================

    def _refresh_model(self, center_slider=False):
        """
        Ports, vias, the grid (fast), then the discretization in the
        background (_start_discretize): self.materials is None until it is
        done, then _mesh_ready() updates the plane and the tree.
        """
        self.ports = self.project.ports(self.geometry) if self.project else {}
        self.elements = self.project.elements(self.geometry) if self.project else {}
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
        self._center_slider = self._center_slider or center_slider
        if self.mesh is not None and self.mesh.ok:
            self._start_discretize()
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
        self._configure_mesh_slider(center=self._center_slider)
        self._center_slider = False
        self._show_mesh_plane()
        if self.profile_window is not None and self.profile_window.isVisible():
            self.profile_window.mark_outdated()

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

    def _z0_here(self):
        if not self._z0_ready():
            return
        axis = self.mesh_axis.currentIndex()
        s = line_impedance(self.materials, axis, self._grid_index(), self.geometry.bounds)
        where = f"{AXES[axis]} = {s.coordinate / MM:.3f} mm"
        note = self._z0_note()
        self.statusBar().showMessage(
            f"Z0 at {where}: {s.z0:.1f} Ω, eps_eff {s.eps_eff:.3f}" + (f"  (narrow lines: {note})" if note else "")
            if not s.error else f"Z0 at {where}: {s.error}", 15000)

    def _z0_profile(self):
        if not self._z0_ready():
            return
        axis = self.mesh_axis.currentIndex()

        def progress(done, total):
            self.statusBar().showMessage(f"Z0 profile along {AXES[axis]}: section {done} / {total}")
            QApplication.processEvents()

        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            profile = impedance_profile(self.materials, axis, self.geometry.bounds, progress)
        finally:
            QApplication.restoreOverrideCursor()
        self.statusBar().showMessage(f"Z0 profile along {AXES[axis]}: {len(profile.sections)} sections", 5000)
        if self.profile_window is None:
            self.profile_window = ProfileWindow(self)
            self.profile_window.coordinate_picked.connect(self._profile_picked)
        self.profile_window.set_profile(profile, AXES[axis], accuracy=self._z0_note())
        if self._mesh_coordinate is not None:
            self.profile_window.set_marker(self._mesh_coordinate)
        self.profile_window.show()
        self.profile_window.raise_()

    def _profile_picked(self, coordinate):
        """The profile's marker moved: put the plane there (and make it visible)."""
        axis = self.profile_window.profile.axis
        if self.mesh is None or not self.mesh.ok:
            return
        if self.mesh_axis.currentIndex() != axis:
            self.mesh_axis.setCurrentIndex(axis)
        if not (self.mesh_check.isChecked() or self.materials_check.isChecked()
                or self.section_check.isChecked()):
            self.materials_check.setChecked(True)
        self.mesh_slider.setValue(int(np.argmin(np.abs(self._slider_positions() - coordinate))))

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
    #   section row       Simulation / Mesh
    #     setting row     "f max" | 5 (editable) | GHz
    #     info rows       mesh statistics
    #   body row          name (visibility checkbox) | kind | size
    #     type row        "Type" | Dielectric
    #       param row     "εr" | 4.3 (editable) | unit
    #       note row      read-only explanation
    # Every item carries (role, owner, param) in UserRole; owner is a body
    # name for body rows and a section name for section rows.
    # =====================================================

    def _fill_tree(self):
        self.tree.blockSignals(True)
        self.tree.clear()

        if self.project is not None:
            for name, section in SECTIONS.items():
                item = QTreeWidgetItem([section.label])
                item.setData(0, Qt.UserRole, ("section", name, None))
                font = item.font(0)
                font.setBold(True)
                item.setFont(0, font)
                self.tree.addTopLevelItem(item)
                self._fill_section(item)

        self._fill_holes()

        for body in self.geometry.bodies:
            lo, hi = body.bounds
            item = QTreeWidgetItem([body.name, body.kind, _fmt_mm(hi - lo)])
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(0, Qt.Checked)
            item.setData(0, Qt.UserRole, ("body", body.name, None))
            self.tree.addTopLevelItem(item)
            self._fill_type(item)

        self.tree.blockSignals(False)
        self._resize_columns()
        self._show_summary()

    def _fill_holes(self):
        """
        The Holes section: open through holes, grouped by diameter, with their
        via assignment. Rebuilt in place after an edit (expanded groups kept).
        """
        old = self._top_item("holes", None)
        index, expanded = self.tree.topLevelItemCount(), set()
        if old is not None:
            index = self.tree.indexOfTopLevelItem(old)
            expanded = {old.child(i).data(0, Qt.UserRole)[1] for i in range(old.childCount())
                        if old.child(i).isExpanded()}
            self.tree.takeTopLevelItem(index)
        if not self.holes:
            return

        n_vias = len(self.project.vias(self.holes)) if self.project else 0
        section = QTreeWidgetItem(["Holes", f"{len(self.holes)} through, {n_vias} via", ""])
        section.setData(0, Qt.UserRole, ("holes", None, None))
        section.setToolTip(0, "Open through holes found in the STEP. Select to highlight them, "
                              "right click a diameter or a hole to make it a via.")
        font = section.font(0)
        font.setBold(True)
        section.setFont(0, font)
        self.tree.insertTopLevelItem(index, section)

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
            value = self.project.settings[name][param.name]
            row = QTreeWidgetItem(item, [param.label, _fmt_value(value), param.unit])
            row.setData(0, Qt.UserRole, ("setting", name, param.name))
            if param.boolean:                       # a check box, toggled with a click
                row.setFlags(row.flags() | Qt.ItemIsUserCheckable)
                row.setCheckState(1, Qt.Checked if value else Qt.Unchecked)
            else:
                row.setFlags(row.flags() | Qt.ItemIsEditable)
                row.setToolTip(1, "Double click to edit")
            if param.tooltip:
                row.setToolTip(0, param.tooltip)
                row.setToolTip(1, param.tooltip)

        if section.note:
            self._note_row(item, name, section.note, "gray")

        if name == "mesh":
            self._mesh_rows(item)
        if name == "simulation":
            results = QTreeWidgetItem(item, ["Results", "", ""])
            results.setData(0, Qt.UserRole, ("results", None, None))
            font = results.font(0)
            font.setBold(True)
            results.setFont(0, font)

        item.setExpanded(True)
        self.tree.blockSignals(False)
        if name == "simulation":
            self._fill_results()

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
        section = self._top_item("section", "simulation")
        if section is None:
            return None
        for i in range(section.childCount()):
            if section.child(i).data(0, Qt.UserRole)[0] == "results":
                return section.child(i)
        return None

    def _fill_results(self):
        """Project > Simulation > Results: one row per run (tick = shown), its parameters inside."""
        item = self._results_item()
        if item is None:
            return
        self.tree.blockSignals(True)
        expanded = {item.child(i).data(0, Qt.UserRole)[1] for i in range(item.childCount())
                    if item.child(i).isExpanded()}
        item.takeChildren()
        item.setText(1, f"{len(self.runs)} run" + ("s" if len(self.runs) != 1 else ""))
        current = self._current_fingerprint()
        primary = self.results.primary
        for run in reversed(self.runs):                 # newest on top
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

    def _top_item(self, role, owner):
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            if item.data(0, Qt.UserRole)[:2] == (role, owner):
                return item
        return None

    def _after_edit(self):
        """Save, recompute ports and mesh, refresh the dependent tree rows."""
        self._save()
        self._refresh_model()
        self._refresh_rows()

    def _refresh_rows(self):
        """The tree rows that depend on the ports and the mesh."""
        for body in self.geometry.bodies:
            self._fill_type(self._top_item("body", body.name))
        self._fill_section(self._top_item("section", "mesh"))
        self._fill_holes()
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
        top = item
        while top.parent() is not None:
            top = top.parent()
        if top.data(0, Qt.UserRole)[0] != "body":
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
        if role not in ("param", "setting") or self.project is None:
            return

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
        self._fill_section(self._top_item("section", "simulation"))
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
            return

        if role == "run" and column == 0:
            self._run_changed(item, owner)
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

        if changed:
            self._after_edit()

    def _save(self):
        try:
            self.project.save()
        except OSError as error:
            QMessageBox.critical(self, "Project file", f"cannot save {self.project.path}\n\n{error}")
            return

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

        top = item
        while top.parent() is not None:
            top = top.parent()
        role, name, _ = top.data(0, Qt.UserRole)

        if role != "body":
            self.view.select(None)
            return

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
