# slicing.py
#
# Slice planes: the bridge between 3D fields and the 2D frontend.
#
# A SlicePlane is axis-aligned (normal along x, y or z) for now; rotated
# planes later only need a different (origin, u, v) frame here - nothing
# downstream depends on the plane being axis-aligned.
#
# Plane coordinates (u, v) are drawn as screen (right, up):
#     normal z -> (u, v) = (x, y)
#     normal y -> (u, v) = (x, z)
#     normal x -> (u, v) = (y, z)

import math
from dataclasses import dataclass

import numpy as np

from domain import SimulationDomain2D
from field import FieldSource, GridArray, Quantity
from field3d import AXIS_INDEX
from mesh import RectangularMesh2D
from scene3d import BoxRegion, Port3D, SphereRegion
from scene_model import DiskRegion, Port, RectRegion, SceneModel

PLANE_AXES = {"z": ("x", "y"), "y": ("x", "z"), "x": ("y", "z")}


@dataclass(frozen=True)
class SlicePlane:
    normal: str          # "x" | "y" | "z"
    position: float      # world coordinate along the normal
    bounds: tuple        # ((x0, y0, z0), (x1, y1, z1)) of the 3D domain
    h: float             # sampling step in the plane (= solver cell)

    @property
    def u_axis(self):
        return PLANE_AXES[self.normal][0]

    @property
    def v_axis(self):
        return PLANE_AXES[self.normal][1]

    def axis_range(self, axis):
        lo, hi = self.bounds
        a = AXIS_INDEX[axis]
        return lo[a], hi[a]

    def to_3d(self, u, v):
        """Plane coordinates -> 3D points (..., 3)."""
        u = np.asarray(u, dtype=np.float64)
        v = np.asarray(v, dtype=np.float64)
        points = np.empty(u.shape + (3,), dtype=np.float64)
        points[..., AXIS_INDEX[self.u_axis]] = u
        points[..., AXIS_INDEX[self.v_axis]] = v
        points[..., AXIS_INDEX[self.normal]] = self.position
        return points

    def lattice(self):
        """Cell-center lattice of the plane: (u0, v0, nu, nv)."""
        u_lo, u_hi = self.axis_range(self.u_axis)
        v_lo, v_hi = self.axis_range(self.v_axis)
        nu = max(2, int(round((u_hi - u_lo) / self.h)))
        nv = max(2, int(round((v_hi - v_lo) / self.h)))
        return u_lo + 0.5 * self.h, v_lo + 0.5 * self.h, nu, nv

    def domain_2d(self):
        u_lo, u_hi = self.axis_range(self.u_axis)
        v_lo, v_hi = self.axis_range(self.v_axis)
        return SimulationDomain2D(u_lo, u_hi, v_lo, v_hi)

    def mesh(self):
        d = self.domain_2d()
        return RectangularMesh2D(
            d,
            max(1, int(round(d.width / self.h))),
            max(1, int(round(d.height / self.h))),
        )

    def describe(self):
        return (
            f"{self.normal} = {self.position:+.3f}   "
            f"(horizontal: {self.u_axis}, vertical: {self.v_axis})"
        )


# =====================================================
# Field on a plane
# =====================================================

