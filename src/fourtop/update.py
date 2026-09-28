"""Tell the user when a newer 4top is on PyPI, and how to get it.

At most one request a day, to PyPI's simple index, carrying nothing but the
project name; the answer is cached. Off with ``[ui] update_check = false``. A
failed check says nothing: an update notice is a convenience, never an error.
"""
from __future__ import annotations

import json
import re
import time
import urllib.request
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path

from session_ls.storage import atomic_json, private_dir, read_json

from . import __version__

INDEX = "https://pypi.org/simple/4top/"
CHECK_SECONDS = 24 * 3600
VERSION = re.compile(r"(\d+)\.(\d+)\.(\d+)(?:(a|b|rc)(\d+))?\Z")
PRE = {"a": 0, "b": 1, "rc": 2, None: 3}


def version_key(value: str) -> tuple | None:
    """Order 4top's own version strings (X.Y.Z with an optional aN/bN/rcN)."""
    match = VERSION.match(value.strip())
    if not match:
        return None
    major, minor, patch, stage, number = match.groups()
    return int(major), int(minor), int(patch), PRE[stage], int(number or 0)


def newest(versions, current: str = __version__) -> str | None:
    """The newest version worth offering: pre-releases only to a pre-release user."""
    mine = version_key(current)
    if mine is None:
        return None
    candidates = [(key, value) for value in versions if (key := version_key(str(value)))]
    if mine[3] == PRE[None]:
        candidates = [(key, value) for key, value in candidates if key[3] == PRE[None]]
    best = max(candidates, default=None)
    return best[1] if best and best[0] > mine else None


def fetch_versions(timeout: float = 5.0) -> list[str]:
    request = urllib.request.Request(INDEX, headers={"Accept": "application/vnd.pypi.simple.v1+json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        versions = json.load(response).get("versions")
    return [str(value) for value in versions] if isinstance(versions, list) else []


def upgrade_command() -> str:
    """How this copy was installed decides how it is upgraded."""
    try:
        dist = metadata.distribution("4top")
        direct = dist.read_text("direct_url.json")
        location = str(Path(dist.locate_file("")).resolve())
    except (metadata.PackageNotFoundError, OSError):
        return "pip install --pre -U 4top"
    try:
        origin = json.loads(direct) if direct else {}
    except ValueError:
        origin = {}
    if isinstance(origin, dict) and (origin.get("dir_info") or {}).get("editable"):
        source = str(origin.get("url", "")).removeprefix("file://")
        return f"git -C {source} pull" if source else "git pull in your checkout"
    if "/uv/tools/" in location:
        return "uv tool upgrade 4top --prerelease allow"
    if "/pipx/" in location:
        return "pipx upgrade --pip-args=--pre 4top"
    return "pip install --pre -U 4top"


@dataclass(frozen=True)
class Notice:
    latest: str
    command: str

    def text(self) -> str:
        return f"4top {self.latest} is available: {self.command}"


def check(cache_dir: Path, fetch=fetch_versions, now=time.time) -> Notice | None:
    """A notice if a newer version exists; asks PyPI at most once a day."""
    cache = Path(cache_dir) / "update.json"
    try:
        cached = read_json(cache, {})
    except (OSError, ValueError):
        cached = {}
    versions = cached.get("versions") if isinstance(cached, dict) else None
    fresh = isinstance(cached, dict) and now() - float(cached.get("checked_at", 0)) < CHECK_SECONDS
    if not fresh or not isinstance(versions, list):
        try:
            versions = fetch()
        except (OSError, ValueError):
            return None
        try:
            private_dir(cache.parent)
            atomic_json(cache, {"checked_at": now(), "versions": versions})
        except OSError:
            pass
    latest = newest(versions or [])
    return Notice(latest, upgrade_command()) if latest else None
