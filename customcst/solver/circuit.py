# circuit.py
#
# Circuit elements on a finished run's ports (like CST's schematic over the
# 3D result): no re-run. A port of the model (a lumped port cone or a
# waveguide port) is where the 3D field meets the circuit; an element
# connected to it terminates it (a 1-port: R, L, C, a .s1p) or joins ports
# (a 2-port: a series element, a T or Pi network, a line, a .s2p; an N-port
# .sNp). The ports no element uses stay the run's outer ports: the result
# shown is the S matrix of those.
#
# Project file (project/project_file.py), one table per element:
#   [circuit.switch]
#   kind = "t"                      # ELEMENT_KINDS
#   enabled = true
#   ports = ["P_pin", "P_rf2"]      # the run's port names, in the element's order
#   l1 = 1.7 ...                    # the kind's parameters (units as shown)
#   port_l = 0.0                    # nH taken off at each element port (see below)
# A Touchstone element has `file` (relative to the model's folder, or
# absolute); its port count is the file's.
#
# Not a result input: the circuit is applied when the runs are shown, so
# changing it never outdates a run (it is not in the fingerprint).
#
# Math. Every block (the run, each element, the port-L corrections) is an
# S matrix with real reference impedances; a connection joins two ports
# with the same reference (a_p = b_q, a_q = b_p). With all blocks on the
# diagonal of S (outer ports E, joined ports I) and the connection matrix
# C (C_pq = 1 for a joined pair):
#   S_EE' = S_EE + S_EI C (1 - S_II C)^-1 S_IE
# An element port joined to run port p uses p's reference (the run's
# resistances: R of a lumped port, the line's Z0 of a waveguide port), so
# a lumped element's S is computed in that reference, a Touchstone file's
# is renormalized to it (Kurokawa, real references).
#
# port_l: a lumped port's edge column has its own inductance (~0.3 nH for
# a 0.8 mm gap in the justswitch model, measured against the lumped
# element body). The element then sees it in series: port_l > 0 puts
# -port_l in series at each element port, so the element acts as its own
# values.
#
# Lumped values: 0 = that part is absent, as for the lumped element body
# (series: R 0 / L 0 shorted, C 0 shorted (no capacitor); parallel: each
# 0 part open).

import os
import re
from dataclasses import dataclass, field

import numpy as np

from project.object_types import Param

PORT_L = Param("port_l", "port L off", 0.0, minimum=0.0, maximum=10.0, unit="nH",
               tooltip="Inductance taken off at each element port: a lumped port's own edge column "
                       "(~0.3 nH for a 0.8 mm gap). 0 for a waveguide port")


def _rlc(prefix=""):
    return (
        Param("topology", "R, L, C in", "series", choices=("series", "parallel")),
        Param("r", "R", 0.0, minimum=0.0, unit="Ω"),
        Param("l", "L", 0.0, minimum=0.0, unit="nH"),
        Param("c", "C", 0.0, minimum=0.0, unit="pF"),
    )


@dataclass(frozen=True)
class ElementKind:
    name: str
    label: str
    ports: int                      # 0 = from the file (Touchstone)
    params: tuple = ()
    note: str = ""

    def param(self, name):
        for p in self.params + (PORT_L,):
            if p.name == name:
                return p
        raise KeyError(name)

    def all_params(self):
        return self.params + (PORT_L,)

    def defaults(self):
        return {p.name: p.default for p in self.all_params()}


