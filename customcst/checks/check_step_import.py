# check_step_import.py
#
# Level 0 checks: STEP round trip keeps names, kinds, colors and units, and
# the triangles approximate the exact CAD geometry (area / volume vs OCCT's
# exact integrals; a solid's triangles must be closed and outward).
#
#   python checks/check_step_import.py

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import numpy as np                                       # noqa: E402
from OCP.BRepGProp import BRepGProp                      # noqa: E402
from OCP.GProp import GProp_GProps                       # noqa: E402

from geometry.step_io import MM, read_step               # noqa: E402

FAILED = []
DEFLECTION = 20e-6   # meters


def check(label, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {label} {detail}")
    if not ok:
        FAILED.append(label)


def exact(shape, kind):
    props = GProp_GProps()
    if kind == "solid":
        BRepGProp.VolumeProperties_s(shape, props)
        return props.Mass() * MM ** 3
    BRepGProp.SurfaceProperties_s(shape, props)
    return props.Mass() * MM ** 2


def closed(body):
    """Every edge of a closed surface is used twice. Faces do not share
    vertices, so compare edges by rounded coordinates."""
    key = np.round(body.vertices / 1e-9).astype(np.int64)
    ids = np.unique(key, axis=0, return_inverse=True)[1].reshape(-1)
    tri = ids[body.triangles]
    e = np.concatenate([tri[:, [0, 1]], tri[:, [1, 2]], tri[:, [2, 0]]])
    e = np.sort(e, axis=1)
    _, counts = np.unique(e, axis=0, return_counts=True)
    return bool(np.all(counts == 2))


def main():
    models = os.path.join(ROOT, "models")

    dipole = read_step(os.path.join(models, "dipole_2g4.step"), DEFLECTION)
    check("dipole names", [b.name for b in dipole.bodies] == ["arm_top", "arm_bottom"])
    lo, hi = dipole.bounds
    check("dipole units are meters", np.allclose(hi[2] - lo[2], 0.058, rtol=1e-3),
          f"length {1e3 * (hi[2] - lo[2]):.3f} mm")

    patch = read_step(os.path.join(models, "patch_2g4.step"), DEFLECTION)
    kinds = {b.name: b.kind for b in patch.bodies}
    check("patch kinds", kinds == {"substrate": "solid", "ground": "sheet",
                                  "patch": "solid", "feed_pin": "solid"}, str(kinds))
    check("colors kept", patch["substrate"].color is not None
          and abs(patch["substrate"].color[1] - 0.55) < 1e-3)

    for geometry in (dipole, patch):
        for body in geometry.bodies:
            if body.kind == "solid":
                # The triangles lie within DEFLECTION of the true surface,
                # so the volume can differ by at most area * DEFLECTION.
                ref, got = exact(body.shape, "solid"), body.volume
                bound = body.area * DEFLECTION
                check(f"{body.name}: volume", abs(got - ref) <= bound,
                      f"error {abs(got - ref) / ref:.2e} (bound {bound / ref:.2e})")
                check(f"{body.name}: closed surface", closed(body))
            else:
                ref, got = exact(body.shape, "sheet"), body.area
                check(f"{body.name}: area", abs(got - ref) <= 1e-6 * ref,
                      f"{got:.4e} vs exact {ref:.4e} m^2")

    print("FAILED: " + ", ".join(FAILED) if FAILED else "all checks passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
