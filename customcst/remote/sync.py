# sync.py
#
# The model to the console runner's machine and its runs back (README
# "Running on another machine"), over SSH with paramiko's SFTP:
#   push(config, step)  model.stp + model.toml -> <path>/models/ on the
#                       remote, each written under a .part name and then
#                       renamed over the old one;
#   pull(config, step)  <path>/models/model.results/*.npz not taken yet
#                       (remote/record.py remembers what was): downloaded
#                       into a temp folder beside it first and moved in
#                       only when every one arrived, so a broken connection
#                       leaves the local runs as they were. A taken run is
#                       renamed by this machine's clock and numbering (the
#                       server's clock and its "Run N" would mix up the list).
#                       The remote's model.toml and model.stp come back
#                       as bytes when they differ from the local ones
#                       (the STEP is compared by size: it is only fetched
#                       when that differs); write_local() puts them in
#                       place, the window asks first.
#   check(config)       connects and finds run.py under the path.
#   start(config, step) starts run.py on the pushed model in the background
#                       (nohup, its output in models/model.log) and waits
#                       until it is going, or reports why it is not;
#   status(config, step) run.py's model.status.json (see run.py), with
#                       "died" for a run whose process is gone, the log's
#                       tail, and the run holding the machine (run.lock);
#   fetch(config, step) status + pull: what the Fetch button does; when the
#                       run is done and every run file of the model there
#                       is taken (or here already), the model's files on
#                       the server are deleted (STEP, .toml, log, status,
#                       results): the next Run on server pushes them again;
#   server_models(config) the models in <path>/models/ grouped by name (their
#                       files, size, runs, state); clean(config, names)
#                       deletes them, never one whose run is going: the
#                       Remote tab's "Clean server..." for what a failed or
#                       stopped run, or a run never fetched, left behind;
#   stop(config, step)  SIGTERM to that model's run.py: it stops at its next
#                       energy check (or at once while meshing), saves
#                       nothing, says "stopped".
# One run at a time per machine: run.py's run.lock; start() refuses while
# a live process holds it.
# <path> is the customcst folder on the remote (the one holding run.py);
# "~/..." is taken from the remote home. The Python there: config.python,
# relative to that folder (default .venv/bin/python).
#
# The settings live in remote/remote.toml (not in git: it holds the
# password; remote.example.toml shows the format), or in the file named by
# CUSTOMCST_REMOTE (tests). An empty password uses the SSH keys and agent.
# Host keys: ~/.ssh/known_hosts and known_hosts beside the settings file.
# An unknown host raises UnknownHost with its fingerprint; trust_host()
# adds it to the latter (the window asks first).

import base64
import hashlib
import json
import os
import posixpath
import shlex
import shutil
import stat
import tempfile
import time
import tomllib
from contextlib import contextmanager
from dataclasses import dataclass, field

from solver.result_file import results_dir

HERE = os.path.dirname(os.path.abspath(__file__))
TIMEOUT = 15.0          # s, connect and authenticate
START_WAIT = 20.0       # s for a started run.py to write its status (or to fail)
LOG_TAIL = 4000         # bytes of the log shown when a run failed
ACTIVE = ("meshing", "running")


class RemoteError(Exception):
    pass


class UnknownHost(RemoteError):
    def __init__(self, host, key):
        self.host, self.key = host, key
        super().__init__(f"unknown host key for {host}: {key.get_name()} {fingerprint(key)}")


@dataclass
class RemoteConfig:
    host: str = ""
    port: int = 22
    user: str = ""
    password: str = ""
    path: str = "~/customcst"
    python: str = ".venv/bin/python"

    @property
    def complete(self):
        return bool(self.host and self.user and self.path)

    @property
    def target(self):
        return f"{self.user}@{self.host}" + ("" if self.port == 22 else f":{self.port}")


@dataclass
class Pulled:
    runs: list = field(default_factory=list)        # local file names now in model.results/
    model: dict = field(default_factory=dict)       # local path -> the remote's bytes, where they differ
    taken: dict = field(default_factory=dict)       # the server's file name -> the local one
    errors: list = field(default_factory=list)      # server files that are no run (left there)