ELEMENT_KINDS = {
    k.name: k
    for k in (
        ElementKind("rlc", "R, L, C (1-port)", 1, _rlc(),
                    "terminates a port: R, L, C in series or parallel; 0 = that part absent"),
        ElementKind("series", "Series R, L, C (2-port)", 2, _rlc(),
                    "between two ports: port 1 — Z — port 2 (no ground)"),
        ElementKind("t", "T network (2-port)", 2, (
            Param("l1", "L1", 0.0, minimum=0.0, unit="nH"),
            Param("r1", "R1", 0.0, minimum=0.0, unit="Ω"),
            Param("c", "C shunt", 0.0, minimum=0.0, unit="pF"),
            Param("r2", "R2", 0.0, minimum=0.0, unit="Ω"),
            Param("l2", "L2", 0.0, minimum=0.0, unit="nH"),
        ), "port 1 — L1 + R1 — shunt C to ground — R2 + L2 — port 2"),
        ElementKind("pi", "Pi network (2-port)", 2, (
            Param("c1", "C1 shunt", 0.0, minimum=0.0, unit="pF"),
            Param("l", "L", 0.0, minimum=0.0, unit="nH"),
            Param("r", "R", 0.0, minimum=0.0, unit="Ω"),
            Param("c2", "C2 shunt", 0.0, minimum=0.0, unit="pF"),
        ), "port 1 — shunt C1 — series L + R — shunt C2 — port 2"),
        ElementKind("line", "Line (2-port)", 2, (
            Param("z0", "Z0", 50.0, minimum=0.1, unit="Ω"),
            Param("delay", "delay", 0.0, minimum=0.0, unit="ps"),
            Param("loss", "loss", 0.0, minimum=0.0, unit="dB/GHz",
                  tooltip="Attenuation of the whole line, growing in proportion to f"),
        ), "a TEM line: Z0, one-way delay, loss"),
        ElementKind("touchstone", "Touchstone file (.sNp)", 0, (
            Param("file", "file", "", text=True,
                  tooltip="A .s1p / .s2p / ... file: relative to the model's folder, or absolute"),
        ), "S-parameters from a file (maker's or measured), interpolated to the run's frequencies"),
    )
}


@dataclass
class Element:
    name: str
    kind: str
    ports: list = field(default_factory=list)       # run port names, in the element's port order
    values: dict = field(default_factory=dict)
    enabled: bool = True

    @property
    def element_kind(self):
        return ELEMENT_KINDS[self.kind]


class CircuitError(ValueError):
    pass


# =====================================================
# Touchstone
# =====================================================

_UNITS = {"hz": 1.0, "khz": 1e3, "mhz": 1e6, "ghz": 1e9}


def read_touchstone(path):
    """
    (frequencies Hz (F,), S (F, n, n), reference R) of a Touchstone v1 file
    .sNp: the option line `# <unit> S <RI|MA|DB> R <r>` (defaults GHz S MA
    R 50), 2-port order S11 S21 S12 S22, others row by row.
    """
    match = re.search(r"\.s(\d+)p$", path.lower())
    if not match:
        raise CircuitError(f"{os.path.basename(path)}: not a .sNp file name")
    n = int(match.group(1))
    if n < 1:
        raise CircuitError(f"{os.path.basename(path)}: no ports")
    unit, fmt, r_ref = 1e9, "ma", 50.0
    numbers = []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.split("!")[0].strip()
                if not line:
                    continue
                if line.startswith("["):
                    raise CircuitError(f"{os.path.basename(path)}: Touchstone 2 keywords are not read")
                if line.startswith("#"):
                    words = line[1:].lower().split()
                    i = 0
                    while i < len(words):
                        w = words[i]
                        if w in _UNITS:
                            unit = _UNITS[w]
                        elif w in ("ri", "ma", "db"):
                            fmt = w
                        elif w == "r" and i + 1 < len(words):
                            r_ref = float(words[i + 1])
                            i += 1
                        elif w != "s":
                            raise CircuitError(f"{os.path.basename(path)}: only S parameters are read, got {w!r}")
                        i += 1
                    continue
                numbers += [float(v) for v in line.split()]
    except OSError as error:
        raise CircuitError(f"cannot read {path}: {error}") from None
    except ValueError as error:
        raise CircuitError(f"{os.path.basename(path)}: {error}") from None

    per = 1 + 2 * n * n
    if not numbers or len(numbers) % per:
        raise CircuitError(f"{os.path.basename(path)}: {len(numbers)} numbers, not a multiple of {per} "
                           f"({n} ports)")
    data = np.array(numbers).reshape(-1, per)
    f = data[:, 0] * unit
    a, b = data[:, 1::2], data[:, 2::2]
    if fmt == "ri":
        values = a + 1j * b
    elif fmt == "ma":
        values = a * np.exp(1j * np.radians(b))
    else:
        values = 10 ** (a / 20) * np.exp(1j * np.radians(b))
    s = values.reshape(-1, n, n)
    if n == 2:
        s = s.transpose(0, 2, 1)                    # S11 S21 S12 S22: column by column
    if np.any(np.diff(f) <= 0):
        raise CircuitError(f"{os.path.basename(path)}: frequencies not increasing")
    return f, s, r_ref


