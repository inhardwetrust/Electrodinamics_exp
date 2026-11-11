# project_file.py
#
# The project of a STEP file lives next to it: model.step -> model.toml.
#
#   # customcst project for patch_2g4.step
#   [bodies.substrate]
#   type = "dielectric"
#   eps_r = 4.3
#   tan_d = 0.025
#   f_ref = 10.0   # GHz
#   mu_r = 1.0
#
#   [bodies.patch]
#   type = "pec"
#
#   [view]
#   up = "y"            # vertical axis of the CAD model (x / y / z)
#   edges = true        # the last display, restored on open (not a result
#   xray = false        #   input: it never outdates a run). The toolbar
#   ref_planes = true   #   switches, the plane (its axis and coordinate,
#   mesh = false        #   mm) and the bodies unticked in the tree.
#   materials = false
#   section = true
#   flip = false
#   plane = "y"
#   at = 25.9
#   hidden = ["board"]
#   camera_center = [24.0, 26.0, 1.5]     # mm, the pivot
#   camera_distance = 30.0                # mm, the zoom (VisPy scale factor)
#   camera_rotation = [1.0, 0.0, ...]     # 3x3 rotation, rows
#
#   [materials.FR4]     # the material library, see materials.py
#   eps_r = 4.3 ...
#
# A dielectric body has `material = "FR4"` (a preset of the library: its
# values are copied into the body, the library wins on load) or "Custom"
# (its own values; also what a file without the key means).
#
#   [holes.diameters]   # through holes (geometry/holes.py) by drill, mm
#   "0.4" = "via"       # "via": a PEC cylinder fills the hole; "none": open
#   [holes.single]      # one hole by its center, mm; beats the diameter rule
#   "-6,0,0.8" = "none"
#
#   [z0path]            # the Z0 path (solver/path_profile.py): a polyline
#   axis = "z"          #   in the plane normal to `axis` at `at` (mm),
#   at = 1.5512         #   points in that plane's two other axes (mm, in
#   half_width = 2.0    #   order: y, z for "x"), each cross-section
#   points = [[27.5, 26.2], [22.4, 26.2]]   # reaching half_width to a side
#   (a display tool like [view]: it never outdates a run). Without it the
#   window makes a default one (default_z0path).
#
#   [simulation] / [mesh]: project-wide settings, see settings.py
#
#   [local_mesh.CAN]    # a body's own, finer mesh (mesh/rectilinear.py step 4b):
#   cells_per_wavelength = 40.0   #   over the body's extent along each axis,
#   cells_per_feature = 8.0       #   between its own faces, at most max_step
#   max_step = 0.0      # mm, 0 = none   (like CST's local mesh properties)
#   Any body may have one, typed or not (an untyped box is a refinement
#   region); it is a result input (in the fingerprint), kept when the type
#   is removed, an orphan when the body is gone.
#
#   [circuit.switch]    # a circuit element on the runs' ports (solver/circuit.py):
#   kind = "t"          #   applied when the runs are shown, not a result
#   enabled = true      #   input (never outdates a run)
#   ports = ["P_pin", "P_rf2"]
#   l1 = 1.7 ...        # the kind's parameters; port_l (nH) for every kind
#
# Bodies without a section have no type yet. Sections whose body is missing
# from the STEP file are kept (the body may come back after a CAD edit) and
# reported as orphans. Missing parameters take their defaults; unknown types,
# unknown keys and invalid values raise ProjectError naming file and body.

import json
import os
import tomllib

from project.materials import BUILTIN, CUSTOM, MATERIAL_PARAMS, builtin_library

HOLE_KINDS = ("via", "none")
HOLE_DIAMETER_TOL_MM = 1e-4     # a rule's drill matches a hole within 0.1 um
HOLE_CENTER_TOL_MM = 1e-3       # a single rule matches a hole center within 1 um
from project.object_types import CONDUCTOR_TYPES, OBJECT_TYPES, Assignment, Param
from project.ports import port_geometry
from project.waveports import waveport_geometry
from project.settings import SECTIONS, check_settings
from solver.circuit import ELEMENT_KINDS, Element, port_count

UP_AXES = ("x", "y", "z")
VIEW_SWITCHES = ("edges", "xray", "ref_planes", "mesh", "materials", "section", "flip")
CAMERA_KEYS = {"camera_center": 3, "camera_rotation": 9}     # number lists and their lengths
VIEW_KEYS = ("up",) + VIEW_SWITCHES + ("plane", "at", "hidden", "camera_center", "camera_distance",
                                      "camera_rotation")
