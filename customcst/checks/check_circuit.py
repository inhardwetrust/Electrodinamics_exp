# check_circuit.py
#
# Circuit elements on a run's ports (solver/circuit.py), against hand
# formulas on synthetic runs (ideal lines, so everything is exact):
#   - a line (Z0 50, port references 49.2 / 50) terminated by series and
#     parallel R, L, C: Γ = (Zin - 49.2) / (Zin + 49.2), Zin by the line
#     formula; an open parallel element (all 0) = an open end;
#   - two lines joined by a T, a Pi, a series element and a lossy line:
#     the cascade by ABCD; the result's ports are the outer two;
#   - a Touchstone element = the same lumped element (its S written to a
#     .s2p at 50 Ω, the run's inner references 45 Ω: renormalized), RI, MA
#     and DB files; a 3-port file read back;
#   - port_l: series L 1.0 nH with port_l 0.3 = L 0.7 without;
#   - errors: a missing port, a port twice, a port used by two elements, a
#     wrong port count, a missing S column; a disabled element is ignored;
#   - the project file: [circuit.*] round trip, not in the fingerprint;
#   - the window: an element added in the tree's way, Results shows the
#     outer ports with the analytic S11; the run stays current; the tree
#     rows; remove.
#
#   python checks/check_circuit.py

import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

import numpy as np                                                       # noqa: E402

from solver.circuit import (CircuitError, Element, ELEMENT_KINDS, apply_circuit, read_touchstone)  # noqa: E402
from solver.result_file import SimulationResult                         # noqa: E402

FAILED = []
F = np.linspace(0.5e9, 8e9, 76)
W = 2 * np.pi * F