class SliceSource(FieldSource):
    """
    2D FieldSource showing a Field3D on a SlicePlane.

    Every quantity is resampled (trilinear) onto the plane's cell-center
    lattice, so components from different Yee positions line up.

    Vectors are PROJECTED onto the plane (what the 2D arrows can show);
    the part sticking out of the plane is a separate signed scalar
    "<vector> normal" (+ = along the positive normal axis).
    """

    VECTOR_LABELS = {
        "E": "Electric field (in-plane part)",
        "H": "Magnetic field (in-plane part)",
        "S": "Poynting vector (in-plane part)",
    }

    # Sign of the "normal" component that points OUT of the screen
    # (screen right x up = u x v):  z: x*y = +z,  y: x*z = -y,  x: y*z = +x.
    TOWARD_VIEWER = {"z": 1.0, "y": -1.0, "x": 1.0}

    def __init__(self, field3d, plane):
        self.field3d = field3d
        self.plane = plane
        self.normal_toward_viewer = self.TOWARD_VIEWER[plane.normal]

        u0, v0, nu, nv = plane.lattice()
        self._lattice = (u0, v0, nu, nv)

        uu, vv = np.meshgrid(
            u0 + np.arange(nu) * plane.h,
            v0 + np.arange(nv) * plane.h,
        )
        self._points3d = plane.to_3d(uu, vv)
        self._cache = {}

    # -------------------------------------------------

    def quantities(self):
        names = [name for name in self.field3d.vectors]
        out = []

        for name in names:
            out.append(Quantity(name, "vector", label=self.VECTOR_LABELS.get(name, name)))

        for name in names:
            out.append(Quantity(f"|{name}|", "scalar", signed=False, label=f"|{name}| (full 3D magnitude)"))
            out.append(Quantity(f"{name} normal", "scalar", signed=True, label=f"{name} component along the plane normal"))

        for name in names:
            for c in self.field3d.vectors[name]:
                if c in self.field3d.arrays or c in self._derived_components():
                    out.append(Quantity(c, "scalar", signed=True, label=c))

        for name in ("u", "eps_r", "div E"):
            if name in self.field3d.arrays:
                out.append(Quantity(name, "scalar", signed=(name == "div E"), label=name))

        return out

    def _derived_components(self):
        return ("Sx", "Sy", "Sz") if "S" in self.field3d.vectors else ()

    # -------------------------------------------------

    def _raw(self, name):
        """Plane values (nv, nu) of a named 3D scalar, cached."""
        if name not in self._cache:
            if name in self.field3d.arrays:
                values = self.field3d.sample(name, self._points3d)
            elif name in ("Sx", "Sy", "Sz"):
                ex, ey, ez = (self._raw(c) for c in self.field3d.vectors["E"])
                hx, hy, hz = (self._raw(c) for c in self.field3d.vectors["H"])
                values = {
                    "Sx": ey * hz - ez * hy,
                    "Sy": ez * hx - ex * hz,
                    "Sz": ex * hy - ey * hx,
                }[name]
            else:
                raise KeyError(name)

            self._cache[name] = values

        return self._cache[name]

    def _components(self, vector):
        return [self._raw(c) for c in self.field3d.vectors[vector]]

    def _plane_values(self, name):
        """Plane values for any exposed scalar (or hidden in-plane part)."""
        plane = self.plane
        ui, vi, ni = (AXIS_INDEX[plane.u_axis], AXIS_INDEX[plane.v_axis], AXIS_INDEX[plane.normal])

        if name.startswith("|") and name.endswith("|"):
            comps = self._components(name[1:-1])
            return np.sqrt(sum(c * c for c in comps))

        if name.endswith(" normal"):
            return self._components(name[: -len(" normal")])[ni]

        if name.endswith(" (u)") or name.endswith(" (v)"):
            vector = name[:-4]
            return self._components(vector)[ui if name.endswith("(u)") else vi]

        return self._raw(name)

    def grid_array(self, name):
        if name in self.field3d.vectors:
            return None

        key = ("grid", name)
        if key not in self._cache:
            u0, v0, _, _ = self._lattice
            self._cache[key] = GridArray(
                self._plane_values(name).astype(np.float32),
                x0=u0, y0=v0, dx=self.plane.h, dy=self.plane.h,
            )
        return self._cache[key]

    def sample(self, name, points):
        if name in self.field3d.vectors:
            return np.stack(
                (
                    self.grid_array(f"{name} (u)").sample(points),
                    self.grid_array(f"{name} (v)").sample(points),
                ),
                axis=-1,
            )

        self.quantity(name)  # unknown names -> KeyError with the valid ones
        return self.grid_array(name).sample(points)


# =====================================================
# Scene and overlays on a plane
# =====================================================

def slice_scene(scene3d, plane, tolerance=None):
    """
    2D cross-section of a 3D scene for the 2D renderer:
        BoxRegion    -> RectRegion   (if the plane cuts the box)
        SphereRegion -> DiskRegion   (radius of the circular cut)
        Port3D       -> Port         (segment lying in the plane), or a
                        zero-length Port marking where it pierces the plane
    """
    tol = 0.5 * plane.h if tolerance is None else tolerance
    n = AXIS_INDEX[plane.normal]
    ui, vi = AXIS_INDEX[plane.u_axis], AXIS_INDEX[plane.v_axis]
    p = plane.position
    out = SceneModel()

    for obj in scene3d.objects:
        if isinstance(obj, BoxRegion):
            if obj.lo[n] - tol <= p <= obj.hi[n] + tol:
                out.add(RectRegion(
                    obj.lo[ui], obj.lo[vi], obj.hi[ui], obj.hi[vi],
                    obj.material, name=obj.name,
                ))

        elif isinstance(obj, SphereRegion):
            d = abs(p - obj.center[n])
            if d < obj.radius:
                out.add(DiskRegion(
                    (obj.center[ui], obj.center[vi]),
                    math.sqrt(obj.radius ** 2 - d ** 2),
                    obj.material, name=obj.name,
                ))

        elif isinstance(obj, Port3D):
            a, b = np.asarray(obj.a, float), np.asarray(obj.b, float)

            if abs(a[n] - p) <= tol and abs(b[n] - p) <= tol:
                out.add(Port((a[ui], a[vi]), (b[ui], b[vi]), name=obj.name))
            elif (a[n] - p) * (b[n] - p) < 0.0:
                t = (p - a[n]) / (b[n] - a[n])
                c = a + t * (b - a)
                out.add(Port((c[ui], c[vi]), (c[ui], c[vi]), name=obj.name))

    return out


def slice_frame(frame3d, plane):
    """A 3D box frame (outer/inner box, e.g. the PML) cut by the plane."""
    ui, vi = AXIS_INDEX[plane.u_axis], AXIS_INDEX[plane.v_axis]
    (olo, ohi), (ilo, ihi) = frame3d["outer"], frame3d["inner"]
    n = AXIS_INDEX[plane.normal]

    # Inside the PML along the normal: the whole plane is absorbing layer.
    if not (ilo[n] <= plane.position <= ihi[n]):
        inner = (olo[ui], olo[vi], olo[ui], olo[vi])
    else:
        inner = (ilo[ui], ilo[vi], ihi[ui], ihi[vi])

    return {
        "kind": "frame",
        "label": frame3d.get("label", ""),
        "outer": (olo[ui], olo[vi], ohi[ui], ohi[vi]),
        "inner": inner,
    }
