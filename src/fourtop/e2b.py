"""E2B: the API behind cloud sandboxes, and what they cost.

A sandbox is reached over ssh like any other host (see ``proxy``). This module is
the rest: its state, waking it within its lifetime, creating, pausing, snapshotting
and killing it, the one file write that lets the first ssh key in, and an estimate
of what it has cost, read from E2B's own lifecycle events.

Two rules keep a sandbox from costing money nobody asked for. Every sandbox 4top
creates has auto-resume off, so traffic (a refresh, a stray ssh) never wakes a
paused one: only ``wake`` does, which is called for an action the person took. And
``wake`` never keeps a sandbox up past the deadline it was created with.

Only sandboxes tagged ``fourtop=1`` are listed or counted: a project's key sees
every sandbox of the project, including ones that are none of 4top's business.
"""
from __future__ import annotations

import json
import os
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .config import Config, Host
from .errors import Conflict, Dependency, Unavailable

TAG = {"fourtop": "1"}
TIMEOUT = 30.0
# The template (contrib/e2b) carries sshd over a websocket on port 8081; ssh's
# ProxyCommand is the other end, and %h is the sandbox ID.
PROXY = "websocat --binary -B 65536 - wss://8081-%h.{domain}"
# How long bringing work home may keep a sandbox up after its lifetime is over.
GRACE_SECONDS = 600

# E2B's usage prices, https://e2b.dev/pricing as read on 2026-09-30: $0.000014 per
# vCPU-second and $0.0000045 per GiB of RAM per second; storage is free. A cost
# shown by 4top is these times the seconds E2B reports: an estimate. E2B's invoice
# is the authority, and a plan's fee and credits are not in it.
VCPU_SECOND_USD = 0.000014
GIB_SECOND_USD = 0.0000045
PRICES_READ = "2026-09-30"


def rate(cpu: float, memory_mb: float) -> float:
    """Dollars per running second for a sandbox of this size."""
    return cpu * VCPU_SECOND_USD + memory_mb / 1024 * GIB_SECOND_USD


def api_url(environment: dict[str, str]) -> str:
    return environment.get("FOURTOP_E2B_API", "https://api.e2b.dev").rstrip("/")


def domain(environment: dict[str, str]) -> str:
    return environment.get("FOURTOP_E2B_DOMAIN", "e2b.app")


def proxy(config: Config) -> str:
    return PROXY.format(domain=domain(config.environment))


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
        detail = exc.read()[:300].decode(errors="replace")
        raise Unavailable(f"{label}: E2B answered {exc.code} {detail}".strip()) from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise Unavailable(f"{label}: E2B unreachable ({type(exc).__name__})") from None


def call(config: Config, method: str, path: str, body: dict | None = None, *,
         label: str = "E2B", timeout: float = TIMEOUT):
    request = urllib.request.Request(
        api_url(config.environment) + path, method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={"X-API-KEY": api_key(config.environment), "Content-Type": "application/json"})
    return _request(request, label, timeout)


def when(value) -> float | None:
    """An E2B timestamp as epoch seconds; None when absent or unreadable."""
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        # E2B writes nanoseconds, which fromisoformat before 3.11's full ISO support
        # may refuse; the seconds are enough.
        text = str(value or "")
        if "." in text:
            return when(text.split(".", 1)[0] + "+00:00")
        return None


def info(config: Config, sandbox: str, label: str | None = None) -> dict:
    """E2B's record of a sandbox: state, size, metadata. Asking does not wake it."""
    return call(config, "GET", f"/sandboxes/{sandbox}", label=label or sandbox)


def state(config: Config, host: Host) -> str:
    """``running`` or ``paused``."""
    return str(info(config, host.e2b, f"{host.name} ({host.e2b})").get("state", ""))


def deadline(item: dict) -> float | None:
    """When a sandbox's lifetime ends, as it was created with; None for a sandbox
    4top did not give one (an ``[hosts] e2b`` entry for someone else's sandbox)."""
    try:
        return float((item.get("metadata") or {})["fourtop_deadline"])
    except (KeyError, TypeError, ValueError):
        return None


def wake(config: Config, host: Host, *, grace: bool = False, now: float | None = None) -> None:
    """Resume a paused sandbox, or keep a running one up, until its deadline.

    Only an action the person took calls this. Past the deadline it refuses,
    except with ``grace``: bringing work home may take a few more minutes, so work
    is never stranded by the cap. A sandbox without a deadline gets the configured
    maximum lifetime from now.
    """
    now = time.time() if now is None else now
    item = info(config, host.e2b, f"{host.name} ({host.e2b})")
    end = deadline(item)
    seconds = int(config.cloud.max_minutes * 60) if end is None else int(end - now)
    if seconds <= 0:
        if not grace:
            raise Conflict(f"{host.name} has used its lifetime; `4top cloud done {host.name}` "
                           "brings its work home, `4top cloud rm` discards it")
        seconds = GRACE_SECONDS
    call(config, "POST", f"/v2/sandboxes/{host.e2b}/connect", {"timeout": max(1, seconds)},
         label=f"{host.name} ({host.e2b})")


