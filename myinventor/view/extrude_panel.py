# extrude_panel.py
#
# The Extrude command's panel (as Inventor's Extrude dialog, for now only
# what is needed): a small tool window over the main one, not modal, so the
# 3D view stays usable for picking profiles.
#   Profiles   how many are selected (picked in 3D); Clear drops them all
#   Operation  New solid, Join, Cut, Intersect (only New without a body)
#   Bodies     how many bodies it joins / cuts / intersects (their names in
#              the tooltip); taken and dropped by clicks on their rows in the
#              browser (toggle_body, the window calls it); Clear drops them.
#              Join takes one (a click replaces it; switching to Join keeps
#              the last); Cut and Intersect take several, each by the same tool
#   Extents    (Extrude) Distance, To Plane, To Body; To: the plane (origin
#              planes, work planes above it) or the body from a list
#              (Distance hidden then); none taken at first ("pick a
#              plane..."); planes the extrusion runs along are greyed out
#              (set_blocked_planes, the reason as their tooltip); To Body:
#              "Where it misses the body: go the distance" (fallback_on):
#              Distance shows again, for the parts that miss it
#   Distance  a formula of the parameters (mm): a formula field (view/
#              formula_field.py: names completed, the value grey at its
#              right, a red frame on an error); the error also below it
#   Apply      makes the extrusion (off until there is a profile and the
#              distance is a number other than 0); Cancel (or Esc, or
#              closing the panel) leaves without one.
# The same panel edits an extrusion (its distance formula filled in, the
# title "Edit Extrusion1"), and serves Revolve (kind="revolve"): Profiles /
# Axis buttons choose what a click in the view takes (selecting(which)), the
# axis taken is shown, and Angle (degrees, 0 < |angle| <= 360) replaces
# Distance; Apply also needs an axis. The panel only shows and reports (applied(distance
# text), cancelled(), cleared() (Clear), changed() on any edit); the window does the rest, the
# preview included.

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
)

from model.expressions import ExpressionError, evaluate
from model.features import body_list
from model.revolve import angle_error
from view.formula_field import FormulaField

ERROR_STYLE = "color: #c81e1e"
OPERATIONS = (("new", "New solid"), ("join", "Join"), ("cut", "Cut"), ("intersect", "Intersect"))
EXTENTS = (("distance", "Distance"), ("plane", "To Plane"), ("body", "To Body"))


