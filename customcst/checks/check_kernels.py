# check_kernels.py
#
# The numba kernels (solver/fdtd_kernels.py) against the numpy path of
# solver/fdtd.py: the same model, the same steps, both paths.
#   - a grounded coplanar line with box waveguide ports (CPML on all sides,
#     a mode source) and a line with a lumped port and a lumped element:
#     every E and H value and every port record identical (the same float32
#     arithmetic in the same order);
#   - the field energy (the stop criterion) equal within 1e-12;
#   - the kernels are faster (they should be several times faster).
# Skipped (passes) without numba.
#
#   python checks/check_kernels.py      (~30 s)

import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8")

import numpy as np                                                       # noqa: E402

import check_waveport as cw                                              # noqa: E402
from discretize.materials import discretize                              # noqa: E402
from geometry.step_io import read_step                                   # noqa: E402
from mesh.rectilinear import build_grid                                  # noqa: E402
from project.project_file import Project                                 # noqa: E402
from solver import fdtd                                                  # noqa: E402

FAILED = []
STEPS = 400


def check(label, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {label} {detail}")
    if not ok:
        FAILED.append(label)


def models():
    g, p = cw.pcb_line(tempfile.mkdtemp(), "coplanar")
    yield "coplanar, box ports", cw.build(g, p)[2], "port1"

    step = os.path.join(ROOT, "models", "02_linie50x30_3_with_cap.stp")
    g = read_step(step)
    p = Project.load(step)
    ports, elements = p.ports(g), p.elements(g)
    mesh = build_grid(g, p, {**ports, **elements})
    m = discretize(mesh, g, p, ports, elements)
    yield "line with a lumped port and element", m, m.ports[0].name


def main():
    if not fdtd.kernels.AVAILABLE:
        print("numba not installed (or CUSTOMCST_NUMPY=1): nothing to compare")
        print("all checks passed")
        return 0
    try:
        for name, m, active in models():
            runs = {}
            for fast in (False, True):
                fdtd.kernels.AVAILABLE = fast
                solver = fdtd.FdtdSolver(m, 1.0, 5.0, active_port=active)
                solver.step(2)              # compile outside the timing
                solver.energy()
                solver.reset()
                start = time.perf_counter()
                solver.step(STEPS)
                runs[fast] = (solver, time.perf_counter() - start)
            (a, t_numpy), (b, t_numba) = runs[False], runs[True]
            same = all(np.array_equal(x, y) for x, y in zip(a.e + a.h, b.e + b.h))
            check(f"{name}: identical fields after {STEPS} steps", same,
                  f"max |Δ| {max(np.abs(x - y).max() for x, y in zip(a.e + a.h, b.e + b.h)):.2e}")
            records = all(ra.v == rb.v and ra.i == rb.i for ra, rb in zip(a.records, b.records))
            check(f"{name}: identical port records", records)
            ea, eb = a.energy(), b.energy()
            check(f"{name}: same energy", abs(eb / ea - 1) < 1e-12, f"{ea:.6e} / {eb:.6e}")
            check(f"{name}: kernels faster", t_numba < t_numpy,
                  f"numpy {t_numpy / STEPS * 1e3:.2f} ms/step, numba {t_numba / STEPS * 1e3:.2f} ms/step, "
                  f"x{t_numpy / t_numba:.1f}")
    finally:
        fdtd.kernels.AVAILABLE = True

    print("FAILED: " + ", ".join(FAILED) if FAILED else "all checks passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
