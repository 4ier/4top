"""Cloud tasks: a repository, a ref and a prompt, worked on in a sandbox of its own.

Home machines are where agents normally run; a cloud task is the overflow, for when
they are asleep or busy. A task is started from any device that has an E2B key, a
GitHub token and the agent's credentials, a tablet included: nothing here reads a
local checkout. It goes:

1. a sandbox from the versioned 4top template, with a deadline (``[cloud]
   max_minutes``) it is created with and never kept up past;
2. the repository cloned *inside* the sandbox at the ref, on a new branch
   ``4top/NAME``; the GitHub token is passed for the clone and never written down;
3. the agent started on the prompt in the sandbox's agent tmux (``4top-agents``,
   fourtop.resident), so it works with nobody attached and the panel opens it with
   ``4top attach`` like any host's resident agent;
4. ``4top cloud done``: the work committed and pushed as ``4top/NAME`` (and a pull
   request, if asked), the agent stopped and its credentials removed, a snapshot
   kept for ``[cloud] keep_snapshot_days``, and the sandbox killed.

Credentials are injected per start, into that sandbox's agent only; none is baked
into a snapshot, the E2B key never enters a sandbox, and the user's own agent
settings in the sandbox are merged into, not replaced.

A task that would take today's spending past ``[cloud] daily_budget_usd`` in the
worst case (its whole lifetime) is refused before anything is created, and
``describe`` gives every sandbox's state, cost so far and lifetime left as data.
"""
from __future__ import annotations

import base64
import json
import re
import shlex
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, replace
from pathlib import Path

from . import e2b
from .config import Config, Host
from .errors import Conflict, Dependency, FourtopError, Missing, Unavailable
from .hosts import ssh_argv
from .resident import CONF

USER_HOME = "/home/user"
WORK = f"{USER_HOME}/work"
SNAPSHOT_PREFIX = "fourtop-task-"
SSH_KEYS = ("id_ed25519.pub", "id_ecdsa.pub", "id_rsa.pub")
# A sandbox is disposable and holds nothing but the task, so its agent runs without
# asking before each command: nobody is watching it to answer.
AGENT_ARGS = {"claude": ("--dangerously-skip-permissions",),
              "codex": ("--dangerously-bypass-approvals-and-sandbox",)}
# Where a task's facts live: E2B's metadata of its sandbox, written once at creation
# (E2B has no call to change it), so every device reads the same task.
FIELDS = ("name", "repo", "ref", "branch", "agent", "prompt", "dir", "deadline")


def say(message: str) -> None:
    print(f"4top: {message}", file=sys.stderr, flush=True)


def slug(text: str, limit: int = 24) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:limit].strip("-") or "task"


def sandbox_host(sandbox: str, name: str) -> Host:
    # Sandbox calls cross the internet twice (E2B, then the websocket), so they get
    # more time than a LAN host.
    return Host(name, f"user@{sandbox}", refresh_seconds=15.0, timeout_seconds=20.0, e2b=sandbox)


# ----- repositories -------------------------------------------------------------------

GITHUB = re.compile(r"(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)?"
                    r"(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+?)(?:\.git)?/?\Z")


def github(url: str) -> tuple[str, str] | None:
    """(owner, repo) of a GitHub URL or ``owner/repo``; None for anything else."""
    if "://" in url and not url.startswith(("https://github.com/", "ssh://git@github.com/")):
        return None
    match = GITHUB.match(url)
    return (match["owner"], match["repo"]) if match else None


def clone_url(url: str) -> str:
    """What the sandbox clones: GitHub over https, where the token applies. Other
    URLs are cloned as given, with no token."""
    found = github(url)
    return f"https://github.com/{found[0]}/{found[1]}.git" if found else url


def origin(cwd: str) -> tuple[str, str]:
    """This directory's origin and branch, as a convenience default. The sandbox
    clones what was pushed; what is only here does not travel."""
    def git(*args):
        result = subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True, timeout=10)
        return result.stdout.strip() if result.returncode == 0 else ""
    try:
        url, branch = git("remote", "get-url", "origin"), git("rev-parse", "--abbrev-ref", "HEAD")
    except (OSError, subprocess.TimeoutExpired):
        url = branch = ""
    if not url:
        raise Missing("Give the repository: --repo URL (or owner/repo for GitHub)")
    return url, "" if branch == "HEAD" else branch


