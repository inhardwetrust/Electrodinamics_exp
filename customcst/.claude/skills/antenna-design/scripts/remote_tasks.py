# remote_tasks.py
#
# The antenna-design skill's server chores, on the machine of the Remote
# tab (remote/remote.toml, remote/sync.py). Never prints the password.
#
#   python .claude/skills/antenna-design/scripts/remote_tasks.py status
#       run.lock, our processes, free disk
#   ... sync-code
#       backs the server's code up (~/customcst-code-<time>.tgz, no models,
#       no .venv; the 3 newest backups are kept) and uploads every git-tracked code file (not models/,
#       docs/, stepmodel/, images): the server must run the same code
#   ... sweep NAME -- <sweep.py arguments without FOLDER>
#       uploads sweep.py and tools/make_test_models.py, starts sweep.py in
#       zz_NAME/ under nohup (log zz_NAME/sweep.log), prints the pid
#   ... wait-sweep NAME PID      polls every minute until PID ends, prints the log
#   ... clean NAME               removes zz_NAME/
#   ... launch STEP              like Run on server: push, start run.py, record the
#                                launch (model.results/remote.json) so the window
#                                shows the pending run
#   ... wait-run STEP            polls the run's status until it ends
#   ... fetch STEP               like Fetch: takes the finished run into model.results/
#
# Run from the customcst folder. The start command is
# "cd ROOT || exit 1; nohup ... & echo $!": "cd ROOT && nohup ... &"
# backgrounds a shell that keeps the SSH channel open (the call hangs).

import os
import posixpath
import shlex
import subprocess
import sys
import time

ROOT = os.getcwd()
sys.path.insert(0, ROOT)
HERE = os.path.dirname(os.path.abspath(__file__))

from remote import record, sync                                         # noqa: E402

CODE_EXCLUDE = ("models/", "docs/", "stepmodel/")


def _config():
    config = sync.load_config()
    if not config.complete:
        sys.exit("the Remote tab is not filled in (remote/remote.toml)")
    return config


def status():
    with sync.session(_config()) as r:
        print("run.lock:", r.read_json(r.path("run.lock")))
        for command in ("pgrep -af 'run.py|sweep.py|zz_' | grep -v pgrep || echo 'no runs'",
                        f"ls -d {r.path('zz_*')} 2>/dev/null || echo 'no zz_ folders'",
                        "df -h ~ | tail -1"):
            print(r.run(command)[1].strip())


def sync_code():
    files = subprocess.run(["git", "ls-files", "."], capture_output=True, text=True, check=True).stdout.splitlines()
    files = [f for f in files if not f.startswith(CODE_EXCLUDE) and not f.endswith(".png")]
    stamp = time.strftime("%Y%m%d-%H%M%S")
    with sync.session(_config()) as r:
        if r.read_json(r.path("run.lock")) is not None:
            sys.exit("a run is going on the server: not touching its code")
        parent, name = posixpath.split(r.root.rstrip("/"))
        backup = f"~/customcst-code-{stamp}.tgz"
        code, out, err = r.run(f"cd {parent} || exit 1; tar czf {backup} --exclude={name}/models "
                               f"--exclude={name}/.venv --exclude={name}/stepmodel --exclude={name}/docs "
                               f"--exclude='*.png' --exclude=__pycache__ {name} && ls -la {backup}", timeout=180)
        if code:
            sys.exit(f"backup failed, nothing uploaded: {err.strip()}")
        print("backup:", out.strip())
        r.run("ls -t ~/customcst-code-*.tgz | tail -n +4 | xargs -r rm -f --")    # the 3 newest stay
        made = set()
        for f in files:
            target = r.path(f)
            folder = posixpath.dirname(target)
            if folder not in made:
                r.run(f"mkdir -p {shlex.quote(folder)}")
                made.add(folder)
            r.sftp.put(f, target + ".part")
            r.sftp.posix_rename(target + ".part", target)
        print(f"uploaded {len(files)} files")
        code, out, err = r.run(f"cd {r.root} || exit 1; {cfg_python(r)} -c "
                               "\"import solver.farfield, solver.simulation, run; print('imports ok')\"")
        print(out.strip() or err.strip()[-400:])


def cfg_python(r):
    python = _config().python
    return python if python.startswith("/") else r.path(python)


def sweep(name, arguments):
    folder = f"zz_{name}"
    with sync.session(_config()) as r:
        if r.read_json(r.path("run.lock")) is not None:
            print("note: run.py is running there too; the sweep shares the CPU")
        r.run(f"mkdir -p {r.path(folder)}")
        r.sftp.put(os.path.join(HERE, "sweep.py"), r.path(folder, "sweep.py"))
        r.sftp.put("tools/make_test_models.py", r.path("tools/make_test_models.py"))
        args = " ".join(shlex.quote(a) for a in arguments)
        command = (f"cd {r.root} || exit 1; nohup {cfg_python(r)} {folder}/sweep.py {folder} {args} "
                   f"> {folder}/sweep.log 2>&1 < /dev/null & echo $!")
        print("pid", r.run(command)[1].strip())


def wait_sweep(name, pid):
    log = posixpath.join(f"zz_{name}", "sweep.log")
    while True:
        with sync.session(_config()) as r:
            alive = r.run(f"kill -0 {int(pid)} 2>/dev/null && echo yes || echo no")[1].strip()
            text = r.run(f"cat {r.path(log)}")[1]
        if alive == "no":
            print("\n".join(line for line in text.splitlines() if not line.startswith("JSON ")))
            return
        time.sleep(60)


def clean(name):
    with sync.session(_config()) as r:
        r.run(f"rm -rf {r.path('zz_' + name)}")
        print("removed", "zz_" + name)


def launch(step):
    from project.project_file import Project
    from solver.result_file import fingerprint
    step = os.path.abspath(step)
    config = _config()
    now = sync.launch(config, step)
    record.add_launch(step, config.target, now, fingerprint(Project.load(step)))
    print("launched:", {k: now.get(k) for k in ("state", "pid", "message")})


def wait_run(step):
    step = os.path.abspath(step)
    while True:
        now = sync.status(_config(), step) or {}
        if now.get("state") not in ("meshing", "running"):
            print("final:", {k: now.get(k) for k in ("state", "message", "result", "elapsed")})
            return
        time.sleep(30)


def fetch(step):
    fetched = sync.fetch(_config(), os.path.abspath(step))
    print("taken:", fetched.pulled.runs, "errors:", fetched.pulled.errors)


def main():
    if len(sys.argv) < 2:
        sys.exit("commands: status, sync-code, sweep NAME -- ARGS, wait-sweep NAME PID, clean NAME, "
                 "launch STEP, wait-run STEP, fetch STEP (see the header of remote_tasks.py)")
    command, rest = sys.argv[1], sys.argv[2:]
    if command == "status":
        status()
    elif command == "sync-code":
        sync_code()
    elif command == "sweep":
        if "--" not in rest:
            sys.exit("sweep NAME -- <sweep.py arguments>")
        split = rest.index("--")
        sweep(rest[0], rest[split + 1:])
    elif command == "wait-sweep":
        wait_sweep(*rest)
    elif command == "clean":
        clean(rest[0])
    elif command == "launch":
        launch(rest[0])
    elif command == "wait-run":
        wait_run(rest[0])
    elif command == "fetch":
        fetch(rest[0])
    else:
        sys.exit("commands: status, sync-code, sweep NAME -- ARGS, wait-sweep NAME PID, clean NAME, "
                 "launch STEP, wait-run STEP, fetch STEP (see the header of remote_tasks.py)")


if __name__ == "__main__":
    main()
