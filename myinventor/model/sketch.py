# sketch.py
#
# The 2D sketch feature: a frame on a plane, and its geometry: points, lines
# and circles, as in Inventor or SolveSpace. A line refers to its two end
# points by name, a circle to its center point, so a polyline is lines
# sharing points (a closed chain is a profile, later), and constraints will
# attach to the points.
#
#   [[feature]]
#   name = "Sketch1"
#   type = "sketch"
#   plane = "XY Plane"        # an origin plane, a work plane ("WorkPlane1"), or a planar face of a feature:
#                             # "Extrusion1:end", "Extrusion1:start", "Extrusion1:side.L3",
#                             # "Revolution1:start", "Revolution1:end", "Revolution1:disc.L3"
#
#   [[feature.point]]
#   name = "P1"
#   x = "5"                   # formulas (mm) in the sketch's own axes
#   y = "L / 2"
#   explicit = true           # optional: a point on its own (the Point tool)
#
#   [[feature.line]]
#   name = "L1"
#   start = "P1"
#   end = "P2"
#   construction = true       # optional: a construction line (drawn dashed)
#
#   [[feature.polyline]]      # a wide line: its center line and a width at every point
#   name = "W1"
#   points = ["P1", "P2", "P3"]
#   widths = ["w", "w", "1.5"]      # formulas (mm), one per point
#   width = "w"               # the width a new point gets (its text, then its own)
#   start = "flat"            # "flat" | "round" (a half circle of the end's width)
#   end = "round"
#   bends = ["normal", "arc", "normal"]     # optional, per point (the ends' unused):
#                             # "normal" | "arc" | "chamfer" | "miter" (model/traces.py)
#   radii = ["", "2", ""]     # optional, per point: the bend's number ("": its default):
#                             # arc: the center line's radius, chamfer: the setback,
#                             # miter: the cutting circle's radius
#
#   [[feature.circle]]
#   name = "C1"
#   center = "P3"
#   radius = "d / 2"          # a formula (mm), > 0
#   construction = true       # optional, as for lines
#
#   [[feature.spline]]        # an interpolating spline: a smooth curve through the points, in order
#   name = "Spline1"
#   points = ["P1", "P2", "P3"]     # the last one = the first: closed (smooth all around)
#   construction = true       # optional
#
#   [[feature.curve]]         # a generator (model/curves.py): a kind and its arguments
#   name = "Parabola1"
#   kind = "parabola"
#   focal = "f"
#   from = "0"
#   to = "D / 2"
#   x0 = "0"                  # optional arguments take their defaults
#   visible = false           # optional: hidden (not drawn, not in profiles; its ends stay)
#
#   [[feature.offset]]        # the Offset modifier of a line, circle, spline or curve (model/offsets.py)
#   of = "Spline1"
#   left = "1"                # mm (formulas, >= 0) to each side, along it from its start to its end
#   right = "0.5"
#   start = "round"           # caps of an open one: "flat" | "round"
#   end = "flat"
#
# An entity with an offset is its spine: it closes no profile itself; its band is a profile of its own, key "<entity>.offset" (a closed
# one's inner loop: "<entity>.offset.inner").
#
# A curve's ends are points "Parabola1.start" / "Parabola1.end" that lines,
# circles and polylines may use (they are not entities of their own: the
# curve sets them; deleting the curve takes what uses them). A curve can be
# replaced by a spline through points on it (curve_to_spline: by hand from
# then on), or copied into one and hidden.
#
# An explicit point is a sketch point in its own right (Inventor's Point),
# not just the end of lines or a circle's center: it is drawn as a dot in a
# gapped cross even outside sketch mode, and it stays when what uses it is
# deleted. Other points go with the last line or circle using them.
#
# Construction lines and circles (Inventor's Construction) help to draw and
# to place things; they are drawn dashed and will not be part of a profile.
#
# Its build result is a SketchResult: the frame (where the sketch lies in 3D)
# and the evaluated points and radii (mm). The frame's u and v are the
# plane's in-plane axes, so the sketch's x runs along u, its y along v, and
# u x v is the normal (the side the sketch is seen from).

import math
import re
from dataclasses import dataclass, field

import numpy as np

from model.features import Feature, FeatureError, Field, PartialBuild, feature_type
from model.curves import KINDS, CurveError, end_points, spline_points, spline_shape
from model.offsets import CAPS, OffsetError, offset_loops
from model.traces import BENDS, TraceError, outline

MM = 1e-3
CIRCLE_SEGMENTS = 96        # a circle drawn (and picked) as this many segments


@dataclass
class SketchFrame:
    origin: np.ndarray      # (3,) m
    u: np.ndarray           # (3,) unit: the sketch's x
    v: np.ndarray           # (3,) unit: the sketch's y
    normal: np.ndarray      # (3,) unit: u x v

    def to_world(self, xy):
        """(N, 2) sketch coordinates (m) -> (N, 3) world points (m)."""
        xy = np.asarray(xy, dtype=float).reshape(-1, 2)
        return self.origin + xy[:, :1] * self.u + xy[:, 1:] * self.v

    def to_sketch(self, points):
        """(N, 3) world points (m) -> (N, 2) sketch coordinates (m), projected onto the plane."""
        d = np.asarray(points, dtype=float).reshape(-1, 3) - self.origin
        return np.stack([d @ self.u, d @ self.v], axis=1)


@dataclass
class SketchPoint:
    name: str
    x: str                  # formula, mm
    y: str
    explicit: bool = False  # a point on its own (the Point tool), not just a line's end


@dataclass
class SketchLine:
    name: str
    start: str              # point names
    end: str
    construction: bool = False


@dataclass
class SketchCircle:
    name: str
    center: str             # point name
    radius: str             # formula, mm
    construction: bool = False