def listed(config: Config, timeout: float = TIMEOUT) -> list[dict]:
    """Every sandbox 4top created, running or paused, in one call."""
    query = urllib.parse.urlencode({"state": "running,paused", "metadata": "fourtop=1"})
    return [item for item in call(config, "GET", f"/v2/sandboxes?{query}", timeout=timeout)
            if (item.get("metadata") or {}).get("fourtop") == "1"]


def create(config: Config, template: str, seconds: int, metadata: dict[str, str], label: str) -> dict:
    """A new sandbox, tagged, that pauses (never dies with the work) when its
    lifetime is over, and that traffic cannot wake. Returns E2B's answer, which
    carries the ``envdAccessToken`` for the first file write."""
    try:
        return call(config, "POST", "/sandboxes", {
            "templateID": template, "timeout": seconds, "autoPause": True,
            "autoResume": {"enabled": False}, "secure": True,
            "metadata": {**TAG, **metadata}}, label=label, timeout=120)
    except Unavailable as exc:
        # The plan's longest sandbox (an hour on Hobby), said as E2B says it.
        if "Timeout cannot be greater" in str(exc):
            said = str(exc).split("message", 1)[-1].strip(':" }')
            raise Conflict(f"E2B refused a {seconds // 60}-minute sandbox for this plan ({said}); "
                           "lower [cloud] max_minutes") from None
        raise


def pause(config: Config, sandbox: str, label: str | None = None) -> None:
    try:
        call(config, "POST", f"/sandboxes/{sandbox}/pause", label=label or sandbox, timeout=120)
    except Unavailable as exc:
        if " 409 " not in str(exc):  # already paused
            raise


def kill(config: Config, sandbox: str, label: str | None = None) -> None:
    try:
        call(config, "DELETE", f"/sandboxes/{sandbox}", label=label or sandbox)
    except Unavailable as exc:
        if "no longer exists" not in str(exc):
            raise


def snapshot(config: Config, sandbox: str, name: str) -> str:
    """A persistent snapshot of the sandbox as it is; returns its ID."""
    return str(call(config, "POST", f"/sandboxes/{sandbox}/snapshots", {"name": name},
                    label=sandbox, timeout=300)["snapshotID"])


def snapshots(config: Config) -> list[dict]:
    return [item for item in call(config, "GET", "/snapshots?limit=100") if isinstance(item, dict)]


def forget(config: Config, snapshot_id: str) -> None:
    """Delete a snapshot: a snapshot is a template, under the name before its tag."""
    template = snapshot_id.split(":", 1)[0]
    call(config, "DELETE", f"/templates/{urllib.parse.quote(template, safe='')}", label=snapshot_id)


def upload(config: Config, sandbox: str, token: str, path: str, data: bytes, user: str = "user") -> None:
    """Write one file through the sandbox's own HTTP API; ssh is not in yet."""
    boundary = uuid.uuid4().hex
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"f\"\r\n"
            f"Content-Type: application/octet-stream\r\n\r\n").encode() + data + \
        f"\r\n--{boundary}--\r\n".encode()
    query = urllib.parse.urlencode({"path": path, "username": user})
    base = config.environment.get("FOURTOP_E2B_ENVD") or f"https://49983-{sandbox}.{domain(config.environment)}"
    request = urllib.request.Request(
        f"{base}/files?{query}", method="POST", data=body,
        headers={"X-Access-Token": token, "Content-Type": f"multipart/form-data; boundary={boundary}"})
    _request(request, sandbox, TIMEOUT)


# ----- what it cost -------------------------------------------------------------------
# E2B records a sandbox's life as events: created, resumed, checkpointed (a
# snapshot), paused, killed. A pause or a kill carries the stretch that just ended,
# with the sandbox's size; a checkpoint ends a stretch without saying so (measured:
# resumed 00:24:59, checkpointed 00:25:25, killed 00:25:26 with 848 ms). So the
# stretches are rebuilt from the timestamps, and the recorded ones are used where
# E2B gives them. That is the running time 4top can know about without keeping any
# record of its own, whichever device started the sandbox.

STARTS = ("sandbox.lifecycle.created", "sandbox.lifecycle.resumed")
CHECKPOINTED = "sandbox.lifecycle.checkpointed"
ENDED = ("sandbox.lifecycle.paused", "sandbox.lifecycle.killed")
LIFECYCLE = (*STARTS, CHECKPOINTED, *ENDED)


