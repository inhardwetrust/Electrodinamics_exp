# scene3d.py
#
# 3D scene objects. Materials are shared with 2D (scene_model.Material).
# Later: imported STEP bodies become one more region type with contains().

from dataclasses import dataclass
from typing import Tuple

from scene_model import Material  # noqa: F401  (re-exported for 3D scenes)

Vec3 = Tuple[float, float, float]


@dataclass
class Port3D:
    """Lumped port: current driven along the segment a -> b."""

    a: Vec3
    b: Vec3
    name: str = "port"


@dataclass
class BoxRegion:
    """Axis-aligned box filled with a material (later objects win)."""

    lo: Vec3
    hi: Vec3
    material: Material
    name: str = ""

    def contains(self, x, y, z):
        return (
            (x >= self.lo[0]) & (x <= self.hi[0])
            & (y >= self.lo[1]) & (y <= self.hi[1])
            & (z >= self.lo[2]) & (z <= self.hi[2])
        )


@dataclass
class SphereRegion:
    """Sphere filled with a material (later objects win)."""

    center: Vec3
    radius: float
    material: Material
    name: str = ""

    def contains(self, x, y, z):
        dx = x - self.center[0]
        dy = y - self.center[1]
        dz = z - self.center[2]
        return dx * dx + dy * dy + dz * dz <= self.radius * self.radius
