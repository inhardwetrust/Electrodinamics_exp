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
#   plane = "XY Plane"        # an origin plane, or a planar face of a feature by meaning:
#                             # "Extrusion1:end", "Extrusion1:start", "Extrusion1:side.L3"
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
#   bends = ["normal", "round", "normal"]   # optional, per point (the ends' unused):
#                             # "normal" | "round" | "chamfer" (model/traces.py)
#   radii = ["", "2", ""]     # optional, per point: the bend's radius ("": half the width)
#
#   [[feature.circle]]
#   name = "C1"
#   center = "P3"
#   radius = "d / 2"          # a formula (mm), > 0
#   construction = true       # optional, as for lines
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

from dataclasses import dataclass, field

import numpy as np

from model.features import Feature, FeatureError, Field, PartialBuild, feature_type
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
    radii: list = None      # per point: a formula (mm), "" for half the width; None: all ""

    def __post_init__(self):
        n = len(self.points)
        self.bends = list(self.bends) if self.bends is not None else ["normal"] * n
        self.radii = list(self.radii) if self.radii is not None else [""] * n

    def take(self, indices):
        """Keeps the per-point lists (widths, bends, radii) of these point indices only."""
        for key in ("points", "widths", "bends", "radii"):
            values = getattr(self, key)
            setattr(self, key, [values[i] for i in indices])