@dataclass
class SketchPolyline:
    name: str
    points: list            # point names, the center line in order
    widths: list            # formulas (mm), one per point
    width: str = "1"        # the width a new point gets
    start: str = "flat"     # "flat" | "round"
    end: str = "flat"
    bends: list = None      # per point: "normal" | "round" | "chamfer"; None: all normal
    radii: list = None      # per point: a formula (mm), "" for the bend's default; None: all ""

    def __post_init__(self):
        n = len(self.points)
        self.bends = list(self.bends) if self.bends is not None else ["normal"] * n
        self.radii = list(self.radii) if self.radii is not None else [""] * n

    def take(self, indices):
        """Keeps the per-point lists (widths, bends, radii) of these point indices only."""
        for key in ("points", "widths", "bends", "radii"):
            values = getattr(self, key)
            setattr(self, key, [values[i] for i in indices])


@dataclass
class SketchSpline:
    """An interpolating spline through points, in order (the last one = the first: closed)."""
    name: str
    points: list            # point names
    construction: bool = False


@dataclass
class SketchCurve:
    """A generator (model/curves.py): its kind and one formula per argument of that kind."""
    name: str
    kind: str
    args: dict              # argument name -> formula, every argument of the kind
    construction: bool = False
    visible: bool = True    # hidden: not drawn, not in profiles (its ends stay points)


@dataclass
class SketchOffset:
    """The Offset modifier of an entity: a band reaching left / right of it (model/offsets.py)."""
    of: str                 # a line, circle, spline or generated curve
    left: str = "1"         # formulas, mm
    right: str = "0"
    start: str = "flat"     # caps: "flat" | "round" (an open entity's)
    end: str = "flat"


ENDS = ("flat", "round")
OFFSET_KINDS = ("line", "circle", "spline", "curve")


@dataclass
class SketchResult:
    frame: SketchFrame
    points: dict = field(default_factory=dict)      # name -> (2,) mm in the sketch
    lines: list = field(default_factory=list)       # SketchLine
    explicit: set = field(default_factory=set)      # names of the explicit points
    circles: list = field(default_factory=list)     # SketchCircle
    radii: dict = field(default_factory=dict)       # circle name -> radius (mm)
    polylines: list = field(default_factory=list)   # SketchPolyline
    outlines: dict = field(default_factory=dict)    # polyline name -> (edges, polygon (M, 2) mm): model/traces.py
    curves: list = field(default_factory=list)      # SketchCurve, the visible ones
    shapes: dict = field(default_factory=dict)      # curve or spline name -> model.curves.CurveShape (built)
    splines: list = field(default_factory=list)     # SketchSpline
    hidden: set = field(default_factory=set)        # ends of hidden curves nothing uses: not drawn, not picked
    offsets: dict = field(default_factory=dict)     # entity -> [(key, edges, polygon)] of its offset (built)
    spines: set = field(default_factory=set)        # entities with an offset: dashed, in no profile themselves

    def offset_segments_mm(self, name):
        """(K, 2, 3) the outlines of an entity's offset (closed rings) in the scene (mm); none if it failed."""
        rings = []
        for _, _, polygon in self.offsets.get(name, []):
            ring = self.frame.to_world(np.vstack([polygon, polygon[:1]]) * MM) / MM
            rings.append(np.stack([ring[:-1], ring[1:]], axis=1))
        return np.concatenate(rings) if rings else np.zeros((0, 2, 3))

    def shown_points(self):
        """{name: (2,) mm} of the points drawn, picked and snapped to (not the hidden curves' unused ends)."""
        return {n: p for n, p in self.points.items() if n not in self.hidden}

    def world_mm(self, name):
        """A point's position in the scene (mm)."""
        return self.frame.to_world(self.points[name] * MM)[0] / MM

    def segments_mm(self):
        """(K, 2, 3) the lines in the scene (mm), in self.lines order."""
        if not self.lines:
            return np.zeros((0, 2, 3))
        return np.array([[self.world_mm(line.start), self.world_mm(line.end)] for line in self.lines])

    def circle_xy(self, name, n=CIRCLE_SEGMENTS):
        """(n + 1, 2) a closed polyline around the circle, in the sketch (mm)."""
        circle = next(c for c in self.circles if c.name == name)
        t = np.linspace(0.0, 2.0 * np.pi, n + 1)
        return self.points[circle.center] + self.radii[name] * np.stack([np.cos(t), np.sin(t)], axis=1)

    def circle_segments_mm(self, name, n=CIRCLE_SEGMENTS):
        """(n, 2, 3) the circle as segments in the scene (mm)."""
        ring = self.frame.to_world(self.circle_xy(name, n) * MM) / MM
        return np.stack([ring[:-1], ring[1:]], axis=1)

    def outline_segments_mm(self, name):
        """(K, 2, 3) a polyline's outline (closed) in the scene (mm); none if it failed."""
        if name not in self.outlines:
            return np.zeros((0, 2, 3))
        polygon = self.outlines[name][1]
        ring = self.frame.to_world(np.vstack([polygon, polygon[:1]]) * MM) / MM
        return np.stack([ring[:-1], ring[1:]], axis=1)

    def curve_segments_mm(self, name):
        """(K, 2, 3) a generated curve as segments in the scene (mm); none if it failed."""
        if name not in self.shapes:
            return np.zeros((0, 2, 3))
        ring = self.frame.to_world(self.shapes[name].points * MM) / MM
        return np.stack([ring[:-1], ring[1:]], axis=1)

    def center_segments_mm(self, name):
        """(N - 1, 2, 3) a polyline's center line, segment by segment, in the scene (mm)."""
        polyline = next(w for w in self.polylines if w.name == name)
        ends = np.array([self.world_mm(p) for p in polyline.points])
        return np.stack([ends[:-1], ends[1:]], axis=1)


