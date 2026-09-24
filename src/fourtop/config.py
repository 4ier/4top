"""Explicit local configuration. No credentials are read from agent stores."""
from __future__ import annotations

import os
import stat
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from session_ls.api import Root

from .errors import FourtopError

ROOT_ENV = {"codex": "CODEX_HOME", "claude": "CLAUDE_CONFIG_DIR", "pi": "PI_CODING_AGENT_DIR"}


def _absolute(value: str) -> Path:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise FourtopError("Expected a nonempty filesystem path", 2)
    result = Path(value).expanduser()
    if not result.is_absolute():
        raise FourtopError("Configuration paths must be absolute (or start with ~)", 2)
    return result


def _number(section: dict, key: str, default: float, minimum: float, integer=False):
    value = section.get(key, default)
    if type(value) not in (int, float) or value < minimum or value != value or value == float("inf"):
        raise FourtopError(f"Invalid {key}; expected a finite number >= {minimum}", 2)
    if integer and type(value) is not int:
        raise FourtopError(f"Invalid {key}; expected an integer", 2)
    return value


@dataclass
class Config:
    state_dir: Path
    cache_dir: Path
    roots: list[Root]
    executables: dict[str, str]
    environment: dict[str, str] = field(repr=False)
    socket: str | None = None
    refresh_seconds: float = 1
    history_refresh_seconds: float = 5
    startup_seconds: float = 10
    control_seconds: float = 5
    metadata_max_bytes: int = 2**21
    metadata_max_lines: int = 2000
    preview_max_lines: int = 200
    color: str = "auto"
    config_path: str | None = None

    def root(self, agent: str) -> Root:
        return next(root for root in self.roots if root.agent == agent)

    @classmethod
    def load(cls, path: str | None = None, socket: str | None = None,
             environment: dict[str, str] | None = None) -> Config:
        env = dict(os.environ if environment is None else environment)
        home = _absolute(env.get("HOME", str(Path.home())))
        config_home = _absolute(env.get("XDG_CONFIG_HOME", str(home / ".config")))
        state = _absolute(env.get("XDG_STATE_HOME", str(home / ".local/state"))) / "4top"
        cache = _absolute(env.get("XDG_CACHE_HOME", str(home / ".cache"))) / "4top"
        config_file = _absolute(path) if path else config_home / "4top/config.toml"
        data = {}
        try:
            fd = os.open(config_file, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
            with os.fdopen(fd, "rb") as handle:
                st = os.fstat(handle.fileno())
                if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid():
                    raise FourtopError("Configuration must be a regular file owned by this user", 2)
                if st.st_size > 2**20:
                    raise FourtopError("Configuration exceeds 1 MiB", 2)
                data = tomllib.load(handle)
        except FileNotFoundError:
            if path:
                raise FourtopError("Explicit configuration file was not found", 2) from None
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise FourtopError(f"Cannot read configuration: {type(exc).__name__}", 2) from None
        allowed = {"ui", "runtime", "history", "agents"}
        if set(data) - allowed:
            raise FourtopError("Unknown configuration section: " + ", ".join(sorted(set(data) - allowed)), 2)
        for key in allowed:
            if not isinstance(data.get(key, {}), dict):
                raise FourtopError(f"[{key}] must be a TOML table", 2)
        ui, runtime, history, agents = (data.get(name, {}) for name in ("ui", "runtime", "history", "agents"))
        for section, values, permitted in (
            ("ui", ui, {"refresh_seconds", "history_refresh_seconds", "color"}),
            ("runtime", runtime, {"socket", "startup_handshake_seconds", "control_timeout_seconds"}),
            ("history", history, {"metadata_max_bytes", "metadata_max_lines", "preview_max_lines"}),
        ):
            if set(values) - permitted:
                raise FourtopError(f"Unknown option in [{section}]: " + ", ".join(sorted(set(values) - permitted)), 2)
        defaults = {"codex": home / ".codex", "claude": home / ".claude",
                    "pi": home / ".pi/agent", "cursor": home / ".cursor"}
        if set(agents) - set(defaults):
            raise FourtopError("Unknown agent in configuration", 2)
        roots = []
        executables = {}
        for agent, default in defaults.items():
            options = agents.get(agent, {})
            if not isinstance(options, dict) or set(options) - {"root", "executable"}:
                raise FourtopError(f"Invalid configuration for agent {agent}", 2)
            root_value = options.get("root", env.get(ROOT_ENV.get(agent, ""), str(default)))
            root_path = str(_absolute(root_value))
            roots.append(Root(agent, root_path))
            # An absent override is meaningful: Claude otherwise looks for
            # .claude.json under CLAUDE_CONFIG_DIR instead of the native HOME.
            key = ROOT_ENV.get(agent)
            if key and ("root" in options or key in env):
                env[key] = root_path
            executable = options.get("executable", agent)
            if not isinstance(executable, str) or not executable or "\x00" in executable:
                raise FourtopError(f"Invalid executable for {agent}", 2)
            executables[agent] = str(Path(executable).expanduser())
        color = ui.get("color", "auto")
        if color not in ("auto", "none"):
            raise FourtopError("ui.color must be auto or none", 2)
        chosen_socket = socket if socket is not None else runtime.get("socket")
        if chosen_socket:
            chosen_socket = str(_absolute(chosen_socket))
        return cls(state, cache, roots, executables, env, chosen_socket,
                   _number(ui, "refresh_seconds", 1.0, 0.1),
                   _number(ui, "history_refresh_seconds", 5.0, 0.1),
                   _number(runtime, "startup_handshake_seconds", 10.0, 0.1),
                   _number(runtime, "control_timeout_seconds", 5.0, 0.1),
                   _number(history, "metadata_max_bytes", 2**21, 1, True),
                   _number(history, "metadata_max_lines", 2000, 1, True),
                   _number(history, "preview_max_lines", 200, 1, True), color, str(config_file))
