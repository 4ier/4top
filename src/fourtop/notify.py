"""Push notifications to a phone, through ntfy, from the agents' own hooks.

There is no daemon to keep running. Each agent already calls out when a turn ends
(Claude Code hooks, Codex ``notify``, a Pi extension), so ``4top notify
--install`` points those at ``4top notify --from-hook AGENT``, which says what
happened in one HTTP POST to an ntfy topic the phone subscribes to. The topic is
the only secret on a public server, so a new one is random.

A hook runs inside the agent's turn, so it must never hold the agent up or fail
it: it hands the work to a detached child and returns at once, and the child
gives up after a few seconds and swallows every error. Wiring is merged into the
agents' configuration files and removed exactly: each file is backed up before the
first change, entries 4top did not write are left alone, and a notify program
Codex already had keeps being called.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shlex
import shutil
import socket
import subprocess
import sys
import time
import tomllib
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from session_ls.storage import atomic_json, private_dir, read_json

from .config import NOTIFY_EVENTS, Config
from .errors import FourtopError

SERVER = "https://ntfy.sh"
TIMEOUT = 5.0
STATE_SCHEMA = 1
REPEAT_SECONDS = 600   # the same news about a session is not worth a second buzz
BURST_SECONDS = 20     # nor is a second message about it this soon, unless it needs you
PAYLOAD_LIMIT = 2**20
TEXT_LIMIT = 300

# What each state looks like on the phone: ntfy priority (3 default, 4 high) and a
# tag that ntfy shows as an emoji.
STYLE = {"needs-you": (4, "raised_hand", "Needs you"),
         "done": (3, "white_check_mark", "Done"),
         "error": (4, "x", "Error")}

# Claude Code: the notifications that mean it is blocked on the person. An idle
# prompt a minute after a finished turn repeats the Stop, so it is not wired.
CLAUDE_WAITING = "permission_prompt|elicitation_dialog"
CLAUDE_EVENTS = {"Notification": CLAUDE_WAITING, "Stop": None, "StopFailure": None}
PI_EXTENSION = "4top-notify.ts"
MARK = "Written by `4top notify --install`"


def new_url(server: str = SERVER) -> str:
    """A fresh topic nobody can guess, on the public server by default."""
    alphabet = "abcdefghijklmnopqrstuvwxyz0123456789"
    return f"{server}/4top-" + "".join(secrets.choice(alphabet) for _ in range(20))


# --- sending -----------------------------------------------------------------------

def publish(url: str, title: str, message: str, priority: int = 3, tags=(),
            timeout: float = TIMEOUT, opener=urllib.request.urlopen) -> int:
    """One message to an ntfy topic. JSON to the server root, so a title in any
    script survives (HTTP headers would have to be Latin-1)."""
    base, _, topic = url.rstrip("/").rpartition("/")
    body = {"topic": topic, "title": title, "message": message or title,
            "priority": priority, "tags": list(tags)}
    request = urllib.request.Request(base + "/", data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json",
                                              "User-Agent": "4top"}, method="POST")
    with opener(request, timeout=timeout) as response:
        return response.status


def host_name() -> str:
    return socket.gethostname().split(".")[0] or "localhost"


def _line(text, limit: int = TEXT_LIMIT) -> str:
    """Whitespace collapsed and cut to a phone's width of attention."""
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


# --- reading what the agent said ---------------------------------------------------

@dataclass
class Event:
    agent: str
    state: str            # needs-you | done | error
    native_id: str = ""
    transcript: str = ""
    cwd: str = ""
    detail: str = ""      # what it asks, its last words, or the error
    request: str = ""     # the latest request, when the agent says it
    kind: str = ""        # with needs-you: "permission" or "question", as Session.attention


