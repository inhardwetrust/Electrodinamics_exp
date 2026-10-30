# importstep.py
#
# The Import feature: the bodies of a STEP file (a connector, a housing)
# as bodies of the part.
#
#   [[feature]]
#   name = "SMA"
#   type = "import"
#   file = "board.files/sma.step"     # relative to the part file's folder (copied there on import)
#   sha256 = "..."                    # the file's content when imported (or reloaded)
#   bodies = ["SMA:Shell", "SMA:Insulator", "SMA:Pin"]    # one per body of the file, in its order
#
# Each body of the file (each solid; a sheet: a shell or face outside a
# solid; solids named like their part: one body) becomes the body named at
# its place in `bodies` (renaming one renames it there), colored as in the
# file unless the part colors it ([[body]]). The file is read through
# ctx.kernel (geometry/step_io.py read_bodies), once per content.
# Its bodies' planar faces carry names a sketch can sit on (before the
# placement): "face.<i>.<k>", face k (the explorer's order) of its body i
# (1-based, in `bodies`), as the file has it: a sketch "SMA:face.2.12" is on
# the face's center, its normal outward (ImportResult.face_frame).
# A file that changed since (another sha256): its bodies are built from it,
# but the feature is marked failed (Reload accepts the new content); a
# different number of bodies, or no file: it fails.

from dataclasses import dataclass, field
import os

import numpy as np

from model.features import Feature, FeatureError, Field, PartialBuild, feature_type

MM = 1e-3


@dataclass
class ImportResult:
    bodies: list            # the body names it made
    path: str               # the file read
    planes: dict = field(default_factory=dict)  # "face.<i>.<k>" -> (center (3,) m, u (3,), normal (3,) outward)
    shapes: list = field(default_factory=list)  # its bodies as read (OCP, mm), in `bodies`' order

    def face_frame(self, role):
        """The SketchFrame of a sketch on planar face `role` ("face.2.12"): its center, its normal outward."""
        from model.sketch import SketchFrame

        if role not in self.planes:
            raise ValueError(f"no planar face {role!r} (a face of a body of the import, 'face.<body>.<face>')")
        origin, u, normal = self.planes[role]
        return SketchFrame(origin.copy(), u.copy(), np.cross(normal, u), normal.copy())

    def face_planes(self):
        """{role: (point (3,) m, outward normal (3,))} of its bodies' planar faces."""
        return {role: (origin, normal) for role, (origin, _, normal) in self.planes.items()}


@feature_type
class Import(Feature):
    TYPE = "import"
    FIELDS = (
        Field("file", "text", doc="the STEP file, relative to the part file's folder"),
        Field("sha256", "text", "", doc="the file's content when imported or reloaded"),
    )

    def __init__(self, name, bodies=(), **values):
        super().__init__(name, **values)
        self.bodies = list(bodies)
        if not self.values["file"].strip():
            raise FeatureError(f"{name}: no file")
        if not self.bodies or len(set(self.bodies)) != len(self.bodies) or not all(
                isinstance(b, str) and b.strip() for b in self.bodies):
            raise FeatureError(f"{name}: bodies must be the names of its bodies, each once")

    @classmethod
    def from_dict(cls, data):
        data = dict(data)
        name = data.pop("name")
        data.pop("type", None)
        bodies = data.pop("bodies", None)
        if not isinstance(bodies, list) or not all(isinstance(b, str) for b in bodies):
            raise FeatureError(f"{name}: bodies must be a list of body names")
        return cls(name, bodies=bodies, **data)

    def to_dict(self):
        data = super().to_dict()
        data["bodies"] = list(self.bodies)
        return data

    def made_bodies(self):
        return list(self.bodies)

    def rename_body(self, old, new):
        self.bodies = [new if b == old else b for b in self.bodies]

    def path(self, folder):
        return os.path.normpath(os.path.join(folder or "", self["file"]))

    def build(self, ctx):
        path = self.path(ctx.folder)
        if not os.path.isfile(path):
            raise ValueError(f"no file {self['file']} (next to the part file)")
        read = ctx.kernel.read_step(path)
        if len(read) != len(self.bodies):
            raise ValueError(f"{self['file']} has {len(read)} bodies, the part names {len(self.bodies)}: "
                             "Reload takes the file as it is")
        taken = [b for b in self.bodies if b in ctx.shapes]
        if taken:
            raise ValueError(f"a body {taken[0]!r} exists already")
        for name, (_, _, shape, color) in zip(self.bodies, read):
            ctx.shapes[name] = shape
            if color is not None:
                ctx.colors[name] = color
        result = ImportResult(list(self.bodies), path, shapes=[shape for _, _, shape, _ in read])
        for i, (_, _, shape, _) in enumerate(read, 1):
            for k, center, u, normal in ctx.kernel.planar_faces(shape):
                result.planes[f"face.{i}.{k}"] = (center * MM, u, normal)
        sha = ctx.kernel.file_sha256(path)
        if self["sha256"] and sha != self["sha256"]:
            raise PartialBuild(f"{self['file']} changed since it was imported (built from it as it is now): "
                               "Reload accepts it", result)
        return result


def body_names(prefix, read):
    """The names an import gives the bodies read from a file: "<prefix>:<their name>"."""
    return [f"{prefix}:{name}" for name, *_ in read]
