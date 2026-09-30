"""Agents that stay on their host when the link to it drops.

A remote agent used to be the child of the ssh session that started it. A dropped
link (a phone switching networks, Termux killed, a locked screen) hung up that
session and took the agent with it, halfway through its work. Instead the agent
runs in a tmux server of its own on the host (socket ``4top-agents``, separate from
the user's tmux and from the panel's), and the ssh session only attaches to it.
When the link drops only the attached client goes; opening the session again, from
this device or another, attaches to the same process.

A tmux session is named after the history key and ends when its agent exits, so
which sessions have an agent here is asked of tmux and never recorded, as in the
panel's layout.
"""
from __future__ import annotations

import re
import subprocess
import time
import uuid
from dataclasses import replace
from pathlib import Path

from .config import Config
from .errors import Conflict, Missing
from .models import LaunchPlan
from .workspace import MARKER, _has_terminfo, terminal, tmux_binary

SOCKET = "4top-agents"  # FOURTOP_AGENTS_SOCKET overrides it, so tests never meet real agents

CONF = """\
# Written by 4top for the tmux server that keeps agents running on this host; your
# own tmux configuration is untouched. It is usually shown inside another tmux (the
# 4top panel), so it stays out of the way: no status line and no prefix key, so
# every key reaches the agent.
set -g status off
set -g prefix None
set -g prefix2 None
set -g mouse on
set -g escape-time 10
set -g focus-events on
set -g history-limit 50000
set -g default-terminal "{terminal}"
set -as terminal-features ",*:RGB"
# Modified keys such as Shift-Enter reach the agent (pi warns without them). -q: an
# older tmux without these options still reads the rest.
set -gq extended-keys on
set -gq extended-keys-format csi-u
# Two devices attached at different sizes: the one used last decides.
set -g window-size latest
# A session is its agent: it ends when the agent exits, and the server with the last.
set -g remain-on-exit off
set -g exit-empty on
"""

# Set by the terminal or by an enclosing tmux, not by the agent's configuration.
CLIENT_ONLY = {"TERM", "TMUX", "TMUX_PANE", MARKER}


def socket(env: dict[str, str]) -> str:
    return env.get("FOURTOP_AGENTS_SOCKET") or SOCKET


def session_name(key: str) -> str:
    # tmux reserves "." and ":" in targets. History keys (h_<hex>) contain neither.
    return re.sub(r"[^A-Za-z0-9_-]", "_", key)


