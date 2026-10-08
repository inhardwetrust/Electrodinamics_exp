# field.py

from dataclasses import dataclass

import numpy as np

from scene_model import PointCharge


# =====================================================
# Physics helpers (shared by analytic source and solver)
# =====================================================

def point_charge_e_field(charges, points, k, softening_radius):
    """
    E = k * q * r / |r|^3, summed over charges, at arbitrary points.

    points: array (..., 2). Returns float64 array (..., 2).
    A softening radius avoids the singularity at the charge position.
    """
    points = np.asarray(points, dtype=np.float64)
    e = np.zeros_like(points)
    softening2 = float(softening_radius) ** 2

    for charge in charges:
        delta = points - np.asarray(charge.position, dtype=np.float64)

        r2 = np.maximum(
            np.einsum("...i,...i->...", delta, delta),
            softening2,
        )

        e += (
            (k * float(charge.charge))
            * delta
            / (r2 * np.sqrt(r2))[..., np.newaxis]
        )

    return e


def point_charge_potential(charges, points, k, softening_radius):
    """V = k * q / |r|, summed over charges. Returns float64 array (...)."""
    points = np.asarray(points, dtype=np.float64)
    v = np.zeros(points.shape[:-1], dtype=np.float64)
    softening2 = float(softening_radius) ** 2

    for charge in charges:
        delta = points - np.asarray(charge.position, dtype=np.float64)

        r2 = np.maximum(
            np.einsum("...i,...i->...", delta, delta),
            softening2,
        )

        v += (k * float(charge.charge)) / np.sqrt(r2)

    return v


# =====================================================
# Contract between field data and the frontend
# =====================================================

@dataclass(frozen=True)
class Quantity:
    """
    A named field quantity a source can provide.

    kind:   "scalar" -> sample() returns (...)
            "vector" -> sample() returns (..., 2)
    signed: scalar that changes sign (Ex, V, later Hz) -> diverging palette.
    """

    name: str
    kind: str
    signed: bool = False
    label: str = ""
    unit: str = ""


class FieldSource:
    """
    The only thing the frontend knows about field data.

    The frontend decides WHERE to look (from the camera), the source
    answers WHAT a named quantity is there. Solvers (own or external),
    analytic models and recorded results all plug in through this.
    """

    def quantities(self):
        """List of Quantity this source provides."""
        raise NotImplementedError

    def quantity(self, name):
        for q in self.quantities():
            if q.name == name:
                return q

        available = ", ".join(q.name for q in self.quantities())
        raise KeyError(f"Unknown quantity {name!r}; available: {available}")

    def sample(self, name, points):
        """
        points: array (..., 2) in world coordinates.
        Returns (...) for scalars or (..., 2) for vectors;
        NaN where the quantity is undefined (e.g. outside a solver domain).
        """
        raise NotImplementedError

    def glsl_scalar(self, name):
        """
        Optional GPU path: GLSL source defining `float field_scalar(vec2 p)`
        for the scalar quantity `name`. None -> not available.
        """
        return None

    def grid_array(self, name):
        """
        Optional native-grid path: the quantity on the source's own grid
        as a GridArray. The frontend uploads it as a texture and colors it
        on the GPU (no CPU resampling) - the fast path for time-stepping
        solvers. None -> not available.
        """
        return None


# =====================================================
# Analytic point charges
# =====================================================

class AnalyticPointChargeField(FieldSource):
    """Exact superposition field of the point charges in the scene."""

    _QUANTITIES = (
        Quantity("E", "vector", label="Electric field"),
        Quantity("|E|", "scalar", signed=False, label="|E|"),
        Quantity("Ex", "scalar", signed=True, label="Ex"),
        Quantity("Ey", "scalar", signed=True, label="Ey"),
        Quantity("V", "scalar", signed=True, label="Potential"),
    )

    _GLSL_SCALARS = {
        "|E|": "length(field_e(p))",
        "Ex": "field_e(p).x",
        "Ey": "field_e(p).y",
        "V": "field_v(p)",
    }

    def __init__(self, scene_model, k=1.0, softening_radius=1e-3):
        self.scene_model = scene_model
        self.k = float(k)
        self.softening_radius = float(softening_radius)

    def _charges(self):
        return self.scene_model.get_objects(PointCharge)

    def quantities(self):
        return list(self._QUANTITIES)

    def sample(self, name, points):
        if name == "V":
            return point_charge_potential(
                self._charges(), points, self.k, self.softening_radius
            )

        e = point_charge_e_field(
            self._charges(), points, self.k, self.softening_radius
        )

        if name == "E":
            return e
        if name == "|E|":
            return np.linalg.norm(e, axis=-1)
        if name == "Ex":
            return e[..., 0]
        if name == "Ey":
            return e[..., 1]

        return self.quantity(name)  # raises KeyError with the valid names

    def glsl_scalar(self, name):
        if name not in self._GLSL_SCALARS:
            return None

        # Charges are baked in as constants; the GPU visual is rebuilt
        # when the scene changes. "%.9e" always yields a valid GLSL float.
        e_terms = []
        v_terms = []

        for c in self._charges():
            kq = self.k * float(c.charge)
            pos = "vec2(%.9e, %.9e)" % (c.position[0], c.position[1])
            e_terms.append("    e += %.9e * point_charge_e(p, %s);" % (kq, pos))
            v_terms.append("    v += %.9e * point_charge_v(p, %s);" % (kq, pos))

        softening2 = "%.9e" % (self.softening_radius ** 2)

        return """
vec2 point_charge_e(vec2 p, vec2 c) {
    vec2 d = p - c;
    float r2 = max(dot(d, d), __S2__);
    return d / (r2 * sqrt(r2));
}

float point_charge_v(vec2 p, vec2 c) {
    vec2 d = p - c;
    return 1.0 / sqrt(max(dot(d, d), __S2__));
}

vec2 field_e(vec2 p) {
    vec2 e = vec2(0.0, 0.0);
__E_TERMS__
    return e;
}

float field_v(vec2 p) {
    float v = 0.0;
__V_TERMS__
    return v;
}

float field_scalar(vec2 p) {
    return __BODY__;
}
""".replace("__S2__", softening2) \
   .replace("__E_TERMS__", "\n".join(e_terms)) \
   .replace("__V_TERMS__", "\n".join(v_terms)) \
   .replace("__BODY__", self._GLSL_SCALARS[name])


