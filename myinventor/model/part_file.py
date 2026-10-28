# part_file.py
#
# The part file: TOML, `name.part.toml`.
#
#   format = "mycad-part"
#   version = 1
#
#   [origin]
#   visible = ["XY Plane"]        # only what differs from the default (hidden)
#
#   [[parameter]]                 # in table order
#   name = "w"
#   formula = "1.6"
#   comment = "line width"        # optional
#
#   [[feature]]                   # in history order, see model/features.py
#   name = "Extrusion1"
#   type = "extrude"
#   ...
#
# Reading is strict (Part.from_dict): any unknown key, wrong type, unknown
# name or reference raises PartFileError naming the place, and the file is
# not opened (so it is never overwritten by a half-read part).
# Writing goes to a temporary file that replaces the old one, and is read
# back and compared before that (a writer bug cannot destroy a model).
# The TOML writer is ours (the venv has tomllib for reading, no writer):
# strings, numbers, booleans, lists of those, tables and arrays of tables.

import json
import math
import os
import tomllib

from model.part import Part, PartError

FORMAT = "mycad-part"
LEGACY_FORMATS = ("myinventor-part",)       # written before the program was renamed (myCad); still read
VERSION = 1
EXTENSION = ".part.toml"


class PartFileError(ValueError):
    pass


def load(path):
    """The Part of a file; PartFileError with the reason if it is not a valid part."""
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except OSError as error:
        raise PartFileError(f"{path}: {error.strerror or error}") from None
    except tomllib.TOMLDecodeError as error:
        raise PartFileError(f"{path}: not valid TOML: {error}") from None
    return from_data(data, path)


def from_data(data, path="the part"):
    data = dict(data)
    if data.pop("format", None) not in (FORMAT, *LEGACY_FORMATS):
        raise PartFileError(f"{path}: not a myCad part (format = {FORMAT!r} is missing)")
    version = data.pop("version", None)
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise PartFileError(f"{path}: version must be a positive integer")
    if version > VERSION:
        raise PartFileError(f"{path}: version {version} is newer than this program ({VERSION})")
    try:
        return Part.from_dict(data)
    except PartError as error:
        raise PartFileError(f"{path}: {error}") from None


def dumps(part):
    """The TOML text of a part."""
    return _table({"format": FORMAT, "version": VERSION, **part.to_dict()})


def save(path, part):
    """Writes atomically after checking that the text reads back as the same part."""
    text = dumps(part)
    if from_data(tomllib.loads(text)).to_dict() != part.to_dict():
        raise PartFileError("internal error: the part does not read back the same; not written")
    temp = f"{path}.tmp"
    with open(temp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(temp, path)


# =====================================================
# TOML writer
# =====================================================

def _table(data, prefix=""):
    """Scalars first, then sub-tables, then arrays of tables (TOML's order)."""
    lines, nested = [], []
    for key, value in data.items():
        if isinstance(value, dict):
            nested.append(("table", key, value))
        elif isinstance(value, list) and value and all(isinstance(v, dict) for v in value):
            nested.append(("array", key, value))
        else:
            lines.append(f"{_key(key)} = {_value(value)}")
    text = "\n".join(lines) + ("\n" if lines else "")
    for kind, key, value in nested:
        path = f"{prefix}.{_key(key)}" if prefix else _key(key)
        if kind == "table":
            text += f"\n[{path}]\n" + _table(value, path)
        else:
            for item in value:
                text += f"\n[[{path}]]\n" + _table(item, path)
    return text


def _key(key):
    bare = key.replace("_", "a").replace("-", "a").isalnum() and key.isascii()
    return key if bare else json.dumps(key, ensure_ascii=False)


def _value(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise PartFileError(f"cannot write {value!r}")
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)       # a valid TOML basic string
    if isinstance(value, list):
        return "[" + ", ".join(_value(v) for v in value) + "]"
    raise PartFileError(f"cannot write a {type(value).__name__} to TOML")
