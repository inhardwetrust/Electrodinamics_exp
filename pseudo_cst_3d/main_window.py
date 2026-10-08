# main_window.py
#
# Qt shell around AppController. Thin view: every edit goes through
# controller methods, and controller events ("status", "layers",
# "simulation") refresh the widgets - so canvas keys (Q, V, Space, ...)
# and panel edits always stay in sync.

import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDockWidget,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSlider,
    QStackedWidget,
    QSpinBox,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from app_controller import AppController
from model_spec import ModelError
from color_mapping import palette_names
from layers import scalar_layer_parameters, vector_layer_parameters
from params import get_value
from qt_forms import ParameterForm


KEY_HINT = (
    "Canvas keys: Q heatmap quantity  V arrows  Space play/pause  "
    "N step  R reset  +/- steps per frame  |  wheel zoom, drag pan"
)


class MainWindow(QMainWindow):
    STATUS_REFRESH_S = 1.0 / 15.0

    def __init__(self, model=None):
        super().__init__()

        self.setWindowTitle("Pseudo CST")
        self.resize(1550, 900)

        self.controller = None
        self._last_status_refresh = 0.0

        self._build_toolbar()

        self.layers_dock = self._make_dock("Layers", Qt.DockWidgetArea.LeftDockWidgetArea)
        # Created first so it sits above Simulation in the right dock area.
        self.slice_dock = self._make_dock("Slice (3D)", Qt.DockWidgetArea.RightDockWidgetArea)
        self.sim_dock = self._make_dock("Simulation", Qt.DockWidgetArea.RightDockWidgetArea)
        self.slice_dock.hide()

        # Permanent hint; showMessage() is used for temporary messages only.
        hint = QLabel(KEY_HINT)
        hint.setStyleSheet("color: gray;")
        self.statusBar().addPermanentWidget(hint)

        self.load_model(model or AppController.DEFAULT_MODEL)

    # =====================================================
    # Frame
    # =====================================================

    def _build_toolbar(self):
        toolbar = QToolBar("Model")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        toolbar.addWidget(QLabel(" Model: "))

        # One entry per file in models/ (name from the file, key = file stem).
        self.mode_combo = QComboBox()
        for key, name, path in AppController.available_models():
            self.mode_combo.addItem(name, key)
            self.mode_combo.setItemData(
                self.mode_combo.count() - 1, str(path), Qt.ItemDataRole.ToolTipRole,
            )
        self.mode_combo.currentIndexChanged.connect(
            lambda i: self.load_model(self.mode_combo.itemData(i))
        )
        toolbar.addWidget(self.mode_combo)

        reload_button = QPushButton("Reload")
        reload_button.setToolTip("Re-read the current model file from disk")
        reload_button.clicked.connect(
            lambda: self.load_model(self.controller.model_key, force=True)
        )
        toolbar.addWidget(reload_button)

    def _make_dock(self, title, area):
        dock = QDockWidget(title, self)
        dock.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QDockWidget.DockWidgetFeature.DockWidgetFloatable
        )
        dock.setMinimumWidth(330)
        self.addDockWidget(area, dock)
        return dock

    @staticmethod
    def _scrollable(widget):
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setWidget(widget)
        return area

    # =====================================================
    # Model switching
    # =====================================================

    def load_model(self, key, force=False):
        """Switch to (or with force=True: reload) a model from models/."""
        old = self.controller

        if old is not None and key == old.model_key and not force:
            return

        try:
            controller = AppController(model=key, embedded=True)
        except ModelError as error:
            # Keep the current model; show what is wrong with the file.
            if old is None:
                raise
            QMessageBox.warning(self, "Model file error", str(error))
            self._select_in_combo(old.model_key)
            return

        if old is not None:
            old.shutdown()

        self.controller = controller
        self.controller.add_listener(self._on_controller_event)

        # Views of this controller (3D scene / flat slice) share the central
        # area as pages of a stack. Replacing the central widget deletes the
        # previous model's canvases.
        self.view_stack = QStackedWidget()
        self.setCentralWidget(self.view_stack)
        canvas_widget = self._show_active_view()

        self._select_in_combo(key)

        self.layers_dock.setWidget(self._scrollable(self._build_layers_panel()))
        self.sim_dock.setWidget(self._scrollable(self._build_simulation_panel()))

        if self.controller.is_3d:
            self.slice_dock.setWidget(self._build_slice_panel())
            self.slice_dock.show()
        else:
            self.slice_dock.hide()

        self._refresh_status()
        canvas_widget.setFocus()

    def _select_in_combo(self, key):
        index = self.mode_combo.findData(key)
        if index >= 0 and index != self.mode_combo.currentIndex():
            self.mode_combo.blockSignals(True)
            self.mode_combo.setCurrentIndex(index)
            self.mode_combo.blockSignals(False)

    def _show_active_view(self):
        """Put the controller's active canvas on top (adding it on first use)."""
        widget = self.controller.renderer.canvas.native
        widget.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        if self.view_stack.indexOf(widget) < 0:
            self.view_stack.addWidget(widget)

        self.view_stack.setCurrentWidget(widget)
        return widget

    # =====================================================
    # Layers panel
    # =====================================================

    def _build_layers_panel(self):
        c = self.controller
        panel = QWidget()
        layout = QVBoxLayout(panel)

        # --- heatmap
        heat_box = QGroupBox("Heatmap")
        heat_layout = QVBoxLayout(heat_box)

        self.heatmap_form = ParameterForm(
            scalar_layer_parameters(c.source, palette_names()),
            get=lambda name: get_value(self.controller.heatmap_layer, name),
            on_change=lambda name, value: self.controller.update_heatmap(name, value),
        )
        heat_layout.addWidget(self.heatmap_form)

        self.clim_label = QLabel()
        self.clim_label.setStyleSheet("color: gray;")
        heat_layout.addWidget(self.clim_label)

        layout.addWidget(heat_box)

        # --- arrows
        arrow_box = QGroupBox("Arrows (direction only, fixed length)")
        arrow_layout = QVBoxLayout(arrow_box)

        self.vector_form = ParameterForm(
            vector_layer_parameters(c.source),
            get=lambda name: get_value(self.controller.vector_layer, name),
            on_change=lambda name, value: self.controller.update_vector(name, value),
        )
        arrow_layout.addWidget(self.vector_form)
        layout.addWidget(arrow_box)

        # --- view
        view_box = QGroupBox("View")
        view_layout = QVBoxLayout(view_box)

        mesh_check = QCheckBox("Solver mesh and domain border")
        mesh_check.setEnabled(c.mesh is not None)
        mesh_check.setChecked(c.mesh is not None and c.renderer.mesh_enabled)
        mesh_check.toggled.connect(self.controller.set_mesh_visible)
        view_layout.addWidget(mesh_check)

        reset_view = QPushButton("Reset view")
        reset_view.clicked.connect(self._reset_view)
        view_layout.addWidget(reset_view)

        layout.addWidget(view_box)
        layout.addStretch(1)
        return panel

    def _reset_view(self):
        self.controller.reset_view()

    # =====================================================
    # Simulation panel
    # =====================================================

    def _build_simulation_panel(self):
        c = self.controller
        panel = QWidget()
        layout = QVBoxLayout(panel)

        self.diag_labels = {}

        if c.simulation is None:
            info = QLabel(
                "Static field: nothing evolves in time.\n\n"
                "Choose \"Oscillating dipole\" or \"FDTD\" in the Model "
                "selector for time-dependent fields."
            )
            info.setWordWrap(True)
            layout.addWidget(info)
            layout.addStretch(1)
            return panel

        # --- playback
        play_box = QGroupBox("Playback")
        play_layout = QVBoxLayout(play_box)

        buttons = QHBoxLayout()
        self.play_button = QPushButton()
        self.play_button.clicked.connect(lambda: self.controller.set_playing(not self.controller.playing))
        step_button = QPushButton("Step")
        step_button.clicked.connect(self._step_once)
        reset_button = QPushButton("Reset")
        reset_button.clicked.connect(self.controller.reset_simulation)
        for b in (self.play_button, step_button, reset_button):
            buttons.addWidget(b)
        play_layout.addLayout(buttons)

        form = QFormLayout()
        self.steps_spin = QSpinBox()
        self.steps_spin.setRange(1, 4096)
        self.steps_spin.setValue(c.steps_per_frame)
        self.steps_spin.valueChanged.connect(self.controller.set_steps_per_frame)
        form.addRow("Steps per frame", self.steps_spin)

        self.time_label = QLabel()
        self.step_label = QLabel()
        form.addRow("Time t", self.time_label)
        form.addRow("Step", self.step_label)
        play_layout.addLayout(form)
        layout.addWidget(play_box)

        # --- diagnostics
        diagnostics = c.simulation.diagnostics()
        if diagnostics:
            diag_box = QGroupBox("Diagnostics")
            diag_layout = QFormLayout(diag_box)
            for label in diagnostics:
                value = QLabel()
                value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                self.diag_labels[label] = value
                diag_layout.addRow(label, value)
            layout.addWidget(diag_box)

        # --- parameters (generated from the simulation's schema)
        params_box = QGroupBox("Parameters")
        params_layout = QVBoxLayout(params_box)

        self.sim_form = ParameterForm(
            c.simulation.parameters(),
            get=lambda name: self.controller.simulation.get_parameter(name),
            on_change=self._set_sim_parameter,
        )
        params_layout.addWidget(self.sim_form)

        extra = self._simulation_info()
        if extra:
            info = QLabel(extra)
            info.setWordWrap(True)
            info.setStyleSheet("color: gray;")
            params_layout.addWidget(info)

        layout.addWidget(params_box)
        layout.addStretch(1)
        return panel

    # =====================================================
    # Slice panel (3D models)
    # =====================================================

    AXIS_LABELS = {
        "x": "x  (plane y-z)",
        "y": "y  (plane x-z)",
        "z": "z  (plane x-y)",
    }

    def _build_slice_panel(self):
        c = self.controller
        panel = QWidget()
        layout = QVBoxLayout(panel)

        # Remembered position per normal axis (switching axes and back
        # returns to the same plane).
        self._slice_positions = {a: 0.0 for a in "xyz"}
        self._slice_positions[c.slice_plane.normal] = c.slice_plane.position

        form = QFormLayout()

        self.slice_axis_combo = QComboBox()
        for axis in "xyz":
            self.slice_axis_combo.addItem(self.AXIS_LABELS[axis], axis)
        self.slice_axis_combo.setCurrentIndex("xyz".index(c.slice_plane.normal))
        self.slice_axis_combo.currentIndexChanged.connect(self._slice_axis_changed)
        form.addRow("Normal", self.slice_axis_combo)

        row = QHBoxLayout()
        back = QPushButton("\u25c0")
        back.setFixedWidth(32)
        back.clicked.connect(lambda: self.slice_slider.setValue(self.slice_slider.value() - 1))
        self.slice_slider = QSlider(Qt.Orientation.Horizontal)
        self.slice_slider.valueChanged.connect(self._slice_slider_moved)
        forward = QPushButton("\u25b6")
        forward.setFixedWidth(32)
        forward.clicked.connect(lambda: self.slice_slider.setValue(self.slice_slider.value() + 1))
        row.addWidget(back)
        row.addWidget(self.slice_slider, 1)
        row.addWidget(forward)
        form.addRow("Position", row)

        self.slice_label = QLabel()
        form.addRow("", self.slice_label)

        self.flat_check = QCheckBox("Full viewport (flat slice, 2D view)")
        self.flat_check.setChecked(c.flat_view)
        self.flat_check.setToolTip(
            "Show the slice flat over the whole view area, like the 2D models "
            "(screen-fixed arrows, 2D zoom/pan). Off: the slice inside the 3D scene."
        )
        self.flat_check.toggled.connect(self.controller.set_flat_view)
        form.addRow("", self.flat_check)
        layout.addLayout(form)

        hint = QLabel(
            "3D view: drag to rotate, wheel to zoom, Shift + drag to pan. "
            "Flat view: wheel to zoom, drag to pan."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray;")
        layout.addWidget(hint)
        layout.addStretch(1)

        self._configure_slider()
        self._refresh_slice()
        return panel

    def _configure_slider(self):
        axis = self.controller.slice_plane.normal
        first, step, count = self.controller.slice_axis_nodes(axis)
        index = int(round((self._slice_positions[axis] - first) / step))

        self.slice_slider.blockSignals(True)
        self.slice_slider.setRange(1, count - 2)       # interior nodes
        self.slice_slider.setValue(max(1, min(count - 2, index)))
        self.slice_slider.blockSignals(False)

    def _slice_axis_changed(self, index):
        axis = self.slice_axis_combo.itemData(index)
        self.controller.set_slice(axis, self._slice_positions[axis])
        self._configure_slider()

    def _slice_slider_moved(self, value):
        axis = self.controller.slice_plane.normal
        first, step, _ = self.controller.slice_axis_nodes(axis)
        position = first + value * step
        self._slice_positions[axis] = position
        self.controller.set_slice(axis, position)

    def _refresh_slice(self):
        plane = self.controller.slice_plane
        if plane is None or not hasattr(self, "slice_label"):
            return
        self.slice_label.setText(plane.describe())

    def _simulation_info(self):
        sim = self.controller.simulation
        parts = []

        if hasattr(sim, "nx"):
            dims = f"{sim.nx} x {sim.ny}" + (f" x {sim.nz}" if hasattr(sim, "nz") else "")
            parts.append(f"Grid {dims} cells, h = {sim.h:g}")
        if hasattr(sim, "cells_per_wavelength"):
            parts.append(f"{sim.cells_per_wavelength:.1f} cells per wavelength")
        if hasattr(sim, "min_cells_per_wavelength"):
            densest = sim.min_cells_per_wavelength
            if densest < sim.cells_per_wavelength - 1e-9:
                parts.append(f"{densest:.1f} in the densest material")
        parts.append(f"dt = {sim.dt:.4g}")

        return ", ".join(parts)

    def _set_sim_parameter(self, name, value):
        try:
            self.controller.set_simulation_parameter(name, value)
            self.statusBar().clearMessage()
        except ValueError as error:
            self.statusBar().showMessage(f"Rejected: {error}", 8000)

    def _step_once(self):
        if self.controller.playing:
            self.controller.set_playing(False)
        self.controller.advance(1)

    # =====================================================
    # Controller events
    # =====================================================

    def _on_controller_event(self, kind):
        if kind == "layers":
            self.heatmap_form.refresh()
            self.vector_form.refresh()
            self._refresh_clim()
        elif kind == "simulation":
            if hasattr(self, "sim_form"):
                self.sim_form.refresh()
            self._refresh_status()
        elif kind == "slice":
            self._refresh_slice()
        elif kind == "view":
            self._show_active_view().setFocus()
            self._refresh_clim()
        elif kind == "playback":
            # User actions (play/pause, steps per frame): show immediately.
            self._refresh_status()
        else:
            # "status" arrives every frame; text readouts need ~15 Hz, and
            # each update may relayout the dock.
            now = time.perf_counter()
            if now - self._last_status_refresh >= self.STATUS_REFRESH_S:
                self._last_status_refresh = now
                self._refresh_status()

    def _refresh_clim(self):
        lo, hi = self.controller.current_heatmap_clim()
        self.clim_label.setText(f"Limits in use: {lo:.4g} ... {hi:.4g}")

    def _refresh_status(self):
        c = self.controller
        self._refresh_clim()

        if c.simulation is None:
            return

        sim = c.simulation
        self.play_button.setText("Pause" if c.playing else "Play")
        self.time_label.setText(f"{sim.t:.4f}")
        self.step_label.setText(str(sim.step_index))

        if self.steps_spin.value() != c.steps_per_frame:
            self.steps_spin.blockSignals(True)
            self.steps_spin.setValue(c.steps_per_frame)
            self.steps_spin.blockSignals(False)

        for label, value in sim.diagnostics().items():
            if label in self.diag_labels:
                self.diag_labels[label].setText(f"{value:+.5g}")

    def closeEvent(self, event):
        if self.controller is not None:
            self.controller.shutdown()
        super().closeEvent(event)
