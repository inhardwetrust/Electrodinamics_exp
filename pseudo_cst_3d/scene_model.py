# scene_model.py

from dataclasses import dataclass, field
from typing import List, Tuple, Type, TypeVar


@dataclass
class PointCharge:
    """
    Simple scene object for this prototype.
    """

    position: Tuple[float, float]
    charge: float


@dataclass
class Port:
    """
    Lumped excitation port between two points (like a discrete port in CST).

    The solver drives a current along the segment a -> b; the waveform is
    an excitation setting of the simulation, not part of the geometry.
    """

    a: Tuple[float, float]
    b: Tuple[float, float]
    name: str = "port"


@dataclass(frozen=True)
class Material:
    """
    Linear isotropic material (normalized units: eps0 = mu0 = 1).

    eps_r: relative permittivity (wave speed 1 / sqrt(eps_r))
    sigma: electric conductivity (losses)
    pec:   perfect electric conductor (tangential E = 0)
    """

    name: str = "vacuum"
    eps_r: float = 1.0
    sigma: float = 0.0
    pec: bool = False


@dataclass
class RectRegion:
    """Axis-aligned rectangle filled with a material (later objects win)."""

    x_min: float
    y_min: float
    x_max: float
    y_max: float
    material: Material
    name: str = ""

    def contains(self, x, y):
        return (
            (x >= self.x_min) & (x <= self.x_max)
            & (y >= self.y_min) & (y <= self.y_max)
        )


@dataclass
class DiskRegion:
    """Disk filled with a material (later objects win)."""

    center: Tuple[float, float]
    radius: float
    material: Material
    name: str = ""

    def contains(self, x, y):
        dx = x - self.center[0]
        dy = y - self.center[1]
        return dx * dx + dy * dy <= self.radius * self.radius


T = TypeVar("T")


@dataclass
class SceneModel:
    """
    Collection of physical scene objects.
    """

    objects: List[object] = field(default_factory=list)

    def add(self, obj):
        self.objects.append(obj)

    def get_objects(self, object_type: Type[T]) -> List[T]:
        return [
            obj
            for obj in self.objects
            if isinstance(obj, object_type)
        ]
