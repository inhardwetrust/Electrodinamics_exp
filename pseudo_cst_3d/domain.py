# domain.py

from dataclasses import dataclass


@dataclass(frozen=True)
class SimulationDomain2D:
    """
    Fixed physical area covered by one solver run.
    """

    x_min: float
    x_max: float
    y_min: float
    y_max: float

    def __post_init__(self):
        if self.x_max <= self.x_min:
            raise ValueError("x_max must be greater than x_min")

        if self.y_max <= self.y_min:
            raise ValueError("y_max must be greater than y_min")

    @classmethod
    def centered(cls, width, height, center=(0.0, 0.0)):
        cx, cy = center

        return cls(
            x_min=cx - width / 2.0,
            x_max=cx + width / 2.0,
            y_min=cy - height / 2.0,
            y_max=cy + height / 2.0,
        )

    @property
    def width(self):
        return self.x_max - self.x_min

    @property
    def height(self):
        return self.y_max - self.y_min

    @property
    def aspect(self):
        return self.width / self.height