@dataclass
class Fetched:
    status: dict                                    # status(): None when it never ran there
    pulled: Pulled
    cleaned: list = field(default_factory=list)     # the model's server files deleted after the fetch
    clean_error: str = ""                           # why they could not be (the runs are taken anyway)


@dataclass
class ServerModel:
    name: str                                       # the stem: model.step -> "model"
    files: list = field(default_factory=list)       # names in models/ (a results folder ends in /)
    size: int = 0                                   # bytes, all of them
    runs: int = 0                                   # run files there (.npz)
    state: str = ""                                 # its status file's state, "" without one
    active: bool = False                            # its run.py is going: never deleted


def config_path():
    return os.environ.get("CUSTOMCST_REMOTE") or os.path.join(HERE, "remote.toml")


def known_hosts_path():
    return os.path.join(os.path.dirname(config_path()), "known_hosts")


def load_config():
    """The saved settings; a missing or damaged file gives the defaults."""
    try:
        with open(config_path(), "rb") as file:
            data = tomllib.load(file).get("remote", {})
    except (OSError, tomllib.TOMLDecodeError):
        return RemoteConfig()
    config = RemoteConfig()
    for name in ("host", "user", "password", "path", "python"):
        if isinstance(data.get(name), str):
            setattr(config, name, data[name])
    if isinstance(data.get("port"), int) and 0 < data["port"] < 65536:
        config.port = data["port"]
    return config


def save_config(config):
    """Writes atomically; json.dumps of a str is a valid TOML basic string."""
    lines = ["# customcst remote: the console runner's machine (not in git: the password)",
             "", "[remote]",
             f"host = {json.dumps(config.host)}",
             f"port = {int(config.port)}",
             f"user = {json.dumps(config.user)}",
             f"password = {json.dumps(config.password)}   # empty: SSH keys / agent",
             f"path = {json.dumps(config.path)}   # the customcst folder (with run.py)",
             f"python = {json.dumps(config.python)}   # relative to path, or absolute", ""]
    path = config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temp = path + ".tmp"
    with open(temp, "w", encoding="utf-8", newline="\n") as file:
        file.write("\n".join(lines))
    os.replace(temp, path)


def fingerprint(key):
    """SHA256:... as ssh prints it."""
    digest = hashlib.sha256(key.asbytes()).digest()
    return "SHA256:" + base64.b64encode(digest).decode().rstrip("=")


def trust_host(host, key):
    """Adds the key to known_hosts beside the settings (host as paramiko reported it)."""
    import paramiko
    path = known_hosts_path()
    keys = paramiko.HostKeys()
    if os.path.isfile(path):
        keys.load(path)
    keys.add(host, key.get_name(), key)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    keys.save(path)


@dataclass
class Remote:
    client: object          # paramiko.SSHClient (commands)
    sftp: object            # its SFTP session (files)
    root: str               # the customcst folder, absolute

    def run(self, command, timeout=30.0):
        """(exit code, stdout, stderr) of a shell command on the remote."""
        _, out, err = self.client.exec_command(command, timeout=timeout)
        text, errors = out.read().decode(errors="replace"), err.read().decode(errors="replace")
        return out.channel.recv_exit_status(), text, errors

    def path(self, *parts):
        return posixpath.join(self.root, *parts)

    def read_json(self, path):
        try:
            with self.sftp.open(path, "rb") as file:
                data = json.loads(file.read().decode("utf-8"))
            return data if isinstance(data, dict) else None
        except (OSError, ValueError):
            return None

    def alive(self, pid):
        return isinstance(pid, int) and pid > 0 and self.run(f"kill -0 {pid}")[0] == 0

    def tail(self, path, size=LOG_TAIL):
        try:
            with self.sftp.open(path, "rb") as file:
                end = file.stat().st_size
                file.seek(max(0, end - size))
                return file.read().decode("utf-8", errors="replace")
        except OSError:
            return ""


