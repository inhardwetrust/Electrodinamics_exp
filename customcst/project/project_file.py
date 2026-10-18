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
#
#   [materials.FR4]     # the material library, see materials.py
#   eps_r = 4.3 ...
#
# A dielectric body has `material = "FR4"` (a preset of the library: its
# values are copied into the body, the library wins on load) or "Custom"
# (its own values; also what a file without the key means).
#
#   [simulation] / [mesh]: project-wide settings, see settings.py
#
# Bodies without a section have no type yet. Sections whose body is missing
# from the STEP file are kept (the body may come back after a CAD edit) and
# reported as orphans. Missing parameters take their defaults; unknown types,
# unknown keys and invalid values raise ProjectError naming file and body.

import json
import os
import tomllib

from project.materials import BUILTIN, CUSTOM, MATERIAL_PARAMS, builtin_library
from project.object_types import CONDUCTOR_TYPES, OBJECT_TYPES, Assignment
from project.ports import port_geometry
from project.waveports import waveport_geometry
from project.settings import SECTIONS, check_settings

UP_AXES = ("x", "y", "z")


class ProjectError(ValueError):
    pass


def project_path(step_path):
    return os.path.splitext(os.fspath(step_path))[0] + ".toml"


class Project:
    def __init__(self, step_path, assignments=None, up="z", settings=None, materials=None):
        self.step_path = os.fspath(step_path)
        self.assignments = dict(assignments or {})     # {body name: Assignment}
        self.up = up                                   # vertical axis for the view
        self.materials = builtin_library()             # {preset name: {param: value}}
        for name, values in (materials or {}).items():
            self.materials[name] = dict(values)
        self.settings = {name: section.defaults() for name, section in SECTIONS.items()}
        for name, values in (settings or {}).items():
            self.settings[name].update(values)

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

    def set_setting(self, section, param_name, value):
        """Validates the value alone and against the other settings."""
        value = SECTIONS[section].param(param_name).validate(value)
        trial = {name: dict(values) for name, values in self.settings.items()}
        trial[section][param_name] = value
        check_settings(trial)
        self.settings = trial

    def set_up(self, axis):
        if axis not in UP_AXES:
            raise ProjectError(f"up axis must be one of {UP_AXES}, got {axis!r}")
        self.up = axis

    def ports(self, geometry):
        """{port body name: PortGeometry}, checked against the current conductors."""
        conductors = [
            b for b in geometry.bodies
            if self.get(b.name) is not None and self.get(b.name).type in CONDUCTOR_TYPES
        ]
        return {
            b.name: port_geometry(b, conductors)
            for b in geometry.bodies
            if self.get(b.name) is not None and self.get(b.name).type == "port"
        }

    def elements(self, geometry):
        """{lumped element body name: PortGeometry (no polarity)}."""
        conductors = [
            b for b in geometry.bodies
            if self.get(b.name) is not None and self.get(b.name).type in CONDUCTOR_TYPES
        ]
        return {
            b.name: port_geometry(b, conductors, polar=False)
            for b in geometry.bodies
            if self.get(b.name) is not None and self.get(b.name).type == "lumped"
        }

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

    def orphans(self, geometry):
        names = {b.name for b in geometry.bodies}
        return sorted(n for n in self.assignments if n not in names)

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

        unknown = set(data) - {"bodies", "view", "materials"} - set(SECTIONS)
        if unknown:
            raise ProjectError(f"{path}: unknown top-level keys {sorted(unknown)}")

        view = data.get("view", {})
        if set(view) - {"up"}:
            raise ProjectError(f"{path}: [view]: unknown keys {sorted(set(view) - {'up'})}")
        up = view.get("up", "z")
        if up not in UP_AXES:
            raise ProjectError(f"{path}: [view] up must be one of {UP_AXES}, got {up!r}")

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

        return cls(step_path, assignments, up, settings, library)

    def save(self):
        """Writes atomically (temp file + replace): a crash never leaves half a file."""
        lines = [f"# customcst project for {os.path.basename(self.step_path)}", ""]

        for name, schema in SECTIONS.items():
            lines.append(f"[{name}]")
            for param in schema.params:
                comment = f"   # {param.unit}" if param.unit else ""
                lines.append(f"{param.name} = {self.settings[name][param.name]!r}{comment}")
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

        lines += ["[view]", f'up = "{self.up}"', ""]

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


def _toml_value(value):
    """Numbers as repr, strings as basic (double-quoted) TOML strings."""
    return json.dumps(value, ensure_ascii=False) if isinstance(value, str) else repr(value)


def _key(name):
    """Bare TOML key if possible, else a quoted one (JSON escapes are valid TOML)."""
    if name and all(c.isascii() and (c.isalnum() or c in "_-") for c in name):
        return name
    return json.dumps(name, ensure_ascii=False)


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
