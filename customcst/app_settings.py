# app_settings.py
#
# Settings of the application itself (not of a model): a small JSON file in
# the user's profile,
#   Windows: %APPDATA%\customcst\settings.json
#   else:    ~/.config/customcst/settings.json
# or the path in the CUSTOMCST_SETTINGS environment variable (tests).
#
#   {
#     "version": 1,
#     "recent_files": ["C:/.../04_coonector_plus_pcb.stp", ...],   (newest first)
#     "window": {"x": 100, "y": 80, "width": 1400, "height": 900, "maximized": true}
#   }
#
# "window": the main window when it was last closed; x, y, width, height are
# its normal (not maximized) geometry, so un-maximizing restores it.
#
# A missing or damaged file gives the defaults: the settings must never stop
# the program from starting. Unknown keys are kept on save (a newer version
# may have written them).

import json
import os
from dataclasses import dataclass, field

FORMAT_VERSION = 1
MAX_RECENT = 10


def settings_path():
    override = os.environ.get("CUSTOMCST_SETTINGS")
    if override:
        return override
    base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "customcst", "settings.json")


def _window(value):
    """A valid window entry, or None (a bad one is simply forgotten)."""
    if not isinstance(value, dict):
        return None
    try:
        out = {k: int(value[k]) for k in ("x", "y", "width", "height")}
    except (KeyError, TypeError, ValueError):
        return None
    if out["width"] < 200 or out["height"] < 150:
        return None
    out["maximized"] = bool(value.get("maximized", False))
    return out


def _same_file(a, b):
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


@dataclass
class AppSettings:
    recent_files: list = field(default_factory=list)    # newest first
    window: dict = None                                 # see the header; None = never saved
    extra: dict = field(default_factory=dict)           # keys this version does not know
    error: str = ""                                     # why the file was not read, if so

    @classmethod
    def load(cls):
        path = settings_path()
        if not os.path.isfile(path):
            return cls()
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("not a JSON object")
            recent = data.get("recent_files", [])
            if not isinstance(recent, list) or not all(isinstance(p, str) for p in recent):
                raise ValueError("recent_files must be a list of paths")
        except (OSError, ValueError) as error:          # json.JSONDecodeError is a ValueError
            return cls(error=f"{path}: {error}")
        extra = {k: v for k, v in data.items() if k not in ("version", "recent_files", "window")}
        return cls(recent_files=recent[:MAX_RECENT], window=_window(data.get("window")), extra=extra)

    def save(self):
        """Writes atomically; returns an error text, or "" on success."""
        path = settings_path()
        data = {**self.extra, "version": FORMAT_VERSION, "recent_files": self.recent_files}
        if self.window is not None:
            data["window"] = self.window
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            temp = path + ".tmp"
            with open(temp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(temp, path)
        except OSError as error:
            return f"{path}: {error}"
        return ""

    def add_recent(self, path):
        """Moves `path` to the top of the list (absolute, no duplicates)."""
        path = os.path.abspath(path)
        self.recent_files = [path] + [p for p in self.recent_files if not _same_file(p, path)]
        del self.recent_files[MAX_RECENT:]

    def remove_recent(self, path):
        self.recent_files = [p for p in self.recent_files if not _same_file(p, path)]

    def last_existing(self):
        """The newest recent file that still exists, or None."""
        for p in self.recent_files:
            if os.path.isfile(p):
                return p
        return None
