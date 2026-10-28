# sweep.py
#
# Runs variants of a parametric model from tools/make_test_models.py and
# prints one line of figures per variant (the antenna-design skill's tuning
# loop). Works the same here and on the server (remote_tasks.py sweep).
#
# A generator stem S in tools/make_test_models.py provides S() (the bodies),
# S_project(path) (types, materials, band, far field) and a parameter dict
# (--params, e.g. PATCH_5G8_4L). Each variant updates that dict, writes
# <folder>/<stem>_<tag>.step and its .toml, sets the mesh and the far-field
# frequencies, meshes and runs it (solver/simulation.py simulate()).
#
#   python .claude/skills/antenna-design/scripts/sweep.py FOLDER \
#       --stem patch_5g8_4layer --params PATCH_5G8_4L \
#       --variants '[{"l": 11.0, "via_x": 2.0}, {"l": 11.4, "via_x": 2.0}]' \
#       --cells 30 --feature 4 --f0 5.8 --farfield 5.8
#
# Per variant: S11 minimum (dB, GHz), S11 and Z_in at f0, D, G, realized G,
# radiation efficiency and the beam's direction at the first far-field
# frequency, and the front (theta < 80) asymmetry of the E (phi = 0) and H
# (phi = 90) planes in dB. Also a JSON line ("JSON {...}") for scripts.
# Run from the customcst folder.

import argparse
import json
import os
import sys
import time

ROOT = os.getcwd()
sys.path.insert(0, ROOT)

import numpy as np                                                       # noqa: E402

import tools.make_test_models as models                                  # noqa: E402
from geometry.step_io import write_step                                  # noqa: E402
from project.project_file import Project                                 # noqa: E402
from solver.farfield import cut, figures                                 # noqa: E402
from solver.simulation import prepare, problem, simulate                 # noqa: E402


def front_asymmetry(ff, k, phi):
    """Max |U(theta) - U(-theta)| in dB over theta 1..79 of the phi plane."""
    angles, values = cut(ff, k, "phi", phi)
    return float(np.abs(values[1:80] - values[::-1][1:80]).max())


def run(folder, stem, params, base, variant, cells, feature, f0, farfield):
    values = getattr(models, params)
    values.clear()
    values.update(base, **variant)          # each variant from the generator's own values
    tag = "_".join(f"{k}{v}" for k, v in variant.items()) or "base"
    path = os.path.join(folder, f"{stem}_{tag}.step")
    write_step(path, getattr(models, stem)())
    getattr(models, f"{stem}_project")(path)
    project = Project.load(path)
    project.set_setting("mesh", "cells_per_wavelength", cells)
    project.set_setting("mesh", "cells_per_feature", feature)
    project.set_setting("farfield", "frequencies", ", ".join(f"{f:g}" for f in farfield))
    project.save()

    model = prepare(path)
    why = problem(model.project, model.mesh, model.materials)
    if why or model.materials.shorts:
        print(f"{tag}: cannot run: {why or model.materials.shorts}", flush=True)
        return
    sim = model.project.settings["simulation"]
    begin = time.perf_counter()
    result = simulate(model.materials, sim["f_min"], sim["f_max"], farfield=farfield)
    port = result.ports[0]
    s = np.abs(result.s[0, 0])
    f = result.frequencies
    i, k = int(s.argmin()), int(np.argmin(np.abs(f - f0 * 1e9)))
    z = result.z_in[port][k]
    out = {"tag": tag, "grid": list(model.mesh.grid.shape), "s11_min_db": 20 * np.log10(s[i]),
           "s11_min_ghz": f[i] / 1e9, "s11_f0_db": 20 * np.log10(s[k]), "z_f0": [z.real, z.imag],
           "seconds": time.perf_counter() - begin}
    if port in result.farfield:
        ff = result.farfield[port]
        x = figures(ff)[0]
        out.update(d=x["directivity"], g=x["gain"], realized=x["realized"], efficiency=x["efficiency"],
                   theta=x["theta"], phi=x["phi"], asym_e=front_asymmetry(ff, 0, 0.0),
                   asym_h=front_asymmetry(ff, 0, 90.0))
    line = (f"{tag} {tuple(out['grid'])}: S11 min {out['s11_min_db']:.1f} dB at {out['s11_min_ghz']:.3f} GHz, "
            f"at {f0:g} {out['s11_f0_db']:.1f} dB, Z {z.real:.0f}{z.imag:+.0f}j")
    if "d" in out:
        line += (f"; D {out['d']:.2f} G {out['g']:.2f} realized {out['realized']:.2f} dBi, "
                 f"eff {100 * out['efficiency']:.0f} %, max theta {out['theta']:g} phi {out['phi']:g}, "
                 f"front asym E {out['asym_e']:.1f} H {out['asym_h']:.1f} dB")
    print(line + f"; {out['seconds']:.0f} s", flush=True)
    print("JSON " + json.dumps(out), flush=True)


def main():
    parser = argparse.ArgumentParser(description="Runs variants of a parametric model and prints their figures.")
    parser.add_argument("folder")
    parser.add_argument("--stem", required=True, help="generator in tools/make_test_models.py")
    parser.add_argument("--params", required=True, help="its parameter dict, e.g. PATCH_5G8_4L")
    parser.add_argument("--variants", required=True, help='JSON list of parameter changes')
    parser.add_argument("--cells", type=float, default=30.0, help="mesh cells per wavelength")
    parser.add_argument("--feature", type=float, default=4.0, help="mesh cells per feature")
    parser.add_argument("--f0", type=float, required=True, help="GHz, the design frequency")
    parser.add_argument("--farfield", default="", help="GHz, comma separated ('' = no far field)")
    args = parser.parse_args()
    os.makedirs(args.folder, exist_ok=True)
    farfield = [float(x) for x in args.farfield.split(",") if x.strip()]
    base = dict(getattr(models, args.params))
    for variant in json.loads(args.variants):
        run(args.folder, args.stem, args.params, base, variant, args.cells, args.feature, args.f0, farfield)


if __name__ == "__main__":
    main()
