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
#   [simulation] / [mesh]: project-wide settings, see settings.py
#
# Bodies without a section have no type yet. Sections whose body is missing
# from the STEP file are kept (the body may come back after a CAD edit) and
# reported as orphans. Missing parameters take their defaults; unknown types,
# unknown keys and invalid values raise ProjectError naming file and body.

import json
import os
import tomllib

from project.object_types import CONDUCTOR_TYPES, OBJECT_TYPES, Assignment
from project.ports import port_geometry
from project.settings import SECTIONS, check_settings

UP_AXES = ("x", "y", "z")


class ProjectError(ValueError):
    pass


def project_path(step_path):
    return os.path.splitext(os.fspath(step_path))[0] + ".toml"


class Project:
    def __init__(self, step_path, assignments=None, up="z", settings=None):
        self.step_path = os.fspath(step_path)
        self.assignments = dict(assignments or {})     # {body name: Assignment}
        self.up = up                                   # vertical axis for the view
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

    def remove_type(self, body_name):
        self.assignments.pop(body_name, None)

    def set_value(self, body_name, param_name, value):
        assignment = self.assignments[body_name]
        param = assignment.object_type.param(param_name)
        assignment.values[param_name] = param.validate(value)

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

        unknown = set(data) - {"bodies", "view"} - set(SECTIONS)
        if unknown:
            raise ProjectError(f"{path}: unknown top-level keys {sorted(unknown)}")

        view = data.get("view", {})
        if set(view) - {"up"}:
            raise ProjectError(f"{path}: [view]: unknown keys {sorted(set(view) - {'up'})}")
        up = view.get("up", "z")
        if up not in UP_AXES:
            raise ProjectError(f"{path}: [view] up must be one of {UP_AXES}, got {up!r}")

        assignments = {}
        for name, section in data.get("bodies", {}).items():
            assignments[name] = _parse_assignment(path, name, section)

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

        return cls(step_path, assignments, up, settings)

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

            for param in assignment.object_type.params:
                value = assignment.values[param.name]
                comment = f"   # {param.unit}" if param.unit else ""
                lines.append(f"{param.name} = {value!r}{comment}")

            lines.append("")

        lines += ["[view]", f'up = "{self.up}"', ""]

        temp = self.path + ".tmp"
        with open(temp, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(lines))
        os.replace(temp, self.path)


def _key(name):
    """Bare TOML key if possible, else a quoted one (JSON escapes are valid TOML)."""
    if name and all(c.isascii() and (c.isalnum() or c in "_-") for c in name):
        return name
    return json.dumps(name, ensure_ascii=False)


def _parse_assignment(path, name, section):
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

    for key, value in section.items():
        if key == "type":
            continue
        if key not in known:
            raise ProjectError(f"{where}: unknown key {key!r} for type {type_name!r}")
        try:
            assignment.values[key] = object_type.param(key).validate(value)
        except ValueError as error:
            raise ProjectError(f"{where}: {error}") from None

    return assignment