Z0PATH_PARAMS = (
    Param("axis", "plane ⊥", "z", choices=UP_AXES,
          tooltip="The path is drawn in the plane normal to this axis"),
    Param("at", "plane at", 0.0, unit="mm", tooltip="The plane's coordinate along its axis"),
    Param("half_width", "section half width", 2.0, minimum=0.05, unit="mm",
          tooltip="How far each cross-section reaches to either side of the path: wider than "
                  "the trace and its gaps, narrower than the distance to the next line. "
                  "It must reach the ground: at a connector, its legs (half the connector's "
                  "width or a bit more)"),
)


LOCAL_MESH_PARAMS = (
    Param("cells_per_wavelength", "cells per λ", 20.0, minimum=5.0, maximum=400.0,
          tooltip="Over this body's extent along each axis: the step at most λ(f max) / N, λ in the "
                  "body's own material (air for a conductor or an untyped body)"),
    Param("cells_per_feature", "cells per feature", 4.0, minimum=1.0, maximum=200.0,
          tooltip="Between this body's own faces (planar faces normal to the axis and its extent): "
                  "at least N cells across each such gap. A curved body (a can) is one feature across"),
    Param("max_step", "max step (0 = none)", 0.0, minimum=0.0, unit="mm",
          tooltip="Over this body's extent: the step at most this, whatever the rules above give"),
)


class ProjectError(ValueError):
    pass


def project_path(step_path):
    return os.path.splitext(os.fspath(step_path))[0] + ".toml"


