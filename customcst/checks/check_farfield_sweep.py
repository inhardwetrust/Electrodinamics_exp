# check_farfield_sweep.py
#
# The far field against frequency: [farfield] sweep_points
# (project/settings.py), the run's far field at the listed and the sweep's
# frequencies (solver/simulation.py, solver/farfield.py sweep(),
# realized_from_s()), the Far field tab's slider and the Far field vs f tab
# (view/farfield_panel.py, view/farfield_sweep_panel.py), the control
# command farfield_f.
#
# Settings:
#   - the sweep: N points evenly over f min .. f max, both ends in, rounded
#     to 0.1 MHz; a point within 1 MHz of a listed frequency left out; the
#     merged list sorted with its `named` mask; off (enabled false) or 0:
#     none; N = 1, 2.5 and 202 refused; the key round-trips the project
#     file, a file without it reads as 0;
#   - the auto padding follows the listed frequencies (a sweep added keeps
#     it and the run fingerprint), a sweep alone: λ/2 at f min.
# A run: the 47 mm dipole of check_farfield (2-4 GHz), 3.0 GHz listed plus
# a sweep of 5 (2, 2.5, [3.0 = the listed], 3.5, 4):
#   - the pattern at 3.0 GHz bit-identical to a run with 3.0 alone, the
#     port's powers within 1e-12 (the port DFT sums all frequencies at
#     once): a sweep changes nothing at the listed frequencies;
#   - `named` saved and read back; an older file without it: all listed;
#   - the run's info: one line per listed frequency, one for the sweep;
#   - sweep() at the maximum equals figures(); realized = G + mismatch
#     exactly; the mismatch from the far field's powers equals
#     10 log10(1 - |S11|^2) of the run's S (within 0.01 dB), and so does
#     realized_from_s() at the far-field frequencies;
#   - the readout warns at the band's outer tenths when the run stopped
#     above -60 dB (there the pulse is weak, see farfield_sweep_panel);
#   - a fixed direction: theta 90 (broadside) reads the maximum's D within
#     0.05 dB, theta 0 (the axis) a null; the beamwidth in the phi 0 cut at
#     3 GHz is the half-wave dipole's ~78 degrees (within 4).
# The tabs (offscreen): the Far field tab lists 5 frequencies, the sweep's
# marked, a slider over them; moving it moves the Far field vs f marker and
# back; both views draw; the readout and the control command farfield_f
# answer the figures at the marker.
#
#   python checks/check_farfield_sweep.py      (~1 min)

import math
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np                                                       # noqa: E402

from checks.check_farfield import GAP, RADIUS, cone_z, rod_z             # noqa: E402
from discretize.materials import discretize                              # noqa: E402
from geometry.step_io import read_step, write_step                       # noqa: E402
from mesh.rectilinear import build_grid                                  # noqa: E402
from project.project_file import Project                                 # noqa: E402
from project.settings import (auto_padding, check_settings, farfield_frequencies, farfield_named,  # noqa: E402
                              farfield_sweep, far_field_padding_mm, merge_frequencies)
from solver.farfield import figures, in_dbi, component_intensity, mismatch_db, named_mask, realized_from_s, sweep, summary  # noqa: E402
from solver.result_file import fingerprint, read_result, write_result    # noqa: E402
from solver.simulation import simulate                                   # noqa: E402

FAILED = []
LENGTH = 47.0           # mm, the half-wave dipole of check_farfield