def _tmux(tmux: str, env: dict[str, str], *args: str) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run([tmux, "-L", socket(env), *args], capture_output=True, text=True,
                              env=env, timeout=10, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return None


def running(env: dict[str, str]) -> set[str]:
    """Names of the sessions whose agent runs on this host now. No server, none."""
    tmux = tmux_binary(env)
    result = _tmux(tmux, env, "list-sessions", "-F", "#{session_name}") if tmux else None
    return set(result.stdout.split()) if result and result.returncode == 0 else set()


def _prepare(config: Config, tmux: str) -> tuple[list[str], dict[str, str], dict[str, str] | None]:
    """The tmux command prefix, the client's environment, and the running server's
    global environment (None when there is no server yet)."""
    env = {k: v for k, v in config.environment.items() if k not in ("TMUX", "TMUX_PANE", MARKER)}
    # The client draws on the terminal that ssh -t described. A host without that
    # terminal's entry (tmux-256color from the panel, say) refuses to attach at all.
    if env.get("TERM") and not _has_terminfo(env["TERM"]):
        env["TERM"] = terminal()
    conf = Path(config.state_dir) / "agents.tmux.conf"
    conf.write_text(CONF.format(terminal=terminal()), encoding="utf-8")
    shown = _tmux(tmux, env, "show-environment", "-g")
    server = None
    if shown and shown.returncode == 0:
        # A server started by an earlier attach keeps the configuration it read then.
        _tmux(tmux, env, "source-file", str(conf))
        server = dict(line.split("=", 1) for line in shown.stdout.splitlines() if "=" in line)
    return [tmux, "-L", socket(env), "-f", str(conf)], env, server


def attach(config: Config, name: str, agent: str) -> LaunchPlan | None:
    """A plan that shows the running session ``name``; None without tmux."""
    tmux = tmux_binary(config.environment)
    if tmux is None:
        return None
    base, env, _ = _prepare(config, tmux)
    return LaunchPlan(agent, tmux, (*base, "attach-session", "-t", f"={name}"),
                      str(Path.home()), env)


def keep(config: Config, plan: LaunchPlan, name: str | None = None) -> LaunchPlan:
    """The same agent, started in this host's agent server and shown from there.

    ``new-session -A`` attaches instead if the session appeared in the meantime, so
    two devices opening it at once still share one agent. Without tmux the plan is
    returned unchanged: the agent runs in this terminal, as it always did.
    """
    tmux = tmux_binary(config.environment)
    if tmux is None:
        return plan
    name = name or session_name(plan.history_key or f"new-{plan.agent}-{uuid.uuid4().hex[:8]}")
    base, env, server = _prepare(config, tmux)
    # A new server takes this client's environment. A running one was started by an
    # earlier connection, so whatever the plan needs differently is passed along.
    changed = [] if server is None else [
        f"{k}={v}" for k, v in plan.environment.items()
        if k not in CLIENT_ONLY and server.get(k) != v]
    command = ("env", *changed, *plan.argv) if changed else plan.argv  # env(1): no tmux version needed
    argv = (*base, "new-session", "-A", "-s", name, "-c", plan.cwd, "--", *command)
    return replace(plan, executable=tmux, argv=argv, environment=env)



# ----- reading and answering an agent without attaching to it ----------------------
#
# A phone mostly wants to know what an agent is doing and to say one sentence to it.
# Attaching takes the whole terminal for that, so these read the agent's screen and
# type into it through tmux instead. They act only on a session that tmux says runs
# here now, and never start anything.

SEPARATOR = "\x1e4top:"  # between screens in one batched capture; never on a screen


def find(env: dict[str, str], query: str) -> str:
    """The running session a key names: exact, or a unique prefix of four or more."""
    names = running(env)
    name = session_name(query)
    if name in names:
        return name
    matches = [n for n in names if len(query) >= 4 and n.startswith(name)]
    if len(matches) > 1:
        raise Conflict("Identifier is ambiguous; use a complete key")
    if not matches:
        raise Missing("No agent for this session runs on this host now")
    return matches[0]


def capture(env: dict[str, str], name: str, history: int = 0) -> str | None:
    """The session's screen as plain text (tmux strips colours unless asked), plus up
    to ``history`` lines above it; None when the session is not there."""
    tmux = tmux_binary(env)
    args = ["capture-pane", "-p", "-t", f"={name}:"] + (["-S", f"-{history}"] if history else [])
    result = _tmux(tmux, env, *args) if tmux else None
    return result.stdout if result and result.returncode == 0 else None


def screens(env: dict[str, str], names) -> dict[str, str]:
    """The screens of several sessions in one tmux call. A session that exited in the
    meantime ends the batch early; the ones before it are still returned."""
    names = sorted(names)
    tmux = tmux_binary(env)
    if not names or tmux is None:
        return {}
    args: list[str] = []
    for name in names:
        args += [";"] if args else []
        args += ["display-message", "-p", SEPARATOR + name, ";", "capture-pane", "-p", "-t", f"={name}:"]
    result = _tmux(tmux, env, *args)
    if result is None:
        return {}
    found = {}
    for chunk in result.stdout.split(SEPARATOR)[1:]:
        name, _, screen = chunk.partition("\n")
        found[name] = screen
    return found


def paste(env: dict[str, str], name: str, text: str, enter: bool = True) -> bool:
    """Type ``text`` into the session as one bracketed paste, then Enter.

    A paste arrives as one piece, so a newline inside it does not submit half a
    message and the agent sees pasted text rather than keystrokes. Claude Code reads
    an Enter that arrives with the paste as part of it, hence the pause.
    """
    tmux = tmux_binary(env)
    if tmux is None:
        return False
    buffer = f"4top-{uuid.uuid4().hex[:8]}"
    try:
        loaded = subprocess.run([tmux, "-L", socket(env), "load-buffer", "-b", buffer, "-"],
                                input=text, capture_output=True, text=True, env=env, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return False
    if loaded.returncode != 0:
        return False
    pasted = _tmux(tmux, env, "paste-buffer", "-p", "-d", "-b", buffer, "-t", f"={name}:")
    if not pasted or pasted.returncode != 0:
        _tmux(tmux, env, "delete-buffer", "-b", buffer)
        return False
    if enter:
        time.sleep(PASTE_SETTLE)
        return press(env, name, ("Enter",))
    return True


PASTE_SETTLE = 0.3


def press(env: dict[str, str], name: str, keys) -> bool:
    """Press tmux key names (``Enter``, ``Escape``, ``y``) in the session."""
    tmux = tmux_binary(env)
    result = _tmux(tmux, env, "send-keys", "-t", f"={name}:", *keys) if tmux else None
    return bool(result and result.returncode == 0)