class Project:
    def __init__(self, step_path, assignments=None, up="z", settings=None, materials=None,
                 holes=None, view=None, z0path=None, local_mesh=None, circuit=None):
        self.step_path = os.fspath(step_path)
        self.assignments = dict(assignments or {})     # {body name: Assignment}
        self.up = up                                   # vertical axis for the view
        self.view = dict(view or {})                   # last display: [view] keys but up; {} = defaults
        self.z0path = z0path                           # [z0path] as a dict (points: [[u, v], ...]), or None
        self.materials = builtin_library()             # {preset name: {param: value}}
        # Hole rules: {drill mm: kind}, {(x, y, z) center mm: kind}.
        self.hole_diameters = dict((holes or {}).get("diameters", {}))
        self.hole_single = dict((holes or {}).get("single", {}))
        for name, values in (materials or {}).items():
            self.materials[name] = dict(values)
        self.settings = {name: section.defaults() for name, section in SECTIONS.items()}
        for name, values in (settings or {}).items():
            self.settings[name].update(values)
        self.local_mesh = {name: dict(values) for name, values in (local_mesh or {}).items()}   # {body: params}
        self.circuit = dict(circuit or {})             # {element name: solver.circuit.Element}, file order

    @property
    def path(self):
        return project_path(self.step_path)

    # =====================================================
    # Editing (each call validates; the caller saves)
    # =====================================================

    def get(self, body_name):
        return self.assignments.get(body_name)

    def set_type(self, body, type_name):
        """Assigns a type with default parameters (no-op if it already has it)."""
        object_type = OBJECT_TYPES[type_name]

        if body.kind not in object_type.allowed_kinds:
            raise ProjectError(
                f"{object_type.label} cannot be assigned to a {body.kind} body ({body.name})"
            )

        current = self.assignments.get(body.name)
        if current is None or current.type != type_name:
            self.assignments[body.name] = Assignment.new(type_name)
            if type_name == "dielectric":
                self.set_material(body.name, "Default" if "Default" in self.materials else CUSTOM)

    def set_material(self, body_name, material):
        """A dielectric body takes a preset's values, or keeps its own (CUSTOM)."""
        assignment = self.assignments[body_name]
        if assignment.type != "dielectric":
            raise ProjectError(f"{body_name}: only dielectrics have a material")
        if material != CUSTOM and material not in self.materials:
            raise ProjectError(f"{body_name}: unknown material {material!r}")
        assignment.material = material
        if material != CUSTOM:
            assignment.values.update(self.materials[material])

    def material_users(self, material):
        """Names of the bodies that use a preset."""
        return sorted(n for n, a in self.assignments.items()
                      if a.type == "dielectric" and a.material == material)

    # --- the library itself ---------------------------------------------------

    def unique_material_name(self, base):
        """`base`, or `base 2`, `base 3` ... whichever is free."""
        name, n = base, 1
        while name in self.materials or name == CUSTOM:
            n += 1
            name = f"{base} {n}"
        return name

    def _check_new_name(self, name):
        name = str(name).strip()
        if not name:
            raise ProjectError("a material needs a name")
        if name == CUSTOM:
            raise ProjectError(f"{CUSTOM!r} is reserved (a body's own values)")
        if name in self.materials:
            raise ProjectError(f"material {name!r} already exists")
        return name

    def add_material(self, name, values=None):
        """A new preset (default: the dielectric defaults); returns its name."""
        name = self._check_new_name(name)
        dielectric = OBJECT_TYPES["dielectric"]
        new = {p: dielectric.param(p).default for p in MATERIAL_PARAMS}
        for param, value in (values or {}).items():
            new[param] = dielectric.param(param).validate(value)
        self.materials[name] = new
        return name

    def duplicate_material(self, source, name=None):
        return self.add_material(name or self.unique_material_name(f"{source} copy"),
                                 self.materials[source])

    def rename_material(self, old, new):
        if old in BUILTIN:
            raise ProjectError(f"{old} is built-in: it cannot be renamed (duplicate it instead)")
        new = str(new).strip()
        if new == old:
            return old
        new = self._check_new_name(new)
        # Keep the library order: rebuild with the new key in place.
        self.materials = {(new if k == old else k): v for k, v in self.materials.items()}
        for assignment in self.assignments.values():
            if assignment.material == old:
                assignment.material = new
        return new

    def delete_material(self, name):
        if name in BUILTIN:
            raise ProjectError(f"{name} is built-in: it cannot be deleted")
        users = self.material_users(name)
        if users:
            raise ProjectError(f"{name} is used by {', '.join(users)}")
        del self.materials[name]

    def set_material_value(self, name, param_name, value):
        """Edits a preset; every body that uses it follows."""
        if param_name not in MATERIAL_PARAMS:
            raise KeyError(param_name)
        value = OBJECT_TYPES["dielectric"].param(param_name).validate(value)
        self.materials[name][param_name] = value
        for body_name in self.material_users(name):
            self.assignments[body_name].values[param_name] = value

    def reset_material(self, name):
        """A built-in preset back to the program's values (its bodies follow)."""
        if name not in BUILTIN:
            raise ProjectError(f"{name} is not built-in")
        for param_name, value in BUILTIN[name].items():
            self.set_material_value(name, param_name, value)

    def remove_type(self, body_name):
        self.assignments.pop(body_name, None)

    def set_value(self, body_name, param_name, value):
        assignment = self.assignments[body_name]
        param = assignment.object_type.param(param_name)
        value = param.validate(value)
        if (assignment.material not in (None, CUSTOM) and param_name in MATERIAL_PARAMS
                and value != assignment.values[param_name]):
            assignment.material = CUSTOM        # no longer the preset's values
        assignment.values[param_name] = value

    # --- a body's local mesh ---------------------------------------------------
    def create_local_mesh(self, body_name):
        """A local mesh for a body, starting from the main mesh's values (no-op if it has one)."""
        if body_name not in self.local_mesh:
            mesh = self.settings["mesh"]
            self.local_mesh[body_name] = {
                "cells_per_wavelength": mesh["cells_per_wavelength"],
                "cells_per_feature": mesh["cells_per_feature"],
                "max_step": 0.0,
            }

    def set_local_mesh_value(self, body_name, param_name, value):
        param = next((p for p in LOCAL_MESH_PARAMS if p.name == param_name), None)
        if param is None:
            raise ProjectError(f"local mesh: unknown parameter {param_name!r}")
        self.local_mesh[body_name][param_name] = param.validate(value)

    def remove_local_mesh(self, body_name):
        self.local_mesh.pop(body_name, None)

    # --- circuit elements on the runs' ports (solver/circuit.py) --------------
    def add_element(self, kind, name=None, ports=None):
        """A new element of `kind` with its defaults; returns its name ("<kind>N" if none given)."""
        if kind not in ELEMENT_KINDS:
            raise ProjectError(f"unknown element kind {kind!r}, expected one of {sorted(ELEMENT_KINDS)}")
        if name is None:
            n = 1
            while f"{kind}{n}" in self.circuit:
                n += 1
            name = f"{kind}{n}"
        name = self._check_element_name(name)
        k = ELEMENT_KINDS[kind]
        self.circuit[name] = Element(name, kind, [""] * k.ports, k.defaults())
        for i, port in enumerate(ports or []):
            self.set_element_port(name, i, port)
        return name

    def _check_element_name(self, name):
        name = str(name).strip()
        if not name:
            raise ProjectError("an element needs a name")
        if name in self.circuit:
            raise ProjectError(f"an element {name!r} exists")
        return name

    def element(self, name):
        if name not in self.circuit:
            raise ProjectError(f"no circuit element {name!r}")
        return self.circuit[name]

    def remove_element(self, name):
        self.circuit.pop(name, None)

    def rename_element(self, old, new):
        element = self.element(old)
        if str(new).strip() == old:
            return
        new = self._check_element_name(new)
        self.circuit = {(new if k == old else k): v for k, v in self.circuit.items()}
        element.name = new

    def set_element_value(self, name, param_name, value):
        element = self.element(name)
        try:
            param = element.element_kind.param(param_name)
        except KeyError:
            raise ProjectError(f"{name}: no parameter {param_name!r} for kind {element.kind!r}") from None
        element.values[param_name] = param.validate(value)
        if param_name == "file":                    # the port count follows the file
            n = port_count(element)
            element.ports = (element.ports + [""] * n)[:n]

    def set_element_port(self, name, index, port):
        element = self.element(name)
        if not 0 <= index < len(element.ports):
            raise ProjectError(f"{name}: has {len(element.ports)} ports, no port {index + 1}")
        element.ports[index] = str(port).strip()

    def set_element_enabled(self, name, enabled):
        self.element(name).enabled = bool(enabled)

    def set_setting(self, section, param_name, value):
        """Validates the value alone and against the other settings."""
        value = SECTIONS[section].param(param_name).validate(value)
        trial = {name: dict(values) for name, values in self.settings.items()}
        trial[section][param_name] = value
        check_settings(trial)
        self.settings = trial

    # --- the Z0 path ------------------------------------------------------------

    def default_z0path(self, geometry):
        """
        A path without points in the plane normal to the up axis, at the top
        of the first body (a KiCad board's F.Cu: its copper's top face).
        """
        axis = UP_AXES.index(self.up)
        top = geometry.bodies[0].bounds[1][axis] * 1e3 if geometry.bodies else 0.0
        return {"axis": self.up, "at": round(float(top), 4),
                "half_width": Z0PATH_PARAMS[2].default, "points": []}

    def set_z0path_value(self, name, value):
        param = next((p for p in Z0PATH_PARAMS if p.name == name), None)
        if param is None:
            raise KeyError(name)
        self.z0path[name] = param.validate(value)

    def set_z0path_points(self, points):
        self.z0path["points"] = _z0path_points(points)

    def set_up(self, axis):
        if axis not in UP_AXES:
            raise ProjectError(f"up axis must be one of {UP_AXES}, got {axis!r}")
        self.up = axis

    def ports(self, geometry, cache=None):
        """
        {port body name: PortGeometry}, checked against the current conductors.
        cache: a dict kept with the geometry (cleared when it is reloaded): a
        port's geometry depends only on its body and the conductors, so an
        edit of values does not repeat it.
        """
        return self._port_geometries(geometry, "port", True, cache)

    def elements(self, geometry, cache=None):
        """{lumped element body name: PortGeometry (no polarity)}; cache: as ports()."""
        return self._port_geometries(geometry, "lumped", False, cache)

    def _port_geometries(self, geometry, kind, polar, cache):
        conductors = [
            b for b in geometry.bodies
            if self.get(b.name) is not None and self.get(b.name).type in CONDUCTOR_TYPES
        ]
        names = tuple(c.name for c in conductors)
        out = {}
        for b in geometry.bodies:
            if self.get(b.name) is None or self.get(b.name).type != kind:
                continue
            key = (b.name, polar, names)
            if cache is None or key not in cache:
                found = port_geometry(b, conductors, polar=polar)
                if cache is None:
                    out[b.name] = found
                    continue
                cache[key] = found
            out[b.name] = cache[key]
        return out

    def waveports(self, geometry):
        """{waveguide port body name: WavePortGeometry}."""
        materials = [
            b for b in geometry.bodies
            if self.get(b.name) is not None and self.get(b.name).type in CONDUCTOR_TYPES + ("dielectric",)
        ]
        return {
            b.name: waveport_geometry(b, materials)
            for b in geometry.bodies
            if self.get(b.name) is not None and self.get(b.name).type == "waveport"
        }

    # --- holes -----------------------------------------------------------------

    def set_hole_diameter(self, diameter_mm, kind):
        """Rule for every hole of this drill; kind None removes the rule."""
        key = _drill_key(diameter_mm)
        for existing in list(self.hole_diameters):
            if abs(existing - key) < HOLE_DIAMETER_TOL_MM:
                del self.hole_diameters[existing]
        if kind is not None:
            self.hole_diameters[key] = _hole_kind(kind)

    def set_hole_single(self, center_mm, kind):
        """Rule for one hole (by its center); kind None: back to the diameter rule."""
        key = _center_key(center_mm)
        for existing in list(self.hole_single):
            if max(abs(a - b) for a, b in zip(existing, key)) < HOLE_CENTER_TOL_MM:
                del self.hole_single[existing]
        if kind is not None:
            self.hole_single[key] = _hole_kind(kind)

    def hole_rule(self, hole):
        """(kind, source) of a geometry.holes.Hole: source "single", "diameter" or "" (none)."""
        center = hole.center * 1e3
        for key, kind in self.hole_single.items():
            if max(abs(a - b) for a, b in zip(key, center)) < HOLE_CENTER_TOL_MM:
                return kind, "single"
        drill = hole.diameter * 1e3
        for key, kind in self.hole_diameters.items():
            if abs(key - drill) < HOLE_DIAMETER_TOL_MM:
                return kind, "diameter"
        return "none", ""

    def vias(self, holes):
        """The holes that are vias."""
        return [h for h in holes if self.hole_rule(h)[0] == "via"]

    def orphans(self, geometry):
        names = {b.name for b in geometry.bodies}
        return sorted(n for n in set(self.assignments) | set(self.local_mesh) if n not in names)

    # =====================================================
    # File
    # =====================================================

    @classmethod
    def load(cls, step_path):
        """The project next to step_path, or an empty one if there is none."""
        path = project_path(step_path)

        if not os.path.isfile(path):
            return cls(step_path)

        try:
            with open(path, "rb") as f:
                data = tomllib.load(f)
        except tomllib.TOMLDecodeError as error:
            raise ProjectError(f"{path}: {error}") from None

        unknown = (set(data) - {"bodies", "view", "materials", "holes", "z0path", "local_mesh", "circuit"}
                   - set(SECTIONS))
        if unknown:
            raise ProjectError(f"{path}: unknown top-level keys {sorted(unknown)}")

        view = dict(data.get("view", {}))
        up = view.pop("up", "z")
        if up not in UP_AXES:
            raise ProjectError(f"{path}: [view] up must be one of {UP_AXES}, got {up!r}")
        view = _parse_view(path, view)

        library = builtin_library()
        for name, section in data.get("materials", {}).items():
            library[name] = _parse_material(path, name, section, library.get(name))

        assignments = {}
        for name, section in data.get("bodies", {}).items():
            assignments[name] = _parse_assignment(path, name, section, library)

        settings = {}
        for name, schema in SECTIONS.items():
            values = schema.defaults()
            for key, value in data.get(name, {}).items():
                try:
                    values[key] = schema.param(key).validate(value)
                except KeyError:
                    raise ProjectError(f"{path}: [{name}]: unknown key {key!r}") from None
                except ValueError as error:
                    raise ProjectError(f"{path}: [{name}]: {error}") from None
            settings[name] = values
        try:
            check_settings(settings)
        except ValueError as error:
            raise ProjectError(f"{path}: {error}") from None

        holes = _parse_holes(path, data.get("holes", {}))
        z0path = _parse_z0path(path, data["z0path"]) if "z0path" in data else None
        local_mesh = {name: _parse_local_mesh(path, name, section)
                      for name, section in data.get("local_mesh", {}).items()}
        circuit = {name: _parse_element(path, name, section) for name, section in data.get("circuit", {}).items()}
        return cls(step_path, assignments, up, settings, library, holes, view, z0path, local_mesh, circuit)

    def save(self):
        """Writes atomically (temp file + replace): a crash never leaves half a file."""
        lines = [f"# customcst project for {os.path.basename(self.step_path)}", ""]

        for name, schema in SECTIONS.items():
            lines.append(f"[{name}]")
            for param in schema.params:
                comment = f"   # {param.unit}" if param.unit else ""
                lines.append(f"{param.name} = {_toml_value(self.settings[name][param.name])}{comment}")
            lines.append("")

        for name in sorted(self.assignments):
            assignment = self.assignments[name]
            lines.append(f"[bodies.{_key(name)}]")
            lines.append(f'type = "{assignment.type}"')
            if assignment.material is not None:
                lines.append(f"material = {_toml_value(assignment.material)}")

            for param in assignment.object_type.params:
                value = assignment.values[param.name]
                comment = f"   # {param.unit}" if param.unit else ""
                lines.append(f"{param.name} = {_toml_value(value)}{comment}")

            lines.append("")

        for name in sorted(self.local_mesh):
            lines.append(f"[local_mesh.{_key(name)}]")
            for param in LOCAL_MESH_PARAMS:
                comment = f"   # {param.unit}" if param.unit else ""
                lines.append(f"{param.name} = {_toml_value(self.local_mesh[name][param.name])}{comment}")
            lines.append("")

        lines += ["[view]", f'up = "{self.up}"']
        for key in VIEW_KEYS[1:]:
            if key in self.view:
                value = self.view[key]
                if key == "hidden":
                    text = json.dumps(value, ensure_ascii=False)
                elif key in CAMERA_KEYS:
                    text = "[" + ", ".join(repr(float(v)) for v in value) + "]"
                else:
                    text = _toml_value(value)
                unit = key in ("at", "camera_center", "camera_distance")
                lines.append(f"{key} = {text}" + ("   # mm" if unit else ""))
        lines.append("")

        if self.z0path is not None:
            lines.append("[z0path]")
            for param in Z0PATH_PARAMS:
                comment = f"   # {param.unit}" if param.unit else ""
                lines.append(f"{param.name} = {_toml_value(self.z0path[param.name])}{comment}")
            points = ", ".join(f"[{_fmt_number(u)}, {_fmt_number(v)}]" for u, v in self.z0path["points"])
            lines.append(f"points = [{points}]   # mm")
            lines.append("")

        for name, element in self.circuit.items():
            lines.append(f"[circuit.{_key(name)}]")
            lines.append(f'kind = "{element.kind}"')
            lines.append(f"enabled = {_toml_value(element.enabled)}")
            lines.append(f"ports = {json.dumps(element.ports, ensure_ascii=False)}")
            for param in element.element_kind.all_params():
                comment = f"   # {param.unit}" if param.unit else ""
                lines.append(f"{param.name} = {_toml_value(element.values[param.name])}{comment}")
            lines.append("")

        if self.hole_diameters:
            lines.append("[holes.diameters]   # drill, mm")
            for key in sorted(self.hole_diameters):
                lines.append(f'"{_fmt_number(key)}" = "{self.hole_diameters[key]}"')
            lines.append("")
        if self.hole_single:
            lines.append("[holes.single]   # hole center x,y,z, mm")
            for key in sorted(self.hole_single):
                lines.append(f'"{",".join(_fmt_number(v) for v in key)}" = "{self.hole_single[key]}"')
            lines.append("")

        dielectric = OBJECT_TYPES["dielectric"]
        for name, values in self.materials.items():
            lines.append(f"[materials.{_key(name)}]")
            for param_name in MATERIAL_PARAMS:
                param = dielectric.param(param_name)
                comment = f"   # {param.unit}" if param.unit else ""
                lines.append(f"{param_name} = {_toml_value(values[param_name])}{comment}")
            lines.append("")

        temp = self.path + ".tmp"
        with open(temp, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(lines))
        os.replace(temp, self.path)


