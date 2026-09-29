"""Cloud sessions: a session with a machine of its own.

A sandbox is a machine that can be checkpointed with its memory, and every verb
here is that one primitive. A project checkpoint (dependencies installed) makes a
new machine in seconds; N sandboxes from one checkpoint are a fork or a race; an
earlier checkpoint is a rewind; a checkpoint plus this machine's work and
transcript carries a session to the cloud.

The project lives at its local absolute path in the sandbox as well, so a
transcript's directories mean the same thing on both sides and moving a session is
a file copy. Everything after the first file write goes over ssh.

In a sandbox the agent runs inside the sandbox's own tmux (session ``agent``), and
this machine only attaches to it. A fork therefore carries a *running* agent, a
dropped link costs nothing, and the laptop can close while the agent works.
"""
from __future__ import annotations

import contextlib
import dataclasses
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from . import e2b
from .config import Config, Host
from .errors import Dependency, FourtopError, Missing, Unavailable
from .hosts import ssh_argv

USER_HOME = "/home/user"
AGENT_SESSION = "agent"  # the sandbox's tmux session that holds its agent
# What installs a project's dependencies, by the lock file that says how. The first
# match wins; its files (and a setup script, if the project has one) name the
# project checkpoint, so it is rebuilt exactly when they change.
INSTALLERS = (
    ("uv.lock", "uv sync"),
    ("pnpm-lock.yaml", "npx -y pnpm install --frozen-lockfile"),
    ("yarn.lock", "npx -y yarn install --frozen-lockfile"),
    ("package-lock.json", "npm ci"),
    ("requirements.txt", "python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"),
)
SETUP = ".4top/setup.sh"  # the project's own step, run after the installer
SSH_KEYS = ("id_ed25519.pub", "id_ecdsa.pub", "id_rsa.pub")

# 4top's side of a sandbox, installed as ~/.local/bin/4top-sandbox.
#   keep        while an agent is writing a transcript, keep the sandbox up; ten
#               minutes after the last write, it pauses (with its processes). The
#               loop is in the checkpoint's memory, so every sandbox runs it.
#   checkpoint  an agent's turn ended (Claude's Stop hook, Codex's notify): log the
#               turn's prompt under a checkpoint name, then snapshot the machine.
#               A snapshot freezes the machine and resets its connections, so the
#               log line comes first and nothing waits for the answer.
SANDBOX_SCRIPT = r"""#!/bin/sh
. "$HOME/.4top/env"
api() {
    curl -fsS --max-time 120 -X POST "https://api.e2b.dev$1" -H "X-API-KEY: $E2B_API_KEY" \
        -H 'Content-Type: application/json' -d "$2"
}
case "${1:-}" in
keep)
    while sleep 60; do
        id=$(cat /run/e2b/.E2B_SANDBOX_ID)  # re-read: a fork carries this loop
        if [ -n "$(find "$HOME/.claude/projects" "$HOME/.codex/sessions" -type f -mmin -3 2>/dev/null | head -1)" ]; then
            api "/v2/sandboxes/$id/connect" '{"timeout": 600}' >/dev/null 2>&1
        fi
    done ;;
checkpoint)
    id=$(cat /run/e2b/.E2B_SANDBOX_ID)
    payload=${2:-$(cat)}
    prompt=$(printf '%s' "$payload" | python3 -c '
import json, sys
try:
    event = json.loads(sys.stdin.read())
except ValueError:
    event = {}
text = ""
for message in event.get("input-messages") or []:
    text = message
path = event.get("transcript_path")
if path:
    for line in open(path, encoding="utf-8", errors="replace"):
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        content = (entry.get("message") or {}).get("content") if entry.get("type") == "user" else None
        if isinstance(content, str):
            text = content
print(" ".join(str(text).split())[:120])')
    at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
    name="fourtop-cp-$id-$(date +%s)"
    mkdir -p "$HOME/.4top"
    printf '%s\t%s\t%s\n' "$at" "$name" "$prompt" >> "$HOME/.4top/checkpoints"
    # Detached from the hook's output, and a second late, so the agent has seen
    # the hook finish before the machine freezes: a copy never waits on it.
    (sleep 1; api "/sandboxes/$id/snapshots" "{\"name\": \"$name\"}") </dev/null >/dev/null 2>&1 &
    ;;
esac
"""
# The sandbox's tmux is only a keeper for the agent: no prefix key and no status
# line, so inside the panel's own tmux it is invisible.
SANDBOX_TMUX = "set -g prefix None\nset -g status off\nset -g mouse on\nset -g history-limit 50000\n"