@contextmanager
def session(config):
    """A Remote: the connection and the absolute customcst folder on the remote."""
    try:
        import paramiko
    except ImportError:
        raise RemoteError("paramiko is not installed (pip install paramiko)") from None
    if not config.complete:
        raise RemoteError("set host, user and path in the Remote tab")

    class Ask(paramiko.MissingHostKeyPolicy):
        def missing_host_key(self, client, hostname, key):
            raise UnknownHost(hostname, key)

    client = paramiko.SSHClient()
    client.load_system_host_keys()
    if os.path.isfile(known_hosts_path()):
        client.load_host_keys(known_hosts_path())
    client.set_missing_host_key_policy(Ask())
    try:
        client.connect(config.host, port=config.port, username=config.user,
                       password=config.password or None, timeout=TIMEOUT,
                       banner_timeout=TIMEOUT, auth_timeout=TIMEOUT,
                       look_for_keys=not config.password, allow_agent=not config.password)
        sftp = client.open_sftp()
    except UnknownHost:
        client.close()
        raise
    except paramiko.AuthenticationException as error:
        client.close()
        raise RemoteError(f"{config.target}: authentication failed ({error})") from None
    except (OSError, paramiko.SSHException) as error:
        client.close()
        raise RemoteError(f"{config.target}: {error}") from None
    try:
        path = config.path.strip()
        if path == "~":
            path = "."
        elif path.startswith("~/"):
            path = path[2:]                     # relative = from the remote home
        try:
            root = sftp.normalize(path)
            sftp.stat(posixpath.join(root, "run.py"))
        except OSError:
            raise RemoteError(f"{config.target}: no run.py in {config.path} "
                              "(path must be the customcst folder)") from None
        yield Remote(client, sftp, root)
    finally:
        client.close()


def check(config):
    """'user@host: /abs/customcst' or raises."""
    with session(config) as connection:
        return f"{config.target}: {connection.root}"


def push(config, step_path, progress=None):
    """Uploads model.stp and model.toml; returns the remote STEP path."""
    toml = os.path.splitext(step_path)[0] + ".toml"
    for local in (step_path, toml):
        if not os.path.isfile(local):
            raise RemoteError(f"no {local}")
    with session(config) as connection:
        sftp, root = connection.sftp, connection.root
        models = posixpath.join(root, "models")
        try:
            sftp.stat(models)
        except OSError:
            sftp.mkdir(models)
        for local in (step_path, toml):
            name = os.path.basename(local)
            remote = posixpath.join(models, name)
            part = remote + ".part"
            callback = None if progress is None else (
                lambda done, total, name=name: progress(f"push {name}: {done / 2**20:.1f} / {total / 2**20:.1f} MB"))
            sftp.put(local, part, callback=callback)
            try:
                sftp.posix_rename(part, remote)    # replaces the old one (OpenSSH)
            except OSError:
                try:
                    sftp.remove(remote)
                except OSError:
                    pass
                sftp.rename(part, remote)
        return posixpath.join(models, os.path.basename(step_path))


