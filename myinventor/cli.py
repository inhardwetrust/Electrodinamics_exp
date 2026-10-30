# cli.py
#
# The part without the window: rebuilt, reported, exported. For scripts and
# for an AI that writes part files (the skill .claude/skills/mycad): it
# writes the TOML, runs this, reads the numbers, fixes, exports.
#
#   python main.py name.part.toml --report                 the report (text)
#   python main.py name.part.toml --report --json          the same as JSON
#   python main.py name.part.toml --export-step out.step   rebuild, write STEP
#   (both flags may go together)
#
# The report: the parameters (formula -> value, or the error), each feature
# (ok or why it failed; a sketch: its curves and its profiles, each with its
# key as an extrusion names it and its area; an extrusion: its profiles,
# distance, operation, body) and the bodies (exact volume, solids, exact
# bounds, color). All lengths in mm.
#
# Exit code: 0 all built, 1 a parameter or a feature failed (the report says
# which; a STEP is still written of the bodies built), 2 the file could not
# be read or the STEP not written.

import json
import os
import sys

from model.part_file import PartFileError, load
from model.profiles import find_regions, whole_regions
from model.rebuild import rebuild


def report(part, result):
    """The report as plain data (see the top)."""
    values = result.values
    parameters = [{"name": p.name, "formula": p.formula,
                   **({"error": result.errors[p.name]} if p.name in result.errors
                      else {"value": values.get(p.name)})} for p in part.parameters]
    features = []
    for feature in part.features:
        row = {"name": feature.name, "type": feature.TYPE}
        if feature.name in result.feature_errors:
            row["error"] = result.feature_errors[feature.name]
        out = result.outputs.get(feature.name)
        if feature.TYPE == "sketch":
            row["plane"] = feature["plane"]
            row["points"] = len(feature.points)
            row["lines"] = [line.name + (" (construction)" if line.construction else "") for line in feature.lines]
            row["circles"] = [c.name + (" (construction)" if c.construction else "") for c in feature.circles]
            row["polylines"] = [w.name for w in feature.polylines]
            row["arcs"] = [a.name + (f" (center {a.center}, r {a.radius}, {a.start_angle}..{a.end_angle} deg)"
                                     if a.kind == "center" else f" ({a.start} through {a.through} to {a.end})")
                           for a in feature.arcs]
            row["curves"] = [{"name": c.name, "kind": c.kind, "args": dict(c.args), "visible": c.visible,
                              **({"start": [round(float(v), 6) for v in out.points[c.name + ".start"]],
                                  "end": [round(float(v), 6) for v in out.points[c.name + ".end"]]}
                                 if out is not None and c.name in out.shapes else {})}
                             for c in feature.curves]
            row["splines"] = [{"name": w.name, "points": list(w.points), "construction": w.construction,
                               **({"spans": len(out.shapes[w.name].edges[0][2]) - 1}
                                  if out is not None and w.name in out.shapes else {})}
                              for w in feature.splines]
            row["offsets"] = [{"of": o.of, "left": o.left, "right": o.right, "start": o.start, "end": o.end}
                              for o in feature.offsets]
            if out is not None:                 # whole loops (what a file names) and, where things cross, the pieces
                whole = whole_regions(out)
                row["profiles"] = [{"key": list(r.key), "area": round(r.area, 6), "holes": len(r.holes)}
                                   for r in whole]
                keys = {r.key for r in whole}
                row["pieces"] = [{"key": list(r.key), "area": round(r.area, 6), "holes": len(r.holes)}
                                 for r in find_regions(out) if r.key not in keys]
        elif feature.TYPE == "extrude":
            row.update(sketch=feature["sketch"], profiles=[list(p) for p in feature.profiles],
                       distance=feature["distance"], operation=feature["operation"], body=feature["body"])
        elif feature.TYPE == "import":
            row.update(file=feature["file"], bodies=list(feature.bodies))
        elif feature.TYPE == "workplane":
            row.update(base=feature["base"], offset=feature["offset"], axis=feature["axis"], angle=feature["angle"],
                       points=feature.points)
        elif feature.TYPE == "revolve":
            row.update(sketch=feature["sketch"], profiles=[list(p) for p in feature.profiles], axis=feature["axis"],
                       angle=feature["angle"], operation=feature["operation"], body=feature["body"])
        features.append(row)
    bodies = []
    if result.shapes:
        from geometry import kernel

        for name, shape in result.shapes.items():
            lo, hi = kernel.bounds(shape)
            bodies.append({"name": name, "volume": round(kernel.volume(shape), 6),
                           "solids": kernel.solid_count(shape),
                           "bounds": [[round(float(v), 6) for v in lo], [round(float(v), 6) for v in hi]],
                           "size": [round(float(v), 6) for v in hi - lo],
                           "color": part.body_color(name)})
    groups = [{"name": g.name, "bodies": list(g.bodies)} for g in part.groups]
    transforms = []
    for t in part.transforms:
        row = {"name": t.name, "type": t.TYPE, "targets": list(t.targets), **t.values, "what": t.describe()}
        if t.name in result.feature_errors:
            row["error"] = result.feature_errors[t.name]
        transforms.append(row)
    return {"parameters": parameters, "features": features, "bodies": bodies, "groups": groups,
            "transforms": transforms,
            "ok": not result.errors and not result.feature_errors}


