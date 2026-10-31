# presets.py
#
# Presets of the mesh and of the monitors: settings that do not depend on
# the model, kept as files beside it so they travel by copying, like KiCad
# footprints:
#
#   model.step
#   model.presets/mesh/Final.toml          one file = one preset, named by it
#   model.presets/monitors/Pattern.toml
#
#   # customcst mesh preset            (mesh: the [mesh] settings)
#   cells_per_wavelength = 50.0
#   cells_per_feature = 12.0
#   max_ratio = 1.3
#   min_step = 0.0
#   slanted = true
#
#   # customcst monitors preset        (monitors: the far field, relative)
#   offsets = "-2, 0, 2"              # % from the far field's f0
#
# A preset is a template: applying it copies its values into the project
# (the far field's frequencies = f0 (1 + offset / 100), f0 = [farfield] f0,
# or the band's center when that is 0); editing a preset changes no
# project until it is applied again. The project does not store which
# preset it came from: the tree shows the preset whose values it has
# (matching()). Built-in presets come from the program (BUILTIN), are not
# files and cannot be changed: Duplicate one to edit it.
# Missing keys take the defaults, unknown keys and bad values raise
# PresetError naming the file.

import os
import re
import tomllib

from project.object_types import Param
from project.settings import SECTIONS

KINDS = ("mesh", "monitors")
MONITOR_PARAMS = (
    Param("offsets", "far field from f0", "0", minimum=-90.0, maximum=900.0, unit="%", numbers=True,
          tooltip="The far-field frequencies relative to the far field's f0 (0 = the band's center): "
                  "-2, 0, 2 gives f0 - 2 %, f0, f0 + 2 %"),
)
PARAMS = {"mesh": SECTIONS["mesh"].params, "monitors": MONITOR_PARAMS}

BUILTIN = {
    "mesh": {
        "Draft": {"cells_per_wavelength": 20.0, "cells_per_feature": 4.0},
        "Sweep": {"cells_per_wavelength": 30.0, "cells_per_feature": 4.0},
        "Fine": {"cells_per_wavelength": 40.0, "cells_per_feature": 8.0},
        "Final": {"cells_per_wavelength": 50.0, "cells_per_feature": 12.0},
    },
    "monitors": {
        "f0": {"offsets": "0"},
        "f0 ±2 %": {"offsets": "-2, 0, 2"},
    },
}
NAME = re.compile(r"^[^\\/:*?\"<>|]+$")    # a file name on Windows too


class PresetError(Exception):
    pass


def presets_dir(step_path, kind):
    return os.path.splitext(os.fspath(step_path))[0] + ".presets" + os.sep + kind


def defaults(kind):
    return {p.name: p.default for p in PARAMS[kind]}


def validate(kind, values, where=""):
    """Full values of a preset (missing keys: defaults), or PresetError."""
    params = {p.name: p for p in PARAMS[kind]}
    unknown = set(values) - set(params)
    if unknown:
        raise PresetError(f"{where}unknown keys {sorted(unknown)}")
    out = defaults(kind)
    for key, value in values.items():
        try:
            out[key] = params[key].validate(value)
        except ValueError as error:
            raise PresetError(f"{where}{error}") from None
    return out


def builtin(kind):
    return {name: validate(kind, values) for name, values in BUILTIN[kind].items()}


def load_presets(step_path, kind):
    """
    ({name: values} of the model's own presets, [errors]): a file that does
    not read is skipped and reported, never fatal.
    """
    folder = presets_dir(step_path, kind)
    presets, errors = {}, []
    if not os.path.isdir(folder):
        return presets, errors
    for entry in sorted(os.listdir(folder), key=str.lower):
        stem, ext = os.path.splitext(entry)
        if ext.lower() != ".toml":
            continue
        path = os.path.join(folder, entry)
        try:
            with open(path, "rb") as f:
                presets[stem] = validate(kind, tomllib.load(f), f"{entry}: ")
        except (OSError, tomllib.TOMLDecodeError, PresetError) as error:
            errors.append(f"{entry}: {error}" if not isinstance(error, PresetError) else str(error))
    return presets, errors


def all_presets(step_path, kind):
    """[(name, values, "built-in" | "model")]: the built-in ones first, then the model's."""
    own, _ = load_presets(step_path, kind)
    return ([(n, v, "built-in") for n, v in builtin(kind).items()]
            + [(n, v, "model") for n, v in own.items()])