def pull(config, step_path, progress=None):
    """
    Takes the server's runs of this model that were not taken yet (Pulled)
    and reads its model files where they differ. A taken run gets a local
    name: its file the local time of its launch here (record.py; the time
    of taking for one started on the server by hand), its name the next
    "Run N" of this model; its info says where and when it was computed.
    """
    from remote import record
    from solver.result_file import _next_run_number, list_results, read_result, write_result
    folder = results_dir(step_path)
    pulled = Pulled()
    with session(config) as connection:
        sftp, root = connection.sftp, connection.root
        models = posixpath.join(root, "models")
        toml = os.path.splitext(step_path)[0] + ".toml"
        for local in (toml, step_path):
            remote = posixpath.join(models, os.path.basename(local))
            try:
                size = sftp.stat(remote).st_size
            except OSError:
                continue                            # never pushed
            if local == step_path and os.path.isfile(local) and os.path.getsize(local) == size:
                continue
            with sftp.open(remote, "rb") as file:
                data = file.read()
            if len(data) != size:
                raise RemoteError(f"{os.path.basename(local)}: incomplete download")
            if not os.path.isfile(local) or open(local, "rb").read() != data:
                pulled.model[local] = data

        remote = posixpath.join(models, os.path.basename(folder))
        try:
            entries = sftp.listdir_attr(remote)
        except OSError:
            return pulled                           # no runs there yet
        taken = record.load(step_path)["pulled"].get(config.target, {})
        wanted = sorted((entry for entry in entries
                         if entry.filename.endswith(".npz") and not entry.filename.endswith(".tmp.npz")
                         and stat.S_ISREG(entry.st_mode) and entry.filename not in taken
                         and not os.path.isfile(os.path.join(folder, entry.filename))),   # an older pull's
                        key=lambda entry: entry.filename)
        if not wanted:
            return pulled
        parent = os.path.dirname(folder)
        os.makedirs(parent, exist_ok=True)
        temp = tempfile.mkdtemp(prefix=".pull-", dir=parent)    # the same disk: moves are renames
        try:
            for k, entry in enumerate(wanted):
                target = os.path.join(temp, entry.filename)
                callback = None if progress is None else (
                    lambda done, total, k=k: progress(f"pull {k + 1}/{len(wanted)}: "
                                                      f"{done / 2**10:.0f} / {total / 2**10:.0f} kB"))
                sftp.get(posixpath.join(remote, entry.filename), target, callback=callback)
                if os.path.getsize(target) != entry.st_size:
                    raise RemoteError(f"{entry.filename}: incomplete download")

            data = record.load(step_path)
            runs = list_results(step_path)[0] if os.path.isdir(folder) else []
            named, names = [], {}
            for entry in wanted:
                try:
                    result = read_result(os.path.join(temp, entry.filename))
                except Exception as error:          # not a run file: leave it on the server
                    pulled.errors.append(f"{entry.filename}: {error}")
                    continue
                launch = record.launch_of(data, config.target, entry.filename)
                when = (time.strptime(launch["started"], "%Y-%m-%d %H:%M:%S") if launch and launch.get("started")
                        else time.localtime())
                result.info.append(f"computed on {config.target} ({entry.filename}, saved "
                                   f"{result.saved} by its clock)")
                result.saved = time.strftime("%Y-%m-%d %H:%M:%S", when)
                result.name = f"Run {_next_run_number(runs + named)}"
                named.append(result)
                names[entry.filename] = (time.strftime("%Y%m%d-%H%M%S", when), result)

            os.makedirs(folder, exist_ok=True)
            for remote_name, (stamp, result) in names.items():
                path, n = os.path.join(folder, f"{stamp}.npz"), 1
                while os.path.exists(path):
                    n += 1
                    path = os.path.join(folder, f"{stamp}-{n}.npz")
                write_result(path, result)          # atomic: temp + replace
                pulled.runs.append(os.path.basename(path))
                pulled.taken[remote_name] = os.path.basename(path)
        finally:
            shutil.rmtree(temp, ignore_errors=True)
        if pulled.taken:
            record.note_pulled(step_path, config.target, pulled.taken)
        return pulled


def fetch(config, step_path, progress=None):
    """
    The model's run on the server (status) and whatever of it is to take
    (pull): Fetched. A done run, all taken: the model's server files go.
    """
    from remote import record
    now = status(config, step_path)
    record.note_status(step_path, config.target, now)
    fetched = Fetched(now, pull(config, step_path, progress))
    if now is not None and now.get("state") == "done" and not fetched.pulled.errors:
        try:
            fetched.cleaned = _clean_fetched(config, step_path)
        except (RemoteError, OSError) as error:     # the runs are here: only say it
            fetched.clean_error = str(error)
    return fetched


SUFFIXES = (".status.json", ".log", ".toml", ".step", ".stp", ".npz")


def _stem(name, is_dir):
    """The model a file of models/ belongs to, or None (not one of ours)."""
    if is_dir:
        return name[:-len(".results")] if name.endswith(".results") else None
    if name.endswith(".part"):
        name = name[:-len(".part")]
    for suffix in SUFFIXES:
        if name.endswith(suffix):
            return name[:-len(suffix)]
    return None