def _fmt_number(value):
    """Shortest exact-enough text of a number in mm (um resolution kept)."""
    text = f"{value:.4f}".rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def _drill_key(diameter_mm):
    return round(float(diameter_mm), 4)


def _center_key(center_mm):
    return tuple(round(float(v), 4) for v in center_mm)


def _hole_kind(kind):
    if kind not in HOLE_KINDS:
        raise ProjectError(f"hole kind must be one of {HOLE_KINDS}, got {kind!r}")
    return kind


def _parse_holes(path, section):
    where = f"{path}: [holes]"
    if not isinstance(section, dict) or set(section) - {"diameters", "single"}:
        raise ProjectError(f"{where}: expected the tables [holes.diameters] and [holes.single]")
    out = {"diameters": {}, "single": {}}
    for key, kind in section.get("diameters", {}).items():
        try:
            drill = float(key)
        except ValueError:
            raise ProjectError(f"{where}.diameters: {key!r} is not a diameter in mm") from None
        if not drill > 0:
            raise ProjectError(f"{where}.diameters: diameter {key!r} must be > 0")
        if kind not in HOLE_KINDS:
            raise ProjectError(f"{where}.diameters: {key!r} = {kind!r}, expected one of {HOLE_KINDS}")
        out["diameters"][_drill_key(drill)] = kind
    for key, kind in section.get("single", {}).items():
        try:
            center = [float(v) for v in key.split(",")]
        except ValueError:
            center = []
        if len(center) != 3:
            raise ProjectError(f"{where}.single: {key!r} is not a center \"x,y,z\" in mm")
        if kind not in HOLE_KINDS:
            raise ProjectError(f"{where}.single: {key!r} = {kind!r}, expected one of {HOLE_KINDS}")
        out["single"][_center_key(center)] = kind
    return out


