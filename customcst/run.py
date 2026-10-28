# run.py
#
# The console runner: a simulation without the window, e.g. on a faster
# Linux machine. Set the model up in the app (the STEP and its .toml),
# copy both there, run, copy model.results/<run>.npz back into the
# model's results folder: the app lists it as a current run (the same
# fingerprint: STEP bytes, types, values, settings). The app's Remote tab
# does the copying and starts, follows and stops this runner over SSH.
#
#   python run.py model.stp                 run (excite as in the project)
#   python run.py model.stp --excite "J2 5" one port only
#   python run.py model.stp --threads 16    numba threads (default: all cores)
#   python run.py model.stp --dry-run       mesh, checks, memory; no run
#   python run.py model.stp --allow-shorts  run although metal joins a port's ends
#
# It runs solver/simulation.py, the window's own steps. Progress goes to
# stdout every --every seconds (default 30); Ctrl+C or SIGTERM stops
# (nothing is saved). Exit code: 0 saved, 1 failed, 2 not runnable,
# 3 another run is going, 130 stopped.
# --excite changes the setting in memory only; it is in the fingerprint,
# so the app shows such a run as outdated unless the project has the same.
# Without a terminal: nohup python run.py model.stp > run.log 2>&1 &
#
# One run at a time on a machine: a run holds run.lock in this folder
# ({"pid", "model", "started"}); another one exits with 3 while that
# process lives (a lock left by a dead one is taken over). A dry run takes
# no lock.
# model.status.json beside the model tells the app how it goes (written
# atomically, at most every STATUS_EVERY s while stepping):
#   state     meshing | running | done | failed | stopped
#   pid, started, updated (epoch s), elapsed (s), message
#   ports (excited, in order), port, port_index (1-based), t_ns,
#   pulse_ns, pulse_pct, energy_db, end_db, port_elapsed (s)
#   result    the saved run's file name (done)
# A run that died (killed, the machine rebooted) leaves "meshing" or
# "running" with a pid that no longer lives: the app checks it.

import argparse
import json
import os
import signal
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

LOCK = os.path.join(HERE, "run.lock")
STATUS_EVERY = 2.0      # s


def status_path(step):
    return os.path.splitext(step)[0] + ".status.json"


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

    step = os.path.abspath(args.step)
    print(f"model      {step}")
    if not os.path.isfile(step):
        print(f"no such file: {step}")
        return 2
    if args.dry_run:
        return _run(args, step, None)

    holder = _lock(step)
    if holder is not None:
        print(f"another run is going on this machine: {holder.get('model')} (pid {holder.get('pid')}); "
              "one at a time")
        return 3
    status = _Status(step)
    status.write(state="meshing", message="starting")      # before the slow imports: the app waits for it
    try:
        code = _run(args, step, status)
    except BaseException as error:          # the status must not say "running" after we are gone
        status.write(state="failed", message=f"{type(error).__name__}: {error}")
        raise
    finally:
        _unlock()
    return code


def _run(args, step, status):
    """The run; `status` is None for a dry run (no status file)."""
    if status is None:
        status = _Status(None)
    stage = {"stepping": False, "stop": False}

    def interrupt(*_):
        if stage["stop"] or not stage["stepping"]:
            raise KeyboardInterrupt         # a second one, or still meshing: now
        stage["stop"] = True
        print("\nstopping (again to kill)...")

    signal.signal(signal.SIGINT, interrupt)
    for name in ("SIGTERM", "SIGBREAK"):    # SIGBREAK: Windows' polite stop (Ctrl+Break)
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), interrupt)
    try:
        return _simulate(args, step, status, stage)
    except KeyboardInterrupt:
        status.write(state="stopped", message="stopped: nothing saved")
        print("stopped: nothing saved")
        return 130


