# The task (a draft: the user fills in the TODOs, then gives it to Claude)

Work in this folder (`patch_antenna_sma/`): read README.md and CLAUDE.md
first. Design a patch antenna on FR4 with the SMA connector in `parts/`,
with myCad and customcst, the way README.md describes; show each stage in
the tools' windows; log decisions and sweep rounds in notes/log.md.

## The goal
- Design frequency: TODO GHz (band: TODO .. TODO GHz).
- Match: S11 below TODO dB at f0 (and below -10 dB over TODO MHz?).
- Pattern: TODO (gain wanted? beam at the board normal? anything else).

## The board
- FR4, 1.6 mm, eps_r TODO (4.3-4.4), tan d TODO (0.02).
- Copper: TODO (35 um, or zero-thickness sheets for speed).
- Board size: TODO (free / at most TODO x TODO mm); ground: full, bottom.

## The connector
- `parts/realconnector.stp`: SMA end launch; the cylinder
  `Volumenkörper16` is the coax port; `connector_dielectric` is PTFE
  (eps_r TODO); the rest metal.
- Where: TODO (the middle of one board edge? which edge?), its pin lying
  on the microstrip; solder: TODO (a small block extruded on the pad?).

## The feed and the matching
- The microstrip is a myCad polyline, 50 Ohm from the connector.
- Matching to the patch: TODO (inset feed / a quarter-wave transformer /
  your choice, explained).

## How far to go
- Runs: TODO (local only / the server allowed), time budget TODO.
- Stop when TODO (the goal met, or N rounds without progress: then report
  what limits it).
- Final run: mesh TODO (converged as the skill says), far field at f0.

## Deliverables
- The myCad part (`model/`), the STEP, the customcst project and the final
  run (`sim/`) the user opens; notes/log.md and a short report.
- Commit: TODO (no / when asked).
