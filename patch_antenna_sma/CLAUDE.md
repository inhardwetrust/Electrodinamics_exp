# CLAUDE.md — patch_antenna_sma

A design project: read README.md first, then `prompt.md` (the task) and
`notes/log.md` (where it stands). The user writes in Russian: answer in
Russian; files here are in English.

## The tools (use them, do not change them from here)

- myCad (`../myinventor/`): read its skill `../myinventor/.claude/skills/mycad/SKILL.md`
  before writing the part. Headless:

      ../.venv/Scripts/python.exe ../myinventor/main.py model/patch.part.toml --report [--json] \
          [--export-step model/patch.step] [--set NAME=FORMULA ...]

  (`--set` overrides a parameter for that run only; being added.) The user
  keeps the part open in the myCad window: it reloads on every write, so
  write the file in meaningful steps (not 30 tiny ones), and never while
  the window has unsaved edits of theirs (the window then only warns: ask).
- customcst (`../customcst/`): read its skill
  `../customcst/.claude/skills/antenna-design/SKILL.md` (goal, sweeps,
  convergence, the final run, the server rules) and the bridge section
  there (the model from myCad, `TASK_mycad_bridge.md` until it is done).
- A tool missing something: say so, note it in notes/log.md, and fix it in
  that tool's own session or folder rules (its checks, its CLAUDE.md), not
  by hacks here.

## Rules

- Everything about this design lives here: the part, the STEPs, the
  customcst projects, sweeps, notes. Nothing of it in the tools' folders.
- `parts/` holds inputs as received: never edit them; myCad copies an
  imported STEP into the part's `*.files/` itself.
- Log every decision and every sweep round in `notes/log.md` (date, what,
  why, the numbers). A result that contradicts an earlier statement:
  correct it there plainly.
- Names: the bodies are named by meaning (`Substrate`, `Ground`, `Patch`,
  `Feed`, the connector's `SMA:*`): customcst assigns materials and the
  port by those names, so do not rename them once the project exists.
- The connector: `parts/realconnector.stp`, an SMA end launch, 5 bodies:
  `connector_outer`, `connector_outer_2` (shell), `connector_dielectric`
  (PTFE, eps_r ~2.1), `connector_wire` (center pin), `Volumenkörper16`
  (the cylinder at the coax's outer end: the coax waveport, r 2.675 mm,
  1 mm long). In the file the coax axis is -y, the pin ends at y 0, its
  top at z 0.135. Place it with Move / Rotate in 90 degree steps only: the
  waveport must stay along x, y or z, on a domain end.
- Conductors may simply touch or overlap (the pin on the microstrip, solder
  extruded on a pad): no union needed. Avoid two dielectrics in one place.
- The server is the user's: only when the task or the user allows it.
- Commit only when the user asks; this folder is part of the repo the user
  manages.