def parse(agent: str, payload: dict) -> Event | None:
    """The event an agent's hook describes, or None if it is not news for a person."""
    if not isinstance(payload, dict):
        return None
    if agent == "claude":
        if payload.get("agent_id"):
            return None  # a subagent's turn; its parent reports the outcome
        name = payload.get("hook_event_name")
        base = dict(native_id=str(payload.get("session_id") or ""),
                    transcript=str(payload.get("transcript_path") or ""),
                    cwd=str(payload.get("cwd") or ""))
        if name == "Notification":
            kind = {"permission_prompt": "permission",
                    "elicitation_dialog": "question"}.get(payload.get("notification_type"))
            if kind is None:
                return None
            return Event(agent, "needs-you", detail=str(payload.get("message") or ""), kind=kind,
                         **base)
        if name == "Stop":
            return Event(agent, "done", detail=str(payload.get("last_assistant_message") or ""), **base)
        if name == "StopFailure":
            return Event(agent, "error", detail=str(payload.get("error_message")
                                                    or payload.get("error_type") or ""), **base)
        return None
    if agent == "codex":
        kind = payload.get("type")
        state = {"agent-turn-complete": "done"}.get(kind) or (
            "needs-you" if isinstance(kind, str) and "approval" in kind else None)
        if state is None:
            return None
        inputs = payload.get("input-messages")
        request = inputs[-1] if isinstance(inputs, list) and inputs else ""
        return Event(agent, state, native_id=str(payload.get("thread-id") or ""),
                     cwd=str(payload.get("cwd") or ""),
                     detail=str(payload.get("last-assistant-message") or ""),
                     request=str(request or ""), kind="permission" if state == "needs-you" else "")
    if agent == "pi":
        state = {"prompt": "needs-you", "done": "done", "error": "error"}.get(payload.get("event"))
        if state is None:
            return None
        detail = payload.get("title") if state == "needs-you" else (
            payload.get("error") if state == "error" else payload.get("text"))
        return Event(agent, state, native_id=str(payload.get("session_id") or ""),
                     transcript=str(payload.get("session_file") or ""),
                     cwd=str(payload.get("cwd") or ""), detail=str(detail or ""),
                     kind="question" if state == "needs-you" else "")
    return None


def _record(manager, event: Event):
    """The history record the event is about: by native id, else by transcript path."""
    try:
        records = manager.history(force=True).records
    except Exception:  # noqa: BLE001 - a hook reports what it can
        return None
    for record in records:
        if record.agent != event.agent:
            continue
        if event.native_id and record.native_id == event.native_id:
            return record
        if event.transcript and str(record.file) == event.transcript:
            return record
    return None


def repo_name(cwd: str) -> str:
    """The repository a directory belongs to, so a worktree is named after its repo,
    as the panel names it; the directory's own name outside a repository."""
    try:
        common = subprocess.run(["git", "-C", cwd, "rev-parse", "--path-format=absolute",
                                 "--git-common-dir"], capture_output=True, text=True,
                                timeout=2, stdin=subprocess.DEVNULL)
        if common.returncode == 0 and common.stdout.strip():
            path = Path(common.stdout.strip())
            return (path.parent if path.name == ".git" else path).name or Path(cwd).name
    except (OSError, subprocess.SubprocessError):
        pass
    return Path(cwd).name or cwd


def compose(event: Event, record=None, host: str | None = None) -> dict:
    """Title, message, priority and tags for one event."""
    cwd = (record.cwd if record is not None and record.cwd else "") or event.cwd
    project = repo_name(cwd) if cwd else event.agent
    priority, tag, label = STYLE[event.state]
    if event.kind:
        label += f" ({event.kind})"
    request = event.request or (str(getattr(record, "last_request", "") or "")
                                or (record.title if record is not None else ""))
    lines = [f"{label}: {_line(event.detail)}" if event.detail.strip() else label]
    if request.strip():
        lines.append("› " + _line(request, 200))
    return {"title": f"{host or host_name()} · {project}", "message": "\n".join(lines),
            "priority": priority, "tags": [tag, event.agent],
            "key": record.key if record is not None else f"{event.agent}:{event.native_id}"}