def _z0path_points(points):
    """[[u, v], ...] in mm (0.1 um kept), or ValueError."""
    out = []
    for point in points:
        if not (len(point) == 2 and all(isinstance(v, (int, float)) and not isinstance(v, bool)
                                         for v in point)):
            raise ValueError(f"a path point must be two numbers (mm), got {point!r}")
        out.append([round(float(point[0]), 4), round(float(point[1]), 4)])
    return out


def _parse_z0path(path, section):
    """The [z0path] table: every key validated, missing ones from the defaults."""
    where = f"{path}: [z0path]"
    if not isinstance(section, dict):
        raise ProjectError(f"{where}: expected a table")
    unknown = set(section) - {p.name for p in Z0PATH_PARAMS} - {"points"}
    if unknown:
        raise ProjectError(f"{where}: unknown keys {sorted(unknown)}")
    out = {p.name: p.default for p in Z0PATH_PARAMS}
    try:
        for param in Z0PATH_PARAMS:
            if param.name in section:
                out[param.name] = param.validate(section[param.name])
        points = section.get("points", [])
        if not isinstance(points, list):
            raise ValueError("points must be a list of [u, v] pairs (mm)")
        out["points"] = _z0path_points(points)
    except (ValueError, TypeError) as error:
        raise ProjectError(f"{where}: {error}") from None
    return out