def _models(connection):
    """{stem: ServerModel} of <path>/models/ as it is now."""
    sftp, models = connection.sftp, connection.path("models")
    try:
        entries = sftp.listdir_attr(models)
    except OSError:
        return {}
    found = {}
    for entry in entries:
        is_dir = stat.S_ISDIR(entry.st_mode)
        stem = _stem(entry.filename, is_dir)
        if stem is None:
            continue
        model = found.setdefault(stem, ServerModel(stem))
        if is_dir:
            model.files.append(entry.filename + "/")
            try:
                inside = sftp.listdir_attr(posixpath.join(models, entry.filename))
            except OSError:
                inside = []
            model.size += sum(e.st_size or 0 for e in inside)
            model.runs += sum(1 for e in inside if e.filename.endswith(".npz") and not e.filename.endswith(".tmp.npz"))
        else:
            model.files.append(entry.filename)
            model.size += entry.st_size or 0
            if entry.filename.endswith(".npz"):
                model.runs += 1                     # an old single-result file
    holder = _holder(connection)
    for stem, model in found.items():
        data = connection.read_json(posixpath.join(models, stem + ".status.json"))
        model.state = (data or {}).get("state", "")
        model.active = model.state in ACTIVE and connection.alive((data or {}).get("pid"))
        if holder is not None and _stem(os.path.basename(str(holder.get("model", ""))), False) == stem:
            model.active = True
    return found


def _delete(connection, model):
    """Removes a model's files from models/ (the caller checked it is not running)."""
    models = connection.path("models")
    paths = " ".join(shlex.quote(posixpath.join(models, f.rstrip("/"))) for f in model.files)
    code, out, err = connection.run(f"rm -rf -- {paths}")
    if code != 0:
        raise RemoteError(f"{model.name}: not deleted: {(err or out).strip()}")
    return list(model.files)


def _clean_fetched(config, step_path):
    """
    After a fetch of a done run: the model's server files, if its run is not
    going and every run file there is taken by this machine (record.py) or
    here already; else nothing (Clean server shows what is left).
    """
    from remote import record
    stem = os.path.splitext(os.path.basename(step_path))[0]
    taken = record.load(step_path)["pulled"].get(config.target, {})
    folder = results_dir(step_path)
    with session(config) as connection:
        model = _models(connection).get(stem)
        if model is None or model.active:
            return []
        remote = connection.path("models", os.path.basename(folder))
        try:
            runs = [e.filename for e in connection.sftp.listdir_attr(remote)
                    if e.filename.endswith(".npz") and not e.filename.endswith(".tmp.npz")]
        except OSError:
            runs = []
        if any(r not in taken and not os.path.isfile(os.path.join(folder, r)) for r in runs):
            return []                               # a run nobody took: keep it
        if any(f.endswith(".npz") for f in model.files):
            return []                               # an old single-result file: by hand (Clean server)
        return _delete(connection, model)


def server_models(config, step_path=None, progress=None):
    """[ServerModel] of <path>/models/, by name."""
    with session(config) as connection:
        return [m for _, m in sorted(_models(connection).items(), key=lambda kv: kv[0].lower())]


def clean(config, names, progress=None):
    """Deletes these models' files on the server ([names]); skips (and reports) one whose run is going."""
    deleted, skipped = [], []
    with session(config) as connection:
        found = _models(connection)
        for name in names:
            model = found.get(name)
            if model is None:
                continue
            if model.active:
                skipped.append(name)
                continue
            if progress is not None:
                progress(f"clean: {name}")
            _delete(connection, model)
            deleted.append(name)
    return {"deleted": deleted, "skipped": skipped}


def write_local(model):
    """Puts pulled model files in place ({path: bytes}), each atomically."""
    for path, data in model.items():
        temp = path + ".pull.tmp"
        with open(temp, "wb") as file:
            file.write(data)
        os.replace(temp, path)


def _model_paths(connection, step_path):
    """(remote STEP, its status file, its log) for a local model."""
    stem = os.path.splitext(os.path.basename(step_path))[0]
    models = connection.path("models")
    return (posixpath.join(models, os.path.basename(step_path)),
            posixpath.join(models, stem + ".status.json"), posixpath.join(models, stem + ".log"))