# ----- credentials, gathered here and handed to one sandbox ---------------------------

def github_token(config: Config) -> str:
    env = config.environment
    token = env.get("GH_TOKEN") or env.get("GITHUB_TOKEN")
    if not token and shutil.which("gh", path=env.get("PATH")):
        try:
            result = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True,
                                    timeout=10, env=env)
            token = result.stdout.strip() if result.returncode == 0 else ""
        except (OSError, subprocess.TimeoutExpired):
            token = ""
    return token or ""


def agent_credentials(config: Config, agent: str) -> dict[str, str]:
    """The environment that signs ``agent`` in, from this device. Codex's
    ``auth.json`` travels as ``CODEX_AUTH_JSON`` and is written for the agent only."""
    env = config.environment
    home = Path(env.get("HOME", str(Path.home())))
    if agent == "claude":
        token = env.get("CLAUDE_CODE_OAUTH_TOKEN")
        if not token:
            try:
                token = (home / ".config/claude-code/oauth-token").read_text(encoding="utf-8").strip()
            except OSError:
                token = ""
        if token:
            return {"CLAUDE_CODE_OAUTH_TOKEN": token}
        if env.get("ANTHROPIC_API_KEY"):
            return {"ANTHROPIC_API_KEY": env["ANTHROPIC_API_KEY"]}
        raise Dependency("No Claude credentials here: set CLAUDE_CODE_OAUTH_TOKEN (`claude setup-token`) "
                         "or ANTHROPIC_API_KEY")
    if agent == "codex":
        if env.get("OPENAI_API_KEY"):
            return {"OPENAI_API_KEY": env["OPENAI_API_KEY"]}
        auth = Path(config.root("codex").path) / "auth.json"
        if auth.is_file():
            return {"CODEX_AUTH_JSON": auth.read_text(encoding="utf-8")}
        raise Dependency("No Codex credentials here: set OPENAI_API_KEY or run `codex login`")
    raise Dependency("Cloud tasks run claude or codex")


def ssh_key(config: Config) -> bytes:
    home = Path(config.environment.get("HOME", str(Path.home())))
    for name in SSH_KEYS:
        if (home / ".ssh" / name).is_file():
            return (home / ".ssh" / name).read_bytes()
    raise Dependency("No ssh public key in ~/.ssh; create one with ssh-keygen")


def identity(config: Config) -> tuple[str, str]:
    """Who the task's commits are by: this device's git identity, else 4top."""
    def get(key):
        try:
            result = subprocess.run(["git", "config", "--global", key], capture_output=True, text=True,
                                    timeout=5, env=config.environment)
            return result.stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            return ""
    return get("user.name") or "4top", get("user.email") or "4top@localhost"


# ----- talking to a sandbox -----------------------------------------------------------
# Scripts run as ``sh -c SCRIPT`` over ssh. Values, secrets among them, go on stdin
# as NAME=base64 lines, never in an argument list or a file.

PRELUDE = r"""set -eu
while IFS= read -r line; do
    [ -n "$line" ] || continue
    # Split by hand: read's IFS would drop base64's trailing "=".
    export "${line%%=*}=$(printf '%s' "${line#*=}" | base64 -d)"
done
export PATH="$HOME/.local/bin:$PATH"
SOCK=${FOURTOP_AGENTS_SOCKET:-4top-agents}
STATE="$HOME/.4top-task"
auth() {
    # git -c for one command: the token reaches git's memory and nothing else.
    if [ -n "${GH_TOKEN:-}" ]; then
        git -c "http.https://github.com/.extraheader=AUTHORIZATION: basic $(printf 'x-access-token:%s' "$GH_TOKEN" | base64 | tr -d '\n')" "$@"
    else
        git "$@"
    fi
}
"""


def values(**pairs: str) -> bytes:
    return b"".join(f"{key}={base64.b64encode(str(value).encode()).decode()}\n".encode()
                    for key, value in pairs.items() if value is not None)


