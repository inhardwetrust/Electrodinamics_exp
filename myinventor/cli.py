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
import sys

from model.part_file import PartFileError, load
from model.profiles import find_regions
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
            row["curves"] = [{"name": c.name, "kind": c.kind, "args": dict(c.args),
                              **({"start": [round(float(v), 6) for v in out.points[c.name + ".start"]],
                                  "end": [round(float(v), 6) for v in out.points[c.name + ".end"]]}
                                 if out is not None and c.name in out.shapes else {})}
                             for c in feature.curves]
            row["splines"] = [{"name": w.name, "points": list(w.points), "construction": w.construction,
                               **({"spans": len(out.shapes[w.name].edges[0][2]) - 1}
                                  if out is not None and w.name in out.shapes else {})}
                              for w in feature.splines]
            if out is not None:
                row["profiles"] = [{"key": list(r.key), "area": round(r.area, 6), "holes": len(r.holes)}
                                   for r in find_regions(out)]
        elif feature.TYPE == "extrude":
            row.update(sketch=feature["sketch"], profiles=[list(p) for p in feature.profiles],
                       distance=feature["distance"], operation=feature["operation"], body=feature["body"])
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
    return {"parameters": parameters, "features": features, "bodies": bodies,
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
            for c in f["curves"]:
                ends = (f"from ({', '.join(f'{v:g}' for v in c['start'])}) to "
                        f"({', '.join(f'{v:g}' for v in c['end'])})" if "start" in c else "not built")
                args = ", ".join(f"{k} = {v}" for k, v in c["args"].items())
                lines.append(f"    curve {c['name']} ({c['kind']}: {args}): {ends}; "
                             f"its ends {c['name']}.start / {c['name']}.end")
            for w in f["splines"]:
                built = f"a B-spline of {w['spans']} span(s)" if "spans" in w else "not built"
                lines.append(f"    spline {w['name']}{' (construction)' if w['construction'] else ''} through "
                             f"{', '.join(w['points'])}: {built}")
            for r in f.get("profiles", []):
                holes = f", {r['holes']} hole(s)" if r["holes"] else ""
                lines.append(f"    profile {r['key']}: area {r['area']:g} mm2{holes}")
            if "profiles" in f and not f["profiles"]:
                lines.append("    no closed profile")
        elif f["type"] == "extrude":
            lines.append(f"    {f['operation']} body {f['body']!r} from {f['sketch']} {f['profiles']}, "
                         f"distance {f['distance']}")
    lines.append("bodies:" if data["bodies"] else "bodies: none")
    for b in data["bodies"]:
        lo, hi = b["bounds"]
        lines.append(f"  {b['name']}: volume {b['volume']:g} mm3, {b['solids']} solid(s), "
                     f"size {' x '.join(f'{v:g}' for v in b['size'])} mm, "
                     f"from ({', '.join(f'{v:g}' for v in lo)}) to ({', '.join(f'{v:g}' for v in hi)})"
                     + (f", color {b['color']}" if b["color"] else ""))
    lines.append("ok" if data["ok"] else "NOT OK: see the errors above")
    return "\n".join(lines)


def run(path, want_report, as_json, step_path, out=sys.stdout):
    """Returns the exit code (see the top)."""
    try:
        part = load(path)
    except PartFileError as error:
        print(f"cannot read the part: {error}", file=out)
        return 2
    result = rebuild(part)
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
