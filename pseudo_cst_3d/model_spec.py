# model_spec.py
#
# Models are files: models/*.toml. This module reads and validates them;
# model_builder.py turns a ModelSpec into a scene + field source / simulation.
#
# File layout (all tables optional unless the solver needs them):
#
#   name = "Dipole + glass slab"         shown in the model selector
#   description = "..."
#   solver = "analytic" | "grid" | "oscillating" | "fdtd2d" | "fdtd3d"
#
#   [domain]      bounds = [[x0, y0], [x1, y1]]  (3D: [[x0, y0, z0], [x1, y1, z1]])
#                 cell_size = 0.1  or  cells_per_wavelength = 20 (FDTD)
#   [boundary]    kind = "pml" | "mur",  cells = 12                 (FDTD)
#   [excitation]  frequency, waveform, amplitude                    (FDTD)
#                 period, steps_per_period, amplitude               (oscillating)
#   [numerics]    courant (FDTD);  k, softening (point charges)
#   [materials]   glass = { eps_r = 4.0, sigma = 0.0 },  metal = { pec = true }
#   [[objects]]   type = "charge" | "port" | "rect" | "disk" | "box" | "sphere"
#   [view]        height, heatmap, arrows, steps_per_frame,
#                 slice = { normal = "y", position = 0.0 }          (3D)

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from scene3d import BoxRegion, Port3D, SphereRegion
from scene_model import DiskRegion, Material, PointCharge, Port, RectRegion

MODELS_DIR = Path(__file__).resolve().parent / "models"

SOLVERS = ("analytic", "grid", "oscillating", "fdtd2d", "fdtd3d")

TOP_KEYS = {
    "name", "description", "solver", "domain", "boundary",
    "excitation", "numerics", "materials", "objects", "view",
}
TABLE_KEYS = {
    "domain": {"bounds", "cell_size", "cells_per_wavelength"},
    "boundary": {"kind", "cells"},
    "excitation": {"frequency", "waveform", "amplitude", "period", "steps_per_period"},
    "numerics": {"courant", "k", "softening"},
    "view": {"height", "heatmap", "arrows", "steps_per_frame", "slice"},
}
MATERIAL_KEYS = {"eps_r", "sigma", "pec", "name"}

# Object types per dimension and their keys (required, optional).
OBJECT_TYPES = {
    2: {
        "charge": ({"position", "charge"}, {"name"}),
        "port": ({"a", "b"}, {"name"}),
        "rect": ({"lo", "hi", "material"}, {"name"}),
        "disk": ({"center", "radius", "material"}, {"name"}),
    },
    3: {
        "port": ({"a", "b"}, {"name"}),
        "box": ({"lo", "hi", "material"}, {"name"}),
        "sphere": ({"center", "radius", "material"}, {"name"}),
    },
}


class ModelError(ValueError):
    """A model file is invalid; the message says where and why."""


@dataclass
class ModelSpec:
    key: str                    # file stem, used on the command line
    path: Path
    name: str
    description: str
    solver: str
    domain: dict = field(default_factory=dict)
    boundary: dict = field(default_factory=dict)
    excitation: dict = field(default_factory=dict)
    numerics: dict = field(default_factory=dict)
    materials: dict = field(default_factory=dict)     # {name: Material}
    objects: list = field(default_factory=list)       # scene objects
    view: dict = field(default_factory=dict)

    @property
    def dims(self):
        return 3 if self.solver == "fdtd3d" else 2

    @property
    def is_simulation(self):
        return self.solver in ("oscillating", "fdtd2d", "fdtd3d")


# =====================================================
# Discovery
# =====================================================

def list_models(folder=MODELS_DIR):
    """[(key, name, path)] of all model files, sorted by file name."""
    out = []
    for path in sorted(Path(folder).glob("*.toml")):
        try:
            name = tomllib.loads(path.read_text(encoding="utf-8")).get("name", path.stem)
        except tomllib.TOMLDecodeError:
            name = f"{path.stem} (invalid file)"
        out.append((path.stem, name, path))
    return out


def resolve(ref, folder=MODELS_DIR):
    """A model key ("fdtd_dipole") or a path to a .toml file -> Path."""
    candidate = Path(ref)
    if candidate.suffix == ".toml" and candidate.exists():
        return candidate

    path = Path(folder) / f"{ref}.toml"
    if path.exists():
        return path

    keys = ", ".join(key for key, _, _ in list_models(folder))
    raise ModelError(f"Unknown model {ref!r}. Available: {keys}")


def load(ref, folder=MODELS_DIR):
    path = resolve(ref, folder)

    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as error:
        raise ModelError(f"{path.name}: TOML syntax error: {error}") from None

    return parse(data, path)


# =====================================================
# Parsing and validation
# =====================================================