def run_in(config: Config, host: Host, script: str, stdin: bytes = b"", *, timeout: float = 600,
           check: bool = True) -> subprocess.CompletedProcess:
    """Run a script in the sandbox; tests replace this with a local shell."""
    argv = ssh_argv(config, replace(host, command="sh"), ["-c", PRELUDE + script])
    try:
        result = subprocess.run(argv, input=stdin, capture_output=True, timeout=timeout,
                                env=config.environment)
    except subprocess.TimeoutExpired:
        raise Unavailable(f"{host.name}: no answer within {timeout:g}s") from None
    if check and result.returncode != 0:
        detail = result.stderr.decode(errors="replace").strip()[-400:]
        raise Unavailable(f"{host.name}: exit {result.returncode}" + (f" · {detail}" if detail else ""))
    return result


def wait_for_ssh(config: Config, host: Host, seconds: float = 90) -> None:
    deadline = time.monotonic() + seconds
    while True:
        try:
            run_in(config, host, "true", timeout=30)
            return
        except Unavailable:
            if time.monotonic() > deadline:
                raise
            time.sleep(1)


# Names the agent's tmux session after its history key once the transcript exists,
# so the row for it is marked resident and `4top attach KEY` (what the panel runs on
# Enter) attaches to this agent instead of resuming a second one.
NAMER = r"""
import json, os, subprocess, sys, time
sock, session, directory, agent = sys.argv[1:5]
for _ in range(120):
    time.sleep(5)
    try:
        out = subprocess.run(["4top", "list", "--json"], capture_output=True, text=True, timeout=60).stdout
    except (OSError, subprocess.TimeoutExpired):
        continue
    rows = []
    for line in out.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get("key") and row.get("cwd") == directory and row.get("agent") == agent:
            rows.append(row)
    if rows:
        key = max(rows, key=lambda row: row.get("started", ""))["key"]
        if subprocess.run(["tmux", "-L", sock, "rename-session", "-t", "=" + session, key]).returncode == 0:
            with open(os.path.expanduser("~/.4top-task/session"), "w") as handle:
                handle.write(key)
        break
"""

# Claude and Codex ask on first use whether to trust a folder, and Claude whether
# to use a key; this is the person's own task, so the answers are given. Merged into
# whatever the template's agents already have.
SETTLE = r"""
import json, os, sys
directory, agent = sys.argv[1:3]
home = os.path.expanduser("~")
def merge(path, change):
    try:
        with open(path) as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        data = {}
    change(data)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as handle:
        json.dump(data, handle, indent=2)
if agent == "claude":
    def claude(data):
        data["hasCompletedOnboarding"] = True
        data.setdefault("projects", {}).setdefault(directory, {})["hasTrustDialogAccepted"] = True
        key = os.environ.get("ANTHROPIC_API_KEY")
        if key:
            approved = data.setdefault("customApiKeyResponses", {}).setdefault("approved", [])
            if key[-20:] not in approved:
                approved.append(key[-20:])
    merge(os.path.join(home, ".claude.json"), claude)
    merge(os.path.join(home, ".claude/settings.json"),
          lambda data: data.__setitem__("skipDangerousModePermissionPrompt", True))
else:
    path = os.path.join(home, ".codex/config.toml")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        text = open(path).read()
    except OSError:
        text = ""
    entry = "[projects.%s]" % json.dumps(directory)
    if entry not in text:
        with open(path, "a") as handle:
            handle.write("\n%s\ntrust_level = \"trusted\"\n" % entry)
"""

START = r"""
umask 077
mkdir -p "$STATE" "$(dirname "$T_DIR")"
auth clone -q "$T_REPO" "$T_DIR"
unset GH_TOKEN
cd "$T_DIR"
if [ -n "$T_REF" ]; then git checkout -q "$T_REF"; fi
git checkout -q -b "$T_BRANCH"
git config user.name "$T_GIT_NAME"
git config user.email "$T_GIT_EMAIL"
git rev-parse HEAD > "$STATE/base"
if [ -n "${CODEX_AUTH_JSON:-}" ]; then
    mkdir -p "$HOME/.codex"
    printf '%s' "$CODEX_AUTH_JSON" > "$HOME/.codex/auth.json"
    unset CODEX_AUTH_JSON
fi
python3 -c "$T_SETTLE" "$T_DIR" "$T_AGENT"
# `4top attach`, which the panel runs to open a row, arrived in 4top 0.2.0a8; an
# older template gets it here.
if ! 4top attach --help >/dev/null 2>&1; then
    python3 -m pip install --user -q --pre --break-system-packages '4top>=0.2.0a8' >/dev/null 2>&1 ||
        python3 -m pip install --user -q --pre '4top>=0.2.0a8' >/dev/null 2>&1 || true
fi
printf '%s' "$T_CONF" > "$STATE/agents.tmux.conf"
printf '%s' "$T_SESSION" > "$STATE/session"
tmux -L "$SOCK" -f "$STATE/agents.tmux.conf" new-session -d -s "$T_SESSION" -x 160 -y 48 \
    -c "$T_DIR" "$T_COMMAND"
nohup python3 -c "$T_NAMER" "$SOCK" "$T_SESSION" "$T_DIR" "$T_AGENT" >/dev/null 2>&1 &
"""

