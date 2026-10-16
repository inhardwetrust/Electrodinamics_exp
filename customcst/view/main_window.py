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

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDockWidget, QFileDialog, QLabel,
    QMainWindow, QMenu, QMessageBox, QSlider, QTabWidget, QToolBar, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)

from geometry.step_io import read_step
from discretize.materials import discretize
from mesh.rectilinear import build_grid
from project.object_types import OBJECT_TYPES
from project.project_file import UP_AXES, Project, ProjectError
from project.settings import SECTIONS
from view.geometry_view import GeometryView
from view.results_panel import ResultsPanel
from solver.result_file import fingerprint, load_result, result_path, save_result
from view.sim_runner import SimulationRunner

MM = 1e-3
AXES = "xyz"


def _fmt_mm(values):
    return " x ".join(f"{v / MM:.3g}" for v in values)


def _fmt_value(value):
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
        self.mesh = None            # MeshResult
        self.materials = None       # MaterialGrid (solver's view)
        self._mesh_coordinate = None    # where the mesh plane is (m)
        self.view = GeometryView()
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.addTab(self.view.canvas.native, "3D View")
        self.setCentralWidget(self.tabs)

        self.runner = SimulationRunner(self)
        self.runner.progress.connect(self._sim_progress)
        self.runner.finished.connect(self._sim_finished)
        self.runner.failed.connect(self._sim_failed)
        self.result = None

        self._build_toolbar()
        self._build_tree_panel()
        self._build_results_panel()

        if path:
            self.load(path)
        else:
            self.setWindowTitle("customcst")

    # =====================================================
    # UI
    # =====================================================

    def _build_toolbar(self):
        bar = QToolBar("Main")
        bar.setMovable(False)
        self.addToolBar(bar)

        bar.addAction("Open STEP...", self._open_dialog)
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
        self.mesh_check.toggled.connect(lambda _: self._show_mesh_plane())
        bar.addWidget(self.mesh_check)

        self.materials_check = QCheckBox("Materials")
        self.materials_check.setToolTip("Edges on the mesh plane as the solver sees them")
        self.materials_check.toggled.connect(lambda _: self._show_mesh_plane())
        bar.addWidget(self.materials_check)

        bar.addWidget(QLabel(" plane ⊥ "))
        self.mesh_axis = QComboBox()
        self.mesh_axis.addItems([a.upper() for a in AXES])
        self.mesh_axis.currentIndexChanged.connect(self._mesh_axis_changed)
        bar.addWidget(self.mesh_axis)

        self.mesh_slider = QSlider(Qt.Horizontal)
        self.mesh_slider.setMinimumWidth(220)
        self.mesh_slider.valueChanged.connect(self._mesh_slider_moved)
        bar.addWidget(self.mesh_slider)

        self.mesh_label = QLabel()
        self.mesh_label.setMinimumWidth(90)
        bar.addWidget(self.mesh_label)

        self.legend = QLabel(
            "  <span style='color:#ccc;background:#222'>&nbsp;"
            "<b style='color:#fff21a'>━ PEC</b>&nbsp; "
            "<b style='color:#ff33e6'>━ port</b>&nbsp; "
            "<b style='color:#ffffff'>━ lumped</b>&nbsp; "
            "<b style='color:#4df2ff'>━ dielectric</b> (brighter = higher εr)&nbsp;</span>"
        )
        self.legend.setVisible(False)
        self.statusBar().addPermanentWidget(self.legend)   # the toolbar is full

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

        dock = QDockWidget("Project")
        dock.setFeatures(QDockWidget.DockWidgetMovable)
        dock.setWidget(panel)
        dock.setMinimumWidth(400)
        self.addDockWidget(Qt.LeftDockWidgetArea, dock)

    def _build_results_panel(self):
        self.results = ResultsPanel()
        self.tabs.addTab(self.results, "Results")

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
            return "no materials"
        if self.materials.errors:
            return self.materials.errors[0]
        if not self.materials.ports and not self.materials.waveports:
            return "no valid port"
        return None

    def _simulate(self):
        problem = self._simulation_problem()
        if problem:
            QMessageBox.warning(self, "Simulate", f"Cannot simulate: {problem}")
            return
        sim = self.project.settings["simulation"]
        self._run_fingerprint = fingerprint(self.project)      # the inputs of this run
        self.simulate_action.setEnabled(False)
        self.stop_action.setEnabled(True)
        self.results.set_message("running...")
        self.runner.start(self.materials, sim["f_min"], sim["f_max"])

    def _sim_progress(self, port, t, level):
        self.statusBar().showMessage(
            f"simulating, {port} excited: t = {t * 1e9:.2f} ns, energy {level:.1f} dB "
            f"(stops at -40 dB)")

    def _sim_done(self):
        self.simulate_action.setEnabled(True)
        self.stop_action.setEnabled(False)

    def _sim_finished(self, result):
        self._sim_done()
        if result is None:
            self.statusBar().showMessage("simulation stopped", 4000)
            self.results.set_message("stopped")
            return
        self.result = result
        result.fingerprint = self._run_fingerprint
        try:
            path = save_result(self.geometry.source, result)
            self.statusBar().showMessage(f"simulation finished, saved to {os.path.basename(path)}", 6000)
        except OSError as error:
            self.statusBar().showMessage(f"simulation finished, NOT saved: {error}", 10000)
        self.results.default_dir = os.path.dirname(self.geometry.source)
        self.results.set_result(result)
        self._update_result_status()
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
        self._load_result(path)

        lo, hi = geometry.bounds
        self.setWindowTitle(f"customcst - {os.path.basename(path)}  ({_fmt_mm(hi - lo)} mm)")

    # =====================================================
    # Derived model: ports, colors, mesh (recomputed after every edit)
    # =====================================================

    def _refresh_model(self, center_slider=False):
        self.ports = self.project.ports(self.geometry) if self.project else {}
        self.elements = self.project.elements(self.geometry) if self.project else {}
        self.waveports = self.project.waveports(self.geometry) if self.project else {}
        self.view.set_ports(list(self.ports.values()), list(self.elements.values()))

        types = {n: a.type for n, a in self.project.assignments.items()} if self.project else {}
        self.view.set_type_colors(types)

        columns = {**self.ports, **self.elements}       # bodies that need a line on their axis
        self.mesh = (build_grid(self.geometry, self.project, columns, self.waveports)
                     if self.project else None)
        self.materials = (
            discretize(self.mesh, self.geometry, self.project, self.ports, self.elements,
                       self.waveports)
            if self.mesh is not None and self.mesh.ok else None
        )
        self._configure_mesh_slider(center=center_slider)
        self._show_mesh_plane()

    def _configure_mesh_slider(self, center=False):
        if self.mesh is None or not self.mesh.ok:
            self.mesh_slider.setEnabled(False)
            return

        # The plane keeps its coordinate when the grid changes (the line
        # index would point somewhere else); new axis or first mesh: center.
        axis = self.mesh_axis.currentIndex()
        lines = self.mesh.grid.lines[axis]
        if center or self._mesh_coordinate is None:
            lo, hi = self.geometry.bounds
            self._mesh_coordinate = 0.5 * (lo + hi)[axis]

        self.mesh_slider.blockSignals(True)
        self.mesh_slider.setEnabled(True)
        self.mesh_slider.setRange(0, len(lines) - 1)
        self.mesh_slider.setValue(int(np.argmin(np.abs(lines - self._mesh_coordinate))))
        self.mesh_slider.blockSignals(False)

    def _mesh_slider_moved(self, index):
        if self.mesh is not None and self.mesh.ok:
            self._mesh_coordinate = self.mesh.grid.lines[self.mesh_axis.currentIndex()][index]
        self._show_mesh_plane()

    def _mesh_axis_changed(self, _index):
        self._configure_mesh_slider(center=True)
        self._show_mesh_plane()

    def _show_mesh_plane(self):
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
        index = self.mesh_slider.value()
        coordinate = self.mesh.grid.lines[axis][index]
        self.view.set_mesh_plane(self.mesh.grid if show_mesh else None, axis, coordinate)
        if show_materials and self.materials is not None:
            self.view.set_material_plane(self.materials, axis, index)
        else:
            self.view.set_material_plane(None)
        self.view.set_domain(self.mesh.grid.bounds)
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

    def _fill_section(self, item):
        self.tree.blockSignals(True)
        item.takeChildren()
        name = item.data(0, Qt.UserRole)[1]
        section = SECTIONS[name]

        for param in section.params:
            row = QTreeWidgetItem(item, [
                param.label, _fmt_value(self.project.settings[name][param.name]), param.unit,
            ])
            row.setData(0, Qt.UserRole, ("setting", name, param.name))
            row.setFlags(row.flags() | Qt.ItemIsEditable)
            row.setToolTip(1, "Double click to edit")

        if section.note:
            self._note_row(item, name, section.note, "gray")

        if name == "mesh":
            self._mesh_rows(item)

        item.setExpanded(True)
        self.tree.blockSignals(False)

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

        for axis, merged in enumerate(mesh.merged):
            for kept, dropped in merged:
                text = ", ".join(f"{d / MM:.4g}" for d in dropped)
                self._note_row(item, "mesh", f"{AXES[axis]}: {text} → {kept / MM:.4g} mm (merged)", "#888")

        for warning in mesh.warnings:
            self._note_row(item, "mesh", f"⚠ {warning}", "#c80")

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

            for param in object_type.params:
                row = QTreeWidgetItem(type_item, [
                    param.label, _fmt_value(assignment.values[param.name]), param.unit,
                ])
                row.setData(0, Qt.UserRole, ("param", name, param.name))
                row.setFlags(row.flags() | Qt.ItemIsEditable)
                row.setToolTip(1, "Double click to edit")

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
            ("⌀", f"{2 * port.radius / MM:.4g}", "mm"),
        ]
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

    def _load_result(self, step_path):
        """Shows the results saved next to the STEP file, if any."""
        try:
            self.result = load_result(step_path)
        except Exception as error:          # a damaged file must not block opening the model
            self.result = None
            self.statusBar().showMessage(f"{os.path.basename(result_path(step_path))}: {error}", 10000)
        self.results.default_dir = os.path.dirname(step_path)
        self.results.set_result(self.result)
        if self.result is None:
            self.results.set_message("no results yet: ▶ Simulate")
        self._update_result_status()

    def _update_result_status(self):
        """Saved / outdated line under the plot (the model may change after a run)."""
        if self.result is None:
            self.results.set_status("")
            return
        saved = f"saved {self.result.saved}" if self.result.saved else "not saved"
        current = fingerprint(self.project) if self.project is not None else None
        if current is not None and self.result.fingerprint == current:
            self.results.set_status(f"{saved}; matches the current model and settings", "#2a2")
        else:
            self.results.set_status(
                f"⚠ {saved}; OUTDATED: the STEP file, the body types or the settings changed "
                f"since this run (▶ Simulate to update)", "#c80")

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

        for body in self.geometry.bodies:
            self._fill_type(self._top_item("body", body.name))
        self._fill_section(self._top_item("section", "mesh"))
        self._resize_columns()
        self._show_summary()
        self._update_result_status()

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
        if role not in ("param", "setting") or self.project is None:
            return

        param = (self.project.get(owner).object_type.param(name) if role == "param"
                 else SECTIONS[owner].param(name))
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

    def _item_changed(self, item, column):
        role, owner, param = item.data(0, Qt.UserRole)

        if role == "body" and column == 0:
            self.view.set_visible(owner, item.checkState(0) == Qt.Checked)
            return

        if role not in ("param", "setting") or column != 1:
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
