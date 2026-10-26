# sync.py
#
# The model to the console runner's machine and its runs back (README
# "Running on another machine"), over SSH with paramiko's SFTP:
#   push(config, step)  model.stp + model.toml -> <path>/models/ on the
#                       remote, each written under a .part name and then
#                       renamed over the old one;
#   pull(config, step)  <path>/models/model.results/*.npz that the local
#                       model.results/ lacks (or has with another size):
#                       downloaded into a temp folder beside it first and
#                       moved in only when every one arrived, so a broken
#                       connection leaves the local runs as they were.
#                       The remote's model.toml and model.stp come back
#                       as bytes when they differ from the local ones
#                       (the STEP is compared by size: it is only fetched
#                       when that differs); write_local() puts them in
#                       place, the window asks first.
#   check(config)       connects and finds run.py under the path.
# <path> is the customcst folder on the remote (the one holding run.py);
# "~/..." is taken from the remote home.
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
import shutil
import stat
import tempfile
import tomllib
from contextlib import contextmanager
from dataclasses import dataclass, field

from solver.result_file import results_dir

HERE = os.path.dirname(os.path.abspath(__file__))
TIMEOUT = 15.0          # s, connect and authenticate


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

    @property
    def complete(self):
        return bool(self.host and self.user and self.path)

    @property
    def target(self):
        return f"{self.user}@{self.host}" + ("" if self.port == 22 else f":{self.port}")


@dataclass
class Pulled:
    runs: list = field(default_factory=list)        # file names now in model.results/
    model: dict = field(default_factory=dict)       # local path -> the remote's bytes, where they differ


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
    for name in ("host", "user", "password", "path"):
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
             f"path = {json.dumps(config.path)}   # the customcst folder (with run.py)", ""]
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


@contextmanager
def session(config):
    """(sftp, root): an SFTP session and the absolute customcst folder on the remote."""
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
        yield sftp, root
    finally:
        client.close()


def check(config):
    """'user@host: /abs/customcst' or raises."""
    with session(config) as (sftp, root):
        return f"{config.target}: {root}"


def push(config, step_path, progress=None):
    """Uploads model.stp and model.toml; returns the remote STEP path."""
    toml = os.path.splitext(step_path)[0] + ".toml"
    for local in (step_path, toml):
        if not os.path.isfile(local):
            raise RemoteError(f"no {local}")
    with session(config) as (sftp, root):
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
    """Downloads the remote runs this model lacks and its differing model files (Pulled)."""
    folder = results_dir(step_path)
    pulled = Pulled()
    with session(config) as (sftp, root):
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
        wanted = []
        for entry in entries:
            name = entry.filename
            if not name.endswith(".npz") or name.endswith(".tmp.npz") or not stat.S_ISREG(entry.st_mode):
                continue
            local = os.path.join(folder, name)
            if not os.path.isfile(local) or os.path.getsize(local) != entry.st_size:
                wanted.append(entry)
        if not wanted:
            return pulled
        parent = os.path.dirname(folder)
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
            os.makedirs(folder, exist_ok=True)
            for entry in wanted:
                os.replace(os.path.join(temp, entry.filename), os.path.join(folder, entry.filename))
        finally:
            shutil.rmtree(temp, ignore_errors=True)
        pulled.runs = [entry.filename for entry in wanted]
        return pulled


def write_local(model):
    """Puts pulled model files in place ({path: bytes}), each atomically."""
    for path, data in model.items():
        temp = path + ".pull.tmp"
        with open(temp, "wb") as file:
            file.write(data)
        os.replace(temp, path)