# What is in the sandbox that is not at the branch yet: "none", or counts.
UNSAVED = r"""
cd "$T_DIR"
base=$(cat "$STATE/base" 2>/dev/null || true)
changed=$(git status --porcelain | wc -l | tr -d ' ')
ahead=0
if [ -n "$base" ]; then ahead=$(git rev-list --count "$base..HEAD"); fi
if [ "$changed" = 0 ] && [ "$ahead" = 0 ]; then echo none; exit 0; fi
if [ "$changed" = 0 ]; then
    there=$(auth ls-remote origin "refs/heads/$T_BRANCH" 2>/dev/null | cut -f1)
    if [ "$there" = "$(git rev-parse HEAD)" ]; then echo none; exit 0; fi
fi
echo "$changed $ahead"
"""

DONE = r"""
cd "$T_DIR"
git add -A
git diff --cached --quiet || git commit -q --no-verify -m "$T_MESSAGE"
base=$(cat "$STATE/base" 2>/dev/null || true)
if [ "$(git rev-parse HEAD)" = "$base" ]; then
    echo unchanged
else
    auth push -q origin "HEAD:refs/heads/$T_BRANCH"
    echo "pushed $(git rev-parse HEAD)"
fi
"""

# Before the snapshot: the agent stops, and with it every copy of its credentials
# in memory; the one file that held any goes.
WIPE = r"""
tmux -L "$SOCK" kill-server 2>/dev/null || true
rm -f "$HOME/.codex/auth.json"
"""


# ----- what the person and the panel see ----------------------------------------------

def task_of(item: dict) -> dict[str, str]:
    metadata = item.get("metadata") or {}
    return {field: str(metadata.get(f"fourtop_{field}", "")) for field in FIELDS}


def describe(config: Config, item: dict, now: float | None = None) -> dict:
    """A live sandbox as data for a list row or section header: its task, state,
    cost so far (an estimate, see fourtop.e2b) and lifetime left."""
    now = time.time() if now is None else now
    task = task_of(item)
    sandbox = str(item.get("sandboxID", ""))
    try:
        dollars, seconds = e2b.cost(config, item, now)
    except FourtopError:
        dollars = seconds = None
    end = e2b.deadline(item)
    cpu, memory = float(item.get("cpuCount") or 0), float(item.get("memoryMB") or 0)
    return {
        **task, "name": task["name"] or sandbox, "sandbox": sandbox,
        "state": str(item.get("state", "")),
        "task": bool((item.get("metadata") or {}).get("fourtop_task")),
        "deadline": e2b.utc(end) if end else "",
        "lifetime_left": max(0, round(end - now)) if end else None,
        "cost_usd": None if dollars is None else round(dollars, 4),
        "running_seconds": None if seconds is None else int(seconds),
        "usd_per_hour": round(e2b.rate(cpu, memory) * 3600, 4),
        "estimate": True, "cpu": cpu, "memory_mb": memory,
    }