def _parse_view(path, view):
    """The display keys of [view] (all optional), validated."""
    where = f"{path}: [view]"
    unknown = set(view) - set(VIEW_KEYS)
    if unknown:
        raise ProjectError(f"{where}: unknown keys {sorted(unknown)}")
    for key in VIEW_SWITCHES:
        if key in view and not isinstance(view[key], bool):
            raise ProjectError(f"{where}: {key} must be true or false, got {view[key]!r}")
    if "plane" in view and view["plane"] not in UP_AXES:
        raise ProjectError(f"{where}: plane must be one of {UP_AXES}, got {view['plane']!r}")
    if "at" in view:
        if isinstance(view["at"], bool) or not isinstance(view["at"], (int, float)):
            raise ProjectError(f"{where}: at must be a coordinate in mm, got {view['at']!r}")
        view["at"] = float(view["at"])
    if "hidden" in view and not (isinstance(view["hidden"], list)
                                 and all(isinstance(n, str) for n in view["hidden"])):
        raise ProjectError(f"{where}: hidden must be a list of body names")
    for key, length in CAMERA_KEYS.items():
        if key in view:
            value = view[key]
            if not (isinstance(value, list) and len(value) == length
                    and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value)):
                raise ProjectError(f"{where}: {key} must be a list of {length} numbers")
            view[key] = [float(v) for v in value]
    if "camera_distance" in view:
        value = view["camera_distance"]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not value > 0:
            raise ProjectError(f"{where}: camera_distance must be a number > 0, got {value!r}")
        view["camera_distance"] = float(value)
    return view


