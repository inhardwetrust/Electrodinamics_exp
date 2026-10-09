# main_window.py
#
# Thin Qt shell: canvas in the middle, controls on the right. Every action
# goes through the Controller; controller events refresh the widgets.

from PySide6.QtCore import QSignalBlocker, Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QComboBox,
    QDockWidget,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from controller import Controller
from feed_plot import FeedPlot

KEY_HINT = "Keys: Right arrow step (hold = repeat)   |   canvas: Space start/pause   N step   R reset   |   wheel zoom, drag pan"


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("atomic FDTD - local diffusion")
        self.resize(1300, 560)

        self.controller = Controller(embedded=True)
        self.controller.add_listener(self._on_event)

        canvas = self.controller.renderer.canvas.native
        canvas.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCentralWidget(canvas)

        self.dock = QDockWidget("Controls", self)
        self.dock.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetMovable
                              | QDockWidget.DockWidgetFeature.DockWidgetFloatable)
        self.dock.setMinimumWidth(320)
        self.dock.setWidget(self._scrollable(self._build_panel()))
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.dock)

        hint = QLabel(KEY_HINT)
        hint.setStyleSheet("color: gray;")
        self.statusBar().addPermanentWidget(hint)

        # Right arrow = Step anywhere in the window (holding it repeats).
        # A focused number field still uses it to move its text cursor.
        step_key = QShortcut(QKeySequence(Qt.Key.Key_Right), self)
        step_key.activated.connect(self._step_key)

        self._refresh_all()
        canvas.setFocus()

    def _step_key(self):
        if not self.controller.playing:
            self.controller.step()

    # =====================================================
    # Panel
    # =====================================================

    def _build_panel(self):
        c = self.controller
        panel = QWidget()
        layout = QVBoxLayout(panel)

        # --- model selector
        row = QHBoxLayout()
        row.addWidget(QLabel("Model"))
        model_combo = QComboBox()
        model_combo.addItems(list(c.MODELS))
        model_combo.setCurrentText(c.model_name)
        model_combo.currentTextChanged.connect(c.set_model)
        row.addWidget(model_combo, 1)
        layout.addLayout(row)

        # --- playback
        box = QGroupBox("Run")
        box_layout = QVBoxLayout(box)
        row = QHBoxLayout()
        self.play_button = QPushButton()
        self.play_button.clicked.connect(lambda: c.set_playing(not c.playing))
        step_button = QPushButton("Step")
        step_button.clicked.connect(lambda: c.step())
        reset_button = QPushButton("Reset")
        reset_button.setToolTip("Recreate the array with the current settings: zeros, then + dif")
        reset_button.clicked.connect(c.reset)
        for b in (self.play_button, step_button, reset_button):
            row.addWidget(b)
        box_layout.addLayout(row)

        form = QFormLayout()
        self.speed_spin = QDoubleSpinBox()
        self.speed_spin.setRange(0.1, 1000.0)
        self.speed_spin.setDecimals(1)
        self.speed_spin.setValue(c.steps_per_second)
        self.speed_spin.valueChanged.connect(c.set_steps_per_second)
        form.addRow("Steps per second", self.speed_spin)
        box_layout.addLayout(form)
        layout.addWidget(box)

        # --- model parameters (generated from the schema)
        box = QGroupBox("Model")
        form = QFormLayout(box)
        self.param_widgets = {}
        self.pending_labels = {}

        self._add_param_rows(form, "model")
        layout.addWidget(box)

        # --- initial state
        box = QGroupBox("Initial state (dif)")
        box_layout = QVBoxLayout(box)
        hint = QLabel("Reset: all zeros, then state += dif, then the feed (set) is applied. "
                      "\"Apply dif now\" adds the dif to the current state.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray;")
        box_layout.addWidget(hint)
        row = QHBoxLayout()
        self.dif_combo = QComboBox()
        self.dif_combo.addItems(c.simulation.dif_presets())
        self.dif_combo.setCurrentText(c.dif)
        self.dif_combo.currentTextChanged.connect(c.set_dif)
        apply_button = QPushButton("Apply dif now")
        apply_button.clicked.connect(c.apply_dif)
        row.addWidget(self.dif_combo, 1)
        row.addWidget(apply_button)
        box_layout.addLayout(row)
        layout.addWidget(box)

        # --- set ("feed")
        box = QGroupBox("Feed (set)")
        box_layout = QVBoxLayout(box)
        hint = QLabel("After every step: where the set has a value, the cell is forced "
                      "to it (state[mask] = set[mask]); other cells keep the computed "
                      "value. Takes effect immediately. Forced cells are outlined.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray;")
        box_layout.addWidget(hint)
        self.set_combo = QComboBox()
        self.set_combo.addItems(c.simulation.set_presets())
        self.set_combo.setCurrentText(c.set_name)
        self.set_combo.currentTextChanged.connect(c.set_set)
        box_layout.addWidget(self.set_combo)
        feed_form = QFormLayout()
        self._add_param_rows(feed_form, "feed")
        box_layout.addLayout(feed_form)
        self.feed_plot = FeedPlot()
        self.feed_plot.setToolTip("The feed waveform, step by step (per set value 1).\n"
                                  "Gray ticks: the cell is free at that step.\n"
                                  "Red line: the current step.")
        box_layout.addWidget(self.feed_plot)
        layout.addWidget(box)

        # --- view
        box = QGroupBox("View")
        form = QFormLayout(box)
        limits = self.limits_combo = QComboBox()
        limits.addItems(Controller.COLOR_LIMITS)
        limits.setCurrentText(c.color_limits)
        limits.setToolTip("initial: colors fixed by the state after Reset (decay is visible)\n"
                          "auto: colors stretched to the current min..max")
        limits.currentTextChanged.connect(c.set_color_limits)
        form.addRow("Color limits", limits)

        palette = self.palette_combo = QComboBox()
        palette.addItems(c.renderer.PALETTES)
        palette.setCurrentText(c.renderer.palette)
        palette.setToolTip("heat: black (low) - green - yellow - red (high)\n"
                           "signed: purple (-) - black (0) - yellow (+), limits symmetric")
        palette.currentTextChanged.connect(c.set_palette)
        form.addRow("Palette", palette)

        r = c.renderer
        digits = QSpinBox()
        digits.setRange(1, 8)
        digits.setValue(r.digits)
        digits.valueChanged.connect(lambda v: c.set_number_format(digits=v))
        form.addRow("Digits", digits)

        number_format = QComboBox()
        number_format.addItems(r.NUMBER_FORMATS)
        number_format.setCurrentText(r.number_format)
        number_format.setToolTip("fixed: digits after the point (small values become 0.000)\n"
                                 "significant: significant figures (0.000496, 1.2e-05)")
        number_format.currentTextChanged.connect(lambda v: c.set_number_format(number_format=v))
        form.addRow("Format", number_format)

        color_mode = QComboBox()
        color_mode.addItems(r.COLOR_MODES)
        color_mode.setCurrentText(r.color_mode)
        color_mode.setToolTip("match numbers: color from the rounded value in the cell,\n"
                              "equal numbers -> equal colors (quantized heatmap)\n"
                              "continuous: color from the exact value")
        color_mode.currentTextChanged.connect(lambda v: c.set_number_format(color_mode=v))
        form.addRow("Colors", color_mode)
        fit = QPushButton("Fit view")
        fit.clicked.connect(lambda: c.renderer.reset_view())
        form.addRow("", fit)
        layout.addWidget(box)

        # --- diagnostics
        box = QGroupBox("State")
        form = QFormLayout(box)
        self.diag_labels = {}
        for name in c.simulation.diagnostics():
            label = QLabel()
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.diag_labels[name] = label
            form.addRow(name, label)
        layout.addWidget(box)

        layout.addStretch(1)
        return panel

    def _add_param_rows(self, form, group):
        """One row per parameter of this group (generated from the schema)."""
        for p in self.controller.parameters():
            if p.group != group:
                continue

            widget = self._param_widget(p)
            widget.setToolTip(p.tooltip)
            self.param_widgets[p.name] = (p, widget)

            if p.restart:
                pending = QLabel("applies on Reset")
                pending.setStyleSheet("color: #c07000;")
                self.pending_labels[p.name] = pending
                row = QHBoxLayout()
                row.addWidget(widget, 1)
                row.addWidget(pending)
                form.addRow(p.label, row)
            else:
                form.addRow(p.label, widget)

    def _param_widget(self, p):
        c = self.controller

        if p.kind == "choice":
            w = QComboBox()
            w.addItems([str(x) for x in p.choices])
            w.currentTextChanged.connect(lambda v, n=p.name: c.set_parameter(n, v))
            return w

        if p.kind == "int":
            w = QSpinBox()
            w.setRange(int(p.minimum), int(p.maximum))
            w.setKeyboardTracking(False)
            w.valueChanged.connect(lambda v, n=p.name: c.set_parameter(n, int(v)))
            return w

        w = QDoubleSpinBox()
        w.setRange(p.minimum, p.maximum)
        w.setDecimals(p.decimals)
        if p.step:
            w.setSingleStep(p.step)
        w.setKeyboardTracking(False)
        w.valueChanged.connect(lambda v, n=p.name: c.set_parameter(n, float(v)))
        return w

    # =====================================================
    # Refresh from the controller
    # =====================================================

    def _on_event(self, kind):
        if kind == "state":
            self._refresh_state()
        elif kind == "playback":
            self._refresh_playback()
        elif kind == "params":
            self._refresh_params()
        elif kind == "model":
            # Other parameters and difs: rebuild the panel. Deferred - this
            # event comes from a signal of a widget the rebuild deletes.
            QTimer.singleShot(0, self._rebuild_panel)

    @staticmethod
    def _scrollable(widget):
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setWidget(widget)
        return area

    def _rebuild_panel(self):
        self.dock.setWidget(self._scrollable(self._build_panel()))
        self._refresh_all()

    def _refresh_all(self):
        self._refresh_params()
        self._refresh_playback()
        self._refresh_state()

    def _refresh_params(self):
        c = self.controller
        current = {p.name for p in c.parameters()}
        for name, (p, w) in self.param_widgets.items():
            if name not in current:
                continue  # an old panel, about to be rebuilt for another model
            value = c.get_parameter(name)
            with QSignalBlocker(w):
                if p.kind == "choice":
                    w.setCurrentText(str(value))
                else:
                    w.setValue(value)
            if name in self.pending_labels:
                self.pending_labels[name].setVisible(c.is_pending(name))
        self._refresh_feed_plot()

    def _refresh_playback(self):
        c = self.controller
        self.play_button.setText("Pause" if c.playing else "Start")
        with QSignalBlocker(self.speed_spin):
            self.speed_spin.setValue(c.steps_per_second)

    def _refresh_feed_plot(self):
        sim = self.controller.simulation
        steps = sim.feed_window()
        self.feed_plot.set_data(steps, sim.feed_curves(steps), sim.step_index)

    def _refresh_state(self):
        self._refresh_feed_plot()
        for name, value in self.controller.simulation.diagnostics().items():
            if name in self.diag_labels:
                self.diag_labels[name].setText(str(value) if isinstance(value, int) else f"{value:.6g}")

    def closeEvent(self, event):
        self.controller.shutdown()
        super().closeEvent(event)