def kept(config: Config) -> list[dict]:
    """Finished tasks: the snapshots ``done`` kept, oldest first, each with the
    time it expires. A snapshot's name says its task and when it was taken."""
    found = []
    for item in e2b.snapshots(config):
        snapshot = str(item.get("snapshotID", ""))
        base = snapshot.split("/")[-1].split(":")[0]
        if not base.startswith(SNAPSHOT_PREFIX):
            continue
        name, _, stamp = base[len(SNAPSHOT_PREFIX):].rpartition("-")
        if not name or not stamp.isdigit():
            continue
        taken = int(stamp)
        found.append({"name": name, "sandbox": "", "state": "done", "task": True,
                      "branch": f"4top/{name}", "snapshot": snapshot, "done_at": e2b.utc(taken),
                      "expires": e2b.utc(taken + config.cloud.keep_snapshot_days * 86400),
                      "expired": taken + config.cloud.keep_snapshot_days * 86400 <= time.time()})
    return sorted(found, key=lambda entry: entry["done_at"])


def tasks(config: Config, now: float | None = None) -> tuple[list[dict], list[dict]]:
    """(live sandboxes described, E2B's listing of them) — plus finished tasks at
    the end of the first list. What `4top cloud ls --json` prints."""
    listing = e2b.listed(config)
    live = sorted((describe(config, item, now) for item in listing), key=lambda row: row["name"])
    return live + kept(config), listing


def hosts(config: Config) -> list[tuple[Host, dict]]:
    """Live sandboxes as hosts, one section each, with the data for its header.
    What a panel adds beside the configured hosts; a paused one is never woken to
    list it (see Manager.snapshot)."""
    return [(sandbox_host(row["sandbox"], row["name"]), row)
            for row in tasks(config)[0] if row["state"] != "done"]


def sources(config: Config) -> list[Host]:
    """Live tasks as hosts for the panel's sections, from one listing call and
    nothing else, so opening the panel waits on E2B once. Each section's Manager
    then reads the sandbox's state, cost and lifetime into ``Snapshot.cloud`` as it
    refreshes, and never wakes a paused one. No key, or E2B unreachable: none."""
    try:
        listing = e2b.listed(config, timeout=5)
    except FourtopError:
        return []
    return sorted((host_of(item) for item in listing), key=lambda host: host.name)


def budget(config: Config, listing: list[dict] | None = None, now: float | None = None) -> dict:
    listing = e2b.listed(config) if listing is None else listing
    spent = e2b.spent_since(config, e2b.start_of_day(now), listing, now)
    limit = config.cloud.daily_budget_usd
    return {"spent_today_usd": round(spent, 4), "daily_budget_usd": limit,
            "left_usd": round(max(0.0, limit - spent), 4), "estimate": True,
            "prices_read": e2b.PRICES_READ}


def find(config: Config, name: str) -> dict:
    """E2B's record of the live sandbox named ``name`` (or with that ID)."""
    for item in e2b.listed(config):
        task = task_of(item)
        if name in (task["name"], item.get("sandboxID")):
            return item
    raise Missing(f"No live cloud task named {name}; `4top cloud ls` lists them")


def host_of(item: dict) -> Host:
    return sandbox_host(str(item["sandboxID"]), task_of(item)["name"] or str(item["sandboxID"]))


# ----- the verbs ----------------------------------------------------------------------

@dataclass(frozen=True)
class Request:
    agent: str
    prompt: str
    repo: str
    ref: str = ""
    name: str = ""


def template_size(config: Config) -> tuple[float, float]:
    """(vCPUs, memory MB) of the configured template, for the worst-case cost
    before anything is created. Unknown: the template in contrib/e2b."""
    wanted = config.cloud.template.split(":", 1)[0].split("/")[-1]
    try:
        for item in e2b.call(config, "GET", "/templates"):
            names = list(item.get("aliases") or []) + list(item.get("names") or [])
            if wanted in names or wanted == item.get("templateID"):
                cpu, memory = float(item.get("cpuCount") or 0), float(item.get("memoryMB") or 0)
                if cpu and memory:
                    return cpu, memory
    except (FourtopError, TypeError, AttributeError):
        pass
    return 2.0, 2048.0


def _unique(config: Config, base: str, listing: list[dict]) -> str:
    taken = {task_of(item)["name"] for item in listing} | {entry["name"] for entry in kept(config)} \
        | set(config.hosts)
    if base not in taken:
        return base
    return next(f"{base}-{n}" for n in range(2, 1000) if f"{base}-{n}" not in taken)