def _toml_value(value):
    """Numbers as repr, strings as basic (double-quoted) TOML strings."""
    if isinstance(value, (str, bool)):          # TOML: "text", true / false
        return json.dumps(value, ensure_ascii=False)
    return repr(value)


def _key(name):
    """Bare TOML key if possible, else a quoted one (JSON escapes are valid TOML)."""
    if name and all(c.isascii() and (c.isalnum() or c in "_-") for c in name):
        return name
    return json.dumps(name, ensure_ascii=False)


def _parse_local_mesh(path, name, section):
    """A [local_mesh.NAME] table: missing parameters take the defaults."""
    where = f"{path}: local mesh of {name!r}"
    if not isinstance(section, dict):
        raise ProjectError(f"{where}: expected a table")
    values = {p.name: p.default for p in LOCAL_MESH_PARAMS}
    params = {p.name: p for p in LOCAL_MESH_PARAMS}
    for key, value in section.items():
        if key not in params:
            raise ProjectError(f"{where}: unknown key {key!r}")
        try:
            values[key] = params[key].validate(value)
        except ValueError as error:
            raise ProjectError(f"{where}: {error}") from None
    return values


def _parse_element(path, name, section):
    """A [circuit.NAME] table: kind, enabled, ports and the kind's parameters (missing: defaults)."""
    where = f"{path}: circuit element {name!r}"
    if not isinstance(section, dict):
        raise ProjectError(f"{where}: expected a table")
    kind = section.get("kind")
    if kind not in ELEMENT_KINDS:
        raise ProjectError(f"{where}: unknown kind {kind!r}, expected one of {sorted(ELEMENT_KINDS)}")
    k = ELEMENT_KINDS[kind]
    values = k.defaults()
    enabled, ports = True, []
    for key, value in section.items():
        if key == "kind":
            continue
        if key == "enabled":
            if not isinstance(value, bool):
                raise ProjectError(f"{where}: enabled must be true or false")
            enabled = value
            continue
        if key == "ports":
            if not isinstance(value, list) or not all(isinstance(p, str) for p in value):
                raise ProjectError(f"{where}: ports must be a list of port names")
            ports = [p.strip() for p in value]
            continue
        try:
            values[key] = k.param(key).validate(value)
        except KeyError:
            raise ProjectError(f"{where}: unknown key {key!r} for kind {kind!r}") from None
        except ValueError as error:
            raise ProjectError(f"{where}: {error}") from None
    element = Element(name, kind, [], values, enabled)
    n = port_count(element)
    element.ports = (ports + [""] * n)[:n] if n else ports
    return element