@feature_type
class Sketch(Feature):
    TYPE = "sketch"
    FIELDS = (
        Field("plane", "ref", doc="the plane the sketch lies on: a datum, or 'Feature:face'"),
    )

    def __init__(self, name, points=(), lines=(), circles=(), polylines=(), curves=(), splines=(), offsets=(),
                 **values):
        super().__init__(name, **values)
        self.points = list(points)
        self.lines = list(lines)
        self.circles = list(circles)
        self.polylines = list(polylines)
        self.curves = list(curves)
        self.splines = list(splines)
        self.offsets = list(offsets)
        names = [p.name for p in self.points] + [e.name for e in self.lines + self.circles + self.polylines
                                                 + self.curves + self.splines]
        duplicates = sorted({n for n in names if names.count(n) > 1})
        if duplicates:
            raise FeatureError(f"{name}: duplicate sketch names {duplicates}")
        for c in self.curves:
            kind = KINDS.get(c.kind)
            if kind is None:
                raise FeatureError(f"{name}: curve {c.name}: unknown kind {c.kind!r} (one of {', '.join(KINDS)})")
            wanted = {a.name for a in kind.ARGS}
            if set(c.args) != wanted:
                raise FeatureError(f"{name}: curve {c.name} ({c.kind}) takes {', '.join(a.name for a in kind.ARGS)}")
        points = self.point_names()
        for w in self.splines:
            missing = [p for p in w.points if p not in points]
            if missing:
                raise FeatureError(f"{name}: spline {w.name}: no point {missing[0]!r}")
            closed = len(w.points) > 3 and w.points[0] == w.points[-1]
            body = w.points[:-1] if closed else w.points
            if len(w.points) < 2 or len(set(body)) != len(body):
                raise FeatureError(f"{name}: spline {w.name} needs two points or more, each once "
                                   "(the last may be the first: closed, then three or more)")
        seen = set()
        for o in self.offsets:
            if o.of in seen:
                raise FeatureError(f"{name}: {o.of} has two offsets")
            seen.add(o.of)
            if o.of not in {e.name for e in self.lines + self.circles + self.splines + self.curves}:
                raise FeatureError(f"{name}: offset of {o.of!r}: no line, circle, spline or curve of that name")
            if o.start not in CAPS or o.end not in CAPS:
                raise FeatureError(f"{name}: offset of {o.of}: a cap is one of {', '.join(CAPS)}")
        for line in self.lines:
            for end in (line.start, line.end):
                if end not in points:
                    raise FeatureError(f"{name}: line {line.name}: no point {end!r}")
            if line.start == line.end:
                raise FeatureError(f"{name}: line {line.name} starts and ends at {line.start}")
        for circle in self.circles:
            if circle.center not in points:
                raise FeatureError(f"{name}: circle {circle.name}: no point {circle.center!r}")
        for w in self.polylines:
            missing = [p for p in w.points if p not in points]
            if missing:
                raise FeatureError(f"{name}: polyline {w.name}: no point {missing[0]!r}")
            if len(w.points) < 2 or len(w.widths) != len(w.points):
                raise FeatureError(f"{name}: polyline {w.name} needs two points or more and a width for each")
            if w.start not in ENDS or w.end not in ENDS:
                raise FeatureError(f"{name}: polyline {w.name}: an end is 'flat' or 'round'")
            if len(w.bends) != len(w.points) or len(w.radii) != len(w.points) \
                    or any(b not in BENDS for b in w.bends):
                raise FeatureError(f"{name}: polyline {w.name}: a bend per point, one of {', '.join(BENDS)}, "
                                   "and a radius per point")

    # =====================================================
    # Plain data
    # =====================================================

    @classmethod
    def from_dict(cls, data):
        data = dict(data)
        name = data.pop("name")
        data.pop("type", None)
        points = [_point(name, i, row) for i, row in enumerate(_rows(name, data.pop("point", []), "point"), 1)]
        lines = [_line(name, i, row) for i, row in enumerate(_rows(name, data.pop("line", []), "line"), 1)]
        circles = [_circle(name, i, row)
                   for i, row in enumerate(_rows(name, data.pop("circle", []), "circle"), 1)]
        polylines = [_polyline(name, i, row)
                     for i, row in enumerate(_rows(name, data.pop("polyline", []), "polyline"), 1)]
        curves = [_curve(name, i, row) for i, row in enumerate(_rows(name, data.pop("curve", []), "curve"), 1)]
        splines = [_spline(name, i, row)
                   for i, row in enumerate(_rows(name, data.pop("spline", []), "spline"), 1)]
        offsets = [_offset(name, i, row)
                   for i, row in enumerate(_rows(name, data.pop("offset", []), "offset"), 1)]
        return cls(name, points=points, lines=lines, circles=circles, polylines=polylines, curves=curves,
                   splines=splines, offsets=offsets, **data)

    def to_dict(self):
        data = super().to_dict()
        if self.points:
            data["point"] = [{"name": p.name, "x": p.x, "y": p.y, **({"explicit": True} if p.explicit else {})}
                             for p in self.points]
        if self.lines:
            data["line"] = [{"name": line.name, "start": line.start, "end": line.end, **_construction(line)}
                            for line in self.lines]
        if self.circles:
            data["circle"] = [{"name": c.name, "center": c.center, "radius": c.radius, **_construction(c)}
                              for c in self.circles]
        if self.polylines:
            data["polyline"] = [{"name": w.name, "points": list(w.points), "widths": list(w.widths),
                                 "width": w.width, "start": w.start, "end": w.end, **_bends(w)}
                                for w in self.polylines]
        if self.curves:
            data["curve"] = [{"name": c.name, "kind": c.kind, **c.args, **_construction(c),
                              **({} if c.visible else {"visible": False})} for c in self.curves]
        if self.splines:
            data["spline"] = [{"name": w.name, "points": list(w.points), **_construction(w)} for w in self.splines]
        if self.offsets:
            data["offset"] = [{"of": o.of, "left": o.left, "right": o.right, "start": o.start, "end": o.end}
                              for o in self.offsets]
        return data

    def references(self):
        """The plane's datum or feature ("Extrusion1:end" refers to Extrusion1)."""
        return [self["plane"].split(":", 1)[0]]

    def rename_reference(self, old, new):
        feature, sep, face = self["plane"].partition(":")
        if feature == old:
            self.values["plane"] = new + sep + face

    def formulas(self):
        rows = super().formulas()
        for p in self.points:
            rows += [(f"{p.name}.x", p.x), (f"{p.name}.y", p.y)]
        rows += [(f"{c.name}.radius", c.radius) for c in self.circles]
        for w in self.polylines:
            rows += [(f"{w.name}.width", w.width)] + [(f"{w.name}.width[{p}]", t) for p, t in zip(w.points, w.widths)]
            rows += [(f"{w.name}.radius[{p}]", t) for p, t in zip(w.points, w.radii) if t]
        for c in self.curves:
            # A kind's own variable (a function's t, in x and y) is not a parameter: hidden from the search.
            kind = KINDS[c.kind]
            for arg, text in c.args.items():
                if arg in getattr(kind, "VARIABLE_ARGS", ()):
                    text = re.sub(rf"\b{kind.VARIABLE}\b", "0", text)
                rows.append((f"{c.name}.{arg}", text))
        return rows

    def rename_formulas(self, rename):
        super().rename_formulas(rename)
        for p in self.points:
            p.x, p.y = rename(p.x), rename(p.y)
        for c in self.circles:
            c.radius = rename(c.radius)
        for w in self.polylines:
            w.width = rename(w.width)
            w.widths = [rename(t) for t in w.widths]
            w.radii = [rename(t) if t else t for t in w.radii]
        for c in self.curves:
            kind = KINDS[c.kind]
            own = getattr(kind, "VARIABLE", None)
            hides = own is not None and rename(own) != own      # a parameter named like the variable
            c.args = {arg: text if hides and arg in kind.VARIABLE_ARGS else rename(text)
                      for arg, text in c.args.items()}

    # =====================================================
    # Editing
    # =====================================================

    def free_name(self, prefix):
        """P1, P2..., L1..., C1..., W1...: the first one not taken in this sketch."""
        taken = {p.name for p in self.points} | {e.name for e in self.lines + self.circles + self.polylines
                                                 + self.curves + self.splines}
        n = 1
        while f"{prefix}{n}" in taken:
            n += 1
        return f"{prefix}{n}"

    def kind_of(self, name):
        """
        "point", "line", "circle", "polyline", "curve" (a generator), or
        "segment" (a polyline's center segment, "W1.2": the second one);
        KeyError if there is none (a curve's end point is not an entity).
        """
        for kind, items in (("point", self.points), ("line", self.lines), ("circle", self.circles),
                            ("polyline", self.polylines), ("curve", self.curves), ("spline", self.splines)):
            if any(item.name == name for item in items):
                return kind
        owner, _, index = name.rpartition(".")
        if owner and index.isdigit() and any(w.name == owner and 1 <= int(index) < len(w.points)
                                             for w in self.polylines):
            return "segment"
        raise KeyError(name)

    def polyline(self, name):
        return next(w for w in self.polylines if w.name == name)

    # ---- Deparametrize: formulas -> plain numbers (the geometry stays, it becomes draggable) ----

    def formula_slots(self, names=None):
        """
        [(label, get, set)] of every value of these entities (all if None)
        that is a formula, not a plain number: a point's x / y, a circle's
        radius (and its center), a polyline's points, widths and bend radii,
        a line's end points. label: "P1.x", "C1.radius", "W1.width[P2]"...
        """
        if names is None:
            names = [p.name for p in self.points] + [c.name for c in self.circles] \
                + [w.name for w in self.polylines]
        points, slots, seen = [], [], set()

        def add(label, get, put):
            if label not in seen and not _is_number(get()):
                seen.add(label)
                slots.append((label, get, put))

        for name in names:
            kind = self.kind_of(name)
            if kind == "curve":
                continue                # a generator: freed into primitives later (DESIGN.md), not here
            if kind == "point":
                points.append(name)
            elif kind == "line":
                line = self.line(name)
                points += [line.start, line.end]
            elif kind == "spline":
                points += list(self.spline(name).points)
            elif kind == "circle":
                circle = self.circle(name)
                points.append(circle.center)
                add(f"{circle.name}.radius", lambda c=circle: c.radius, lambda v, c=circle: setattr(c, "radius", v))
            else:                       # a polyline or one of its segments: the whole polyline
                w = self.polyline(name.rpartition(".")[0] if kind == "segment" else name)
                points += list(w.points)
                add(f"{w.name}.width", lambda w=w: w.width, lambda v, w=w: setattr(w, "width", v))
                for i, p in enumerate(w.points):
                    add(f"{w.name}.width[{p}]", lambda w=w, i=i: w.widths[i],
                        lambda v, w=w, i=i: w.widths.__setitem__(i, v))
                    if w.radii[i]:
                        add(f"{w.name}.radius[{p}]", lambda w=w, i=i: w.radii[i],
                            lambda v, w=w, i=i: w.radii.__setitem__(i, v))
        real = {p.name for p in self.points}
        for name in points:
            if name not in real:
                continue                # a curve's end: set by the curve
            point = self.point(name)
            add(f"{name}.x", lambda p=point: p.x, lambda v, p=point: setattr(p, "x", v))
            add(f"{name}.y", lambda p=point: p.y, lambda v, p=point: setattr(p, "y", v))
        return slots

    def deparametrize(self, values, names=None):
        """
        Every formula of these entities (all if None) replaced by its value
        now (values: the parameters' values), 12 significant digits: the
        geometry stays where it is and no longer follows the parameters.
        Returns (labels done, {label: why not} for formulas that fail).
        """
        from model.expressions import ExpressionError, evaluate

        done, failed = [], {}
        for label, get, put in self.formula_slots(names):
            try:
                value = evaluate(get(), values)
            except ExpressionError as error:
                failed[label] = str(error)
                continue
            put(_plain(value))
            done.append(label)
        return done, failed

    def add_polyline(self, start, end, width):
        """A new polyline from point `start` to point `end`, both with the width `width` (a formula)."""
        if start == end:
            raise FeatureError(f"{self.name}: a polyline needs two different points")
        for p in (start, end):
            self._check_point(p)
        name = self.free_name("W")
        self.polylines.append(SketchPolyline(name, [start, end], [str(width)] * 2, str(width)))
        return name

    def extend_polyline(self, name, point):
        """Appends a point at its end; it gets the polyline's width (its text, then its own)."""
        self._check_point(point)
        w = self.polyline(name)
        w.points.append(point)
        w.widths.append(w.width)
        w.bends.append("normal")
        w.radii.append("")

    def insert_into_polyline(self, name, index, point, width):
        """A point put into the center line before point `index` (1..N-1), with this width (a formula)."""
        self._check_point(point)
        w = self.polyline(name)
        if not 1 <= index < len(w.points):
            raise FeatureError(f"{self.name}: polyline {w.name}: no segment before point {index}")
        w.points.insert(index, point)
        w.widths.insert(index, str(width))
        w.bends.insert(index, "normal")
        w.radii.insert(index, "")

    def spline(self, name):
        return next(w for w in self.splines if w.name == name)

    def add_spline(self, start, end, construction=False):
        """A new spline from point `start` to point `end` (a line until it gets more points)."""
        if start == end:
            raise FeatureError(f"{self.name}: a spline needs two different points")
        for p in (start, end):
            self._check_point(p)
        name = self.free_name("Spline")
        self.splines.append(SketchSpline(name, [start, end], construction))
        return name

    def extend_spline(self, name, point):
        """Appends a point at its end (the first point again closes it)."""
        self._check_point(point)
        w = self.spline(name)
        if point in w.points[1:] or (point == w.points[0] and len(w.points) < 3):
            raise FeatureError(f"{self.name}: spline {name} has {point} already")
        w.points.append(point)

    def polyline_widths(self, point):
        """[(polyline, index)] where a point is on a polyline's center line."""
        return [(w, i) for w in self.polylines for i, p in enumerate(w.points) if p == point]

    def point(self, name):
        return next(p for p in self.points if p.name == name)

    def point_names(self):
        """Every point a line, circle or polyline may use: the sketch's points and the curves' ends."""
        return {p.name for p in self.points} | {e for c in self.curves for e in end_points(c.name)}

    def _check_point(self, name):
        if name not in self.point_names():
            raise FeatureError(f"{self.name}: no point {name!r}")

    def gen_curve(self, name):
        """A generated curve (SketchCurve) by name."""
        return next(c for c in self.curves if c.name == name)

    def add_curve(self, kind, construction=False, **args):
        """A new generated curve of a kind (model/curves.py); missing arguments take their defaults."""
        if kind not in KINDS:
            raise FeatureError(f"{self.name}: unknown curve kind {kind!r}")
        declared = KINDS[kind].ARGS
        unknown = set(args) - {a.name for a in declared}
        if unknown:
            raise FeatureError(f"{self.name}: a {kind} has no {sorted(unknown)}")
        values = {}
        for a in declared:
            if a.name not in args and a.default is None:
                raise FeatureError(f"{self.name}: a {kind} needs {a.name!r}")
            values[a.name] = str(args.get(a.name, a.default))
        name = self.free_name(kind.capitalize())
        self.curves.append(SketchCurve(name, kind, values, construction))
        return name

    def offset(self, name):
        """The offset of entity `name` (SketchOffset), or None."""
        return next((o for o in self.offsets if o.of == name), None)

    def add_offset(self, name, left="1", right="0", start="flat", end="flat"):
        """An Offset of a line, circle, spline or curve (none yet); returns it."""
        if self.kind_of(name) not in OFFSET_KINDS:
            raise FeatureError(f"{self.name}: no offset of a {self.kind_of(name)} ({name})")
        if self.offset(name) is not None:
            raise FeatureError(f"{self.name}: {name} has an offset already")
        offset = SketchOffset(name, str(left), str(right), start, end)
        self.offsets.append(offset)
        return offset

    def remove_offset(self, name):
        self.offsets = [o for o in self.offsets if o.of != name]

    def curve_to_spline(self, name, shape, tolerance, keep):
        """
        A spline in place of generated curve `name` (shape: its built
        CurveShape): through points on it with plain numbers, within
        `tolerance` mm of it (model/curves.spline_points). What used the
        curve's ends uses the spline's ends. keep: the curve stays, hidden
        (a copy); else it goes. Returns the spline's name.
        """
        curve = self.gen_curve(name)
        xy = spline_points(shape.edges[0], tolerance)
        closed = len(xy) > 3 and np.allclose(xy[0], xy[-1], atol=1e-9)
        names = [self.add_point(_plain(x), _plain(y)) for x, y in (xy[:-1] if closed else xy)]
        names += names[:1] if closed else []
        spline = self.free_name("Spline")
        self.splines.append(SketchSpline(spline, names, curve.construction))
        start, end = end_points(name)
        moved = {start: names[0], end: names[-1]}
        for line in self.lines:
            line.start, line.end = moved.get(line.start, line.start), moved.get(line.end, line.end)
        for c in self.circles:
            c.center = moved.get(c.center, c.center)
        for w in self.polylines + self.splines[:-1]:
            w.points = [moved.get(p, p) for p in w.points]
        for o in self.offsets:                  # its offset goes with the spline
            if o.of == name:
                o.of = spline
        if keep:
            curve.visible = False
        else:
            self.curves.remove(curve)
        return spline

    def line(self, name):
        return next(line for line in self.lines if line.name == name)

    def circle(self, name):
        return next(c for c in self.circles if c.name == name)

    def add_point(self, x, y, explicit=False):
        name = self.free_name("P")
        self.points.append(SketchPoint(name, str(x), str(y), explicit))
        return name

    def add_line(self, start, end, construction=False):
        if start == end:
            raise FeatureError(f"{self.name}: a line needs two different points")
        for p in (start, end):
            self._check_point(p)
        name = self.free_name("L")
        self.lines.append(SketchLine(name, start, end, construction))
        return name

    def add_circle(self, center, radius, construction=False):
        self._check_point(center)
        name = self.free_name("C")
        self.circles.append(SketchCircle(name, center, str(radius), construction))
        return name

    def curve(self, name):
        """A line, a circle or a generated curve by name (what may be construction)."""
        return next(e for e in self.lines + self.circles + self.curves + self.splines if e.name == name)

    def points_of(self, name):
        """The points an entity is made of: a point itself, a line's ends, a circle's center, a polyline's."""
        kind = self.kind_of(name)
        if kind == "point":
            return [name]
        if kind == "line":
            line = self.line(name)
            return [line.start, line.end]
        if kind == "polyline":
            return list(dict.fromkeys(self.polyline(name).points))
        if kind == "segment":
            owner, _, index = name.rpartition(".")
            points = self.polyline(owner).points
            return [points[int(index) - 1], points[int(index)]]
        if kind == "curve":
            return list(end_points(name))
        if kind == "spline":
            return list(dict.fromkeys(self.spline(name).points))
        return [self.circle(name).center]

    def _used_points(self):
        return ({e for line in self.lines for e in (line.start, line.end)} | {c.center for c in self.circles}
                | {p for w in self.polylines + self.splines for p in w.points})

    def delete(self, name):
        """Deletes an entity (_delete), and the offsets of what is gone with it."""
        self._delete(name)
        alive = {e.name for e in self.lines + self.circles + self.splines + self.curves}
        self.offsets = [o for o in self.offsets if o.of in alive]

    def _delete(self, name):
        """
        Deletes a point (with the lines and circles that use it; out of a
        polyline, whose pieces then join), a line, a circle or a polyline
        (with its points if nothing else uses them and they are not explicit,
        as Inventor does), or a polyline's center segment ("W1.2": the
        polyline splits in two there, each keeping its own ends; a piece of
        one point goes), or a generated curve (with what uses its end points,
        as for a point), or a spline (with its points as for a polyline; a
        point deleted out of a spline: it runs through the others).
        """
        kind = self.kind_of(name)
        if kind == "curve":
            ends = set(end_points(name))
            gone = [line for line in self.lines if {line.start, line.end} & ends]
            others = {p for line in gone for p in (line.start, line.end)}
            self.lines = [line for line in self.lines if line not in gone]
            self.circles = [c for c in self.circles if c.center not in ends]
            for w in self.polylines:
                others |= set(w.points)
                w.take([i for i, p in enumerate(w.points) if p not in ends])
            self.polylines = [w for w in self.polylines if len(w.points) >= 2]
            others |= {p for w in self.splines for p in w.points}
            self._drop_from_splines(ends)
            self.curves = [c for c in self.curves if c.name != name]
            used = self._used_points()
            self.points = [p for p in self.points if p.explicit or p.name in used or p.name not in others]
            return
        if kind == "point":
            self.lines = [line for line in self.lines if name not in (line.start, line.end)]
            self.circles = [c for c in self.circles if c.center != name]
            for w in self.polylines:
                w.take([i for i, p in enumerate(w.points) if p != name])
            self.polylines = [w for w in self.polylines if len(w.points) >= 2]
            self._drop_from_splines({name})
            self.points = [p for p in self.points if p.name != name]
            return
        mine = self.points_of(name)
        if kind == "segment":
            owner, _, index = name.rpartition(".")
            w, k = self.polyline(owner), int(index)
            second = SketchPolyline(self.free_name("W"), w.points[k:], w.widths[k:], w.width, "flat", w.end,
                                    w.bends[k:], w.radii[k:])
            w.take(range(k))
            w.end = "flat"
            at = self.polylines.index(w)
            self.polylines[at + 1:at + 1] = [second]
            self.polylines = [p for p in self.polylines if len(p.points) >= 2]
        self.lines = [line for line in self.lines if line.name != name]
        self.circles = [c for c in self.circles if c.name != name]
        self.polylines = [w for w in self.polylines if w.name != name]
        self.splines = [w for w in self.splines if w.name != name]
        used = self._used_points()
        self.points = [p for p in self.points if p.explicit or p.name in used or p.name not in mine]

    def _drop_from_splines(self, names):
        """These points out of every spline (it runs on through the others); one left with < 2 goes."""
        for w in self.splines:
            w.points = [p for p in w.points if p not in names]
            if len(w.points) < 4 and w.points[:1] == w.points[-1:] and len(w.points) > 1:
                w.points = w.points[:-1]        # closed with too few points left: open
        self.splines = [w for w in self.splines if len(w.points) >= 2]

    # =====================================================
    # Build
    # =====================================================

    def _frame(self, ctx):
        """Where the sketch lies (plane_frame)."""
        return plane_frame(ctx, self["plane"])

    def build(self, ctx):
        frame = self._frame(ctx)
        points = {}
        for p in self.points:
            points[p.name] = np.array([ctx.evaluate(p.x, f"point {p.name}"), ctx.evaluate(p.y, f"point {p.name}")])
        shapes, broken = {}, []
        for c in self.curves:
            try:
                shape = KINDS[c.kind].build(lambda arg, c=c: ctx.evaluate(c.args[arg], f"curve {c.name}: {arg}"),
                                            lambda arg, c=c: c.args[arg], ctx.values)
            except ValueError as error:         # CurveError or a formula's: no curve, no ends
                broken.append(f"curve {c.name}: {error}" if isinstance(error, CurveError) else str(error))
                continue
            shapes[c.name] = shape
            start, end = end_points(c.name)
            points[start], points[end] = shape.start, shape.end
        # What uses the ends of a curve that failed is left out (the sketch fails, but shows the rest).
        lines = [line for line in self.lines if line.start in points and line.end in points]
        circles = [c for c in self.circles if c.center in points]
        polylines = [w for w in self.polylines if all(p in points for p in w.points)]
        splines = []
        for w in self.splines:
            if not all(p in points for p in w.points):
                continue
            try:
                shapes[w.name] = spline_shape([points[p] for p in w.points])
            except CurveError as error:
                broken.append(f"spline {w.name}: {error}")
                continue
            splines.append(w)
        radii = {}
        for c in circles:
            radii[c.name] = ctx.evaluate(c.radius, f"circle {c.name}: the radius")
            if radii[c.name] <= 0:
                raise ValueError(f"circle {c.name}: the radius must be > 0, it is {radii[c.name]:g} mm")
        outlines = {}
        for w in polylines:
            widths = [ctx.evaluate(t, f"polyline {w.name}: the width at {p}") for p, t in zip(w.points, w.widths)]
            bend_radii = [ctx.evaluate(t, f"polyline {w.name}: the bend radius at {p}") if t.strip() else None
                          for p, t in zip(w.points, w.radii)]
            try:
                outlines[w.name] = outline([points[p] for p in w.points], widths, w.start, w.end, w.bends,
                                           bend_radii)
            except TraceError as error:
                broken.append(f"polyline {w.name}: {error}")    # no outline; its center line still shows
        used = ({e for line in lines for e in (line.start, line.end)} | {c.center for c in circles}
                | {p for w in polylines + splines for p in w.points})
        hidden = {e for c in self.curves if not c.visible for e in end_points(c.name)} - used
        result = SketchResult(frame, points, lines, {p.name for p in self.points if p.explicit},
                              circles, radii, polylines, outlines, [c for c in self.curves if c.visible], shapes,
                              splines)
        result.hidden = hidden
        bases = {line.name: ("line", points[line.start], points[line.end]) for line in lines if not line.construction}
        bases.update({c.name: ("circle", points[c.center], radii[c.name]) for c in circles if not c.construction})
        bases.update({w.name: ("curve", shapes[w.name]) for w in splines if not w.construction})
        bases.update({c.name: ("curve", shapes[c.name]) for c in self.curves
                      if c.visible and not c.construction and c.name in shapes})
        for o in self.offsets:
            if o.of not in bases:
                continue                        # a construction or hidden entity, or one that failed
            result.spines.add(o.of)
            try:
                left = ctx.evaluate(o.left, f"offset of {o.of}: left")
                right = ctx.evaluate(o.right, f"offset of {o.of}: right")
                result.offsets[o.of] = offset_loops(o.of, bases[o.of], left, right, o.start, o.end)
            except OffsetError as error:
                broken.append(f"offset of {o.of}: {error}")
            except ValueError as error:
                broken.append(str(error))
        if broken:
            raise PartialBuild("; ".join(broken), result)
        return result