def prune(config: Config) -> list[str]:
    """Delete finished tasks' snapshots past ``keep_snapshot_days``."""
    gone = []
    for entry in kept(config):
        if entry["expired"]:
            try:
                e2b.forget(config, entry["snapshot"])
                gone.append(entry["snapshot"])
            except FourtopError as exc:
                say(f"could not delete expired snapshot {entry['snapshot']}: {exc}")
    return gone


def new(config: Config, request: Request, now: float | None = None) -> dict:
    """Start a task; returns its description. Refuses before creating anything if
    credentials are missing or the budget would not cover the worst case, and
    kills the sandbox if any later step fails, so a failure leaves nothing running."""
    if request.agent not in AGENT_ARGS:
        raise Dependency("Cloud tasks run claude or codex")
    if not request.prompt.strip():
        raise FourtopError("A cloud task needs a prompt", 2)
    now = int(time.time()) if now is None else now
    token = github_token(config)
    if not token and github(request.repo):
        raise Dependency("No GitHub token here: set GH_TOKEN (or sign in with `gh auth login`); "
                         "the task's outcome is a pushed branch")
    secrets = agent_credentials(config, request.agent)
    key = ssh_key(config)
    listing = e2b.listed(config)
    cloud = config.cloud
    seconds = int(cloud.max_minutes * 60)
    size = template_size(config)
    worst = seconds * e2b.rate(*size)
    spent = budget(config, listing, now)["spent_today_usd"]
    if spent + worst > cloud.daily_budget_usd:
        raise Conflict(f"Budget: ${spent:.2f} spent today (estimate) and this task could cost up to "
                       f"${worst:.2f} ({cloud.max_minutes:g} min); [cloud] daily_budget_usd is "
                       f"${cloud.daily_budget_usd:.2f}")
    prune(config)
    found = github(request.repo)
    name = _unique(config, slug(request.name or (found[1] if found else Path(request.repo).stem)), listing)
    directory = f"{WORK}/{slug(found[1] if found else Path(request.repo).stem, 64)}"
    task = {"fourtop_task": "1", "fourtop_name": name, "fourtop_repo": clone_url(request.repo),
            "fourtop_ref": request.ref, "fourtop_branch": f"4top/{name}", "fourtop_agent": request.agent,
            "fourtop_prompt": " ".join(request.prompt.split())[:200], "fourtop_dir": directory,
            "fourtop_deadline": str(int(now + seconds))}
    created = e2b.create(config, cloud.template, seconds, task, name)
    sandbox = str(created["sandboxID"])
    try:
        e2b.upload(config, sandbox, str(created.get("envdAccessToken", "")),
                   f"{USER_HOME}/.ssh/authorized_keys", key)
        host = sandbox_host(sandbox, name)
        wait_for_ssh(config, host)
        git_name, git_email = identity(config)
        command = shlex.join([request.agent, *AGENT_ARGS[request.agent], request.prompt])
        run_in(config, host, START, values(
            GH_TOKEN=token, **secrets, T_REPO=task["fourtop_repo"], T_REF=request.ref,
            T_DIR=directory, T_BRANCH=task["fourtop_branch"], T_AGENT=request.agent,
            T_SESSION=f"task-{name}", T_COMMAND=command, T_CONF=CONF.format(terminal="xterm-256color"),
            T_SETTLE=SETTLE, T_NAMER=NAMER, T_GIT_NAME=git_name, T_GIT_EMAIL=git_email), timeout=900)
    except BaseException:
        say(f"starting {name} failed; removing its sandbox")
        try:
            e2b.kill(config, sandbox, name)
        except FourtopError as exc:
            say(f"could not remove sandbox {sandbox}: {exc}; `4top cloud rm {name} --discard`")
        raise
    return describe(config, {**created, "metadata": {**e2b.TAG, **task}, "state": "running",
                             "startedAt": e2b.utc(now), "cpuCount": size[0], "memoryMB": size[1]}, now)


def open_argv(config: Config, name: str) -> list[str]:
    """ssh into the task's agent, waking its sandbox within its lifetime."""
    item = find(config, name)
    host = host_of(item)
    e2b.wake(config, host)
    script = ('export PATH="$HOME/.local/bin:$PATH"; s=$(cat ~/.4top-task/session 2>/dev/null); '
              'exec tmux -L "${FOURTOP_AGENTS_SOCKET:-4top-agents}" attach -t "=$s" || '
              '{ echo "4top: the agent has exited; open its session from the list to resume it"; exit 3; }')
    return ssh_argv(config, replace(host, command="sh"), ["-c", script], tty=True)


