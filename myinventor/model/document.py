# document.py
#
# An open part: the Part, its file, whether it changed, undo and redo.
#
# Undo works on snapshots: the part's plain data (Part.to_dict) after every
# committed edit. A part is small text, so this is cheap, and it cannot get
# out of step the way per-command undo can. An edit changes self.part, then
# calls commit(); undo / redo replace self.part with a new Part made from a
# snapshot (callers must re-read doc.part).
#
# modified: the current snapshot differs from the one last saved or opened.
# changed_on_disk: the file's modification time is not the one we read or
# wrote (another program edited it): the window asks before overwriting.

import os

from model.part import Part
from model.part_file import EXTENSION, load, save

UNDO_LIMIT = 200
UNTITLED = "Part1"


class Document:
    def __init__(self, part=None, path=None):
        self.part = part if part is not None else Part()
        self.path = path
        self._state = self.part.to_dict()
        self._saved = self._state
        self._undo = []
        self._redo = []
        self._mtime = _mtime(path)

    @classmethod
    def open(cls, path):
        """PartFileError if the file is not a valid part."""
        return cls(load(path), os.path.abspath(path))

    @property
    def name(self):
        """The part's name, as Inventor's browser shows it: the file's name, or Part1."""
        if self.path is None:
            return UNTITLED
        base = os.path.basename(self.path)
        return base[:-len(EXTENSION)] if base.lower().endswith(EXTENSION) else os.path.splitext(base)[0]

    @property
    def modified(self):
        return self._state != self._saved

    def commit(self):
        """Records the current part as one undo step; False if nothing changed."""
        state = self.part.to_dict()
        if state == self._state:
            return False
        self._undo.append(self._state)
        del self._undo[:-UNDO_LIMIT]
        self._redo.clear()
        self._state = state
        return True

    def revert(self):
        """Drops uncommitted edits of self.part (an edit that turned out invalid)."""
        self.part = Part.from_dict(self._state)

    @property
    def can_undo(self):
        return bool(self._undo)

    @property
    def can_redo(self):
        return bool(self._redo)

    def undo(self):
        if not self._undo:
            return False
        self._redo.append(self._state)
        self._state = self._undo.pop()
        self.part = Part.from_dict(self._state)
        return True

    def redo(self):
        if not self._redo:
            return False
        self._undo.append(self._state)
        self._state = self._redo.pop()
        self.part = Part.from_dict(self._state)
        return True

    def save(self, path=None):
        """Writes to `path` (Save As) or to the document's file."""
        path = os.path.abspath(path or self.path)
        self.commit()
        save(path, self.part)
        self.path = path
        self._saved = self._state
        self._mtime = _mtime(path)

    def changed_on_disk(self):
        return self.path is not None and _mtime(self.path) != self._mtime


def _mtime(path):
    try:
        return os.stat(path).st_mtime_ns if path else None
    except OSError:
        return None
