"""E2B: the API behind sandboxes, hosts that pause, copy and rewind.

A sandbox is reached over ssh like any other host (see ``PROXY``). This module is
the rest: asking whether one is awake, waking it, creating one, checkpointing it,
and the one file write that lets the first ssh key in. Any traffic wakes a paused
sandbox, so the panel asks here first and never polls one that is asleep.

Only sandboxes tagged ``fourtop=1`` are listed: a project's key sees every sandbox
of the project, including ones that are none of 4top's business.
"""
from __future__ import annotations

import json
import os
import shutil
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from .config import Config, Host
from .errors import Dependency, Unavailable

API = "https://api.e2b.dev"
TEMPLATE = "4top"
TAG = {"fourtop": "1"}
# The template (contrib/e2b) carries sshd over a websocket on port 8081; ssh's
# ProxyCommand is the other end, and %h is the sandbox ID.
PROXY = "websocat --binary -B 65536 - wss://8081-%h.e2b.app"
# How long a sandbox stays up after the last sign of use: waking it, or an agent of
# it still open in the panel. Then it pauses, with its processes intact.
KEEP_SECONDS = 600
TIMEOUT = 30.0


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


def _request(request: urllib.request.Request, label: str, timeout: float):
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise Unavailable(f"{label}: no longer exists") from None
        detail = exc.read()[:200].decode(errors="replace")
        raise Unavailable(f"{label}: E2B answered {exc.code} {detail}".strip()) from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise Unavailable(f"{label}: E2B unreachable ({type(exc).__name__})") from None


def call(config: Config, method: str, path: str, body: dict | None = None, *,
         label: str = "E2B", timeout: float = TIMEOUT):
    request = urllib.request.Request(
        API + path, method=method, data=None if body is None else json.dumps(body).encode(),
        headers={"X-API-KEY": api_key(config.environment), "Content-Type": "application/json"})
    return _request(request, label, timeout)


def _host_call(config: Config, host: Host, method: str, path: str, body: dict | None = None):
    return call(config, method, path, body, label=f"{host.name} ({host.e2b})",
                timeout=host.timeout_seconds)


def state(config: Config, host: Host) -> str:
    """``running`` or ``paused``. Asking does not wake the sandbox."""
    return str(_host_call(config, host, "GET", f"/sandboxes/{host.e2b}").get("state", ""))


def wake(config: Config, host: Host, seconds: int = KEEP_SECONDS) -> None:
    """Resume a paused sandbox, or keep a running one up for ``seconds`` more.
    E2B only ever extends the deadline here, never shortens it."""
    _host_call(config, host, "POST", f"/v2/sandboxes/{host.e2b}/connect", {"timeout": seconds})


def tagged(config: Config) -> list[dict]:
    """Every sandbox 4top created, running or paused, in one call."""
    query = urllib.parse.urlencode({"state": "running,paused", "metadata": "fourtop=1"})
    listed = call(config, "GET", f"/v2/sandboxes?{query}")
    return [item for item in listed if (item.get("metadata") or {}).get("fourtop") == "1"]


def create(config: Config, template: str, name: str, metadata: dict[str, str] | None = None,
           seconds: int = KEEP_SECONDS) -> dict:
    """A new sandbox, tagged and named. Returns E2B's answer, which carries the
    ``envdAccessToken`` needed for the first file write."""
    return call(config, "POST", "/sandboxes", {
        "templateID": template, "timeout": seconds, "autoPause": True, "secure": True,
        "metadata": {**TAG, "fourtop_name": name, **(metadata or {})}}, label=name, timeout=120)


def kill(config: Config, sandbox: str) -> None:
    call(config, "DELETE", f"/sandboxes/{sandbox}", label=sandbox)


def checkpoint(config: Config, sandbox: str, name: str | None = None) -> str:
    """A snapshot of the running sandbox, memory included; returns its ID, which
    ``create`` accepts as a template."""
    body = {"name": name or f"fourtop-{uuid.uuid4().hex[:12]}"}
    return str(call(config, "POST", f"/sandboxes/{sandbox}/snapshots", body, label=sandbox,
                    timeout=300)["snapshotID"])


def checkpoints(config: Config, name: str | None = None, sandbox: str | None = None) -> list[dict]:
    query = urllib.parse.urlencode({key: value for key, value in
                                    (("name", name), ("sandboxID", sandbox)) if value})
    return list(call(config, "GET", "/snapshots" + (f"?{query}" if query else "")))


def forget(config: Config, snapshot: str) -> None:
    """Delete a checkpoint: a snapshot is a template, under the name before its tag."""
    template = snapshot.split(":", 1)[0]
    call(config, "DELETE", f"/templates/{urllib.parse.quote(template, safe='')}", label=snapshot)


def upload(sandbox: str, token: str, path: str, data: bytes, user: str = "user") -> None:
    """Write one file through the sandbox's own HTTP API; ssh is not in yet."""
    boundary = uuid.uuid4().hex
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"f\"\r\n"
            f"Content-Type: application/octet-stream\r\n\r\n").encode() + data + \
        f"\r\n--{boundary}--\r\n".encode()
    query = urllib.parse.urlencode({"path": path, "username": user})
    request = urllib.request.Request(
        f"https://49983-{sandbox}.e2b.app/files?{query}", method="POST", data=body,
        headers={"X-Access-Token": token, "Content-Type": f"multipart/form-data; boundary={boundary}"})
    _request(request, sandbox, TIMEOUT)


def proxy_missing(config: Config) -> bool:
    return shutil.which("websocat", path=config.environment.get("PATH", os.defpath)) is None
