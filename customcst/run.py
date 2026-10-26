# run.py
#
# The console runner: a simulation without the window, e.g. on a faster
# Linux machine. Set the model up in the app (the STEP and its .toml),
# copy both there, run, copy model.results/<run>.npz back into the
# model's results folder: the app lists it as a current run (the same
# fingerprint: STEP bytes, types, values, settings).
#
#   python run.py model.stp                 run (excite as in the project)
#   python run.py model.stp --excite "J2 5" one port only
#   python run.py model.stp --threads 16    numba threads (default: all cores)
#   python run.py model.stp --dry-run       mesh, checks, memory; no run
#   python run.py model.stp --allow-shorts  run although metal joins a port's ends
#
# It runs solver/simulation.py, the window's own steps. Progress goes to
# stdout every --every seconds (default 30); Ctrl+C stops (nothing is
# saved). Exit code: 0 saved, 1 failed, 2 not runnable, 130 stopped.
# --excite changes the setting in memory only; it is in the fingerprint,
# so the app shows such a run as outdated unless the project has the same.
# Without a terminal: nohup python run.py model.stp > run.log 2>&1 &

import argparse
import os
import signal
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def main(argv):
    parser = argparse.ArgumentParser(description="customcst: run a simulation without the window")
    parser.add_argument("step", help="the model's STEP file (its .toml beside it)")
    parser.add_argument("--excite", help="the one port to excite (default: the project's setting)")
    parser.add_argument("--threads", type=int, help="numba threads (default: all cores)")
    parser.add_argument("--dry-run", action="store_true", help="mesh, check and size the model; no run")
    parser.add_argument("--allow-shorts", action="store_true", help="run although metal joins a port's ends")
    parser.add_argument("--every", type=float, default=30.0, help="seconds between progress lines (default 30)")
    args = parser.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    except AttributeError:
        pass

    if args.threads:
        try:
            import numba
            numba.set_num_threads(min(args.threads, numba.config.NUMBA_NUM_THREADS))
        except ImportError:
            pass

    from project.project_file import ProjectError
    from project.settings import EXCITE_ALL, accuracy_db
    from solver import fdtd_kernels
    from solver.result_file import fingerprint, format_duration, save_result
    from solver.simulation import parameters, port_names, prepare, problem, simulate

    step = os.path.abspath(args.step)
    begin = time.perf_counter()
    print(f"model      {step}")
    if not os.path.isfile(step):
        print(f"no such file: {step}")
        return 2
    try:
        model = prepare(step, progress=_discretize_progress(args.every))
    except (OSError, ProjectError, ValueError) as error:
        print(f"cannot read the model: {error}")
        return 2
    project, mesh, materials = model.project, model.mesh, model.materials
    sim = project.settings["simulation"]
    if args.excite:
        project.settings["simulation"]["excite"] = args.excite      # in memory only: the file is not written

    print(f"prepared   in {format_duration(time.perf_counter() - begin)}")
    if mesh is not None and mesh.ok:
        print(f"grid       {parameters(project, mesh, materials, model.via_bodies)['grid']}"
              if materials is not None else f"grid       {mesh.grid.cell_count:,} cells")
    for line in (mesh.warnings if mesh is not None else []) + (materials.warnings if materials else []):
        print(f"warning    {line}")
    why = problem(project, mesh, materials)
    if why:
        print(f"cannot simulate: {why}")
        return 2
    params = parameters(project, mesh, materials, model.via_bodies)
    for line in params["ports"]:
        print(f"port       {line}")
    for line in params["elements"]:
        print(f"element    {line}")
    if materials.shorts:
        for line in materials.shorts:
            print(f"short      {line}")
        if not args.allow_shorts:
            print("metal joins what should be apart (see above); --allow-shorts runs anyway")
            return 2

    excite = sim["excite"]
    excited = port_names(materials) if excite == EXCITE_ALL else [excite]
    end_db = accuracy_db(project.settings)
    threads = _threads()
    print(f"run        {sim['f_min']:g}-{sim['f_max']:g} GHz, ports {', '.join(excited)}, "
          f"stops at {end_db:.0f} dB; {'numba' if fdtd_kernels.AVAILABLE else 'numpy (slow)'}"
          + (f", {threads} threads" if threads else ""))

    model_fingerprint = fingerprint(project)
    if args.dry_run:
        from solver.fdtd import FdtdSolver
        solver = FdtdSolver(materials, sim["f_min"], sim["f_max"], active_port=excited[0],
                            trim_pulse=sim["trim_pulse"])
        print(f"memory     {(_array_bytes(solver) + _array_bytes(materials)) / 2**30:.2f} GB of arrays "
              f"({int(solver.shape[0]) * int(solver.shape[1]) * int(solver.shape[2]):,} cells with PML), "
              f"time step {solver.dt * 1e15:.1f} fs, pulse {solver.pulse.duration * 1e9:.2f} ns")
        print("dry run: nothing simulated")
        return 0

    stop = {"now": False}

    def interrupt(*_):
        if stop["now"]:
            raise KeyboardInterrupt
        stop["now"] = True
        print("\nstopping (Ctrl+C again to kill)...")

    signal.signal(signal.SIGINT, interrupt)
    state = {"last": 0.0, "port": None, "start": 0.0}

    def started(port, solver):
        state.update(port=port, start=time.perf_counter(), last=time.perf_counter())
        index = excited.index(port) + 1 if port in excited else 0
        print(f"port {index}/{len(excited)} {port}: {_array_bytes(solver) / 2**30:.2f} GB, "
              f"pulse {solver.pulse.duration * 1e9:.2f} ns")

    def progress(port, t, level, pulse_duration):
        now = time.perf_counter()
        if now - state["last"] >= args.every:
            state["last"] = now
            where = (f"pulse {100 * t / pulse_duration:3.0f} %" if t < pulse_duration
                     else f"energy {level:6.1f} dB -> {end_db:.0f}")
            print(f"  {format_duration(now - state['start']):>12}  t {t * 1e9:6.3f} ns  {where}")
        return not stop["now"]

    try:
        result = simulate(materials, sim["f_min"], sim["f_max"], sim["trim_pulse"], end_db,
                          None if excite == EXCITE_ALL else [excite], progress=progress, started=started)
    except KeyboardInterrupt:
        print("killed: nothing saved")
        return 130
    except Exception as error:          # report, do not lose the traceback
        import traceback
        traceback.print_exc()
        print(f"failed: {type(error).__name__}: {error}")
        return 1
    if result is None:
        print("stopped: nothing saved")
        return 130

    result.fingerprint = model_fingerprint
    result.params = params
    path = save_result(step, result)
    for line in result.info:
        print(f"done       {line}")
    for k, name in enumerate(result.ports):
        if (k, k) in result.s:
            s = abs(result.s[k, k])
            i = int(s.argmin())
            print(f"S{k + 1}{k + 1}        min {20 * _log10(s[i]):.1f} dB at {result.frequencies[i] / 1e9:.3f} GHz ({name})")
    print(f"saved      {path}  ({result.name}, {format_duration(result.elapsed)})")
    print("copy it into the model's results folder on the other machine: "
          f"{os.path.basename(os.path.dirname(path))}/")
    return 0


def _discretize_progress(every):
    state = {"last": time.perf_counter()}

    def progress(done, total, what):
        now = time.perf_counter()
        if now - state["last"] >= every:
            state["last"] = now
            print(f"  meshing {done}/{total}: {what}")
    return progress


def _threads():
    try:
        import numba
        return numba.get_num_threads()
    except ImportError:
        return None


def _array_bytes(obj, depth=0):
    """Bytes of the numpy arrays an object holds (its attributes, lists, tuples, dicts)."""
    import numpy as np
    if isinstance(obj, np.ndarray):
        return obj.nbytes
    if depth > 3:
        return 0
    if isinstance(obj, (list, tuple)):
        return sum(_array_bytes(v, depth + 1) for v in obj)
    if isinstance(obj, dict):
        return sum(_array_bytes(v, depth + 1) for v in obj.values())
    if depth == 0 and hasattr(obj, "__dict__"):
        return sum(_array_bytes(v, depth + 1) for k, v in vars(obj).items() if k != "materials")
    return 0


def _log10(x):
    import math
    return math.log10(max(x, 1e-30))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