def parse(data, path):
    where = path.name

    def fail(message):
        raise ModelError(f"{where}: {message}")

    unknown = set(data) - TOP_KEYS
    if unknown:
        fail(f"unknown top-level keys {sorted(unknown)}; allowed: {sorted(TOP_KEYS)}")

    solver = data.get("solver")
    if solver not in SOLVERS:
        fail(f"solver must be one of {SOLVERS}, got {solver!r}")

    tables = {}
    for table, allowed in TABLE_KEYS.items():
        value = data.get(table, {})
        if not isinstance(value, dict):
            fail(f"[{table}] must be a table")
        extra = set(value) - allowed
        if extra:
            fail(f"[{table}] unknown keys {sorted(extra)}; allowed: {sorted(allowed)}")
        tables[table] = dict(value)

    materials = {}
    for name, props in data.get("materials", {}).items():
        if not isinstance(props, dict):
            fail(f"material {name!r} must be a table, e.g. {name} = {{ eps_r = 4.0 }}")
        extra = set(props) - MATERIAL_KEYS
        if extra:
            fail(f"material {name!r}: unknown keys {sorted(extra)}; allowed: {sorted(MATERIAL_KEYS)}")
        materials[name] = Material(
            name=props.get("name", name),
            eps_r=float(props.get("eps_r", 1.0)),
            sigma=float(props.get("sigma", 0.0)),
            pec=bool(props.get("pec", False)),
        )
        if materials[name].eps_r <= 0.0:
            fail(f"material {name!r}: eps_r must be positive")

    dims = 3 if solver == "fdtd3d" else 2
    objects = [
        _parse_object(i, obj, dims, materials, fail)
        for i, obj in enumerate(data.get("objects", []))
    ]

    spec = ModelSpec(
        key=path.stem,
        path=path,
        name=str(data.get("name", path.stem)),
        description=str(data.get("description", "")),
        solver=solver,
        materials=materials,
        objects=objects,
        **tables,
    )
    _check_solver_needs(spec, fail)
    return spec


def _vector(value, dims, what, fail):
    if not (isinstance(value, (list, tuple)) and len(value) == dims):
        fail(f"{what} must be a list of {dims} numbers, got {value!r}")
    return tuple(float(v) for v in value)


def _parse_object(index, obj, dims, materials, fail):
    if not isinstance(obj, dict) or "type" not in obj:
        fail(f"objects[{index}] needs a 'type'")

    kind = obj["type"]
    types = OBJECT_TYPES[dims]
    if kind not in types:
        fail(f"objects[{index}]: type {kind!r} is not available in {dims}D; use one of {sorted(types)}")

    required, optional = types[kind]
    keys = set(obj) - {"type"}
    missing = required - keys
    extra = keys - required - optional
    if missing:
        fail(f"objects[{index}] ({kind}): missing {sorted(missing)}")
    if extra:
        fail(f"objects[{index}] ({kind}): unknown keys {sorted(extra)}")

    what = f"objects[{index}] ({kind})"
    name = str(obj.get("name", ""))

    def material():
        ref = obj["material"]
        if ref not in materials:
            fail(f"{what}: unknown material {ref!r}; defined: {sorted(materials)}")
        return materials[ref]

    if kind == "charge":
        return PointCharge(position=_vector(obj["position"], 2, f"{what}.position", fail),
                           charge=float(obj["charge"]))

    if kind == "port":
        a = _vector(obj["a"], dims, f"{what}.a", fail)
        b = _vector(obj["b"], dims, f"{what}.b", fail)
        port_name = name or "port"
        return Port3D(a=a, b=b, name=port_name) if dims == 3 else Port(a=a, b=b, name=port_name)

    if kind in ("rect", "box"):
        lo = _vector(obj["lo"], dims, f"{what}.lo", fail)
        hi = _vector(obj["hi"], dims, f"{what}.hi", fail)
        if any(h <= l for l, h in zip(lo, hi)):
            fail(f"{what}: hi must be greater than lo in every axis")
        if kind == "rect":
            return RectRegion(lo[0], lo[1], hi[0], hi[1], material(), name=name)
        return BoxRegion(lo, hi, material(), name=name)

    center = _vector(obj["center"], dims, f"{what}.center", fail)
    radius = float(obj["radius"])
    if radius <= 0.0:
        fail(f"{what}: radius must be positive")
    if kind == "disk":
        return DiskRegion(center, radius, material(), name=name)
    return SphereRegion(center, radius, material(), name=name)


def _check_solver_needs(spec, fail):
    kinds = [type(obj).__name__ for obj in spec.objects]
    dims = spec.dims
    domain = spec.domain

    if spec.solver in ("analytic", "grid", "oscillating"):
        if "PointCharge" not in kinds:
            fail(f"solver {spec.solver!r} needs at least one charge object")
        if "cell_size" not in domain:
            fail("[domain] cell_size is required (solver grid / display base step)")

    if spec.solver in ("grid", "oscillating", "fdtd2d", "fdtd3d"):
        if "bounds" not in domain:
            fail("[domain] bounds is required")
        bounds = domain["bounds"]
        if not (isinstance(bounds, list) and len(bounds) == 2):
            fail("[domain] bounds must be [[lo...], [hi...]]")
        lo = _vector(bounds[0], dims, "[domain] bounds[0]", fail)
        hi = _vector(bounds[1], dims, "[domain] bounds[1]", fail)
        if any(h <= l for l, h in zip(lo, hi)):
            fail("[domain] bounds: hi must be greater than lo in every axis")
        domain["bounds"] = (lo, hi)

    if spec.solver in ("fdtd2d", "fdtd3d"):
        if "Port" not in kinds and "Port3D" not in kinds:
            fail(f"solver {spec.solver!r} needs at least one port object")
        if ("cell_size" in domain) == ("cells_per_wavelength" in domain):
            fail("[domain] give exactly one of cell_size / cells_per_wavelength")

    slice_spec = spec.view.get("slice")
    if slice_spec is not None:
        if dims != 3:
            fail("[view] slice only applies to 3D models")
        if slice_spec.get("normal") not in ("x", "y", "z"):
            fail("[view] slice.normal must be 'x', 'y' or 'z'")
