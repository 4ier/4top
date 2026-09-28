"""E2B sandboxes: hosts that pause.

A sandbox is reached over ssh like any other host (see ``PROXY``); this module only
asks E2B whether one is awake, and wakes it or keeps it awake. Any traffic wakes a
paused sandbox, so the panel asks here first and never polls one that is asleep.
"""
from __future__ import annotations

import json
import os
import shutil
import urllib.error
import urllib.request
from pathlib import Path

from .config import Config, Host
from .errors import Dependency, Unavailable

API = "https://api.e2b.dev"
# The template (contrib/e2b) carries sshd over a websocket on port 8081; ssh's
# ProxyCommand is the other end, and %h is the sandbox ID.
PROXY = "websocat --binary -B 65536 - wss://8081-%h.e2b.app"
# How long a sandbox stays up after the last sign of use: waking it, or an agent of
# it still open in the panel. Then it pauses, with its processes intact.
KEEP_SECONDS = 600


def api_key(environment: dict[str, str]) -> str:
    """``E2B_API_KEY``, else the key of the project ``e2b auth login`` selected."""
    key = environment.get("E2B_API_KEY")
    if key:
        return key
    home = Path(environment.get("HOME", str(Path.home())))
    try:
        key = json.loads((home / ".e2b/config.json").read_text(encoding="utf-8")).get("projectApiKey")
    except (OSError, ValueError, AttributeError):
        key = None
    if not isinstance(key, str) or not key:
        raise Dependency("No E2B API key: set E2B_API_KEY or run `e2b auth login`")
    return key


def _call(config: Config, host: Host, method: str, path: str, body: dict | None = None) -> dict:
    request = urllib.request.Request(
        API + path, method=method, data=None if body is None else json.dumps(body).encode(),
        headers={"X-API-KEY": api_key(config.environment), "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=host.timeout_seconds) as response:
            return json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise Unavailable(f"{host.name}: sandbox {host.e2b} no longer exists") from None
        raise Unavailable(f"{host.name}: E2B answered {exc.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise Unavailable(f"{host.name}: E2B unreachable ({type(exc).__name__})") from None


def state(config: Config, host: Host) -> str:
    """``running`` or ``paused``. Asking does not wake the sandbox."""
    return str(_call(config, host, "GET", f"/sandboxes/{host.e2b}").get("state", ""))


def wake(config: Config, host: Host, seconds: int = KEEP_SECONDS) -> None:
    """Resume a paused sandbox, or keep a running one up for ``seconds`` more.
    E2B only ever extends the deadline here, never shortens it."""
    _call(config, host, "POST", f"/v2/sandboxes/{host.e2b}/connect", {"timeout": seconds})


def proxy_missing(config: Config) -> bool:
    return shutil.which("websocat", path=config.environment.get("PATH", os.defpath)) is None