def plane_frame(ctx, name):
    """
    The SketchFrame of a plane by name: an origin plane, a feature's planar
    face ("Extrusion1:end": its frame, normal outward), or a work plane
    (model/workplane.py); ValueError if it is none of them.
    """
    feature, _, face = name.partition(":")
    if face:
        built = ctx.outputs.get(feature)
        if built is None or not hasattr(built, "face_frame"):
            raise ValueError(f"{feature!r} has no faces to sketch on")
        return built.face_frame(face)
    built = ctx.outputs.get(name)
    if built is not None and hasattr(built, "plane_frame"):
        return built.plane_frame()
    try:
        datum = ctx.part.datum(name)
    except KeyError:
        raise ValueError(f"{name!r} is not a plane") from None
    if datum.kind != "plane":
        raise ValueError(f"{name!r} is a work {datum.kind}, not a plane")
    return SketchFrame(datum.origin.copy(), datum.u.copy(), datum.v.copy(), datum.direction.copy())


def _construction(entity):
    return {"construction": True} if entity.construction else {}


def _flag(owner, where, row, key):
    value = row.get(key, False)
    if not isinstance(value, bool):
        raise FeatureError(f"{owner}: {where}: {key} must be true or false")
    return value


def _rows(owner, rows, kind):
    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        raise FeatureError(f"{owner}: {kind} must be an array of tables ([[feature.{kind}]])")
    return rows


