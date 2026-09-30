"""4top's own metadata; native transcripts are never written here.

Only three things are durable now: the local identity that scopes history keys,
the view preference, and which finished sessions the person has already looked at. There is no runtime state to keep, because 4top does not own
a process.
"""
from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

from session_ls.api import is_uuid
from session_ls.storage import atomic_json, file_lock, private_dir, read_json

from .errors import Unavailable

SCHEMA = 1
VIEW_SCHEMA = 3
SEEN_SCHEMA = 1
SEEN_LIMIT = 2000  # the most recent looks are enough; older rows are history anyway


class StateStore:
    def __init__(self, directory: Path):
        self.directory = private_dir(directory)
        self.locks_dir = private_dir(directory / "locks")
        with self.lock("identity"):
            identity = read_json(directory / "identity.json")
            if identity is None:
                identity = {"schema_version": SCHEMA, "host_id": str(uuid.uuid4())}
                atomic_json(directory / "identity.json", identity)
            if (not isinstance(identity, dict) or identity.get("schema_version") != SCHEMA
                    or not is_uuid(identity.get("host_id"))):
                raise Unavailable("Invalid local identity metadata; refusing unsafe mutation")
            self.host_id = identity["host_id"]

    def lock(self, key: str, timeout: float = 10):
        digest = hashlib.sha256(key.encode()).hexdigest()
        return file_lock(self.locks_dir / (digest + ".lock"), timeout)

    def load_view(self) -> dict:
        try:
            value = read_json(self.directory / "view.json", {})
        except (OSError, ValueError):
            return {}
        if not isinstance(value, dict) or value.get("schema_version") != VIEW_SCHEMA:
            return {}
        return value

    def save_view(self, selected: str | None) -> None:
        # Deliberately do not retain searches, prompts, or preview contents.
        with self.lock("view"):
            atomic_json(self.directory / "view.json",
                        {"schema_version": VIEW_SCHEMA, "selected": selected})

    def load_seen(self) -> dict[str, str]:
        """Panel tag -> the `last` of the row when the person last opened or peeked it."""
        try:
            value = read_json(self.directory / "seen.json", {})
        except (OSError, ValueError):
            return {}
        if not isinstance(value, dict) or value.get("schema_version") != SEEN_SCHEMA:
            return {}
        seen = value.get("seen")
        return {str(k): str(v) for k, v in seen.items()} if isinstance(seen, dict) else {}

    def save_seen(self, seen: dict[str, str]) -> None:
        recent = dict(sorted(seen.items(), key=lambda item: item[1])[-SEEN_LIMIT:])
        with self.lock("seen"):
            atomic_json(self.directory / "seen.json", {"schema_version": SEEN_SCHEMA, "seen": recent})