def due(state_file: Path, key: str, state: str, digest: str, now: float) -> bool:
    """Whether this message should go out; records it if so.

    The same news within ten minutes is dropped, and so is anything within twenty
    seconds of the last message about the session, unless it newly needs the person.
    """
    try:
        seen = read_json(state_file, {})
    except (OSError, ValueError):
        seen = {}
    sessions = seen.get("sessions") if isinstance(seen, dict) else None
    sessions = {k: v for k, v in (sessions or {}).items()
                if isinstance(v, dict) and now - float(v.get("at", 0)) < 86400}
    last = sessions.get(key)
    if last:
        age = now - float(last.get("at", 0))
        if last.get("digest") == digest and age < REPEAT_SECONDS:
            return False
        if age < BURST_SECONDS and not (state == "needs-you" and last.get("state") != state):
            return False
    sessions[key] = {"state": state, "digest": digest, "at": now}
    atomic_json(state_file, {"schema_version": STATE_SCHEMA, "sessions": sessions})
    return True


def deliver(config: Config, agent: str, payload: dict, manager=None, opener=urllib.request.urlopen,
            now=time.time) -> bool:
    """Parse, resolve, dedupe and send. True if a message went out."""
    if not config.notify_url:
        return False
    event = parse(agent, payload)
    if event is None or event.state not in config.notify_events:
        return False
    own = manager is None
    if own:
        from .services import Manager
        manager = Manager(config)
    try:
        record = _record(manager, event)
        if record is not None and (getattr(record, "subagent", False)
                                   or getattr(record, "scripted", False)):
            return False  # started by an agent or a script: nobody is waiting on it
        message = compose(event, record)
        digest = hashlib.sha256(message["message"].encode()).hexdigest()[:16]
        with manager.store.lock("notify", timeout=2):
            if not due(Path(config.state_dir) / "notify.json", message["key"], event.state,
                       digest, now()):
                return False
        publish(config.notify_url, message["title"], message["message"], message["priority"],
                message["tags"], opener=opener)
        return True
    finally:
        if own:
            manager.close()


def _read_stdin() -> str:
    if sys.stdin is None or sys.stdin.isatty():
        return ""
    try:
        return sys.stdin.read(PAYLOAD_LIMIT)
    except (OSError, ValueError):
        return ""


def _chain(argv: list[str]) -> None:
    """Start the notify program Codex had before, detached, exactly as Codex would."""
    try:
        subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True)
    except (OSError, ValueError):
        pass


def from_hook(config_path: str | None, agent: str, extra: tuple[str, ...], detach: bool = True) -> int:
    """What an agent's hook runs. Always 0, fast, and silent on stdout: Claude Code
    adds a Stop hook's output to the conversation."""
    try:
        if agent == "codex":
            # Codex appends its JSON as the last argument; before it, after `--`,
            # is the notify program it had before 4top was installed.
            text, previous = (extra[-1] if extra else ""), list(extra[:-1])
            if previous:
                _chain(previous + [text])
        else:
            text = _read_stdin()
        payload = json.loads(text) if text.strip() else None
        if detach and os.fork() != 0:
            return 0  # the agent goes on; the child reports
        try:
            if detach:
                os.setsid()
                null = os.open(os.devnull, os.O_RDWR)
                for fd in (0, 1, 2):
                    os.dup2(null, fd)
            deliver(Config.load(config_path), agent, payload)
        except BaseException:  # noqa: BLE001 - nothing may reach the agent
            pass
        finally:
            if detach:
                os._exit(0)
    except BaseException:  # noqa: BLE001
        pass
    return 0


# --- wiring the agents -------------------------------------------------------------

def launcher(env: dict[str, str] | None = None) -> list[str]:
    """This host's 4top as an absolute command that works without a login shell.

    The script that is running now if it is called 4top (a uv or pip entry point, or
    a wrapper), else 4top on PATH, else this interpreter with the module.
    """
    env = os.environ if env is None else env
    argv0 = Path(sys.argv[0]) if sys.argv and sys.argv[0] else None
    if argv0 is not None and argv0.name == "4top":
        found = shutil.which(str(argv0)) if not argv0.is_absolute() else str(argv0)
        if found and os.access(found, os.X_OK):
            return [os.path.abspath(found)]
    found = shutil.which("4top", path=env.get("PATH"))
    if found:
        return [os.path.abspath(found)]
    return [sys.executable, "-m", "fourtop"]