def _execution(event: dict) -> dict | None:
    data = event.get("eventData")
    execution = data.get("execution") if isinstance(data, dict) else None
    return execution if isinstance(execution, dict) else None


def stretches(events: list[dict], size: tuple[float, float] = (0.0, 0.0), *,
              running_since: float | None = None, now: float | None = None) -> list[tuple]:
    """(start, seconds, vcpus, memory MB) of each stretch one sandbox ran, from its
    events in any order. ``running_since``: it runs now, since then if its events
    do not say (they arrive a few seconds late)."""
    ordered = sorted((event for event in events if event.get("type") in LIFECYCLE),
                     key=lambda event: when(event.get("timestamp")) or 0.0)
    for event in ordered:
        execution = _execution(event)
        if execution and execution.get("vcpu_count"):
            size = (float(execution["vcpu_count"]), float(execution.get("memory_mb", 0)))
    found, since = [], None
    for event in ordered:
        at, kind, execution = when(event.get("timestamp")), event.get("type"), _execution(event)
        if at is None:
            continue
        if kind in STARTS:
            since = at
        elif kind == CHECKPOINTED:
            if since is not None:
                found.append((since, at - since, *size))
            since = at  # it runs on; E2B counts the rest as a new stretch
        else:
            try:
                recorded = float(execution["execution_time"]) / 1000 if execution else None
            except (KeyError, TypeError, ValueError):
                recorded = None
            if recorded is not None:
                start = when(execution.get("started_at")) or at - recorded
                # Never count a stretch twice; E2B gives its start in whole seconds.
                if since is not None and start < since - 1:
                    recorded, start = max(0.0, at - since), since
                found.append((start, recorded, *size))
            elif since is not None:
                found.append((since, at - since, *size))
            since = None
    if now is not None and (since is not None or running_since is not None):
        begin = since if since is not None else running_since
        found.append((begin, max(0.0, now - begin), *size))
    return found


def _size(item: dict) -> tuple[float, float]:
    return float(item.get("cpuCount") or 0), float(item.get("memoryMB") or 0)


def _events(config: Config, sandbox: str) -> list[dict]:
    query = urllib.parse.urlencode([("limit", 100), *(("types", kind) for kind in LIFECYCLE)])
    events = call(config, "GET", f"/events/sandboxes/{sandbox}?{query}", label=sandbox)
    return events if isinstance(events, list) else []


def cost(config: Config, item: dict, now: float | None = None) -> tuple[float, float]:
    """(dollars, running seconds) of one listed sandbox so far."""
    now = time.time() if now is None else now
    running = item.get("state") == "running"
    found = stretches(_events(config, str(item.get("sandboxID", ""))), _size(item), now=now if running else None,
                      running_since=when(item.get("startedAt")) if running else None)
    return (sum(length * rate(cpu, memory) for _, length, cpu, memory in found),
            sum(length for _, length, _, _ in found))


def spent_since(config: Config, since: float, running: list[dict], now: float | None = None,
                pages: int = 20) -> float:
    """Dollars 4top's sandboxes ran up since ``since`` (epoch seconds), any device:
    every sandbox with a lifecycle event since then, or running now, costed from
    its own events. Stretches that began earlier count only from ``since``."""
    now = time.time() if now is None else now
    active = {str(item.get("sandboxID")): item for item in running}
    for page in range(pages):
        query = urllib.parse.urlencode([("limit", 100), ("offset", page * 100),
                                        *(("types", kind) for kind in LIFECYCLE)])
        events = call(config, "GET", f"/events/sandboxes?{query}")
        if not isinstance(events, list) or not events:
            break
        for event in events:
            metadata = (event.get("eventData") or {}).get("sandbox_metadata") or {}
            if metadata.get("fourtop") == "1" and (when(event.get("timestamp")) or 0) >= since:
                active.setdefault(str(event.get("sandboxId")), {})
        # Newest first: once a page ends before ``since``, the rest are older.
        if (when(events[-1].get("timestamp")) or 0) < since or len(events) < 100:
            break
    total = 0.0
    for sandbox, item in active.items():
        running_now = item.get("state") == "running"
        for start, length, cpu, memory in stretches(
                _events(config, sandbox), _size(item), now=now if running_now else None,
                running_since=when(item.get("startedAt")) if running_now else None):
            total += max(0.0, min(length, start + length - since)) * rate(cpu, memory)
    return total


def start_of_day(now: float | None = None) -> float:
    """Local midnight today, as epoch seconds: the budget is per calendar day here."""
    moment = datetime.fromtimestamp(time.time() if now is None else now).astimezone()
    return moment.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def utc(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def proxy_missing(config: Config) -> bool:
    return shutil.which("websocat", path=config.environment.get("PATH", os.defpath)) is None