def _path(step_path, kind, name):
    return os.path.join(presets_dir(step_path, kind), name + ".toml")


def check_name(step_path, kind, name, allow=None):
    """A valid new name for a model preset (stripped), or PresetError."""
    name = str(name).strip()
    if not name or not NAME.match(name) or name.startswith("."):
        raise PresetError(f"not a usable name: {name!r}")
    if name in BUILTIN[kind]:
        raise PresetError(f"{name} is a built-in preset's name")
    if name != allow and os.path.exists(_path(step_path, kind, name)):
        raise PresetError(f"a preset {name} exists already")
    return name


def unique_name(step_path, kind, base):
    name, n = base, 1
    while name in BUILTIN[kind] or os.path.exists(_path(step_path, kind, name)):
        n += 1
        name = f"{base} {n}"
    return name


def save_preset(step_path, kind, name, values):
    """Writes (or overwrites) a model preset, atomically; returns its path."""
    values = validate(kind, values)
    folder = presets_dir(step_path, kind)
    os.makedirs(folder, exist_ok=True)
    lines = [f"# customcst {kind} preset", ""]
    for param in PARAMS[kind]:
        value = values[param.name]
        text = ("true" if value else "false") if isinstance(value, bool) else (
            f'"{value}"' if isinstance(value, str) else repr(float(value)))
        lines.append(f"{param.name} = {text}" + (f"   # {param.unit}" if param.unit else ""))
    path = _path(step_path, kind, name)
    temp = path + ".part"
    with open(temp, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    os.replace(temp, path)
    return path


def set_value(step_path, kind, name, param, value):
    """One value of a model preset; returns the stored value."""
    own, _ = load_presets(step_path, kind)
    if name not in own:
        raise PresetError(f"{name}: not a preset of this model (built-in ones cannot be changed)")
    values = dict(own[name])
    values[param] = value
    values = validate(kind, values)
    save_preset(step_path, kind, name, values)
    return values[param]


def rename_preset(step_path, kind, old, new):
    new = check_name(step_path, kind, new, allow=old)
    if new != old:
        os.replace(_path(step_path, kind, old), _path(step_path, kind, new))
    return new


def delete_preset(step_path, kind, name):
    os.remove(_path(step_path, kind, name))


def from_project(project, kind):
    """The project's current values as a preset of `kind`."""
    if kind == "mesh":
        return dict(project.settings["mesh"])
    f0 = farfield_f0(project.settings)
    frequencies = [float(x) for x in project.settings["farfield"]["frequencies"].split(",") if x.strip()]
    if not frequencies:
        raise PresetError("no far-field frequencies to save")
    offsets = ", ".join(f"{round(100.0 * (f / f0 - 1.0), 2):g}" for f in frequencies)   # 0.01 %: the frequencies are rounded to 0.1 MHz
    return validate("monitors", {"offsets": offsets})


def farfield_f0(settings):
    """GHz: the far field's f0, or the band's center when it is 0."""
    f0 = settings["farfield"].get("f0", 0.0)
    sim = settings["simulation"]
    return f0 if f0 > 0 else 0.5 * (sim["f_min"] + sim["f_max"])


def frequencies(settings, values):
    """The far-field frequencies (GHz text) a monitors preset gives in these settings."""
    f0 = farfield_f0(settings)
    offsets = [float(x) for x in values["offsets"].split(",") if x.strip()]
    return ", ".join(f"{round(f0 * (1.0 + o / 100.0), 4):g}" for o in offsets)


def apply(project, kind, values):
    """Copies a preset into the project (validated as a whole: nothing changes on an error)."""
    if kind == "mesh":
        updates = [("mesh", key, value) for key, value in values.items()]
    else:
        updates = [("farfield", "frequencies", frequencies(project.settings, values)),
                   ("farfield", "enabled", True)]           # a monitors preset is asked for: on
    saved = project.settings
    try:
        for section, key, value in updates:
            project.set_setting(section, key, value)
    except ValueError as error:
        project.settings = saved
        raise PresetError(str(error)) from None


def matching(project, kind, presets):
    """The name of the first preset (of all_presets()) whose values the project has, or None."""
    for name, values, _ in presets:
        if kind == "mesh" and all(project.settings["mesh"][k] == v for k, v in values.items()):
            return name
        if kind == "monitors":
            given = SECTIONS["farfield"].param("frequencies").validate(frequencies(project.settings, values))
            if given == project.settings["farfield"]["frequencies"]:
                return name
    return None