def _text(owner, where, row, key, formula=False):
    value = row.get(key)
    if formula and isinstance(value, (int, float)) and not isinstance(value, bool):
        return repr(value)
    if not isinstance(value, str):
        kind = "a formula" if formula else "a string"
        raise FeatureError(f"{owner}: {where}: {key} must be {kind}")
    return value


def _keys(owner, where, row, allowed):
    unknown = sorted(set(row) - set(allowed))
    if unknown:
        raise FeatureError(f"{owner}: {where}: unknown key(s) {unknown}")


def _where(kind, i, row):
    return f"{kind} {i}" + (f" ({row['name']})" if isinstance(row.get("name"), str) else "")


def _point(owner, i, row):
    where = _where("point", i, row)
    _keys(owner, where, row, ("name", "x", "y", "explicit"))
    explicit = row.get("explicit", False)
    if not isinstance(explicit, bool):
        raise FeatureError(f"{owner}: {where}: explicit must be true or false")
    return SketchPoint(_text(owner, where, row, "name"), _text(owner, where, row, "x", True),
                       _text(owner, where, row, "y", True), explicit)


def _line(owner, i, row):
    where = _where("line", i, row)
    _keys(owner, where, row, ("name", "start", "end", "construction"))
    return SketchLine(_text(owner, where, row, "name"), _text(owner, where, row, "start"),
                      _text(owner, where, row, "end"), _flag(owner, where, row, "construction"))