def _interpolate(f_file, s, f):
    """S of a file at the run's frequencies (linear in Re, Im); NaN outside the file's band."""
    out = np.empty((len(f),) + s.shape[1:], complex)
    for j in range(s.shape[1]):
        for k in range(s.shape[2]):
            out[:, j, k] = (np.interp(f, f_file, s[:, j, k].real, left=np.nan, right=np.nan)
                            + 1j * np.interp(f, f_file, s[:, j, k].imag, left=np.nan, right=np.nan))
    return out


def renormalize(s, r_old, r_new):
    """S (F, n, n) from real references r_old (n,) to r_new (n,) (Kurokawa power waves)."""
    r_old = np.broadcast_to(np.asarray(r_old, float), (s.shape[1],))
    r_new = np.broadcast_to(np.asarray(r_new, float), (s.shape[1],))
    g = (r_new - r_old) / (r_new + r_old)
    a = 2.0 * np.sqrt(r_new * r_old) / (r_new + r_old)
    eye = np.eye(s.shape[1])
    out = (s - np.diag(g)) @ np.linalg.inv(eye - g[:, None] * s)
    return out * a[None, None, :] / a[None, :, None]


# =====================================================
# Element S matrices
# =====================================================

def _rlc_z(v, w):
    """(Z, open) of an R, L, C in series or parallel; 0 = that part absent."""
    r, l, c = v["r"], v["l"] * 1e-9, v["c"] * 1e-12
    if v["topology"] == "series":
        z = np.full(w.shape, r, complex) + 1j * w * l
        if c > 0:
            z = z + 1 / (1j * w * c)
        return z, False
    y = np.zeros(w.shape, complex)
    if r > 0:
        y = y + 1 / r
    if l > 0:
        y = y + 1 / (1j * w * l)
    if c > 0:
        y = y + 1j * w * c
    with np.errstate(divide="ignore", invalid="ignore"):
        return 1 / y, bool(np.all(y == 0))


def _abcd_series(z):
    one, zero = np.ones_like(z), np.zeros_like(z)
    return np.array([[one, z], [zero, one]])


def _abcd_shunt(y):
    one, zero = np.ones_like(y), np.zeros_like(y)
    return np.array([[one, zero], [y, one]])


def _chain(*blocks):
    out = blocks[0]
    for b in blocks[1:]:
        out = np.einsum("ijf,jkf->ikf", out, b)
    return out


def _abcd_to_s(m, z1, z2):
    """(F, 2, 2) S of an ABCD (2, 2, F) with real references z1, z2."""
    a, b, c, d = m[0, 0], m[0, 1], m[1, 0], m[1, 1]
    den = a * z2 + b + c * z1 * z2 + d * z1
    s = np.empty((m.shape[-1], 2, 2), complex)
    s[:, 0, 0] = (a * z2 + b - c * z1 * z2 - d * z1) / den
    s[:, 0, 1] = 2 * (a * d - b * c) * np.sqrt(z1 * z2) / den
    s[:, 1, 0] = 2 * np.sqrt(z1 * z2) / den
    s[:, 1, 1] = (-a * z2 + b - c * z1 * z2 + d * z1) / den
    return s


def _lumped_abcd(kind, v, w):
    jw = 1j * w
    zero = np.zeros(w.shape, complex)
    if kind == "series":
        z, is_open = _rlc_z(v, w)
        if is_open:
            raise CircuitError("a series element with nothing in it is an open: no connection")
        return _abcd_series(z)
    if kind == "t":
        return _chain(_abcd_series(v["r1"] + jw * v["l1"] * 1e-9),
                      _abcd_shunt(jw * v["c"] * 1e-12 + zero),
                      _abcd_series(v["r2"] + jw * v["l2"] * 1e-9))
    if kind == "pi":
        return _chain(_abcd_shunt(jw * v["c1"] * 1e-12 + zero),
                      _abcd_series(v["r"] + jw * v["l"] * 1e-9 + zero),
                      _abcd_shunt(jw * v["c2"] * 1e-12 + zero))
    if kind == "line":
        f = w / (2 * np.pi)
        alpha = v["loss"] * f / 1e9 / 8.685889638          # Np
        gl = alpha + 1j * w * v["delay"] * 1e-12
        z0 = v["z0"]
        return np.array([[np.cosh(gl), z0 * np.sinh(gl)], [np.sinh(gl) / z0, np.cosh(gl)]])
    raise CircuitError(f"unknown kind {kind!r}")