def _simulate(args, step, status, stage):
    if args.threads:
        try:
            import numba
            numba.set_num_threads(min(args.threads, numba.config.NUMBA_NUM_THREADS))
        except ImportError:
            pass

    from project.project_file import ProjectError
    from project.settings import EXCITE_ALL, accuracy_db, farfield_frequencies
    from solver import fdtd_kernels
    from solver.result_file import fingerprint, format_duration, save_result
    from solver.simulation import parameters, port_names, prepare, problem, simulate

    begin = time.perf_counter()
    status.write(state="meshing", message="reading and meshing the model")
    try:
        model = prepare(step, progress=_discretize_progress(args.every, status))
    except (OSError, ProjectError, ValueError) as error:
        print(f"cannot read the model: {error}")
        status.write(state="failed", message=f"cannot read the model: {error}")
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
        status.write(state="failed", message=f"cannot simulate: {why}")
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
            status.write(state="failed", message="metal joins what should be apart: " + "; ".join(materials.shorts))
            return 2

    excite = sim["excite"]
    excited = port_names(materials) if excite == EXCITE_ALL else [excite]
    end_db = accuracy_db(project.settings)
    threads = _threads()
    print(f"run        {sim['f_min']:g}-{sim['f_max']:g} GHz, ports {', '.join(excited)}, "
          f"stops at {end_db:.0f} dB; {'numba' if fdtd_kernels.AVAILABLE else 'numpy (slow)'}"
          + (f", {threads} threads" if threads else ""))
    if farfield_frequencies(project.settings):
        print(f"far field  at {', '.join(f'{f:g}' for f in farfield_frequencies(project.settings))} GHz")

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

    state = {"last": 0.0, "port": None, "start": 0.0}
    status.write(state="running", ports=excited, end_db=end_db, message="")

    def started(port, solver):
        state.update(port=port, start=time.perf_counter(), last=time.perf_counter())
        index = excited.index(port) + 1 if port in excited else 0
        print(f"port {index}/{len(excited)} {port}: {_array_bytes(solver) / 2**30:.2f} GB, "
              f"pulse {solver.pulse.duration * 1e9:.2f} ns")
        status.write(port=port, port_index=index, t_ns=0.0, pulse_ns=solver.pulse.duration * 1e9,
                     pulse_pct=0.0, energy_db=None, port_elapsed=0.0)

    def progress(port, t, level, pulse_duration):
        now = time.perf_counter()
        in_pulse = t < pulse_duration
        status.write(force=False, t_ns=t * 1e9, pulse_pct=min(100.0, 100 * t / pulse_duration),
                     energy_db=None if in_pulse else level, port_elapsed=now - state["start"])
        if now - state["last"] >= args.every:
            state["last"] = now
            where = (f"pulse {100 * t / pulse_duration:3.0f} %" if in_pulse
                     else f"energy {level:6.1f} dB -> {end_db:.0f}")
            print(f"  {format_duration(now - state['start']):>12}  t {t * 1e9:6.3f} ns  {where}")
        return not stage["stop"]

    stage["stepping"] = True
    try:
        result = simulate(materials, sim["f_min"], sim["f_max"], sim["trim_pulse"], end_db,
                          None if excite == EXCITE_ALL else [excite], progress=progress, started=started,
                          farfield=farfield_frequencies(project.settings))
    except KeyboardInterrupt:
        raise
    except Exception as error:          # report, do not lose the traceback
        import traceback
        traceback.print_exc()
        print(f"failed: {type(error).__name__}: {error}")
        status.write(state="failed", message=f"{type(error).__name__}: {error}")
        return 1
    stage["stepping"] = False
    if result is None:
        print("stopped: nothing saved")
        status.write(state="stopped", message="stopped: nothing saved")
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
    status.write(state="done", result=os.path.basename(path), message=f"saved {result.name}")
    return 0


class _Status:
    """model.status.json, written atomically; path None writes nothing (dry run)."""

    def __init__(self, step):
        self.path = status_path(step) if step else None
        self.last = 0.0
        now = time.time()
        self.data = {"state": "meshing", "model": os.path.basename(step) if step else "",
                     "pid": os.getpid(), "started": now, "updated": now, "elapsed": 0.0, "message": ""}
        self.begin = time.perf_counter()

    def write(self, force=True, **fields):
        self.data.update(fields)
        self.data["updated"] = time.time()
        self.data["elapsed"] = time.perf_counter() - self.begin
        now = time.perf_counter()
        if self.path is None or (not force and now - self.last < STATUS_EVERY):
            return
        self.last = now
        temp = self.path + ".tmp"
        try:
            with open(temp, "w", encoding="utf-8") as file:
                json.dump(self.data, file)
            os.replace(temp, self.path)
        except OSError:
            pass                        # the run matters more than its status


def _lock(step):
    """None when this process now holds run.lock, else the holder's entry."""
    for _ in range(3):
        try:
            fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            holder = _read_json(LOCK)
            if holder is None:
                time.sleep(0.2)         # being written: look again
                holder = _read_json(LOCK)
            if holder is not None and not _alive(holder.get("pid")):
                try:
                    os.remove(LOCK)     # a dead run's: take it over
                except FileNotFoundError:
                    pass
                continue
            return holder or {"model": "?", "pid": "?"}
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump({"pid": os.getpid(), "model": os.path.basename(step), "started": time.time()}, file)
        return None
    return {"model": "?", "pid": "?"}


def _unlock():
    holder = _read_json(LOCK)
    if holder is not None and holder.get("pid") == os.getpid():
        try:
            os.remove(LOCK)
        except OSError:
            pass


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as file:
            data = json.load(file)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _alive(pid):
    """Whether a process with this pid lives (never signals it)."""
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == "nt":                 # os.kill(pid, 0) would terminate it on Windows
        import ctypes
        kernel = ctypes.windll.kernel32
        handle = kernel.OpenProcess(0x1000, False, pid)     # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        kernel.GetExitCodeProcess(handle, ctypes.byref(code))
        kernel.CloseHandle(handle)
        return code.value == 259                            # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _discretize_progress(every, status):
    state = {"last": time.perf_counter()}

    def progress(done, total, what):
        status.write(force=False, message=f"meshing {done}/{total}: {what}")
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