class ExtrudePanel(QDialog):
    applied = Signal(str)
    cancelled = Signal()
    cleared = Signal()
    changed = Signal()
    selecting = Signal(str)         # "profiles" | "axis": what a click in the view takes (Revolve)

    def __init__(self, parent, values, bodies=(), distance="10", operation=None, body=None, title="Extrude",
                 kind="extrude", axis=None, extent="distance", to="", planes=(), fallback=False):
        """
        values: the parameters' values, for evaluating the distance formula;
        bodies: the names of the bodies there are (to join, cut...);
        operation, body: the start (body: a name or a list; default: Join the
        last body if there is one, else New solid); title: "Edit Extrusion1"...
        extent, to: how far (Extrude): "distance", or up to a plane / a body
        named `to`; planes: the planes it may go up to (above it); fallback:
        up to a body, where it misses go the distance.
        """
        super().__init__(parent, Qt.Tool)
        self.setWindowTitle(title)
        self.setModal(False)
        self.values = values
        self.kind = kind
        self._axis = axis                       # the axis taken (its label), or None
        self.profiles = QLabel()
        self.operation = QComboBox()
        for key, text in OPERATIONS:
            self.operation.addItem(text, key)
        self.available = list(bodies)
        if not bodies:                          # nothing to join, cut or intersect
            for i in range(1, len(OPERATIONS)):
                self.operation.model().item(i).setEnabled(False)
        operation = operation or ("join" if bodies else "new")
        self.operation.setCurrentIndex([k for k, _ in OPERATIONS].index(operation))
        taken = [b for b in body_list(body) if b in self.available] if body else []
        if operation == "new" or not taken:
            taken = self.available[-1:]
        self._bodies = taken
        self.body = QLabel()
        self.body_clear_button = QPushButton("Clear")
        self.body_clear_button.setToolTip("Drop all the bodies, to pick them again in the browser")
        self.body_clear_button.setAutoDefault(False)
        self.body_clear_button.setFixedWidth(self.body_clear_button.fontMetrics().horizontalAdvance("Clear") + 16)
        self.body_clear_button.clicked.connect(lambda: self.set_bodies([]))
        self.operation.currentIndexChanged.connect(self._operation_changed)
        self.distance = FormulaField(distance, values, unit="mm" if kind == "extrude" else "°")
        self.distance.setToolTip("How far along the sketch's normal (mm): a number or a formula of the "
                                 "parameters; negative: the other way" if kind == "extrude" else
                                 "How far around the axis (degrees): a number or a formula; 360: all the way "
                                 "round; negative: the other way")
        self.value = QLabel()
        self.distance.textChanged.connect(self._update)
        self.clear_button = QPushButton("Clear")
        self.clear_button.setToolTip("Drop all the selected profiles, to pick them again")
        self.clear_button.setAutoDefault(False)
        self.clear_button.setFixedWidth(self.clear_button.fontMetrics().horizontalAdvance("Clear") + 16)
        self.clear_button.clicked.connect(self.cleared.emit)
        profiles = QHBoxLayout()
        profiles.addWidget(self.profiles, 1)
        profiles.addWidget(self.clear_button)
        form = QFormLayout()
        if kind == "revolve":
            self.pick_profiles = QPushButton("Profiles")
            self.pick_axis = QPushButton("Axis")
            for button, which in ((self.pick_profiles, "profiles"), (self.pick_axis, "axis")):
                button.setCheckable(True)
                button.setAutoDefault(False)
                button.clicked.connect(lambda _=False, which=which: self.select(which))
            self.pick_profiles.setToolTip("Clicks in the view take or drop profiles")
            self.pick_axis.setToolTip("A click in the view takes the axis: a straight line of any sketch, or an "
                                      "origin axis")
            picks = QHBoxLayout()
            picks.addWidget(self.pick_profiles)
            picks.addWidget(self.pick_axis)
            form.addRow("Select:", picks)
        form.addRow("Profiles:", profiles)
        if kind == "revolve":
            self.axis = QLabel()
            form.addRow("Axis:", self.axis)
        form.addRow("Operation:", self.operation)
        body_row = QHBoxLayout()
        body_row.addWidget(self.body, 1)
        body_row.addWidget(self.body_clear_button)
        form.addRow("Bodies:", body_row)
        self.planes = list(planes)
        self.extent = QComboBox()
        for key, text in EXTENTS:
            self.extent.addItem(text, key)
        self.extent.setToolTip("How far: by a distance, up to a plane (an origin plane, a work plane), or until "
                               "the profiles meet a body")
        self.extent.setCurrentIndex([k for k, _ in EXTENTS].index(extent))
        self.target = QComboBox()
        self.target.setToolTip("The plane or the body it goes up to (a plane the extrusion runs along: greyed out)")
        self._to = to
        self._blocked = {}                      # plane -> why it is never reached (set_blocked_planes)
        self.fallback = QCheckBox("Where it misses the body: go the distance")
        self.fallback.setToolTip("Off: a profile partly missing the body fails (it would go on forever). On: the "
                                 "parts that miss go the distance below (toward the body: a plane at that distance); "
                                 "the rest still stops on the body")
        self.fallback.setChecked(bool(fallback))
        if kind == "extrude":
            form.addRow("Extents:", self.extent)
            form.addRow("To:", self.target)
            form.addRow("", self.fallback)
        form.addRow("Distance:" if kind == "extrude" else "Angle:", self.distance)
        form.addRow("", self.value)
        self.form = form
        self.extent.currentIndexChanged.connect(self._extent_changed)
        self.target.currentIndexChanged.connect(lambda _: self._update())
        self.fallback.toggled.connect(self._extent_changed)
        self.apply_button = QPushButton("Apply")
        self.apply_button.setDefault(True)
        self.apply_button.clicked.connect(self._apply)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.apply_button)
        buttons.addWidget(cancel)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addLayout(buttons)
        self._count = 0
        self._done = False
        self._fill_targets()
        self.set_profile_count(0)
        if kind == "revolve":
            self.set_axis(axis)
            self.pick_profiles.setChecked(True)

    def select(self, which):
        """What a click in the view takes: "profiles" or "axis" (Revolve)."""
        self.pick_profiles.setChecked(which == "profiles")
        self.pick_axis.setChecked(which == "axis")
        self.selecting.emit(which)

    def selecting_axis(self):
        return self.kind == "revolve" and self.pick_axis.isChecked()

    def set_axis(self, label):
        """The axis taken (shown by its label), or None."""
        self._axis = label
        if self.kind == "revolve":
            self.axis.setText(label or "click a straight line or an origin axis")
            self.axis.setStyleSheet("" if label else "color: gray")
        self._update()

    def set_profile_count(self, count):
        self._count = count
        self.profiles.setText(f"{count} selected" if count else "click closed regions in the view")
        self.clear_button.setEnabled(count > 0)
        self._update()

    def operation_key(self):
        return self.operation.currentData()

    def body_names(self):
        """The bodies joined / cut / intersected (None for a new solid)."""
        return None if self.operation_key() == "new" else list(self._bodies)

    def body_name(self):
        """The first of body_names() (None for a new solid or none taken)."""
        names = self.body_names()
        return names[0] if names else None

    def set_bodies(self, names):
        """The bodies taken (Join: the last one only)."""
        names = [n for n in dict.fromkeys(names) if n in self.available]
        self._bodies = names[-1:] if self.operation_key() == "join" else names
        self._update()

    def toggle_body(self, name):
        """A click on a body's row: takes it or drops it (Join: it replaces the one taken). False if it cannot."""
        if self.operation_key() == "new" or name not in self.available:
            return False
        if name in self._bodies:
            self.set_bodies([b for b in self._bodies if b != name])
        else:
            self.set_bodies(self._bodies + [name])
        return True

    def _operation_changed(self):
        self.set_bodies(self._bodies)           # Join keeps the last one

    def extent_key(self):
        """"distance" | "plane" | "body" (Revolve: "distance", its angle)."""
        return self.extent.currentData() if self.kind == "extrude" else "distance"

    def target_name(self):
        """The plane or body it goes up to ("" for a distance, or none to pick)."""
        return (self.target.currentData() or "") if self.extent_key() != "distance" else ""

    def set_blocked_planes(self, blocked):
        """{plane: why it cannot be reached from the profiles' sketch}: greyed out in the To list."""
        if blocked != self._blocked:
            self._blocked = dict(blocked)
            self._fill_targets()
            self._update()

    def _extent_changed(self):
        self._fill_targets()
        self._update()

    def _fill_targets(self):
        """The To list for the extent (planes, or the bodies there are); Distance shown only for a distance."""
        extent = self.extent_key()
        keep = self.target.currentData() or self._to
        self.target.blockSignals(True)
        self.target.clear()
        names = self.planes if extent == "plane" else self.available if extent == "body" else []
        # Nothing taken by default: the first in the list is no better a guess than the others.
        self.target.addItem(f"pick a {extent}...", None)
        for name in names:
            self.target.addItem(name, name)
            why = self._blocked.get(name) if extent == "plane" else None
            if why:                             # never reached from this sketch: shown, not offered
                item = self.target.model().item(self.target.count() - 1)
                item.setEnabled(False)
                item.setToolTip(why)
        if keep in names and not (extent == "plane" and self._blocked.get(keep)):
            self.target.setCurrentIndex(names.index(keep) + 1)
        self.target.blockSignals(False)
        if self.kind == "extrude":
            wants_distance = extent == "distance" or self.fallback_on()
            self.form.setRowVisible(self.target, extent != "distance")
            self.form.setRowVisible(self.fallback, extent == "body")
            self.form.setRowVisible(self.distance, wants_distance)
            self.form.setRowVisible(self.value, wants_distance)

    def fallback_on(self):
        """Up to a body, the parts missing it go the distance (the box ticked)."""
        return self.kind == "extrude" and self.extent_key() == "body" and self.fallback.isChecked()

    def distance_value(self):
        """The distance (mm) or the angle (degrees), or the error text."""
        if self.extent_key() != "distance" and not self.fallback_on():
            return "up to a plane or a body: no distance"
        if self.fallback_on():
            try:
                value = evaluate(self.distance.text(), self.values)
            except ExpressionError as error:
                return str(error)
            return value if value > 0 else "the distance where it misses must be more than 0 (toward the body)"
        try:
            value = evaluate(self.distance.text(), self.values)
        except ExpressionError as error:
            return str(error)
        if self.kind == "revolve":
            return angle_error(value) or value
        return value if value != 0 else "the distance must not be 0"

    def _update(self):
        value = self.distance_value()
        number = not isinstance(value, str)
        ok = number if self.extent_key() == "distance" else \
            bool(self.target_name()) and (number or not self.fallback_on())
        if self.extent_key() == "distance" or self.fallback_on():
            self.value.setText("" if number else value)     # the value itself: grey in the field
            self.value.setStyleSheet("" if number else ERROR_STYLE)
            self.value.setVisible(not number)
        new = self.operation_key() == "new"
        count = len(self._bodies)
        if new:
            self.body.setText("a new body")
        elif count:
            self.body.setText(self._bodies[0] if count == 1 else f"{count} selected")
        else:
            self.body.setText("click bodies in the browser" if self.operation_key() != "join"
                              else "click a body in the browser")
        self.body.setStyleSheet("color: gray" if new or not count else "")
        self.body.setToolTip("" if new else "\n".join(self._bodies) or "Click rows under Solid Bodies (a group: "
                                                                         "all its bodies)")
        self.body_clear_button.setEnabled(not new and count > 0)
        target_ok = new or count > 0
        axis_ok = self.kind != "revolve" or self._axis is not None
        self.apply_button.setEnabled(ok and target_ok and axis_ok and self._count > 0)
        self.changed.emit()

    def _apply(self):
        if self.apply_button.isEnabled():
            self._done = True
            self.applied.emit(self.distance.text().strip())
            self.close()

    def reject(self):
        """Cancel, Esc in the panel, or closing it."""
        if not self._done:
            self._done = True
            self.cancelled.emit()
        super().reject()

    def closeEvent(self, event):
        if not self._done:
            self._done = True
            self.cancelled.emit()
        super().closeEvent(event)