def element_s(element, frequencies, references, base_dir=""):
    """S (F, n, n) of an element in the given real references (one per element port)."""
    kind, v = element.kind, element.values
    f = np.asarray(frequencies, float)
    w = 2 * np.pi * f
    refs = np.asarray(references, float)
    if kind == "rlc":
        z, is_open = _rlc_z(v, w)
        s = np.empty((len(f), 1, 1), complex)
        if is_open:
            s[:, 0, 0] = 1.0
        else:
            with np.errstate(divide="ignore", invalid="ignore"):
                g = (z - refs[0]) / (z + refs[0])
            s[:, 0, 0] = np.where(np.isfinite(z), g, 1.0)    # a parallel L at DC: z = 0 is fine, inf is open
        return s
    if kind == "touchstone":
        path = resolve_file(v.get("file", ""), base_dir)
        f_file, s_file, r_file = read_touchstone(path)
        if s_file.shape[1] != len(refs):
            raise CircuitError(f"{os.path.basename(path)} has {s_file.shape[1]} ports, "
                               f"{len(refs)} are connected")
        return renormalize(_interpolate(f_file, s_file, f), r_file, refs)
    return _abcd_to_s(_lumped_abcd(kind, v, w), refs[0], refs[1])


def resolve_file(text, base_dir):
    text = str(text).strip()
    if not text:
        raise CircuitError("no file given")
    path = text if os.path.isabs(text) else os.path.join(base_dir, text)
    return os.path.normpath(path)


def port_count(element, base_dir=""):
    """How many ports the element has (a Touchstone file: from its name)."""
    kind = element.element_kind
    if kind.ports:
        return kind.ports
    match = re.search(r"\.s(\d+)p$", str(element.values.get("file", "")).strip().lower())
    return int(match.group(1)) if match else 0


# =====================================================
# Connecting
# =====================================================

def check_elements(elements, run_ports, base_dir=""):
    """{element name: error text} for the enabled elements that cannot be applied to these ports."""
    errors, used = {}, {}
    for e in elements:
        if not e.enabled:
            continue
        n = port_count(e, base_dir)
        if n == 0:
            errors[e.name] = "no .sNp file given"
            continue
        if len(e.ports) != n or any(not p for p in e.ports):
            errors[e.name] = f"needs {n} port{'s' * (n > 1)}, {sum(1 for p in e.ports if p)} set"
            continue
        if len(set(e.ports)) != len(e.ports):
            errors[e.name] = "the same port twice"
            continue
        missing = [p for p in e.ports if p not in run_ports]
        if missing:
            errors[e.name] = f"no port {', '.join(missing)} in the run"
            continue
        taken = [p for p in e.ports if p in used]
        if taken:
            errors[e.name] = f"{', '.join(taken)} already used by {used[taken[0]]}"
            continue
        for p in e.ports:
            used[p] = e.name
    return errors


def connect(frequencies, run_s, run_refs, run_ports, elements, base_dir=""):
    """
    S (F, m, m) of the run's outer ports with the enabled, valid elements
    connected, and the outer port indices into run_ports. run_s: (F, n, n).
    Raises CircuitError (an element's S cannot be made).
    """
    f = np.asarray(frequencies, float)
    w = 2 * np.pi * f
    n = len(run_ports)
    index = {p: i for i, p in enumerate(run_ports)}
    blocks = [run_s]                # (F, k, k) each, on the diagonal
    pairs = []                      # (global port, global port) joined
    offset = n
    for e in elements:
        refs = [run_refs[index[p]] for p in e.ports]
        try:
            s_e = element_s(e, f, refs, base_dir)
        except CircuitError as error:
            raise CircuitError(f"{e.name}: {error}") from None
        k = s_e.shape[1]
        lp = float(e.values.get("port_l", 0.0))
        el_start = offset + (2 * k if lp > 0 else 0)
        if lp > 0:
            # -L in series at each element port: a 2-port per port, run side first
            for i, p in enumerate(e.ports):
                z = -1j * w * lp * 1e-9
                r = refs[i]
                s_l = np.empty((len(f), 2, 2), complex)
                s_l[:, 0, 0] = s_l[:, 1, 1] = z / (z + 2 * r)
                s_l[:, 0, 1] = s_l[:, 1, 0] = 2 * r / (z + 2 * r)
                blocks.append(s_l)
                pairs += [(index[p], offset + 2 * i), (offset + 2 * i + 1, el_start + i)]
        else:
            pairs += [(index[p], el_start + i) for i, p in enumerate(e.ports)]
        blocks.append(s_e)
        offset = el_start + k

    total = offset
    S = np.zeros((len(f), total, total), complex)
    at = 0
    for b in blocks:
        k = b.shape[1]
        S[:, at:at + k, at:at + k] = b
        at += k
    joined = sorted({p for pair in pairs for p in pair})
    outer = [i for i in range(n) if i not in joined]
    if not pairs:
        return run_s, outer
    pos = {p: i for i, p in enumerate(joined)}
    C = np.zeros((len(joined), len(joined)))
    for p, q in pairs:
        C[pos[p], pos[q]] = C[pos[q], pos[p]] = 1.0
    s_ee = S[:, outer][:, :, outer]
    s_ei = S[:, outer][:, :, joined]
    s_ie = S[:, joined][:, :, outer]
    s_ii = S[:, joined][:, :, joined]
    eye = np.eye(len(joined))
    out = np.full(s_ee.shape, np.nan + 0j)
    ok = np.isfinite(S).all(axis=(1, 2))           # a file's band may not cover the run's: NaN there
    if ok.any():
        inner = np.linalg.solve(eye - s_ii[ok] @ C, s_ie[ok])
        out[ok] = s_ee[ok] + s_ei[ok] @ C @ inner
    return out, outer


