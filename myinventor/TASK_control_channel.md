# Task: a control channel for Claude in myCad (like customcst's)

Written by the customcst session (2026-10-30) for the myCad session. The
user agreed on the idea; build it here, step by step, with checks as usual.

## Why

In a design project (`../patch_antenna_sma/`) Claude changes the model
and the user watches in the windows. Until now Claude only wrote files
and the windows reloaded them. customcst now has a control channel:
Claude drives the user's open window, and the user sees each step. myCad
gets the same, with the same protocol, so one client drives both.

Read first (they are the reference, copy what fits):
- `../customcst/view/control.py`: the server, the commands, the lock rules;
- `../customcst/tools/ui.py`: the client, shared (it imports nothing of
  customcst): `python ../customcst/tools/ui.py --app mycad status`;
- `../customcst/ARCHITECTURE.md`, section "Control by Claude";
- `../customcst/checks/check_control.py`: what to check, and how (the
  client runs in a thread, because the window answers in the GUI thread).

## Two modes (the user's idea)

- **Normal:** the user works. Claude may read (`status`, `screenshot`,
  `describe`), and every command that changes something is refused ("not
  in control").
- **Locked:** Claude works and the user only looks. Claude turns it on
  with `lock "what I am doing"`, or by starting the window with
  `main.py PART --locked`.
  - A yellow banner over the toolbars: "Claude is in control: <message>"
    and a "Take control back" button (look: `#f2c94c`, as in customcst).
  - Off: the toolbars, the menus, the browser and properties panels, all
    editing shortcuts (Ctrl+Z / Ctrl+Y, Delete, Ctrl+C / V, Esc in a tool,
    anything that edits), the right-click menu in 3D (New Sketch...), the
    sketch tools.
  - On: looking only. Rotate, pan and zoom, the standard view buttons,
    hover.
  - **Check that a disabled toolbar really stops its actions' shortcuts.**
    Actions added to the main window stay active on their own.
  - `unlock "look at ..."` gives control back, with the message in the
    status bar. "Take control back" unlocks at once, and Claude's next
    change is then refused, so Claude never acts under the user's hands.

## The protocol (the same as customcst's: keep it identical)

- **Listening:** the window listens on 127.0.0.1, port 0 (a free one),
  with a random token (`secrets.token_hex(16)`).
- **control.json:** the window writes `{"port", "token", "pid"}` to
  `control.json` beside its settings file:
  - `MYCAD_CONTROL` if set;
  - else the folder of `MYCAD_SETTINGS` (tests);
  - else `%APPDATA%\myCad\`.

  The client looks there for `--app mycad`; Windows paths ignore case.
  Write the file atomically. The last window opened owns it, and closing
  removes it only if it is still its own.
- **Request and answer:** one JSON line `{"token", "command", "args": {}}`
  in, one JSON line out: `{"ok": true, "result": ...}` or
  `{"ok": false, "error": "text"}`.
  - The token is compared with `hmac.compare_digest`.
  - A wrong token, a bad line, an unknown command (list the known ones)
    or wrong arguments each get an error answer, never an exception.
- **Handling:** in the GUI thread (QTcpServer signals, no threads). No
  command ever opens a dialog: every problem is the error text. Long work
  goes on after the answer; `status` shows `"busy": {...}` (booleans), and
  `ui.py --app mycad wait` polls it.
- **Commands every app has:**
  - `describe`: `{"app": "mycad", "protocol": 1, "commands": {name: {"changes": bool, "help": "one line"}}}`;
  - `status` (it has `"app"` and `"busy"`), `screenshot path` (`window.grab()`);
  - `lock [message]`, `unlock [message]`;
  - `open path`, `reload`.

## myCad's commands (a proposal: adjust to the code)

- **`status`:**
  - the file, `modified`, `locked`, `busy`, the sketch being edited (or
    null), whether a feature panel is open;
  - the report as `cli.report()` gives it: parameters (formula, value or
    error), features (ok or error), bodies (name, volume, bounds),
    groups, placements, `ok`.

  Reuse `cli.report`, do not write a second report.
- **`set name=formula ...`:** one or more parameters at once, as one undo
  step, through the document as an edit in the window would be. The
  rebuild runs, and the answer is the new report, or the error when a
  formula does not parse.
- **`save`:** write the part (the file on disk then matches what the user
  sees). **`export_step path`:** `export_part`; the answer has the note on
  failed features.
- **`view name`:** iso, top, front... (the view buttons). **`fit`.**
- **`select kind name`:** highlight a body or a feature, as a click in the
  browser does (to show the user what changed).
- **`undo` / `redo`:** maybe; only if it falls out naturally.

## Lock rules specific to myCad

- `lock` is refused while the user is in the middle of something:
  - editing a sketch (`editing`);
  - a feature panel open (`extrude_panel` and the like);
  - a drag going on, or a dialog open;
  - unsaved edits (`doc.modified`). Claude may write the part file, so
    the user saves or discards first; the answer says so.
- While locked:
  - `poll_disk` does not reload. Claude writes the file and sends
    `reload`, or edits through `set`.
  - `unlock` leaves the document as it is. Claude saves first (the skill
    says so); `status` shows `modified`.
- The window's own disk watch stays on in the normal mode, as now.

## Also

- `main.py PART --locked`: opens locked, with the message "started by
  Claude".
- **Do not copy the client.** Use `../customcst/tools/ui.py --app mycad`.
  In the check, load it by path (`importlib.util.spec_from_file_location`)
  or run it as a subprocess: both projects have a `geometry` package, and
  ui.py imports nothing of either.
- **Check** (`checks/check_control.py`, like customcst's), in a temp
  folder, with `MYCAD_SETTINGS` there:
  - `control.json`; a wrong token or a bad line; `describe`;
  - status unlocked; a change refused unlocked;
  - lock refused with unsaved edits and in sketch mode;
  - locked: banner, panels off, shortcuts dead, the view still rotates;
  - `set` (one undo step, report back, a bad formula an error);
  - `save`, `export_step`, `reload`, `open` of a file that does not read
    (an error, the window as it was);
  - `view`, `screenshot`;
  - Take control back; `unlock` with a message;
  - closing removes `control.json`.
- **Docs:**
  - CLAUDE.md: a line on the channel;
  - DESIGN.md: a section like customcst's "Control by Claude";
  - the mycad skill: how Claude drives the window (lock, act, save,
    unlock with what to look at; stop when "not in control").

## Still open from the bridge task (`../customcst/TASK_mycad_bridge.md`)

- `--set NAME=FORMULA` in the CLI.
- Non-ASCII body names written into STEP as ISO 10303-21 escapes
  (`Volumenkörper16` now reads back in customcst as `VolumenkÃ¶rper16`).

## Status (the myCad session, 2026-10-30)

DONE: `view/control.py` (the commands above, `select kind name`, undo /
redo), the lock in `MainWindow.set_locked`, `main.py PART --locked`,
`checks/check_control.py` (through `../customcst/tools/ui.py --app mycad`),
the docs (CLAUDE.md, DESIGN.md "Control by Claude", the skill's "Driving
the open window"). From the bridge task: `--set` and the non-ASCII names,
done too.