def check(label, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {label} {detail}")
    if not ok:
        FAILED.append(label)


def abcd_line(z0, tau, loss=0.0):
    gl = loss * F / 1e9 / 8.685889638 + 1j * W * tau
    return np.array([[np.cosh(gl), z0 * np.sinh(gl)], [np.sinh(gl) / z0, np.cosh(gl)]])


def s_from_abcd(m, z1, z2):
    a, b, c, d = m[0, 0], m[0, 1], m[1, 0], m[1, 1]
    den = a * z2 + b + c * z1 * z2 + d * z1
    s = np.empty((len(F), 2, 2), complex)
    s[:, 0, 0] = (a * z2 + b - c * z1 * z2 - d * z1) / den
    s[:, 0, 1] = 2 * (a * d - b * c) * np.sqrt(z1 * z2) / den
    s[:, 1, 0] = 2 * np.sqrt(z1 * z2) / den
    s[:, 1, 1] = (-a * z2 + b - c * z1 * z2 + d * z1) / den
    return s


def chain(*ms):
    out = ms[0]
    for m in ms[1:]:
        out = np.einsum("ijf,jkf->ikf", out, m)
    return out


def ser(z):
    z = z + 0j * F
    return np.array([[1 + 0 * z, z], [0 * z, 1 + 0 * z]])


def sh(y):
    y = y + 0j * F
    return np.array([[1 + 0 * y, 0 * y], [y, 1 + 0 * y]])


def run_of(blocks, names, refs):
    """A synthetic run: 2-port blocks on the diagonal (ports in order)."""
    n = 2 * len(blocks)
    r = SimulationResult(F, names, refs)
    s = np.zeros((len(F), n, n), complex)
    for i, b in enumerate(blocks):
        s[:, 2 * i:2 * i + 2, 2 * i:2 * i + 2] = b
    r.s = {(j, k): s[:, j, k] for j in range(n) for k in range(n)}
    r.name, r.fingerprint = "Run 1", "fp"
    r.eps_eff = [2.0] * n
    return r


def element(name, kind, ports, **values):
    v = ELEMENT_KINDS[kind].defaults()
    v.update(values)
    return Element(name, kind, list(ports), v)


def close(a, b, tol=1e-9):
    return np.nanmax(np.abs(np.asarray(a) - np.asarray(b))) < tol


def one_ports():
    tau = 80e-12
    run = run_of([s_from_abcd(abcd_line(50.0, tau), 49.2, 50.0)], ["J1", "P_pin"], [49.2, 50.0])
    bl = W * tau
    for label, kind_values, zl in (
        ("series 30 Ω + 1.2 nH + 0.8 pF", dict(topology="series", r=30.0, l=1.2, c=0.8),
         30 + 1j * W * 1.2e-9 + 1 / (1j * W * 0.8e-12)),
        ("parallel 50 Ω ‖ 0.3 pF", dict(topology="parallel", r=50.0, c=0.3), 1 / (1 / 50 + 1j * W * 0.3e-12)),
        ("series R 0 L 0 C 0 (a short)", dict(topology="series"), 0 * W),
    ):
        out, note = apply_circuit(run, [element("t1", "rlc", ["P_pin"], **kind_values)])
        zin = 50 * (zl + 1j * 50 * np.tan(bl)) / (50 + 1j * zl * np.tan(bl))
        check(f"1-port {label}: Γ by the line formula (ref 49.2)", out.ports == ["J1"]
              and close(out.s[0, 0], (zin - 49.2) / (zin + 49.2)), note)
    out, _ = apply_circuit(run, [element("t1", "rlc", ["P_pin"], topology="parallel")])
    zin = -1j * 50 / np.tan(bl)
    check("parallel all 0 = an open end", close(out.s[0, 0], (zin - 49.2) / (zin + 49.2)))

    out, _ = apply_circuit(run, [element("a", "rlc", ["P_pin"], topology="series", l=1.0, port_l=0.3)])
    ref, _ = apply_circuit(run, [element("a", "rlc", ["P_pin"], topology="series", l=0.7)])
    check("port_l 0.3 on L 1.0 = L 0.7", close(out.s[0, 0], ref.s[0, 0]))
    out, _ = apply_circuit(run, [element("a", "rlc", ["P_pin"], r=40.0)])
    out.enabled = None
    dis = element("a", "rlc", ["P_pin"], r=40.0)
    dis.enabled = False
    same, note = apply_circuit(run, [dis])
    check("a disabled element: the run itself, no note", same is run and note == "")


def two_ports(folder):
    la, lb = abcd_line(50.0, 60e-12), abcd_line(50.0, 90e-12, loss=0.05)
    run = run_of([s_from_abcd(la, 50.0, 45.0), s_from_abcd(lb, 45.0, 49.2)],
                 ["J4", "P_rfc", "P_rf2", "J2"], [50.0, 45.0, 45.0, 49.2])
    cases = (
        ("T", element("sw", "t", ["P_rfc", "P_rf2"], l1=1.7, r1=3.5, c=0.59, r2=3.5, l2=2.0),
         chain(ser(3.5 + 1j * W * 1.7e-9), sh(1j * W * 0.59e-12), ser(3.5 + 1j * W * 2.0e-9))),
        ("Pi", element("sw", "pi", ["P_rfc", "P_rf2"], c1=0.2, l=1.1, r=2.0, c2=0.4),
         chain(sh(1j * W * 0.2e-12), ser(2.0 + 1j * W * 1.1e-9), sh(1j * W * 0.4e-12))),
        ("series C 2.2 pF + 0.4 nH", element("c6", "series", ["P_rfc", "P_rf2"], l=0.4, c=2.2),
         ser(1j * W * 0.4e-9 + 1 / (1j * W * 2.2e-12))),
        ("line 35 Ω 40 ps 0.1 dB/GHz", element("ln", "line", ["P_rfc", "P_rf2"], z0=35.0, delay=40.0, loss=0.1),
         abcd_line(35.0, 40e-12, 0.1)),
    )
    for label, e, m in cases:
        out, note = apply_circuit(run, [e])
        want = s_from_abcd(chain(la, m, lb), 50.0, 49.2)
        ok = out.ports == ["J4", "J2"] and all(close(out.s[j, k], want[:, j, k]) for j in range(2) for k in range(2))
        check(f"2-port {label}: the ABCD cascade, outer ports J4, J2", ok, note)

    # the same T as a Touchstone file (50 Ω; the run's inner references are 45 Ω)
    t = cases[0][1]
    want = s_from_abcd(chain(la, cases[0][2], lb), 50.0, 49.2)
    s_t = s_from_abcd(cases[0][2], 50.0, 50.0)
    for fmt in ("RI", "MA", "DB"):
        path = os.path.join(folder, f"sw_{fmt}.s2p")
        with open(path, "w") as f:
            f.write(f"! test\n# MHz S {fmt} R 50\n")
            for i, x in enumerate(F):
                vals = []
                for jk in ((0, 0), (1, 0), (0, 1), (1, 1)):
                    v = s_t[i][jk]
                    if fmt == "RI":
                        vals += [v.real, v.imag]
                    elif fmt == "MA":
                        vals += [abs(v), np.degrees(np.angle(v))]
                    else:
                        vals += [20 * np.log10(abs(v)), np.degrees(np.angle(v))]
                f.write(f"{x / 1e6:.10g} " + " ".join(f"{v:.15g}" for v in vals) + "\n")
        e = element("sw", "touchstone", ["P_rfc", "P_rf2"], file=os.path.basename(path))
        out, note = apply_circuit(run, [e], folder)
        ok = all(close(out.s[j, k], want[:, j, k], 1e-9) for j in range(2) for k in range(2))
        check(f"Touchstone {fmt} (50 Ω, renormalized to 45) = the lumped T", ok, note)

    # a narrower file: NaN outside, noted
    path = os.path.join(folder, "narrow.s2p")
    keep = (F >= 2e9) & (F <= 6e9)
    with open(path, "w") as f:
        f.write("# GHz S RI R 50\n")
        for i in np.nonzero(keep)[0]:
            v = s_t[i]
            f.write(f"{F[i] / 1e9:.10g} " + " ".join(f"{x.real:.15g} {x.imag:.15g}"
                                                     for x in (v[0, 0], v[1, 0], v[0, 1], v[1, 1])) + "\n")
    out, note = apply_circuit(run, [element("sw", "touchstone", ["P_rfc", "P_rf2"], file=path)])
    check("a file narrower than the run: NaN outside, noted", np.isnan(out.s[0, 0][~keep]).all()
          and np.isfinite(out.s[0, 0][keep]).all() and "outside a file's band" in note, note)

    # 3-port file read back
    from solver.vna import write_touchstone
    s3 = {(j, k): (j + 1) * 0.1 + 1j * (k + 1) * 0.01 + 0 * F for j in range(3) for k in range(3)}
    path = os.path.join(folder, "three.s3p")
    write_touchstone(path, F, s3, ["a", "b", "c"], 50.0)
    f3, m3, r3 = read_touchstone(path)
    check("a 3-port file read back (row by row)", close(f3, F, 1e-3) and r3 == 50.0
          and all(close(m3[:, j, k], s3[j, k]) for j in range(3) for k in range(3)))

    # two elements at once: termination of J2 by 50 Ω + the T: a 1-port result
    out, note = apply_circuit(run, [t, element("ld", "rlc", ["J2"], r=49.2)])
    check("T + a 49.2 Ω load on J2: one port left, S11 = the 2-port's S11", out.ports == ["J4"]
          and close(out.s[0, 0], want[:, 0, 0]), note)

    # errors
    for label, elements, text in (
        ("a missing port", [element("x", "rlc", ["nope"], r=1.0)], "no port nope"),
        ("a port twice", [element("x", "t", ["P_rfc", "P_rfc"])], "twice"),
        ("a wrong port count", [element("x", "t", ["P_rfc"])], "needs 2 ports"),
        ("a port used twice", [element("x", "rlc", ["J2"], r=1.0), element("y", "rlc", ["J2"], r=2.0)],
         "already used by x"),
        ("no file", [element("x", "touchstone", [])], "no .sNp"),
        ("a file with 2 ports on 1", [element("x", "touchstone", ["J2"], file=os.path.join(folder, "sw_RI.s2p"))],
         "needs 2 ports"),
    ):
        out, note = apply_circuit(run, elements, folder)
        good = [e for e in elements if text not in note]
        check(f"error: {label}", text in note, note)
    partial = run_of([s_from_abcd(la, 50.0, 45.0)], ["J4", "P_rfc"], [50.0, 45.0])
    del partial.s[0, 1]
    out, note = apply_circuit(partial, [element("x", "rlc", ["P_rfc"], r=10.0)])
    check("a missing S column: not applied, said why", out is partial and "full S matrix" in note, note)
    try:
        read_touchstone(os.path.join(folder, "sw_RI.s2p").replace(".s2p", ".txt"))
        check("a non-.sNp name refused", False)
    except CircuitError:
        check("a non-.sNp name refused", True)
    return run, want


def project_file(folder):
    from checks.check_disk_reload import write_model
    from project.project_file import Project
    from solver.result_file import fingerprint

    step = os.path.join(folder, "p", "m.step")
    os.makedirs(os.path.dirname(step))
    write_model(step)
    project = Project.load(step)
    before = fingerprint(project)
    name = project.add_element("t", ports=["P_rfc", "P_rf2"])
    project.set_element_value(name, "l1", "1.7")
    project.set_element_value(name, "port_l", 0.3)
    project.add_element("touchstone", "meas")
    project.set_element_value("meas", "file", "data/switch.s3p")
    project.set_element_enabled("meas", False)
    project.rename_element(name, "switch")
    project.save()
    again = Project.load(step)
    sw, meas = again.circuit["switch"], again.circuit["meas"]
    check("[circuit] round trip", list(again.circuit) == ["switch", "meas"] and sw.kind == "t"
          and sw.ports == ["P_rfc", "P_rf2"] and sw.values["l1"] == 1.7 and sw.values["port_l"] == 0.3
          and meas.values["file"] == "data/switch.s3p" and meas.ports == ["", "", ""] and not meas.enabled)
    check("the circuit is not in the fingerprint", fingerprint(again) == before)
    for bad, label in (('kind = "nope"', "unknown kind"), ('kind = "t"\nl1 = -1.0', "must be >="),
                       ('kind = "t"\nfoo = 1', "unknown key")):
        with open(project.path, "a", encoding="utf-8") as f:
            f.write(f"\n[circuit.bad]\n{bad}\n")
        try:
            Project.load(step)
            check(f"a bad element refused: {label}", False)
        except Exception as error:
            check(f"a bad element refused: {label}", label in str(error), str(error)[-60:])
        project.save()
    try:
        project.add_element("t", "switch")
        check("a duplicate name refused", False)
    except Exception:
        check("a duplicate name refused", True)


def window_check(folder, run, want):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from checks.check_disk_reload import write_model, write_types
    from project.project_file import Project
    from solver.result_file import fingerprint, save_result
    from view.main_window import MainWindow

    step = os.path.join(folder, "win", "w.step")
    os.makedirs(os.path.dirname(step))
    write_model(step)
    write_types(step, {"board": "dielectric", "patch": "pec"})
    run.fingerprint = fingerprint(Project.load(step))
    run.name = ""
    save_result(step, run)

    app = QApplication.instance() or QApplication(sys.argv)
    window = MainWindow(step)
    window._disk_timer.stop()
    window.show()
    window.wait_for_mesh()
    app.processEvents()
    try:
        check("no circuit: the run's 4 ports shown", window.results.primary.ports == ["J4", "P_rfc", "P_rf2", "J2"])
        window.add_element("t")
        name = next(iter(window.project.circuit))
        window.set_element_port(name, 0, "P_rfc")
        window.set_element_port(name, 1, "P_rf2")
        for key, value in (("l1", 1.7), ("r1", 3.5), ("c", 0.59), ("r2", 3.5), ("l2", 2.0)):
            window.set_element_value(name, key, value)
        app.processEvents()
        shown = window.results.primary
        check("the T added: Results shows J4, J2 with the cascade's S", shown.ports == ["J4", "J2"]
              and close(shown.s[0, 0], want[:, 0, 0]) and close(shown.s[1, 0], want[:, 1, 0]), str(shown.ports))
        check("the run stays current", window._is_current(shown))
        group = window._tree_item("circuit", None)
        rows = [group.child(i) for i in range(group.childCount())]
        el = next(r for r in rows if r.data(0, Qt.UserRole)[0] == "circuit_element")
        kids = [el.child(i).text(0) for i in range(el.childCount())]
        check("tree: Circuit > the element with its ports and values", el.text(0) == name
              and "port 1" in kids and "port 2" in kids and "L1" in kids, str(kids))
        saved = Project.load(step).circuit
        check("saved in the project file", name in saved and saved[name].values["l1"] == 1.7)
        window.set_element_enabled(name, False)
        app.processEvents()
        check("disabled: the 4 ports again", window.results.primary.ports == ["J4", "P_rfc", "P_rf2", "J2"])
        window.set_element_enabled(name, True)
        window.rename_element(name, "switch")
        window.remove_element("switch")
        app.processEvents()
        check("removed: the 4 ports again, the file without it",
              window.results.primary.ports == ["J4", "P_rfc", "P_rf2", "J2"] and not Project.load(step).circuit)
    finally:
        window.close()
        app.processEvents()


def main():
    folder = tempfile.mkdtemp()
    os.environ["CUSTOMCST_SETTINGS"] = os.path.join(folder, "settings.json")
    os.environ["CUSTOMCST_REMOTE"] = os.path.join(folder, "remote.toml")
    try:
        one_ports()
        run, want = two_ports(folder)
        project_file(folder)
        if "--no-window" not in sys.argv:
            window_check(folder, run, want)
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    print("\nall ok" if not FAILED else f"\nFAILED: {FAILED}")
    sys.exit(1 if FAILED else 0)


if __name__ == "__main__":
    main()
