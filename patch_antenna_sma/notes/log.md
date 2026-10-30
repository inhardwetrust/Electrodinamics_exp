# Log

## 2026-10-30: the project set up (in the myCad session)

- Goal (the user): a patch antenna on FR4 1.6 mm with the SMA connector in
  `parts/`, designed end to end by Claude with myCad and customcst, shown
  in their windows: start from estimates, model in myCad, export STEP,
  simulate in customcst (materials, the connector's port cylinder), tune
  for the best match at the design frequency.
- Decided:
  - three folders: the two tools and this project folder; everything of
    the design here (README.md, CLAUDE.md).
  - the feed is a myCad polyline (a microstrip, widths per point); the
    matching to the connector is part of the design.
  - no union of conductors is needed: customcst marks PEC edges inside or
    on any conductor, so touching / overlapping conductors merge on the
    grid; PEC wins over a dielectric; two dielectrics overlapping resolve
    by body order (avoid it).
  - before the run, the tools get their part of the bridge:
    - myCad: `--set NAME=FORMULA` in its CLI (a variant without editing
      the part file);
    - customcst: the sweep bridge (variants from a myCad part), reloading
      a project changed on disk, the skill section
      (`../customcst/TASK_mycad_bridge.md`).
- The connector (read with myCad): 5 solids; the coax axis along -y; the
  cylinder `Volumenkörper16` at y -14.5 .. -13.5 (r 2.675) is the port.
- Next: the tools' parts, then `prompt.md` filled in and run.