def pause(config: Config, name: str) -> None:
    item = find(config, name)
    e2b.pause(config, str(item["sandboxID"]), name)


def unsaved(config: Config, item: dict) -> str:
    """What the task holds that its branch does not: "none", or "FILES COMMITS"."""
    task = task_of(item)
    if not task["dir"]:
        return "unknown"
    host = host_of(item)
    e2b.wake(config, host, grace=True)
    return run_in(config, host, UNSAVED, values(GH_TOKEN=github_token(config), T_DIR=task["dir"],
                                                T_BRANCH=task["branch"])).stdout.decode().strip()


def pull_request(config: Config, task: dict, token: str) -> str:
    """Open a pull request for the pushed branch; returns its URL."""
    found = github(task["repo"])
    if not found:
        raise Dependency("Pull requests are opened on GitHub repositories only")
    owner, repo = found

    def api(method, path, body=None):
        request = urllib.request.Request(
            f"https://api.github.com/repos/{owner}/{repo}{path}", method=method,
            data=None if body is None else json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"})
        return e2b._request(request, f"github {owner}/{repo}", 30)

    base = task["ref"]
    if not base or re.fullmatch(r"[0-9a-f]{7,40}", base):
        base = api("GET", "")["default_branch"]
    title = task["prompt"][:72] or task["branch"]
    body = (f"Work of the cloud task `{task['name']}` ({task['agent']}), started from `{task['ref'] or base}`."
            f"\n\nPrompt:\n\n> {task['prompt']}\n")
    return str(api("POST", "/pulls", {"title": title, "head": task["branch"], "base": base,
                                      "body": body})["html_url"])


def done(config: Config, name: str, *, pr: bool = False, snapshot: bool = True) -> dict:
    """Bring the task's work home and end it: commit and push the branch (a pull
    request if asked), stop the agent and remove its credentials, keep a snapshot,
    kill the sandbox. Any failure before the kill leaves the sandbox as it was."""
    item = find(config, name)
    task, host = task_of(item), host_of(item)
    if not task["dir"]:
        raise Missing(f"{name} is not a cloud task; `4top cloud rm` removes it")
    token = github_token(config)
    e2b.wake(config, host, grace=True)
    message = f"4top: {task['prompt'][:60] or name}\n\nCloud task {name} ({task['agent']})."
    out = run_in(config, host, DONE, values(GH_TOKEN=token, T_DIR=task["dir"], T_BRANCH=task["branch"],
                                            T_MESSAGE=message)).stdout.decode().split()
    pushed = out[1] if out[:1] == ["pushed"] else ""
    result = {"name": name, "branch": task["branch"] if pushed else "", "commit": pushed,
              "pull_request": "", "snapshot": ""}
    if pr and pushed:
        result["pull_request"] = pull_request(config, task, token)
    run_in(config, host, WIPE, b"")
    if snapshot and config.cloud.keep_snapshot_days > 0:
        result["snapshot"] = e2b.snapshot(config, host.e2b, f"{SNAPSHOT_PREFIX}{name}-{int(time.time())}")
    e2b.kill(config, host.e2b, name)
    prune(config)
    return result


def remove(config: Config, name: str, discard: bool = False) -> str:
    """Remove a task. A live one whose work is not on its branch is refused unless
    ``discard``; a finished one's snapshot is deleted. Returns what was removed."""
    for entry in kept(config):
        if entry["name"] == name:
            e2b.forget(config, entry["snapshot"])
            return f"snapshot {entry['snapshot']}"
    item = find(config, name)
    if not discard:
        left = unsaved(config, item)
        if left != "none":
            detail = ("its work is unknown to 4top" if left == "unknown" else
                      "{} changed files and {} commits are not on {}".format(
                          *left.split()[:2], task_of(item)["branch"]))
            raise Conflict(f"{name}: {detail}. `4top cloud done {name}` brings them home; "
                           f"--discard removes them")
    e2b.kill(config, str(item["sandboxID"]), name)
    return f"sandbox {item['sandboxID']}"