def text(data):
    """The report as text, one fact per line."""
    lines = ["parameters:" if data["parameters"] else "parameters: none"]
    for p in data["parameters"]:
        lines.append(f"  {p['name']} = {p['formula']}  ->  "
                     + (f"ERROR: {p['error']}" if "error" in p else f"{p['value']:g}"))
    lines.append("features:" if data["features"] else "features: none")
    for f in data["features"]:
        head = f"  {f['name']} ({f['type']}): " + (f"FAILED: {f['error']}" if "error" in f else "ok")
        lines.append(head)
        if f["type"] == "sketch":
            lines.append(f"    on {f['plane']}; {f['points']} points; lines {', '.join(f['lines']) or '-'}; "
                         f"circles {', '.join(f['circles']) or '-'}; polylines {', '.join(f['polylines']) or '-'}")
            for a in f.get("arcs", []):
                lines.append(f"    arc {a}")
            for c in f["curves"]:
                ends = (f"from ({', '.join(f'{v:g}' for v in c['start'])}) to "
                        f"({', '.join(f'{v:g}' for v in c['end'])})" if "start" in c else "not built")
                args = ", ".join(f"{k} = {v}" for k, v in c["args"].items())
                lines.append(f"    curve {c['name']}{'' if c['visible'] else ' (hidden)'} ({c['kind']}: {args}): {ends}; "
                             f"its ends {c['name']}.start / {c['name']}.end")
            for o in f["offsets"]:
                lines.append(f"    offset of {o['of']}: left {o['left']}, right {o['right']}, caps {o['start']} / "
                             f"{o['end']}; its profile {o['of']}.offset")
            for w in f["splines"]:
                built = f"a B-spline of {w['spans']} span(s)" if "spans" in w else "not built"
                lines.append(f"    spline {w['name']}{' (construction)' if w['construction'] else ''} through "
                             f"{', '.join(w['points'])}: {built}")
            for r in f.get("profiles", []):
                holes = f", {r['holes']} hole(s)" if r["holes"] else ""
                lines.append(f"    profile {r['key']}: area {r['area']:g} mm2{holes}")
            for r in f.get("pieces", []):
                holes = f", {r['holes']} hole(s)" if r["holes"] else ""
                lines.append(f"    piece {r['key']}: area {r['area']:g} mm2{holes} (where curves cross)")
            if "profiles" in f and not f["profiles"]:
                lines.append("    no closed profile")
        elif f["type"] == "extrude":
            lines.append(f"    {f['operation']} body {f['body']!r} from {f['sketch']} {f['profiles']}, "
                         f"distance {f['distance']}")
        elif f["type"] == "import":
            lines.append(f"    {f['file']}: {', '.join(f['bodies'])}")
        elif f["type"] == "workplane":
            if f.get("points"):
                lines.append(f"    through {', '.join(p if isinstance(p, str) else '(' + ', '.join(p) + ')' for p in f['points'])}")
            else:
                how = f"turned {f['angle']} deg around {f['axis']}" if f["axis"] else f"offset {f['offset']} mm"
                lines.append(f"    from {f['base']}, {how}")
        elif f["type"] == "revolve":
            lines.append(f"    {f['operation']} body {f['body']!r} from {f['sketch']} {f['profiles']}, "
                         f"around {f['axis']} by {f['angle']} deg")
    lines.append("bodies:" if data["bodies"] else "bodies: none")
    for b in data["bodies"]:
        lo, hi = b["bounds"]
        lines.append(f"  {b['name']}: volume {b['volume']:g} mm3, {b['solids']} solid(s), "
                     f"size {' x '.join(f'{v:g}' for v in b['size'])} mm, "
                     f"from ({', '.join(f'{v:g}' for v in lo)}) to ({', '.join(f'{v:g}' for v in hi)})"
                     + (f", color {b['color']}" if b["color"] else ""))
    for g in data["groups"]:
        lines.append(f"group {g['name']}: {', '.join(g['bodies'])}")
    for t in data["transforms"]:
        lines.append(f"placement {t['name']} ({t['type']} {', '.join(t['targets'])}): {t['what']}"
                     + (f": FAILED: {t['error']}" if "error" in t else ""))
    lines.append("ok" if data["ok"] else "NOT OK: see the errors above")
    return "\n".join(lines)


def run(path, want_report, as_json, step_path, out=sys.stdout):
    """Returns the exit code (see the top)."""
    try:
        part = load(path)
    except PartFileError as error:
        print(f"cannot read the part: {error}", file=out)
        return 2
    result = rebuild(part, folder=os.path.dirname(os.path.abspath(path)))
    data = report(part, result)
    if want_report:
        print(json.dumps(data, indent=2) if as_json else text(data), file=out)
    if step_path:
        from geometry.step_io import export_part

        try:
            note = export_part(step_path, part, result)
        except (ValueError, OSError) as error:
            print(f"STEP not written: {error}", file=out)
            return 2
        if not as_json:
            print(f"STEP written: {step_path}" + (f" ({note})" if note else ""), file=out)
    return 0 if data["ok"] else 1