# =====================================================
# Gridded data (numerical solvers)
# =====================================================

@dataclass
class GridArray:
    """
    One quantity on its own regular grid.

    values: (ny, nx) scalar or (ny, nx, C) components; row 0 is the lowest y.
    (x0, y0): world position of values[0, 0].

    Each quantity carries its own origin, so staggered layouts (cell
    centers vs nodes now, Yee half-cell offsets later) are exact.
    """

    values: np.ndarray
    x0: float
    y0: float
    dx: float
    dy: float

    def __post_init__(self):
        self.values = np.asarray(self.values, dtype=np.float32)
        ny, nx = self.values.shape[:2]

        if nx < 2 or ny < 2:
            raise ValueError("GridArray needs at least 2x2 samples")

        # Flat (N, C): np.take on flat indices is much faster than
        # 2D fancy indexing for large sample sets.
        self._flat = np.ascontiguousarray(
            self.values.reshape(ny * nx, -1)
        )

    def sample(self, points):
        """Bilinear interpolation; NaN outside the sampled area."""
        points = np.asarray(points, dtype=np.float32)
        ny, nx = self.values.shape[:2]

        fx = (points[..., 0] - np.float32(self.x0)) / np.float32(self.dx)
        fy = (points[..., 1] - np.float32(self.y0)) / np.float32(self.dy)

        inside = (
            (fx >= 0.0) & (fx <= nx - 1)
            & (fy >= 0.0) & (fy <= ny - 1)
        )

        i = np.clip(np.floor(fx).astype(np.int32), 0, nx - 2)
        j = np.clip(np.floor(fy).astype(np.int32), 0, ny - 2)

        tx = np.clip(fx - i, 0.0, 1.0)[..., np.newaxis]
        ty = np.clip(fy - j, 0.0, 1.0)[..., np.newaxis]

        k00 = j * nx + i
        v = self._flat

        out = (
            np.take(v, k00, axis=0) * ((1.0 - tx) * (1.0 - ty))
            + np.take(v, k00 + 1, axis=0) * (tx * (1.0 - ty))
            + np.take(v, k00 + nx, axis=0) * ((1.0 - tx) * ty)
            + np.take(v, k00 + nx + 1, axis=0) * (tx * ty)
        )

        out[~inside] = np.nan

        if self.values.ndim == 2:
            out = out[..., 0]

        return out


class GridFieldSource(FieldSource):
    """
    Quantities stored on grids, plus quantities derived from them.

    arrays:  {name: GridArray}
    derived: {name: (base_name, fn)} -> fn(base samples), e.g. Ex from E.
    vectors: {name: (x_name, y_name)} -> vector assembled from two scalar
             components that may live on DIFFERENT grids (Yee: Ex and Ey
             sit on different cell edges); each is interpolated on its own.
    """

    def __init__(self, quantities, arrays, derived=None, vectors=None):
        self._quantities = list(quantities)
        self.arrays = dict(arrays)
        self.derived = dict(derived or {})
        self.vectors = dict(vectors or {})

    def quantities(self):
        return list(self._quantities)

    def grid_array(self, name):
        if name in self.arrays:
            return self.arrays[name]

        if name in self.derived:
            base_name, fn = self.derived[name]
            base = self.grid_array(base_name)

            if base is None:
                return None

            # Derived values live where their base lives (same geometry).
            return GridArray(
                values=fn(base.values),
                x0=base.x0,
                y0=base.y0,
                dx=base.dx,
                dy=base.dy,
            )

        return None

    def sample(self, name, points):
        if name in self.arrays:
            return self.arrays[name].sample(points)

        if name in self.vectors:
            x_name, y_name = self.vectors[name]
            return np.stack(
                (self.sample(x_name, points), self.sample(y_name, points)),
                axis=-1,
            )

        if name in self.derived:
            base_name, fn = self.derived[name]
            return fn(self.sample(base_name, points))

        return self.quantity(name)  # raises KeyError with the valid names


def grid_source_from_point_charge_result(mesh, result):
    """Wrap PointChargeFieldSolver output; every field keeps its own grid."""
    d = mesh.domain

    nodes = dict(x0=d.x_min, y0=d.y_min, dx=mesh.dx, dy=mesh.dy)
    cells = dict(
        x0=d.x_min + 0.5 * mesh.dx,
        y0=d.y_min + 0.5 * mesh.dy,
        dx=mesh.dx,
        dy=mesh.dy,
    )

    return GridFieldSource(
        quantities=AnalyticPointChargeField._QUANTITIES,
        arrays={
            "E": GridArray(result.get_field("E_nodes"), **nodes),
            "|E|": GridArray(result.get_field("E_mag_cells"), **cells),
            "V": GridArray(result.get_field("V_cells"), **cells),
        },
        derived={
            "Ex": ("E", component_x),
            "Ey": ("E", component_y),
        },
    )


def component_x(vectors):
    return vectors[..., 0]


def component_y(vectors):
    return vectors[..., 1]
