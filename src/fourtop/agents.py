"""Native CLI plans. Capability probes do not authenticate or call a model."""
from __future__ import annotations

import os
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path

from session_ls.api import HistoryRecord, history_key, is_uuid, open_source

from .config import Config
from .errors import Dependency, FourtopError, Missing
from .models import LaunchPlan

RESERVED = {
    "claude": {"--resume", "-r", "--continue", "-c", "--session-id", "--fork-session",
               "--no-session-persistence", "--print", "-p", "--input-format", "--output-format"},
    "codex": {"--cd", "-C", "--ephemeral", "--resume", "--session-id"},
    "pi": {"--resume", "-r", "--continue", "-c", "--session", "--session-id", "--session-dir",
           "--no-session", "--print", "-p", "--mode"},
}

# Capability probes are local `--help` / `--version` calls; they never authenticate.
PROBE_SECONDS = 5.0


@dataclass(frozen=True)
class Capability:
    executable: str
    help_ok: bool
    allocate_id: bool
    resume: bool
    version: str


def validate_extra(agent: str, extra: tuple[str, ...]) -> None:
    for item in extra:
        if "\x00" in item:
            raise FourtopError("NUL is not allowed in command arguments", 2)
        flag = item.split("=", 1)[0]
        if flag in RESERVED[agent]:
            raise FourtopError(f"{flag} changes runtime identity or mode; use a dedicated 4top command", 2)
        # Native parsers accept compact short options such as -rUUID and -C/path.
        if len(item) > 2 and not item.startswith("--"):
            if item[:2] in RESERVED[agent]:
                raise FourtopError(f"{item[:2]} conflicts with 4top's launch plan", 2)
    # CLI subcommands are allowed after global flags by native parsers. Reject them
    # anywhere, rather than letting --model X resume silently escape new semantics.
    if agent == "codex" and any(item in {"resume", "fork", "exec", "e", "app-server", "mcp-server",
                                      "login", "logout", "mcp", "app", "sandbox", "debug",
                                      "apply", "cloud", "features", "completion", "help"}
                                for item in extra):
        raise FourtopError("Use the native interactive command; subcommands are not accepted by new", 2)


class Drivers:
    def __init__(self, config: Config, host_id: str):
        self.config = config
        self.host_id = host_id
        self._probes = {}

    def executable(self, agent: str) -> str:
        if agent not in RESERVED:
            raise Dependency(f"{agent} is read-only or unsupported for runtime operations")
        configured = self.config.executables[agent]
        found = shutil.which(configured, path=self.config.environment.get("PATH", os.defpath))
        if not found:
            raise Dependency(f"{agent} executable not found; install it or configure a real wrapper path")
        return os.path.abspath(found)

    def probe(self, agent: str) -> Capability:
        executable = self.executable(agent)
        st = os.stat(executable)
        key = (executable, st.st_mtime_ns, st.st_size)
        if key in self._probes:
            return self._probes[key]
        try:
            result = subprocess.run([executable, "--help"], capture_output=True, text=True,
                                    errors="replace", timeout=PROBE_SECONDS,
                                    env=self.config.environment, cwd="/")
            help_text = result.stdout + result.stderr
            ok = result.returncode == 0
            version_result = subprocess.run([executable, "--version"], capture_output=True, text=True,
                                            errors="replace", timeout=PROBE_SECONDS,
                                            env=self.config.environment, cwd="/")
            version = version_result.stdout.strip()[:160] if version_result.returncode == 0 else "unknown"
        except (OSError, subprocess.TimeoutExpired):
            ok, help_text, version = False, "", "unavailable"
        resume_token = "resume" if agent == "codex" else "--resume" if agent == "claude" else "--session"
        value = Capability(executable, ok, ok and agent != "codex" and "--session-id" in help_text,
                           ok and resume_token in help_text, version)
        self._probes[key] = value
        return value

    @staticmethod
    def cwd(value: str) -> str:
        if not value or "\x00" in value:
            raise Missing("A working directory is required")
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
        if not path.is_dir() or not os.access(path, os.R_OK | os.X_OK):
            raise Missing("Working directory does not exist or is not accessible")
        return str(path.resolve())

    def plan_new(self, agent: str, cwd: str, extra: tuple[str, ...] = ()) -> LaunchPlan:
        executable = self.executable(agent)
        validate_extra(agent, extra)
        cwd = self.cwd(cwd)
        capability = self.probe(agent)
        native_id = str(uuid.uuid4()) if capability.allocate_id else None
        root = self.config.root(agent).path
        args = [executable]
        if native_id:
            args.extend(["--session-id", native_id])
        args.extend(extra)
        env = dict(self.config.environment)
        key = history_key(self.host_id, agent, root, native_id) if native_id else None
        return LaunchPlan(agent, executable, tuple(args), cwd, env, root, native_id, key)

    def plan_resume(self, record: HistoryRecord, cwd: str | None = None) -> LaunchPlan:
        if record.agent not in RESERVED:
            raise Dependency("This history adapter is read-only; native resume is not supported")
        if record.root != self.config.root(record.agent).path:
            raise Dependency("History root differs from the selected agent profile")
        if not cwd and record.cwd_quality != "native":
            raise Missing("Historical directory is inferred or missing; provide --cwd explicitly")
        cwd = self.cwd(cwd or record.cwd)
        # Reopen the exact approved source immediately before planning an executable action.
        try:
            with open_source(record.file, record.root):
                pass
        except (OSError, ValueError):
            raise Missing("The selected history file is missing or not trusted") from None
        capability = self.probe(record.agent)
        if not capability.resume:
            raise Dependency("Installed CLI did not advertise a supported resume interface (--help)")
        if record.agent in ("claude", "codex") and not is_uuid(record.native_id):
            raise Dependency("An exact UUID is required; 4top will not guess the latest session")
        if record.agent == "claude":
            args = (capability.executable, "--resume", record.native_id)
        elif record.agent == "codex":
            args = (capability.executable, "resume", record.native_id)
        else:
            args = (capability.executable, "--session", record.file)
        env = dict(self.config.environment)
        return LaunchPlan(record.agent, capability.executable, args, cwd, env, record.root,
                          record.native_id, record.key)