def hook_argv(base: list[str], config_path: str | None, agent: str) -> list[str]:
    return [*base, *(["--config", config_path] if config_path else []),
            "notify", "--from-hook", agent]


def _write(path: Path, text: str, backup: bool = True) -> None:
    """Replace a file atomically, keeping its mode; the first time, keep a copy."""
    path = path.resolve() if path.is_symlink() else path
    mode = 0o600
    if path.exists():
        mode = path.stat().st_mode & 0o777
        if backup:
            copy = path.with_name(path.name + ".4top-backup")
            if not copy.exists():
                shutil.copy2(path, copy)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.4top-{os.getpid()}")
    try:
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.chmod(temp, mode)
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


# Claude Code: hooks in settings.json -------------------------------------------------

def _ours_claude(hook) -> bool:
    return (isinstance(hook, dict) and isinstance(hook.get("command"), str)
            and "notify --from-hook claude" in hook["command"])


def _without_claude(settings: dict) -> dict:
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return dict(settings)
    kept = {}
    for event, groups in hooks.items():
        if not isinstance(groups, list):
            kept[event] = groups
            continue
        remaining = []
        for group in groups:
            if isinstance(group, dict) and isinstance(group.get("hooks"), list):
                inner = [hook for hook in group["hooks"] if not _ours_claude(hook)]
                if not inner and group["hooks"]:
                    continue
                group = {**group, "hooks": inner}
            remaining.append(group)
        if remaining or not groups:
            kept[event] = remaining
    result = dict(settings)
    if kept or not hooks:
        result["hooks"] = kept
    else:
        result.pop("hooks")
    return result


def claude(settings_file: Path, argv: list[str] | None) -> str:
    """Install (argv) or remove (None) the Claude Code hooks. Returns what happened."""
    try:
        text = settings_file.read_text(encoding="utf-8") if settings_file.exists() else ""
        settings = json.loads(text) if text.strip() else {}
    except (OSError, ValueError):
        raise FourtopError(f"Cannot read {settings_file} as JSON; left unchanged", 6) from None
    if not isinstance(settings, dict) or not isinstance(settings.get("hooks", {}), dict):
        raise FourtopError(f"Unexpected shape in {settings_file}; left unchanged", 6)
    result = _without_claude(settings)
    if argv is not None:
        command = shlex.join(argv)
        hooks = dict(result.get("hooks", {}))
        for event, matcher in CLAUDE_EVENTS.items():
            group = {"hooks": [{"type": "command", "command": command, "async": True, "timeout": 10}]}
            if matcher:
                group = {"matcher": matcher, **group}
            hooks[event] = [*hooks.get(event, []), group]
        result["hooks"] = hooks
    if result == settings:
        return f"claude: {settings_file} already {'wired' if argv else 'clean'}"
    _write(settings_file, json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    return f"claude: {'hooks Notification, Stop, StopFailure in' if argv else 'removed from'} {settings_file}"


# Codex: the top-level `notify` program in config.toml ------------------------------

def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)  # a JSON string is a TOML basic string


def _ours_codex(value) -> bool:
    return (isinstance(value, list) and "--from-hook" in value
            and value[value.index("--from-hook") + 1:value.index("--from-hook") + 2] == ["codex"])


def _notify_span(text: str) -> tuple[int, int] | None:
    """Offsets of the top-level ``notify = ...`` assignment, which may span lines."""
    lines = text.splitlines(keepends=True)
    offset = 0
    for index, line in enumerate(lines):
        stripped = line.lstrip()
        if stripped.startswith("["):
            return None  # the first table: top-level keys end here
        if re.match(r"notify\s*=", stripped):
            for end in range(index + 1, len(lines) + 1):
                try:
                    tomllib.loads("".join(lines[index:end]))
                except tomllib.TOMLDecodeError:
                    continue
                return offset, offset + len("".join(lines[index:end]))
            return None
        offset += len(line)
    return None


