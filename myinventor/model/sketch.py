# sketch.py
#
# The 2D sketch feature: a frame on a plane, and its geometry: points and
# lines, as in Inventor or SolveSpace. A line refers to its two end points
# by name, so a polyline is lines sharing points (a closed chain is a
# profile, later), and constraints will attach to the points.
#
#   [[feature]]
#   name = "Sketch1"
#   type = "sketch"
#   plane = "XY Plane"        # an origin plane now; later a work plane or a face
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
#
# An explicit point is a sketch point in its own right (Inventor's Point),
# not just the end of lines: it is drawn as a dot in a gapped cross even
# outside sketch mode, and it stays when the lines on it are deleted.
#
# Its build result is a SketchResult: the frame (where the sketch lies in 3D)
# and the evaluated points (mm). The frame's u and v are the plane's in-plane
# axes, so the sketch's x runs along u, its y along v, and u x v is the
# normal (the side the sketch is seen from).

from dataclasses import dataclass, field

import numpy as np

from model.expressions import ExpressionError, evaluate
from model.features import Feature, FeatureError, Field, feature_type

MM = 1e-3


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


@dataclass
class SketchResult:
    frame: SketchFrame
    points: dict = field(default_factory=dict)      # name -> (2,) mm in the sketch
    lines: list = field(default_factory=list)       # SketchLine
    explicit: set = field(default_factory=set)      # names of the explicit points

    def world_mm(self, name):
        """A point's position in the scene (mm)."""
        return self.frame.to_world(self.points[name] * MM)[0] / MM

    def segments_mm(self):
        """(K, 2, 3) the lines in the scene (mm), in self.lines order."""
        if not self.lines:
            return np.zeros((0, 2, 3))
        return np.array([[self.world_mm(line.start), self.world_mm(line.end)] for line in self.lines])


@feature_type
class Sketch(Feature):
    TYPE = "sketch"
    FIELDS = (
        Field("plane", "ref", doc="the plane the sketch lies on"),
    )

    def __init__(self, name, points=(), lines=(), **values):
        super().__init__(name, **values)
        self.points = list(points)
        self.lines = list(lines)
        names = [p.name for p in self.points] + [line.name for line in self.lines]
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
        return cls(name, points=points, lines=lines, **data)

    def to_dict(self):
        data = super().to_dict()
        if self.points:
            data["point"] = [{"name": p.name, "x": p.x, "y": p.y, **({"explicit": True} if p.explicit else {})}
                             for p in self.points]
        if self.lines:
            data["line"] = [{"name": line.name, "start": line.start, "end": line.end} for line in self.lines]
        return data

    def formulas(self):
        rows = super().formulas()
        for p in self.points:
            rows += [(f"{p.name}.x", p.x), (f"{p.name}.y", p.y)]
        return rows

    def rename_formulas(self, rename):
        super().rename_formulas(rename)
        for p in self.points:
            p.x, p.y = rename(p.x), rename(p.y)

    # =====================================================
    # Editing
    # =====================================================

    def free_name(self, prefix):
        """P1, P2... or L1, L2...: the first one not taken in this sketch."""
        taken = {p.name for p in self.points} | {line.name for line in self.lines}
        n = 1
        while f"{prefix}{n}" in taken:
            n += 1
        return f"{prefix}{n}"

    def point(self, name):
        return next(p for p in self.points if p.name == name)

    def line(self, name):
        return next(line for line in self.lines if line.name == name)

    def add_point(self, x, y, explicit=False):
        name = self.free_name("P")
        self.points.append(SketchPoint(name, str(x), str(y), explicit))
        return name

    def add_line(self, start, end):
        if start == end:
            raise FeatureError(f"{self.name}: a line needs two different points")
        for p in (start, end):
            self.point(p)
        name = self.free_name("L")
        self.lines.append(SketchLine(name, start, end))
        return name

    def delete(self, name):
        """
        Deletes a point (with the lines that use it) or a line (with its end
        points if no other line uses them and they are not explicit, as
        Inventor does).
        """
        if any(p.name == name for p in self.points):
            self.lines = [line for line in self.lines if name not in (line.start, line.end)]
            self.points = [p for p in self.points if p.name != name]
            return
        line = self.line(name)
        self.lines.remove(line)
        used = {e for other in self.lines for e in (other.start, other.end)}
        self.points = [p for p in self.points
                       if p.explicit or p.name in used or p.name not in (line.start, line.end)]

    # =====================================================
    # Build
    # =====================================================

    def build(self, ctx):
        name = self["plane"]
        try:
            datum = ctx.part.datum(name)
        except KeyError:
            raise ValueError(f"{name!r} is not a plane") from None
        if datum.kind != "plane":
            raise ValueError(f"{name!r} is a work {datum.kind}, not a plane")
        frame = SketchFrame(datum.origin.copy(), datum.u.copy(), datum.v.copy(), datum.direction.copy())
        points = {}
        for p in self.points:
            try:
                points[p.name] = np.array([evaluate(p.x, ctx.values), evaluate(p.y, ctx.values)])
            except ExpressionError as error:
                raise ValueError(f"point {p.name}: {error}") from None
        return SketchResult(frame, points, list(self.lines), {p.name for p in self.points if p.explicit})


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


def _point(owner, i, row):
    where = f"point {i}" + (f" ({row['name']})" if isinstance(row.get("name"), str) else "")
    _keys(owner, where, row, ("name", "x", "y", "explicit"))
    explicit = row.get("explicit", False)
    if not isinstance(explicit, bool):
        raise FeatureError(f"{owner}: {where}: explicit must be true or false")
    return SketchPoint(_text(owner, where, row, "name"), _text(owner, where, row, "x", True),
                       _text(owner, where, row, "y", True), explicit)


def _line(owner, i, row):
    where = f"line {i}" + (f" ({row['name']})" if isinstance(row.get("name"), str) else "")
    _keys(owner, where, row, ("name", "start", "end"))
    return SketchLine(_text(owner, where, row, "name"), _text(owner, where, row, "start"),
                      _text(owner, where, row, "end"))
