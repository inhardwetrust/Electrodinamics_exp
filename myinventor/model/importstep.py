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
# A file that changed since (another sha256): its bodies are built from it,
# but the feature is marked failed (Reload accepts the new content); a
# different number of bodies, or no file: it fails.

from dataclasses import dataclass
import os

from model.features import Feature, FeatureError, Field, PartialBuild, feature_type


@dataclass
class ImportResult:
    bodies: list            # the body names it made
    path: str               # the file read


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
        result = ImportResult(list(self.bodies), path)
        sha = ctx.kernel.file_sha256(path)
        if self["sha256"] and sha != self["sha256"]:
            raise PartialBuild(f"{self['file']} changed since it was imported (built from it as it is now): "
                               "Reload accepts it", result)
        return result


def body_names(prefix, read):
    """The names an import gives the bodies read from a file: "<prefix>:<their name>"."""
    return [f"{prefix}:{name}" for name, *_ in read]