def apply_circuit(run, elements, base_dir=""):
    """
    (shown run, note): a new SimulationResult with the enabled elements
    connected (the outer ports only), or the run itself when nothing
    applies; note says what was done or why not ("" = no circuit).
    """
    from solver.result_file import SimulationResult

    active = [e for e in elements if e.enabled]
    if not active:
        return run, ""
    errors = check_elements(active, run.ports, base_dir)
    good = [e for e in active if e.name not in errors]
    notes = [f"{name}: {text}" for name, text in errors.items()]
    if not good:
        return run, "circuit not applied: " + "; ".join(notes)
    n = len(run.ports)
    missing = [f"S{j + 1}{k + 1}" for j in range(n) for k in range(n) if (j, k) not in run.s]
    if missing:
        return run, ("circuit not applied: it needs the full S matrix (excite all ports), "
                     f"{run.name} has no {', '.join(missing[:4])}{' ...' if len(missing) > 4 else ''}")
    run_s = np.stack([np.stack([np.asarray(run.s[j, k]) for k in range(n)], axis=-1) for j in range(n)], axis=-2)
    try:
        s, outer = connect(run.frequencies, run_s, run.resistances, run.ports, good, base_dir)
    except CircuitError as error:
        return run, f"circuit not applied: {error}"
    except np.linalg.LinAlgError:
        return run, "circuit not applied: the connection is singular (a loop of ideal elements?)"
    if not outer:
        return run, "circuit not applied: it uses every port of the run (nothing left to show)"
    out = SimulationResult(run.frequencies, [run.ports[i] for i in outer],
                           [run.resistances[i] for i in outer], run.comment)
    out.s = {(j, k): s[:, j, k] for j in range(len(outer)) for k in range(len(outer))}
    out.fingerprint, out.saved, out.name = run.fingerprint, run.saved, run.name
    out.params, out.elapsed, out.path = run.params, run.elapsed, run.path
    out.eps_eff = [run.eps_eff[i] for i in outer]
    nan = np.isnan(s).any(axis=(1, 2))
    if nan.any():
        notes.append(f"no data at {int(nan.sum())} of {len(nan)} frequencies (outside a file's band)")
    applied = ", ".join(f"{e.name} ({e.element_kind.label.split(' (')[0]} on {', '.join(e.ports)})" for e in good)
    note = "circuit: " + applied + ("; " + "; ".join(notes) if notes else "")
    out.info = list(run.info) + [note]
    out.circuit_source = run
    return out, note


def describe(element):
    """One line: the kind's values (for the tree / the control answer)."""
    kind = element.element_kind
    parts = []
    for p in kind.params:
        value = element.values.get(p.name, p.default)
        if p.choices or p.text:
            parts.append(f"{value}")
        elif value:
            parts.append(f"{p.label} {value:g}{(' ' + p.unit) if p.unit else ''}")
    if element.values.get("port_l"):
        parts.append(f"−{element.values['port_l']:g} nH per port")
    return ", ".join(parts)