def codex(config_file: Path, argv: list[str] | None) -> str:
    """Install (argv) or remove (None) 4top as Codex's notify program, keeping the one
    it had: that one is passed along after ``--`` and called first."""
    try:
        text = config_file.read_text(encoding="utf-8") if config_file.exists() else ""
        data = tomllib.loads(text)
    except (OSError, tomllib.TOMLDecodeError):
        raise FourtopError(f"Cannot read {config_file} as TOML; left unchanged", 6) from None
    current = data.get("notify")
    previous = current
    if _ours_codex(current):
        previous = (current[current.index("--") + 1:] or None) if "--" in current else None
    # Codex adds its JSON as the last argument, so `--` always ends 4top's own
    # arguments; the program it had before, if any, goes between.
    wanted = [*argv, "--", *(previous or [])] if argv is not None else previous
    if wanted == current:
        return f"codex: {config_file} already {'wired' if argv else 'clean'}"
    if wanted is not None and not (isinstance(wanted, list) and all(isinstance(v, str) for v in wanted)):
        raise FourtopError(f"Unexpected notify value in {config_file}; left unchanged", 6)
    line = ("notify = [" + ", ".join(_toml_string(v) for v in wanted) + "]\n") if wanted else ""
    span = _notify_span(text)
    if current is not None and span is None:
        raise FourtopError(f"Cannot locate notify in {config_file}; left unchanged", 6)
    updated = text[:span[0]] + line + text[span[1]:] if span else line + text
    check = tomllib.loads(updated)  # the edit must change notify and nothing else
    rest = {k: v for k, v in data.items() if k != "notify"}
    if check.get("notify") != wanted or {k: v for k, v in check.items() if k != "notify"} != rest:
        raise FourtopError(f"Editing {config_file} would change more than notify; left unchanged", 6)
    _write(config_file, updated)
    if argv is None:
        return f"codex: notify {'restored' if wanted else 'removed'} in {config_file}"
    return f"codex: notify in {config_file}" + (" (your previous notify still runs)" if previous else "")


# Pi: an extension file --------------------------------------------------------------

PI_SOURCE = """\
// {mark}; `4top notify --uninstall` removes it.
// It tells 4top when this pi needs you, finishes or fails, so 4top can push a
// notification to your phone. It starts a process and never waits for it.
import {{ spawn }} from "node:child_process";

const COMMAND: string[] = {command};

function textOf(message: any): string {{
  const content = message?.content;
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  return content.filter((part: any) => part?.type === "text" && typeof part.text === "string")
    .map((part: any) => part.text).join("");
}}

export default function (pi: any) {{
  let settledSeen = false; // a pi with agent_settled: agent_end is not the end there
  let lastText = "";
  let failed = "";
  const send = (event: string, ctx: any, extra: Record<string, unknown> = {{}}) => {{
    try {{
      const manager = ctx?.sessionManager;
      const payload = JSON.stringify({{
        event, cwd: ctx?.cwd ?? process.cwd(), text: lastText, error: failed,
        session_id: manager?.getSessionId?.() ?? "", session_file: manager?.getSessionFile?.() ?? "",
        ...extra,
      }});
      const child = spawn(COMMAND[0], COMMAND.slice(1), {{ detached: true, stdio: ["pipe", "ignore", "ignore"] }});
      child.on("error", () => {{}});
      child.stdin?.on("error", () => {{}});
      child.stdin?.end(payload);
      child.unref();
    }} catch {{}}
  }};
  pi.on("agent_start", () => {{ lastText = ""; failed = ""; }});
  pi.on("message_end", (event: any) => {{
    const message = event?.message;
    if (message?.role !== "assistant") return;
    const text = textOf(message);
    if (text) lastText = text;
    if (message.stopReason === "error") failed = String(message.errorMessage ?? "error");
  }});
  pi.on("ui_prompt_start", (event: any, ctx: any) => send("prompt", ctx, {{ title: event?.title ?? "" }}));
  pi.on("agent_settled", (_event: any, ctx: any) => {{
    settledSeen = true;
    send(failed ? "error" : "done", ctx);
  }});
  pi.on("agent_end", (event: any, ctx: any) => {{
    if (settledSeen || event?.willContinue === true) return;
    const timer = setTimeout(() => {{
      if (!settledSeen && ctx?.isIdle?.() !== false) send(failed ? "error" : "done", ctx);
    }}, 1500);
    timer.unref?.();
  }});
}}
"""


