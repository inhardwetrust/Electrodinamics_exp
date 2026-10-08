# qt_forms.py
#
# Widgets generated from a list of params.Parameter. The form knows nothing
# about layers or simulations: it reads values through `get(name)` and
# reports edits through `on_change(name, value)`.

from PySide6.QtCore import QSignalBlocker
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QSpinBox,
    QWidget,
)

from params import is_enabled


class ParameterForm(QWidget):
    def __init__(self, parameters, get, on_change, parent=None):
        super().__init__(parent)

        self.parameters = list(parameters)
        self._get = get
        self._on_change = on_change
        self._widgets = {}

        layout = QFormLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)

        for parameter in self.parameters:
            widget = self._create_widget(parameter)
            self._widgets[parameter.name] = widget

            label = parameter.label
            if parameter.unit:
                label += f" [{parameter.unit}]"

            layout.addRow(label, widget)

        self.refresh()

    # -------------------------------------------------

    def _create_widget(self, p):
        name = p.name

        if p.kind == "bool":
            w = QCheckBox()
            w.toggled.connect(lambda v, n=name: self._edited(n, bool(v)))
            return w

        if p.kind == "choice":
            w = QComboBox()
            w.addItems([str(c) for c in p.choices])
            w.currentIndexChanged.connect(
                lambda i, n=name, c=p.choices: self._edited(n, c[i])
            )
            return w

        if p.kind == "int":
            w = QSpinBox()
            w.setRange(
                int(p.minimum if p.minimum is not None else -2 ** 31),
                int(p.maximum if p.maximum is not None else 2 ** 31 - 1),
            )
            if p.step:
                w.setSingleStep(int(p.step))
            w.setKeyboardTracking(False)
            w.valueChanged.connect(lambda v, n=name: self._edited(n, int(v)))
            return w

        if p.kind == "float":
            w = QDoubleSpinBox()
            w.setDecimals(p.decimals)
            w.setRange(
                p.minimum if p.minimum is not None else -1e12,
                p.maximum if p.maximum is not None else 1e12,
            )
            if p.step:
                w.setSingleStep(p.step)
            else:
                w.setStepType(QAbstractSpinBox.StepType.AdaptiveDecimalStepType)
            # Apply on Enter / focus loss, not on every typed digit.
            w.setKeyboardTracking(False)
            w.valueChanged.connect(lambda v, n=name: self._edited(n, float(v)))
            return w

        raise ValueError(f"Unsupported parameter kind {p.kind!r}")

    def _edited(self, name, value):
        self._on_change(name, value)
        # The owner may have adjusted other values (presets, validation).
        self.refresh()

    # -------------------------------------------------

    def values(self):
        return {p.name: self._get(p.name) for p in self.parameters}

    def refresh(self):
        """Show the owner's current values without emitting edits."""
        values = self.values()

        for p in self.parameters:
            w = self._widgets[p.name]
            value = values[p.name]

            with QSignalBlocker(w):
                if p.kind == "bool":
                    w.setChecked(bool(value))
                elif p.kind == "choice":
                    if value in p.choices:
                        w.setCurrentIndex(list(p.choices).index(value))
                elif p.kind == "int":
                    w.setValue(int(value))
                else:
                    w.setValue(float(value))

            w.setEnabled(is_enabled(p, values))