def _is_number(text):
    """A plain number ("12", "-0.5", "1e-3"), not a formula."""
    try:
        return math.isfinite(float(text))
    except ValueError:
        return False


def _plain(value):
    """A value as the text of a plain number: 12 significant digits, no "-0"."""
    text = f"{value + 0.0:.12g}"
    return "0" if text in ("-0", "0") else text


def _bends(polyline):
    """The optional bends / radii lists, written only when some bend or radius is set."""
    if all(b == "normal" for b in polyline.bends) and not any(polyline.radii):
        return {}
    return {"bends": list(polyline.bends), "radii": list(polyline.radii)}


def _polyline(owner, i, row):
    where = _where("polyline", i, row)
    _keys(owner, where, row, ("name", "points", "widths", "width", "start", "end", "bends", "radii"))
    points, widths = row.get("points"), row.get("widths")
    if not isinstance(points, list) or not all(isinstance(p, str) for p in points):
        raise FeatureError(f"{owner}: {where}: points must be a list of point names")
    if not isinstance(widths, list) or not all(isinstance(t, (str, int, float)) and not isinstance(t, bool)
                                               for t in widths):
        raise FeatureError(f"{owner}: {where}: widths must be a list of formulas")
    bends, radii = row.get("bends"), row.get("radii")
    if bends is not None and (not isinstance(bends, list) or not all(isinstance(b, str) for b in bends)):
        raise FeatureError(f"{owner}: {where}: bends must be a list of {', '.join(BENDS)}")
    if radii is not None and (not isinstance(radii, list) or not all(
            isinstance(t, (str, int, float)) and not isinstance(t, bool) for t in radii)):
        raise FeatureError(f"{owner}: {where}: radii must be a list of formulas (\"\": the bend's default)")
    if bends is not None:
        bends = ["miter" if b == "round" else b for b in bends]     # the first files' outer-corner bend
    return SketchPolyline(_text(owner, where, row, "name"), list(points),
                          [t if isinstance(t, str) else repr(t) for t in widths],
                          _text(owner, where, row, "width", True) if "width" in row else "1",
                          row.get("start", "flat"), row.get("end", "flat"), bends,
                          None if radii is None else [t if isinstance(t, str) else repr(t) for t in radii])