ENDS = ("flat", "round")


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

    def __init__(self, name, points=(), lines=(), circles=(), polylines=(), **values):
        super().__init__(name, **values)
        self.points = list(points)
        self.lines = list(lines)
        self.circles = list(circles)
        self.polylines = list(polylines)
        names = [p.name for p in self.points] + [e.name for e in self.lines + self.circles + self.polylines]
        duplicates = sorted({n for n in names if names.count(n) > 1})
        if duplicates:
            raise FeatureError(f"{name}: duplicate sketch names {duplicates}")
        points = {p.name for p in self.points}
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
        return cls(name, points=points, lines=lines, circles=circles, polylines=polylines, **data)

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

    # =====================================================
    # Editing
    # =====================================================

    def free_name(self, prefix):
        """P1, P2..., L1..., C1..., W1...: the first one not taken in this sketch."""
        taken = {p.name for p in self.points} | {e.name for e in self.lines + self.circles + self.polylines}
        n = 1
        while f"{prefix}{n}" in taken:
            n += 1
        return f"{prefix}{n}"

    def kind_of(self, name):
        """
        "point", "line", "circle", "polyline", or "segment" (a polyline's
        center segment, "W1.2": the second one); KeyError if there is none.
        """
        for kind, items in (("point", self.points), ("line", self.lines), ("circle", self.circles),
                            ("polyline", self.polylines)):
            if any(item.name == name for item in items):
                return kind
        owner, _, index = name.rpartition(".")
        if owner and index.isdigit() and any(w.name == owner and 1 <= int(index) < len(w.points)
                                             for w in self.polylines):
            return "segment"
        raise KeyError(name)

    def polyline(self, name):
        return next(w for w in self.polylines if w.name == name)

    def add_polyline(self, start, end, width):
        """A new polyline from point `start` to point `end`, both with the width `width` (a formula)."""
        if start == end:
            raise FeatureError(f"{self.name}: a polyline needs two different points")
        for p in (start, end):
            self.point(p)
        name = self.free_name("W")
        self.polylines.append(SketchPolyline(name, [start, end], [str(width)] * 2, str(width)))
        return name

    def extend_polyline(self, name, point):
        """Appends a point at its end; it gets the polyline's width (its text, then its own)."""
        self.point(point)
        w = self.polyline(name)
        w.points.append(point)
        w.widths.append(w.width)
        w.bends.append("normal")
        w.radii.append("")

    def insert_into_polyline(self, name, index, point, width):
        """A point put into the center line before point `index` (1..N-1), with this width (a formula)."""
        self.point(point)
        w = self.polyline(name)
        if not 1 <= index < len(w.points):
            raise FeatureError(f"{self.name}: polyline {w.name}: no segment before point {index}")
        w.points.insert(index, point)
        w.widths.insert(index, str(width))
        w.bends.insert(index, "normal")
        w.radii.insert(index, "")

    def polyline_widths(self, point):
        """[(polyline, index)] where a point is on a polyline's center line."""
        return [(w, i) for w in self.polylines for i, p in enumerate(w.points) if p == point]

    def point(self, name):
        return next(p for p in self.points if p.name == name)

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
            self.point(p)
        name = self.free_name("L")
        self.lines.append(SketchLine(name, start, end, construction))
        return name

    def add_circle(self, center, radius, construction=False):
        self.point(center)
        name = self.free_name("C")
        self.circles.append(SketchCircle(name, center, str(radius), construction))
        return name

    def curve(self, name):
        """A line or a circle by name."""
        return next(e for e in self.lines + self.circles if e.name == name)

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
        return [self.circle(name).center]

    def _used_points(self):
        return ({e for line in self.lines for e in (line.start, line.end)} | {c.center for c in self.circles}
                | {p for w in self.polylines for p in w.points})

    def delete(self, name):
        """
        Deletes a point (with the lines and circles that use it; out of a
        polyline, whose pieces then join), a line, a circle or a polyline
        (with its points if nothing else uses them and they are not explicit,
        as Inventor does), or a polyline's center segment ("W1.2": the
        polyline splits in two there, each keeping its own ends; a piece of
        one point goes).
        """
        kind = self.kind_of(name)
        if kind == "point":
            self.lines = [line for line in self.lines if name not in (line.start, line.end)]
            self.circles = [c for c in self.circles if c.center != name]
            for w in self.polylines:
                w.take([i for i, p in enumerate(w.points) if p != name])
            self.polylines = [w for w in self.polylines if len(w.points) >= 2]
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
        used = self._used_points()
        self.points = [p for p in self.points if p.explicit or p.name in used or p.name not in mine]

    # =====================================================
    # Build
    # =====================================================

    def _frame(self, ctx):
        """Where the sketch lies: an origin plane, or a feature's planar face (its frame, normal outward)."""
        name = self["plane"]
        feature, _, face = name.partition(":")
        if face:
            built = ctx.outputs.get(feature)
            if built is None or not hasattr(built, "face_frame"):
                raise ValueError(f"{feature!r} has no faces to sketch on")
            return built.face_frame(face)
        try:
            datum = ctx.part.datum(name)
        except KeyError:
            raise ValueError(f"{name!r} is not a plane") from None
        if datum.kind != "plane":
            raise ValueError(f"{name!r} is a work {datum.kind}, not a plane")
        return SketchFrame(datum.origin.copy(), datum.u.copy(), datum.v.copy(), datum.direction.copy())

    def build(self, ctx):
        frame = self._frame(ctx)
        points = {}
        for p in self.points:
            points[p.name] = np.array([ctx.evaluate(p.x, f"point {p.name}"), ctx.evaluate(p.y, f"point {p.name}")])
        radii = {}
        for c in self.circles:
            radii[c.name] = ctx.evaluate(c.radius, f"circle {c.name}: the radius")
            if radii[c.name] <= 0:
                raise ValueError(f"circle {c.name}: the radius must be > 0, it is {radii[c.name]:g} mm")
        outlines, broken = {}, []
        for w in self.polylines:
            widths = [ctx.evaluate(t, f"polyline {w.name}: the width at {p}") for p, t in zip(w.points, w.widths)]
            bend_radii = [ctx.evaluate(t, f"polyline {w.name}: the bend radius at {p}") if t.strip() else None
                          for p, t in zip(w.points, w.radii)]
            try:
                outlines[w.name] = outline([points[p] for p in w.points], widths, w.start, w.end, w.bends,
                                           bend_radii)
            except TraceError as error:
                broken.append(f"polyline {w.name}: {error}")    # no outline; its center line still shows
        result = SketchResult(frame, points, list(self.lines), {p.name for p in self.points if p.explicit},
                              list(self.circles), radii, list(self.polylines), outlines)
        if broken:
            raise PartialBuild("; ".join(broken), result)
        return result


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
        raise FeatureError(f"{owner}: {where}: radii must be a list of formulas (\"\": half the width)")
    return SketchPolyline(_text(owner, where, row, "name"), list(points),
                          [t if isinstance(t, str) else repr(t) for t in widths],
                          _text(owner, where, row, "width", True) if "width" in row else "1",
                          row.get("start", "flat"), row.get("end", "flat"), bends,
                          None if radii is None else [t if isinstance(t, str) else repr(t) for t in radii])


def _circle(owner, i, row):
    where = _where("circle", i, row)
    _keys(owner, where, row, ("name", "center", "radius", "construction"))
    return SketchCircle(_text(owner, where, row, "name"), _text(owner, where, row, "center"),
                        _text(owner, where, row, "radius", True), _flag(owner, where, row, "construction"))