def check(label, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {label} {detail}")
    if not ok:
        FAILED.append(label)


def settings_checks(folder):
    path = os.path.join(folder, "s.step")
    write_step(path, [("box", rod_z(0.0, 1.0, 1.0), None)])
    p = Project(path)
    p.set_setting("simulation", "f_min", 4.5)
    p.set_setting("simulation", "f_max", 7.0)
    p.set_setting("farfield", "frequencies", "5.42, 5.75")
    check("no sweep: the listed frequencies only", farfield_frequencies(p.settings) == [5.42, 5.75])
    padding_listed = auto_padding(p.settings)
    fp_listed = fingerprint(p)
    p.set_setting("farfield", "sweep_points", 11)
    sweep_f = farfield_sweep(p.settings)
    expect = [round(x, 4) for x in np.linspace(4.5, 7.0, 11) if abs(x - 5.75) > 1e-9]   # 5.75 is listed
    check("sweep: 11 points over the band, both ends, the listed 5.75 left out", sweep_f == expect, f"{sweep_f}")
    p.set_setting("farfield", "sweep_points", 6)            # 4.5 5 5.5 6 6.5 7: none near a listed one
    p.set_setting("farfield", "frequencies", "5.5005, 6.2")
    check("a sweep point within 1 MHz of a listed one is left out", 5.5 not in farfield_sweep(p.settings)
          and len(farfield_sweep(p.settings)) == 5, f"{farfield_sweep(p.settings)}")
    merged, named = merge_frequencies(farfield_named(p.settings), farfield_sweep(p.settings))
    check("merged sorted, the listed marked", merged == sorted(merged) and len(merged) == 7
          and [f for f, n in zip(merged, named) if n] == [5.5005, 6.2], f"{merged} {named}")
    p.set_setting("farfield", "frequencies", "5.42, 5.75")
    p.set_setting("farfield", "sweep_points", 11)
    check("a sweep keeps the listed frequencies' padding", auto_padding(p.settings) == padding_listed,
          f"{auto_padding(p.settings)[1]}")
    check("a sweep keeps the run fingerprint", fingerprint(p) == fp_listed)
    p.set_setting("farfield", "enabled", False)
    check("far field off: no sweep either", farfield_frequencies(p.settings) == [])
    p.set_setting("farfield", "enabled", True)
    p.set_setting("farfield", "frequencies", "")
    meters, why = auto_padding(p.settings)
    check("a sweep alone: λ/2 at f min", abs(meters - 299_792_458.0 / 4.5e9 / 2) < 1e-12, why)
    check("a sweep alone: the padding in the fingerprint", far_field_padding_mm(p.settings) is not None)
    for bad in (1, 2.5, 202, -1):
        try:
            p.set_setting("farfield", "sweep_points", bad)
            check(f"sweep points {bad} refused", False)
        except ValueError as error:
            check(f"sweep points {bad} refused", True, f"({error})")
    p.set_setting("farfield", "sweep_points", 21)
    p.save()
    back = Project.load(path)
    check("sweep points round-trip the project file", back.settings["farfield"]["sweep_points"] == 21)
    with open(p.path, encoding="utf-8") as f:
        text = f.read()
    with open(p.path, "w", encoding="utf-8") as f:
        f.write("\n".join(line for line in text.splitlines() if not line.startswith("sweep_points")) + "\n")
    old = Project.load(path)
    check("a project file without the key: 0", old.settings["farfield"]["sweep_points"] == 0
          and farfield_sweep(old.settings) == [])
    check_settings(old.settings)


def dipole(folder):
    path = os.path.join(folder, "dipole.step")
    half = 0.5 * LENGTH
    write_step(path, [
        ("arm_low", rod_z(-half, -0.5 * GAP, RADIUS), None),
        ("arm_high", rod_z(0.5 * GAP, half, RADIUS), None),
        ("feed", cone_z(-0.5 * GAP, 0.5 * GAP, 0.4, 0.3), None),
    ])
    g = read_step(path)
    project = Project(path)
    for body in g.bodies:
        project.set_type(body, "port" if body.name == "feed" else "pec")
    project.set_setting("simulation", "f_min", 2.0)
    project.set_setting("simulation", "f_max", 4.0)
    project.set_setting("farfield", "frequencies", "3.0")
    project.set_setting("farfield", "sweep_points", 5)
    project.save()
    ports = project.ports(g)
    mesh = build_grid(g, project, ports)
    materials = discretize(mesh, g, project, ports)
    named, swept = farfield_named(project.settings), farfield_sweep(project.settings)
    check("the sweep leaves out 3.0 (listed)", swept == [2.0, 2.5, 3.5, 4.0], f"{swept}")
    result = simulate(materials, 2.0, 4.0, farfield=named, farfield_sweep=swept)
    alone = simulate(materials, 2.0, 4.0, farfield=[3.0])
    ff, ff1 = result.farfield["feed"], alone.farfield["feed"]
    check("five far-field frequencies, 3.0 listed", list(ff["frequencies"] / 1e9) == [2.0, 2.5, 3.0, 3.5, 4.0]
          and list(ff["named"]) == [False, False, True, False, False], f"{ff['frequencies'] / 1e9}")
    same = all(np.array_equal(ff[key][2], ff1[key][0]) for key in ("e_theta", "e_phi", "p_box", "p_sphere"))
    check("3.0 GHz: the pattern bit-identical with or without the sweep", same)
    off = max(abs(ff[key][2] / ff1[key][0] - 1) for key in ("p_acc", "p_inc"))
    check("3.0 GHz: the port's powers the same (the DFT's summing order)", off < 1e-12, f"{off:.1e}")

    saved = os.path.join(folder, "dipole.npz")
    write_result(saved, result)
    back = read_result(saved)
    check("named comes back from the run file", list(named_mask(back.farfield["feed"])) == list(ff["named"]))
    older = {k: v for k, v in ff.items() if k != "named"}
    check("an older far field without named: all listed", named_mask(older).all())
    lines = summary(ff)
    print("\n".join(f"     {line}" for line in lines))
    check("summary: the listed frequency and one sweep line",
          len(lines) == 2 and lines[0].startswith("3 GHz") and lines[1].startswith("sweep: 4 frequencies 2-4 GHz"))
    check("the run's info has the summary", any("sweep: 4 frequencies" in line for line in result.info))
    return result, path


def sweep_checks(result):
    ff = result.farfield["feed"]
    sw = sweep(ff)
    rows = figures(ff)
    worst = max(abs(sw[key][k] - rows[k][key]) for k in range(5) for key in ("directivity", "gain", "realized"))
    check("sweep at the maximum = figures()", worst < 1e-9, f"{worst:.1e}")
    check("efficiency = figures()", np.allclose(sw["efficiency"], [r["efficiency"] for r in rows]))
    budget = np.max(np.abs(sw["realized"] - (sw["gain"] + sw["mismatch"])))
    check("realized = G + mismatch", budget < 1e-9, f"{budget:.1e} dB")
    f = np.asarray(result.frequencies)
    s11 = np.asarray(result.s[(0, 0)])
    index = [int(np.argmin(np.abs(f - x))) for x in sw["frequency"]]
    off = np.max(np.abs(mismatch_db(s11[index]) - sw["mismatch"]))
    check("mismatch of the far field's powers = 10 log10(1 - |S11|²)", off < 0.01, f"worst {off:.4f} dB")
    fs, values = realized_from_s(sw, f, s11)
    at = [int(np.argmin(np.abs(fs - x))) for x in sw["frequency"]]
    off = np.max(np.abs(values[at] - sw["realized"]))
    check("realized from S11 = realized at the far-field frequencies", off < 0.01, f"worst {off:.4f} dB")
    check("realized from S11 spans the band", abs(fs[0] - 2e9) < 1 and abs(fs[-1] - 4e9) < 1 and len(fs) > 300)
    print("     f GHz   D      G      realized  eff    mismatch  bw phi0")
    for k in range(5):
        print(f"     {sw['frequency'][k] / 1e9:4.2f}  {sw['directivity'][k]:5.2f}  {sw['gain'][k]:5.2f}  "
              f"{sw['realized'][k]:6.2f}    {sw['efficiency'][k]:.3f}  {sw['mismatch'][k]:6.2f}    "
              f"{sw['beamwidth_phi0'][k]:.1f}")

    side = sweep(ff, (90.0, 0.0))
    off = np.max(np.abs(side["directivity"] - sw["directivity"]))
    check("fixed theta 90 (broadside) = the maximum within 0.05 dB", off < 0.05, f"{off:.3f} dB")
    check("fixed direction: the grid point used", np.all(side["theta"] == 90.0) and np.all(side["phi"] == 0.0))
    k = 2
    expect = float(in_dbi(ff, k, component_intensity(ff, k)[90, 0], "realized"))
    check("fixed direction = the pattern there", abs(side["realized"][k] - expect) < 1e-9)
    axis = sweep(ff, (0.0, 0.0))
    check("fixed theta 0 (the axis): a null", np.all(axis["directivity"] < -20.0), f"{axis['directivity'].max():.1f} dBi")
    check("beamwidth (phi 0 cut) at 3 GHz ~78 degrees", abs(sw["beamwidth_phi0"][2] - 78.0) < 4.0,
          f"{sw['beamwidth_phi0'][2]:.1f}")
    try:
        sweep(ff, (190.0, 0.0))
        check("theta 190 refused", False)
    except ValueError:
        check("theta 190 refused", True)


def tabs_checks(result):
    from PySide6.QtWidgets import QApplication, QTabWidget

    from view import control
    from view.farfield_panel import FarFieldPanel
    from view.farfield_sweep_panel import FarFieldSweepPanel

    app = QApplication.instance() or QApplication(sys.argv)
    result.name = "Run 1"
    pattern, versus = FarFieldPanel(), FarFieldSweepPanel()
    pattern.frequency_changed.connect(versus.set_frequency)
    versus.frequency_changed.connect(pattern.set_frequency)
    pattern.set_results([result])
    versus.set_results([result])
    labels = [pattern.frequency.itemText(i) for i in range(pattern.frequency.count())]
    check("Far field tab: 5 frequencies, the sweep's marked", len(labels) == 5
          and labels[2] == "3 GHz" and labels[0].endswith("· sweep"), f"{labels}")
    check("Far field tab: a slider over them", pattern.slider.maximum() == 4 and not pattern.slider.isHidden())
    pattern.slider.setValue(3)
    check("the slider moves the frequency and the vs f marker", pattern.frequency.currentData() == 3.5e9
          and versus.marker == 3.5e9, f"{versus.marker}")
    versus._move_marker(2.52e9)
    check("the vs f marker moves the Far field tab", pattern.frequency.currentData() == 2.5e9
          and pattern.slider.value() == 1)
    for view in ("gain", "beam"):
        versus.view.setCurrentIndex(list(versus.view.itemData(i) for i in range(2)).index(view))
        image = versus.grab()
        check(f"vs f: the {view} view draws", not image.isNull() and len(versus.plot._curves) >= 3,
              f"{len(versus.plot._curves)} curves")
    styles = None
    versus.view.setCurrentIndex(0)
    styles = {c[0]: c[5] for c in versus.plot._curves}
    check("vs f: far-field values as points, from S11 dashed, the mismatch a line",
          styles.get("Realized G") == "points" and styles.get("Realized G from S11") == "dash"
          and styles.get("Mismatch") == "line", f"{styles}")
    text = versus.info.text()
    check("vs f: the readout at the marker", "2.5 GHz (sweep" in text and "realized = G + mismatch" in text
          and "within 3 dB" in text, text.replace("<br>", " | ")[:200])

    class Window:                                   # what control.farfield_f uses of MainWindow
        tabs = QTabWidget()
        farfield_sweep_panel = versus
    Window.tabs.addTab(versus, "Far field vs f")
    answer = control.farfield_f(Window, frequency=3.0, direction="fixed", theta=90, phi=0,
                                curves=["realized", "realized_s"])
    check("farfield_f: the marker, the direction, the curves", answer["frequency_ghz"] == 3.0
          and answer["at_marker"]["listed"] and answer["at_marker"]["theta"] == 90.0
          and answer["curves"] == ["realized", "realized_s"] and pattern.frequency.currentData() == 3.0e9,
          f"{answer['at_marker']}")
    from view.farfield_sweep_panel import weak_edge
    result.params = {"simulation": {"f_min": 2.0, "f_max": 4.0, "accuracy": "-50"}}
    check("a warning at the band's edge below accuracy -60", weak_edge(result, 2.0e9) == -50.0
          and weak_edge(result, 2.5e9) is None and weak_edge(result, 3.95e9) == -50.0)
    versus._move_marker(2.0e9)
    check("vs f: the readout warns at the edge", "near the band's edge" in versus.info.text())
    result.params["simulation"]["accuracy"] = "-60"
    check("no warning at accuracy -60", weak_edge(result, 2.0e9) is None)
    for bad in ({"curves": ["nope"]}, {"show": "nope"}, {"theta": 200}):
        try:
            control.farfield_f(Window, **bad)
            check(f"farfield_f {bad} refused", False)
        except control.CommandError as error:
            check(f"farfield_f {bad} refused", True, f"({error})")
    app.processEvents()


def main():
    folder = tempfile.mkdtemp()
    try:
        settings_checks(folder)
        result, _ = dipole(folder)
        sweep_checks(result)
        tabs_checks(result)
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    print()
    print("all checks passed" if not FAILED else f"FAILED: {len(FAILED)}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