def _holder(connection):
    """run.lock's entry when a live process holds it, else None."""
    holder = connection.read_json(connection.path("run.lock"))
    return holder if holder is not None and connection.alive(holder.get("pid")) else None


def start(config, step_path, progress=None):
    """Starts run.py on the pushed model; returns its status once it is going."""
    with session(config) as connection:
        step, status_file, log = _model_paths(connection, step_path)
        try:
            connection.sftp.stat(step)
        except OSError:
            raise RemoteError(f"{os.path.basename(step_path)} is not on the server: push it first") from None
        holder = _holder(connection)
        if holder is not None:
            raise RemoteError(f"a run is going on the server: {holder.get('model')} (pid {holder.get('pid')}); "
                              "one at a time")
        try:
            connection.sftp.remove(status_file)         # an old run's must not be read as this one's
        except OSError:
            pass
        python = config.python.strip() or RemoteConfig.python
        relative = posixpath.relpath(step, connection.root)
        # "cd ... || exit; nohup ... &": with "cd && nohup &" the & would background the
        # whole list, a shell holding this channel open until the run ends ($! its pid)
        command = (f"cd {shlex.quote(connection.root)} || exit 1; nohup {shlex.quote(python)} run.py "
                   f"{shlex.quote(relative)} > {shlex.quote(log)} 2>&1 < /dev/null & echo $!")
        code, out, err = connection.run(command)
        try:
            pid = int(out.strip().splitlines()[-1])
        except (ValueError, IndexError):
            raise RemoteError(f"could not start run.py: {(err or out).strip()}") from None
        if progress is not None:
            progress(f"run.py started (pid {pid}), waiting for it...")
        end = time.monotonic() + START_WAIT
        while time.monotonic() < end:
            data = connection.read_json(status_file)
            if data is not None and data.get("pid") == pid:
                return data
            if not connection.alive(pid):
                data = connection.read_json(status_file)
                if data is not None and data.get("pid") == pid:
                    return data                     # quick to fail (or to finish): its status says
                raise RemoteError("run.py stopped at once:\n" + connection.tail(log).strip())
            time.sleep(1.0)
        raise RemoteError(f"run.py (pid {pid}) wrote no status in {START_WAIT:.0f} s:\n"
                          + connection.tail(log).strip())


def status(config, step_path, progress=None):
    """
    The model's run as its status file tells (None: never run there), with
    state "died" when its process is gone, "log" (the log's tail) when it
    ended without a result, and "busy": the run holding the machine when
    that is another model's.
    """
    with session(config) as connection:
        _, status_file, log = _model_paths(connection, step_path)
        data = connection.read_json(status_file)
        if data is not None and data.get("state") in ACTIVE and not connection.alive(data.get("pid")):
            data["state"] = "died"
            data["message"] = "run.py is gone (killed, out of memory, or the server restarted)"
        if data is not None and data.get("state") in ("failed", "died", "stopped"):
            data["log"] = connection.tail(log).strip()
        holder = _holder(connection)
        if holder is not None and holder.get("model") != os.path.basename(step_path):
            data = dict(data or {}, busy=holder)
        return data


def stop(config, step_path, progress=None):
    """SIGTERM to the model's run.py (checked to be it); returns its pid."""
    with session(config) as connection:
        _, status_file, _ = _model_paths(connection, step_path)
        data = connection.read_json(status_file)
        pid = data.get("pid") if data else None
        if data is None or data.get("state") not in ACTIVE or not connection.alive(pid):
            raise RemoteError("no run of this model is going on the server")
        _, args, _ = connection.run(f"ps -o args= -p {int(pid)}")
        if "run.py" not in args or os.path.basename(step_path) not in args:
            raise RemoteError(f"pid {pid} is not this model's run.py ({args.strip()}): not stopped")
        code, _, err = connection.run(f"kill -TERM {int(pid)}")
        if code != 0:
            raise RemoteError(f"kill {pid}: {err.strip()}")
        return pid


def launch(config, step_path, progress=None):
    """Push, then start: the server runs exactly the model open here."""
    push(config, step_path, progress)
    return start(config, step_path, progress)