def pi(extensions: Path, argv: list[str] | None) -> str:
    target = extensions / PI_EXTENSION
    existing = target.read_text(encoding="utf-8") if target.exists() else None
    if existing is not None and MARK not in existing:
        raise FourtopError(f"{target} was not written by 4top; left unchanged", 4)
    if argv is None:
        if existing is None:
            return f"pi: {target} already clean"
        target.unlink()
        return f"pi: removed {target}"
    source = PI_SOURCE.format(mark=MARK, command=json.dumps(argv, ensure_ascii=False))
    if existing == source:
        return f"pi: {target} already wired"
    _write(target, source, backup=False)
    return f"pi: extension {target} (loaded by new pi sessions, or /reload)"


# --- the commands --------------------------------------------------------------------

def targets(config: Config) -> dict[str, Path]:
    """Each agent's file, for the agents whose store exists on this host."""
    found = {}
    for agent, relative in (("claude", "settings.json"), ("codex", "config.toml"),
                            ("pi", "extensions")):
        root = Path(config.root(agent).path)
        if root.is_dir():
            found[agent] = root / relative
    return found


def record_url(config: Config, url: str) -> None:
    """Add ``[notify] url`` to the 4top configuration file, creating it if needed."""
    path = Path(config.config_path)
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    if re.search(r"^\s*\[notify\]", text, re.MULTILINE):
        raise FourtopError(f"{path} already has a [notify] section; set its url there", 2)
    if not path.exists():
        private_dir(path.parent)
    addition = ("" if not text or text.endswith("\n") else "\n") + ("\n" if text else "") + (
        "# Push notifications (`4top notify`). The topic is the secret: anyone who knows\n"
        "# it can read these messages and send to it.\n"
        f"[notify]\nurl = {_toml_string(url)}\n")
    _write(path, text + addition, backup=False)


def install(config: Config, config_path: str | None, url: str | None = None,
            base: list[str] | None = None) -> list[str]:
    report = []
    if not config.notify_url:
        config.notify_url = url or new_url()
        record_url(config, config.notify_url)
        report.append(f"topic: {config.notify_url} (recorded in {config.config_path})")
    elif url and url != config.notify_url:
        raise FourtopError(f"[notify] url is already {config.notify_url}; edit "
                           f"{config.config_path} to change it", 4)
    else:
        report.append(f"topic: {config.notify_url}")
    base = base or launcher(config.environment)
    wiring = {"claude": claude, "codex": codex, "pi": pi}
    for agent, path in targets(config).items():
        report.append(wiring[agent](path, hook_argv(base, config_path, agent)))
    return report


def uninstall(config: Config) -> list[str]:
    wiring = {"claude": claude, "codex": codex, "pi": pi}
    return [wiring[agent](path, None) for agent, path in targets(config).items()]


def subscribe_help(url: str) -> str:
    server, _, topic = url.rpartition("/")
    lines = ["On the phone: install ntfy (F-Droid or Google Play), tap +, and subscribe to",
             f"topic {topic}" + ("" if server == SERVER else f" on server {server}") + ".",
             "Anyone who knows the topic can read these messages; keep it private."]
    return "\n".join(lines)


def test(config: Config, opener=urllib.request.urlopen) -> str:
    if not config.notify_url:
        raise FourtopError("No [notify] url configured; run `4top notify --install` first", 3)
    try:
        publish(config.notify_url, f"{host_name()} · 4top",
                "Test: notifications from this host reach this device.", 3,
                ["white_check_mark"], opener=opener)
    except (OSError, ValueError) as exc:
        raise FourtopError(f"ntfy did not accept the test message ({type(exc).__name__}: {exc})", 6) \
            from None
    return f"sent a test message to {config.notify_url}"


__all__ = ["NOTIFY_EVENTS", "deliver", "from_hook", "install", "uninstall", "test", "publish"]