def _parse_material(path, name, section, builtin):
    """A [materials.NAME] table: every parameter validated, missing ones from the built-in / defaults."""
    where = f"{path}: material {name!r}"
    if not isinstance(section, dict):
        raise ProjectError(f"{where}: expected a table")
    if name == CUSTOM:
        raise ProjectError(f"{where}: {CUSTOM!r} is reserved (a body's own values)")
    dielectric = OBJECT_TYPES["dielectric"]
    values = dict(builtin) if builtin else {p: dielectric.param(p).default for p in MATERIAL_PARAMS}
    for key, value in section.items():
        if key not in MATERIAL_PARAMS:
            raise ProjectError(f"{where}: unknown key {key!r}")
        try:
            values[key] = dielectric.param(key).validate(value)
        except ValueError as error:
            raise ProjectError(f"{where}: {error}") from None
    return values


def _parse_assignment(path, name, section, library):
    where = f"{path}: body {name!r}"

    if not isinstance(section, dict):
        raise ProjectError(f"{where}: expected a table")

    type_name = section.get("type")
    if type_name not in OBJECT_TYPES:
        raise ProjectError(
            f"{where}: unknown type {type_name!r}, expected one of {sorted(OBJECT_TYPES)}"
        )

    assignment = Assignment.new(type_name)
    object_type = assignment.object_type
    known = {p.name for p in object_type.params}

    if type_name == "dielectric":
        material = section.get("material", CUSTOM)
        if material != CUSTOM and material not in library:
            raise ProjectError(f"{where}: unknown material {material!r}")
        assignment.material = material
    elif "material" in section:
        raise ProjectError(f"{where}: only dielectrics have a material")

    for key, value in section.items():
        if key in ("type", "material"):
            continue
        if key not in known:
            raise ProjectError(f"{where}: unknown key {key!r} for type {type_name!r}")
        try:
            assignment.values[key] = object_type.param(key).validate(value)
        except ValueError as error:
            raise ProjectError(f"{where}: {error}") from None

    if assignment.material not in (None, CUSTOM):
        assignment.values.update(library[assignment.material])     # the library wins
    return assignment