@dataclass(frozen=True)
class Project:
    path: str  # absolute, and the same inside the sandbox
    name: str  # for names: lowercase letters, digits and dashes
    installer: str | None
    digest: str  # of the files that decide what the checkpoint holds

    @property
    def checkpoint(self) -> str:
        return f"fourtop-env-{self.name}-{self.digest[:10]}"


def project(cwd: str) -> Project:
    """The git repository around ``cwd`` (or ``cwd`` itself) as a cloud project."""
    try:
        top = subprocess.run(["git", "-C", cwd, "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, timeout=10)
        path = top.stdout.strip() if top.returncode == 0 and top.stdout.strip() else cwd
    except (OSError, subprocess.TimeoutExpired):
        path = cwd
    root = Path(path).resolve()
    if not root.is_dir():
        raise Missing(f"Not a directory: {root}")
    installer = next((command for name, command in INSTALLERS if (root / name).is_file()), None)
    digest = hashlib.sha256(SANDBOX_SCRIPT.encode() + SANDBOX_TMUX.encode())
    for name in [name for name, _ in INSTALLERS] + [SETUP]:
        if (root / name).is_file():
            digest.update(name.encode() + b"\0" + (root / name).read_bytes() + b"\0")
    slug = re.sub(r"[^a-z0-9]+", "-", root.name.lower()).strip("-")[:30] or "project"
    return Project(str(root), slug, installer, digest.hexdigest())


def fingerprint(path: str) -> str:
    """What the working tree is now: HEAD, the uncommitted diff and the untracked
    files. Equal fingerprints mean nothing changed here in between."""
    def git(*args):
        return subprocess.run(["git", "-C", path, *args], capture_output=True, timeout=60).stdout
    digest = hashlib.sha256(git("rev-parse", "HEAD") + git("diff", "HEAD", "--binary"))
    for name in git("ls-files", "-o", "--exclude-standard", "-z").split(b"\0"):
        if name:
            digest.update(name + b"\0")
            try:
                digest.update(Path(path, name.decode()).read_bytes())
            except (OSError, UnicodeDecodeError):
                pass
    return digest.hexdigest()[:16]


def sandbox_host(sandbox: str, name: str) -> Host:
    # Sandbox calls cross the internet twice (E2B, then the websocket), so they get
    # more time than a LAN host.
    return Host(name, f"user@{sandbox}", refresh_seconds=15.0, timeout_seconds=20.0, e2b=sandbox)


def discover(config: Config) -> list[tuple[Host, dict]]:
    """Every sandbox 4top made that is a place to work, with E2B's record of it."""
    found = []
    for item in e2b.tagged(config):
        metadata = item.get("metadata") or {}
        if metadata.get("fourtop_role") == "builder":
            continue
        sandbox = str(item.get("sandboxID", ""))
        name = str(metadata.get("fourtop_name") or sandbox)
        found.append((sandbox_host(sandbox, name), item))
    found.sort(key=lambda pair: pair[0].name)
    return found


def find(config: Config, name: str) -> tuple[Host, dict]:
    matches = [pair for pair in discover(config) if name in (pair[0].name, pair[0].e2b)]
    if not matches:
        raise Missing(f"No cloud sandbox named {name}; `4top cloud ls` lists them")
    return matches[0]


# A checkpoint resets the sandbox's connections after every agent turn, and a phone's
# link drops on its own; the agent never notices, because it lives in the sandbox's
# tmux. ssh exits 255 when the link is lost, so that, and only that, reattaches.
REATTACH = ('while :; do "$@"; code=$?; [ "$code" -eq 255 ] || exit "$code"; '
            'printf "\\r\\n4top: link lost, reattaching\\r\\n"; sleep 1; done')


def agent_argv(config: Config, host: Host, args: list[str] | None) -> list[str]:
    """Attach to the sandbox's agent, starting it with ``4top ARGS`` if none runs
    (``None``: only attach)."""
    tmux = dataclasses.replace(host, command="tmux")
    command = (["attach-session", "-t", AGENT_SESSION] if args is None else
               ["new-session", "-A", "-s", AGENT_SESSION, host.command, *args])
    return ["sh", "-c", REATTACH, "4top-attach", *ssh_argv(config, tmux, command, tty=True)]


# ----- talking to a sandbox -----------------------------------------------------------

def shell(config: Config, host: Host, script: str, *, check: bool = True, capture: bool = True,
          timeout: float | None = 600) -> subprocess.CompletedProcess:
    """Run a shell script in the sandbox. Output streams to this terminal unless
    captured, so a long install is not a silent wait."""
    argv = ssh_argv(config, dataclasses.replace(host, command="sh"), ["-c", script])
    try:
        result = subprocess.run(argv, capture_output=capture, text=True, timeout=timeout,
                                env=config.environment, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        raise Unavailable(f"{host.name}: no answer within {timeout:g}s") from None
    if check and result.returncode != 0:
        detail = (result.stderr or "").strip()[-300:] if capture else ""
        raise Unavailable(f"{host.name}: exit {result.returncode}" + (f" · {detail}" if detail else ""))
    return result


def wait_for_ssh(config: Config, host: Host, seconds: float = 60) -> None:
    deadline = time.monotonic() + seconds
    while True:
        try:
            shell(config, host, "true", timeout=30)
            return
        except Unavailable:
            if time.monotonic() > deadline:
                raise
            time.sleep(1)


# Files travel as tar over ssh, and git decides which: tracked files and untracked
# ones it does not ignore. What git ignores (installed dependencies, caches, build
# output) is never sent, never overwritten and never deleted, on either side.
# (rsync's .gitignore filter was the first attempt; macOS ships openrsync, which
# ignores it, and --delete then removed the ignored files.)
LISTING = "git ls-files -co --exclude-standard -z"


def _run_pipe(producer: list[str], consumer: list[str], env: dict[str, str], label: str,
              names: list[str] | None = None) -> None:
    """``producer | consumer``; ``names`` go to the producer's input, NUL-separated."""
    with tempfile.TemporaryFile() as errors_out, tempfile.TemporaryFile() as errors_in:
        try:
            first = subprocess.Popen(producer, stdin=subprocess.PIPE if names is not None else subprocess.DEVNULL,
                                     stdout=subprocess.PIPE, stderr=errors_out, env=env)
            second = subprocess.Popen(consumer, stdin=first.stdout, stdout=subprocess.DEVNULL,
                                      stderr=errors_in, env=env)
        except FileNotFoundError as exc:
            raise Dependency(f"{exc.filename} is not installed on this machine") from None
        first.stdout.close()
        if names is not None:
            try:
                first.stdin.write(b"".join(name.encode() + b"\0" for name in names))
            except BrokenPipeError:
                pass
            first.stdin.close()
        codes = first.wait(1800), second.wait(1800)
        if any(codes):
            errors_out.seek(0), errors_in.seek(0)
            detail = (errors_out.read() + errors_in.read()).decode(errors="replace").strip()[-300:]
            raise Unavailable(f"{label}: copy failed {codes} · {detail}")


def _tar_env(config: Config) -> dict[str, str]:
    # macOS tar otherwise adds ._ files for extended attributes.
    return {**config.environment, "COPYFILE_DISABLE": "1"}


def send(config: Config, host: Host, base: str, names: list[str], target: str | None = None,
         *, owned_by_root: bool = False) -> None:
    """These paths under ``base`` here, to the same paths under ``target`` there."""
    target = shlex.quote(target or base)
    make = (f"sudo mkdir -p {target} && sudo chown user:user {target}" if owned_by_root
            else f"mkdir -p {target}")
    remote = ssh_argv(config, dataclasses.replace(host, command="sh"),
                      ["-c", f"{make} && tar -xf - -C {target}"])
    _run_pipe(["tar", "--no-xattrs", "-C", base, "-cf", "-", "--null", "-T", "-"], remote,
              _tar_env(config), host.name, names)


def receive(config: Config, host: Host, base: str, listing: str, target: str | None = None,
            *, keep_newer: bool = False) -> None:
    """What ``listing`` names under ``base`` there, to the same paths under
    ``target`` here. A newer file here wins when ``keep_newer``."""
    remote = ssh_argv(config, dataclasses.replace(host, command="sh"), [
        "-c", f"cd {shlex.quote(base)} && {listing} | "
              "tar --ignore-failed-read -cf - --null -T -"])
    target = target or base
    Path(target).mkdir(parents=True, exist_ok=True)
    _run_pipe(remote, ["tar", "-xf", "-", "-C", target, *(["--keep-newer-files"] if keep_newer else [])],
              _tar_env(config), host.name)


def fetch(config: Config, host: Host, remote_path: str, local_path: str) -> None:
    argv = ssh_argv(config, dataclasses.replace(host, command="cat"), [remote_path])
    with open(local_path, "wb") as handle:
        if subprocess.run(argv, stdout=handle, stderr=subprocess.DEVNULL, env=config.environment,
                          timeout=1800).returncode:
            raise Unavailable(f"{host.name}: could not read {remote_path}")


def tree_files(path: str) -> list[str]:
    """The project's files by git's account, relative, that exist."""
    result = subprocess.run(["git", "-C", path, *LISTING.split()[1:]], capture_output=True, timeout=120)
    if result.returncode != 0:
        raise Dependency(f"{path} is not a git repository; cloud sessions travel by git's account")
    names = sorted({name.decode() for name in result.stdout.split(b"\0") if name})
    return [name for name in names if os.path.lexists(os.path.join(path, name))]


def remote_files(config: Config, host: Host, path: str) -> set[str]:
    listed = shell(config, host, f"cd {shlex.quote(path)} && {LISTING}").stdout
    return {name for name in listed.split("\0") if name}


def git_common(path: str) -> str | None:
    """The repository a worktree belongs to, when ``path`` is a linked worktree:
    its ``.git`` is only a pointer to that repository's git directory."""
    if not Path(path, ".git").is_file():
        return None
    result = subprocess.run(["git", "-C", path, "rev-parse", "--path-format=absolute",
                             "--git-common-dir"], capture_output=True, text=True, timeout=30)
    return result.stdout.strip() or None if result.returncode == 0 else None


def push_tree(config: Config, host: Host, path: str) -> None:
    """Make the sandbox's copy of the project what it is here, git directory included."""
    names = tree_files(path)
    # A worktree's pointer names an absolute path; the repository it points to goes
    # to that same path, so the pointer means the same thing there.
    common = git_common(path)
    if common:
        send(config, host, common, ["."], owned_by_root=True)
    send(config, host, path, names + [".git"], owned_by_root=True)
    gone = sorted(remote_files(config, host, path) - set(names))
    if gone:
        _run_pipe(["cat"], ssh_argv(config, dataclasses.replace(host, command="sh"),
                                    ["-c", f"cd {shlex.quote(path)} && xargs -0 rm -f --"]),
                  config.environment, host.name, gone)


def pull_tree(config: Config, host: Host, path: str) -> None:
    """The sandbox's files, here. Git's own records here are never overwritten; a
    commit made there arrives through ``take``."""
    there = remote_files(config, host, path)
    receive(config, host, path, LISTING)
    for name in set(tree_files(path)) - there:
        with contextlib.suppress(OSError):
            os.unlink(os.path.join(path, name))


# ----- credentials --------------------------------------------------------------------

def credentials(config: Config, project_path: str) -> dict[str, bytes]:
    """This machine's own sign-ins, as files for the sandbox user's home: the ssh
    key that lets 4top in, the Claude token (``claude setup-token``, from
    ``CLAUDE_CODE_OAUTH_TOKEN`` or ~/.config/claude-code/oauth-token), Codex's
    auth.json and the [agents] entries, so a permission mode carries over. Also the
    E2B key, which the sandbox uses to keep itself up and to checkpoint itself."""
    home = Path(config.environment.get("HOME", str(Path.home())))
    files: dict[str, bytes] = {}
    key = next((home / ".ssh" / name for name in SSH_KEYS if (home / ".ssh" / name).is_file()), None)
    if key is None:
        raise Dependency("No ssh public key in ~/.ssh; create one with ssh-keygen")
    files[".ssh/authorized_keys"] = key.read_bytes()
    token = config.environment.get("CLAUDE_CODE_OAUTH_TOKEN")
    if not token:
        try:
            token = (home / ".config/claude-code/oauth-token").read_text(encoding="utf-8").strip()
        except OSError:
            token = ""
    if token:
        files[".ssh/environment"] = f"CLAUDE_CODE_OAUTH_TOKEN={token}\n".encode()
    files[".4top/env"] = f"E2B_API_KEY={shlex.quote(e2b.api_key(config.environment))}\n".encode()
    files[".local/bin/4top-sandbox"] = SANDBOX_SCRIPT.encode()
    files[".tmux.conf"] = SANDBOX_TMUX.encode()
    # Claude asks whether to trust a folder the first time; this folder is the
    # user's own project, brought here by the user.
    files[".claude.json"] = json.dumps({
        "hasCompletedOnboarding": True,
        "projects": {project_path: {"hasTrustDialogAccepted": True}}}).encode()
    checkpoint = f"{USER_HOME}/.local/bin/4top-sandbox checkpoint"
    settings = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": checkpoint}]}]}}
    try:
        local = json.loads((Path(config.root("claude").path) / "settings.json").read_text())
    except (OSError, ValueError):
        local = {}
    # The user's own answer to Claude's bypass-mode warning, if they gave it here.
    if isinstance(local, dict) and local.get("skipDangerousModePermissionPrompt") is True:
        settings["skipDangerousModePermissionPrompt"] = True
    files[".claude/settings.json"] = json.dumps(settings, indent=2).encode()
    # Codex asks the same about the repository root, which for a worktree is the
    # repository it belongs to.
    common = git_common(project_path)
    trusted = [project_path] + ([str(Path(common).parent)] if common else [])
    files[".codex/config.toml"] = (f'notify = ["{USER_HOME}/.local/bin/4top-sandbox", "checkpoint"]\n' + "".join(
        f'\n[projects.{json.dumps(path)}]\ntrust_level = "trusted"\n' for path in trusted)).encode()
    auth = Path(config.root("codex").path) / "auth.json"
    if auth.is_file():
        files[".codex/auth.json"] = auth.read_bytes()
    lines = []
    for agent, args in config.agent_args.items():
        if args:
            lines += [f"[agents.{agent}]", f"args = {json.dumps(list(args))}"]
    if lines:
        files[".config/4top/config.toml"] = ("\n".join(lines) + "\n").encode()
    return files


def bootstrap(config: Config, created: dict, project_path: str) -> Host:
    """Let ssh in and sign the agents in, through the sandbox's file API."""
    sandbox, token = str(created["sandboxID"]), str(created.get("envdAccessToken", ""))
    for relative, data in credentials(config, project_path).items():
        e2b.upload(sandbox, token, f"{USER_HOME}/{relative}", data)
    host = sandbox_host(sandbox, "setup")
    wait_for_ssh(config, host)
    shell(config, host, "chmod 700 ~/.4top ~/.local/bin/4top-sandbox && chmod 600 ~/.4top/env && "
                        "(nohup ~/.local/bin/4top-sandbox keep >/dev/null 2>&1 &)")
    return host


# ----- the verbs ----------------------------------------------------------------------

def say(message: str) -> None:
    print(f"4top: {message}", file=sys.stderr, flush=True)


def environment(config: Config, where: Project, rebuild: bool = False) -> str:
    """The project's checkpoint: its files at their path and its dependencies
    installed. Built once per set of dependency files."""
    if not rebuild:
        for item in e2b.checkpoints(config, name=where.checkpoint):
            if isinstance(item, dict) and item.get("snapshotID"):
                return str(item["snapshotID"])
    say(f"building the machine for {where.path} (once per set of dependency files)")
    created = e2b.create(config, e2b.TEMPLATE, f"{where.name}-setup",
                         {"fourtop_role": "builder"}, seconds=3600)
    sandbox = str(created["sandboxID"])
    try:
        host = bootstrap(config, created, where.path)
        path = shlex.quote(where.path)
        shell(config, host, f"sudo mkdir -p {path} && sudo chown user:user {path}")
        push_tree(config, host, where.path)
        steps = [step for step in (where.installer, f"sh {SETUP}" if Path(where.path, SETUP).is_file()
                                   else None) if step]
        for step in steps:
            say(f"{where.name}: {step}")
            shell(config, host, f"cd {path} && {step}", capture=False, timeout=3600)
        snapshot = e2b.checkpoint(config, sandbox, where.checkpoint)
    finally:
        try:
            e2b.kill(config, sandbox)
        except FourtopError:
            pass
    return snapshot


def _unique(config: Config, name: str) -> str:
    taken = {host.name for host, _ in discover(config)} | set(config.hosts)
    if name not in taken:
        return name
    return next(f"{name}-{n}" for n in range(2, 1000) if f"{name}-{n}" not in taken)


def _start(config: Config, template: str, name: str, metadata: dict[str, str]) -> Host:
    created = e2b.create(config, template, name, metadata)
    host = sandbox_host(str(created["sandboxID"]), name)
    wait_for_ssh(config, host)
    return host


def new(config: Config, cwd: str, name: str | None = None) -> tuple[Host, Project]:
    """A fresh machine for this project, with this machine's current work on it."""
    where = project(cwd)
    snapshot = environment(config, where)
    name = _unique(config, name or where.name)
    host = _start(config, snapshot, name, {"fourtop_path": where.path,
                                           "fourtop_tree": fingerprint(where.path)})
    push_tree(config, host, where.path)
    return host, where


def fork(config: Config, name: str, count: int) -> list[Host]:
    """``count`` copies of a sandbox as it is this instant: files, processes and
    the agent in its tmux, which carries on in every copy."""
    parent, item = find(config, name)
    e2b.wake(config, parent)
    snapshot = e2b.checkpoint(config, parent.e2b)
    metadata = {key: value for key, value in (item.get("metadata") or {}).items()
                if key.startswith("fourtop_") and key != "fourtop_name"}
    names = [_unique(config, f"{parent.name}-{n}") for n in range(1, count + 1)]
    # E2B keeps a checkpoint while a sandbox made from it runs; the last copy to be
    # removed deletes it (see remove).
    metadata.update(fourtop_parent=parent.name, fourtop_origin=snapshot)
    with ThreadPoolExecutor(max_workers=min(count, 8)) as pool:
        return list(pool.map(lambda child: _start(config, snapshot, child, metadata), names))


def race(config: Config, cwd: str, prompt: str, agents: list[str]) -> list[Host]:
    """One machine per agent from the same checkpoint, each agent started on the
    prompt in the sandbox's tmux. They keep working with nobody attached."""
    base, where = new(config, cwd, None)
    try:
        snapshot = e2b.checkpoint(config, base.e2b)
        metadata = {"fourtop_path": where.path, "fourtop_tree": fingerprint(where.path),
                    "fourtop_race": base.name, "fourtop_origin": snapshot}
        names = [_unique(config, f"{where.name}-{agent}") for agent in agents]
        with ThreadPoolExecutor(max_workers=min(len(agents), 8)) as pool:
            hosts = list(pool.map(lambda child: _start(config, snapshot, child, metadata), names))
    finally:
        e2b.kill(config, base.e2b)
    for host, agent in zip(hosts, agents):
        command = shlex.join([host.command, "new", agent, "--cwd", where.path, "--yes", "--", prompt])
        shell(config, host, f"tmux new-session -d -s {AGENT_SESSION} {shlex.quote(command)}")
    return hosts


def take(config: Config, name: str, cwd: str) -> str:
    """The sandbox's work as local branch 4top/NAME: committed there, carried as a
    git bundle, fetched here. Nothing in this working tree changes."""
    host, item = find(config, name)
    path = (item.get("metadata") or {}).get("fourtop_path") or project(cwd).path
    e2b.wake(config, host)
    branch = f"4top/{host.name}"
    quoted = shlex.quote(path)
    shell(config, host, f"cd {quoted} && git add -A && "
                        f"git -c user.name=4top -c user.email=4top@localhost commit -q --allow-empty --no-verify "
                        f"-m {shlex.quote(f'4top: work from {host.name}')} && "
                        f"git bundle create /tmp/4top.bundle HEAD 2>/dev/null")
    with tempfile.TemporaryDirectory(prefix="4top-") as directory:
        local = str(Path(directory, "work.bundle"))
        fetch(config, host, "/tmp/4top.bundle", local)
        result = subprocess.run(["git", "-C", path, "fetch", "-q", local, f"+HEAD:refs/heads/{branch}"],
                                capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        raise Unavailable(f"git fetch failed: {result.stderr.strip()[-300:]}")
    return branch


def checkpoint_log(config: Config, name: str) -> tuple[Host, dict, list[tuple[str, str, str]]]:
    """The turns this sandbox checkpointed: (time, checkpoint, prompt), oldest first."""
    host, item = find(config, name)
    e2b.wake(config, host)
    text = shell(config, host, "cat ~/.4top/checkpoints 2>/dev/null || true").stdout
    turns = [tuple((line.split("\t") + ["", "", ""])[:3]) for line in text.splitlines() if line.strip()]
    return host, item, turns


def rewind(config: Config, name: str, turn: int) -> Host:
    """A new sandbox from the checkpoint after turn ``turn`` (1 is the first)."""
    host, item, turns = checkpoint_log(config, name)
    if not 1 <= turn <= len(turns):
        raise Missing(f"{name} has {len(turns)} checkpointed turns")
    found = [entry for entry in e2b.checkpoints(config, name=turns[turn - 1][1])
             if isinstance(entry, dict) and entry.get("snapshotID")]
    if not found:
        raise Missing(f"The checkpoint after turn {turn} was not taken or is gone")
    metadata = {key: value for key, value in (item.get("metadata") or {}).items()
                if key.startswith("fourtop_") and key != "fourtop_name"}
    child = _unique(config, f"{host.name}-t{turn}")
    return _start(config, str(found[0]["snapshotID"]), child, {**metadata, "fourtop_parent": host.name})


def up(config: Config, record, name: str | None = None) -> tuple[Host, str]:
    """Carry a local session to the cloud: its project, this machine's work on it,
    and its transcript at the same place. Returns the host and what to resume."""
    if record.agent not in ("claude", "codex") or not record.native_id:
        raise Dependency("Only Claude and Codex sessions with an exact ID can move")
    host, where = new(config, record.cwd or ".", name or None)
    root = Path(config.root(record.agent).path)
    relative = Path(record.file).resolve().relative_to(root.resolve())
    # Claude keeps a session's subagent transcripts beside it, under its ID.
    names = [str(relative)] + ([str(relative.with_suffix(""))]
                               if Path(record.file).with_suffix("").is_dir() else [])
    send(config, host, str(root), names, f"{USER_HOME}/.{record.agent}")
    return host, record.native_id


def home(config: Config, name: str) -> tuple[str, str | None]:
    """Bring a cloud session back: its transcripts, and its work into this tree if
    the tree has not changed since it left, else as branch 4top/NAME. Returns the
    project path and the branch, if one was made."""
    host, item = find(config, name)
    metadata = item.get("metadata") or {}
    path = metadata.get("fourtop_path")
    if not path:
        raise Missing(f"{name} does not say which project it holds")
    e2b.wake(config, host)
    shell(config, host, f"tmux kill-session -t {AGENT_SESSION} 2>/dev/null; true")
    for agent, sessions in (("claude", "projects"), ("codex", "sessions")):
        target = Path(config.root(agent).path) / sessions
        if shell(config, host, f"test -d ~/.{agent}/{sessions}", check=False).returncode == 0:
            target.mkdir(parents=True, exist_ok=True)
            receive(config, host, f"{USER_HOME}/.{agent}/{sessions}", "find . -type f -print0",
                    str(target), keep_newer=True)
    if metadata.get("fourtop_tree") != fingerprint(path):
        return path, take(config, name, path)
    there = shell(config, host, f"git -C {shlex.quote(path)} rev-parse HEAD", check=False).stdout.strip()
    here = subprocess.run(["git", "-C", path, "rev-parse", "HEAD"], capture_output=True,
                          text=True, timeout=30).stdout.strip()
    pull_tree(config, host, path)
    # The files are home. If the agent also committed, those commits are a branch.
    return path, take(config, name, path) if there and there != here else None


def remove(config: Config, name: str) -> None:
    """Kill a sandbox and delete the checkpoints it took of itself, and the one it
    was copied from once no other copy runs from it."""
    host, item = find(config, name)
    origin = (item.get("metadata") or {}).get("fourtop_origin")
    for item in e2b.checkpoints(config):
        snapshot = str(item.get("snapshotID", "")) if isinstance(item, dict) else ""
        if f"fourtop-cp-{host.e2b}-" in snapshot:
            try:
                e2b.forget(config, snapshot)
            except FourtopError:
                pass
    e2b.kill(config, host.e2b)
    if origin:
        try:
            e2b.forget(config, origin)
        except FourtopError:
            pass  # a sibling still runs from it