def _spline(owner, i, row):
    where = _where("spline", i, row)
    _keys(owner, where, row, ("name", "points", "construction"))
    points = row.get("points")
    if not isinstance(points, list) or not all(isinstance(p, str) for p in points):
        raise FeatureError(f"{owner}: {where}: points must be a list of point names")
    return SketchSpline(_text(owner, where, row, "name"), list(points), _flag(owner, where, row, "construction"))


def _offset(owner, i, row):
    where = f"offset {i}" + (f" (of {row['of']})" if isinstance(row.get("of"), str) else "")
    _keys(owner, where, row, ("of", "left", "right", "start", "end"))
    if "left" not in row and "right" not in row:
        raise FeatureError(f"{owner}: {where}: left or right (mm) is needed")
    caps = [row.get(k, "flat") for k in ("start", "end")]
    if any(c not in CAPS for c in caps):
        raise FeatureError(f"{owner}: {where}: start and end are one of {', '.join(CAPS)}")
    return SketchOffset(_text(owner, where, row, "of"),
                        _text(owner, where, row, "left", True) if "left" in row else "0",
                        _text(owner, where, row, "right", True) if "right" in row else "0", *caps)


def _curve(owner, i, row):
    where = _where("curve", i, row)
    kind = row.get("kind")
    if kind not in KINDS:
        raise FeatureError(f"{owner}: {where}: kind must be one of {', '.join(KINDS)}")
    declared = KINDS[kind].ARGS
    _keys(owner, where, row, ("name", "kind", "construction", "visible", *(a.name for a in declared)))
    args = {}
    for a in declared:
        if a.name in row:
            args[a.name] = _text(owner, where, row, a.name, True)
        elif a.default is None:
            raise FeatureError(f"{owner}: {where}: a {kind} needs {a.name} ({a.doc})")
        else:
            args[a.name] = a.default
    visible = row.get("visible", True)
    if not isinstance(visible, bool):
        raise FeatureError(f"{owner}: {where}: visible must be true or false")
    return SketchCurve(_text(owner, where, row, "name"), kind, args, _flag(owner, where, row, "construction"),
                       visible)


def _circle(owner, i, row):
    where = _where("circle", i, row)
    _keys(owner, where, row, ("name", "center", "radius", "construction"))
    return SketchCircle(_text(owner, where, row, "name"), _text(owner, where, row, "center"),
                        _text(owner, where, row, "radius", True), _flag(owner, where, row, "construction"))
