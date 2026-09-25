"""Explicit local configuration. No credentials are read from agent stores."""
from __future__ import annotations

import os
import re
import stat
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from session_ls.api import Root

from .errors import FourtopError

ROOT_ENV = {"codex": "CODEX_HOME", "claude": "CLAUDE_CONFIG_DIR", "pi": "PI_CODING_AGENT_DIR"}
HOST_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")


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


@dataclass(frozen=True)
class Host:
    """A remote machine reached by running the same CLI over SSH."""

    name: str
    ssh: str
    command: str = "4top"
    refresh_seconds: float = 15.0
    timeout_seconds: float = 10.0
    ad_hoc: bool = False


def _host(name: str, options: dict, ad_hoc: bool = False) -> Host:
    if not ad_hoc and not HOST_NAME_RE.match(name):
        raise FourtopError("Host names must be short and start with a letter or digit", 2)
    if not isinstance(options, dict):
        raise FourtopError(f"[hosts.{name}] must be a TOML table", 2)
    permitted = {"ssh", "command", "refresh_seconds", "timeout_seconds"}
    if set(options) - permitted:
        raise FourtopError(f"Unknown option in [hosts.{name}]: "
                           + ", ".join(sorted(set(options) - permitted)), 2)
    target = options.get("ssh")
    # A leading dash would be read as an ssh option, and whitespace cannot be an
    # argv element on the remote. Both are refused rather than quoted and guessed.
    if (not isinstance(target, str) or not target or "\x00" in target or target.startswith("-")
            or any(character.isspace() for character in target)):
        raise FourtopError(f"Invalid hosts.{name}.ssh; expected one ssh destination", 2)
    command = options.get("command", "4top")
    if (not isinstance(command, str) or not command or "\x00" in command or command.startswith("-")
            or any(character.isspace() for character in command)):
        raise FourtopError(f"Invalid hosts.{name}.command; expected one executable path", 2)
    return Host(name, target, command,
                _number(options, "refresh_seconds", 15.0, 1.0),
                _number(options, "timeout_seconds", 10.0, 0.1), ad_hoc)


@dataclass
class Config:
    state_dir: Path
    cache_dir: Path
    roots: list[Root]
    executables: dict[str, str]
    environment: dict[str, str] = field(repr=False)
    refresh_seconds: float = 1
    history_refresh_seconds: float = 5
    metadata_max_bytes: int = 2**21
    metadata_max_lines: int = 2000
    preview_max_lines: int = 200
    color: str = "auto"
    config_path: str | None = None
    hosts: dict[str, Host] = field(default_factory=dict)

    def root(self, agent: str) -> Root:
        return next(root for root in self.roots if root.agent == agent)

    def resolve_host(self, target: str | None) -> Host | None:
        """None means the local machine. An unknown target is used as-is."""
        if not target:
            return None
        if target in self.hosts:
            return self.hosts[target]
        return _host(target, {"ssh": target}, ad_hoc=True)

    @classmethod
    def load(cls, path: str | None = None, environment: dict[str, str] | None = None) -> Config:
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
        allowed = {"ui", "history", "agents", "hosts"}
        if set(data) - allowed:
            raise FourtopError("Unknown configuration section: " + ", ".join(sorted(set(data) - allowed)), 2)
        for key in allowed:
            if not isinstance(data.get(key, {}), dict):
                raise FourtopError(f"[{key}] must be a TOML table", 2)
        ui, history, agents, hosts = (data.get(name, {}) for name in ("ui", "history", "agents", "hosts"))
        for section, values, permitted in (
            ("ui", ui, {"refresh_seconds", "history_refresh_seconds", "color"}),
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
        return cls(state, cache, roots, executables, env,
                   _number(ui, "refresh_seconds", 1.0, 0.1),
                   _number(ui, "history_refresh_seconds", 5.0, 0.1),
                   _number(history, "metadata_max_bytes", 2**21, 1, True),
                   _number(history, "metadata_max_lines", 2000, 1, True),
                   _number(history, "preview_max_lines", 200, 1, True), color, str(config_file),
                   {name: _host(name, options) for name, options in hosts.items()})
