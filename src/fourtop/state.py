"""4top's own metadata; native transcripts are never written here."""
from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path

from session_ls.api import is_uuid, utc_now
from session_ls.storage import StorageError, atomic_json, file_lock, private_dir, read_json

from .errors import Missing, Unavailable

SCHEMA = 1


class StateStore:
    def __init__(self, directory: Path):
        self.directory = private_dir(directory)
        self.runs_dir = private_dir(directory / "runs")
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

    def _path(self, run_id: str) -> Path:
        if not is_uuid(run_id):
            raise Missing("Expected a complete, valid run identifier")
        return self.runs_dir / (run_id + ".json")

    def validate(self, data) -> dict:
        if not isinstance(data, dict) or data.get("schema_version") != SCHEMA:
            raise Unavailable("Unsupported runtime state schema; no mutation performed")
        if not is_uuid(data.get("run_id")) or data.get("host_id") != self.host_id:
            raise Unavailable("Invalid runtime identity")
        if data.get("agent") not in ("codex", "claude", "pi"):
            raise Unavailable("Unknown runtime agent")
        for key in ("cwd", "name", "created_at", "launch_phase", "root"):
            if not isinstance(data.get(key), str):
                raise Unavailable("Incomplete runtime state: " + key)
        for key in ("launch_history_key", "native_id"):
            if data.get(key) is not None and not isinstance(data[key], str):
                raise Unavailable("Invalid runtime association")
        if data.get("tmux") is not None:
            target = data["tmux"]
            if (not isinstance(target, dict) or not isinstance(target.get("socket"), str)
                    or not isinstance(target.get("server"), str)
                    or not isinstance(target.get("pane"), str)):
                raise Unavailable("Incomplete tmux identity")
        return data

    def save(self, record: dict) -> None:
        self.validate(record)
        atomic_json(self._path(record["run_id"]), record)

    def get(self, run_id: str) -> dict:
        try:
            record = read_json(self._path(run_id))
        except (OSError, ValueError) as exc:
            raise Unavailable(f"Runtime metadata unreadable ({type(exc).__name__})") from None
        if record is None:
            raise Missing("Runtime not found")
        return self.validate(record)

    def list(self) -> tuple[list[dict], list[str]]:
        records, issues = [], []
        for path in sorted(self.runs_dir.glob("*.json")):
            try:
                value = self.validate(read_json(path))
                if value["run_id"] != path.stem:
                    raise Unavailable("Runtime filename does not match its identity")
                records.append(value)
            except (OSError, ValueError, Unavailable, TypeError) as exc:
                issues.append(f"Unreadable runtime metadata {path.name}: {type(exc).__name__}")
        return records, issues

    def update(self, run_id: str, **changes) -> dict:
        with self.lock("run:" + run_id):
            value = self.get(run_id)
            value.update(changes)
            self.save(value)
            return value

    def load_view(self) -> dict:
        try:
            value = read_json(self.directory / "view.json", {})
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    def save_view(self, selected: str | None, history: bool) -> None:
        # Deliberately do not retain searches, prompts, or preview contents.
        with self.lock("view"):
            atomic_json(self.directory / "view.json", {
                "schema_version": 1, "selected": selected, "history": history,
            })

    def event(self, operation: str, run_id: str | None = None, phase: str = "", code: int = 0) -> str:
        operation_id = str(uuid.uuid4())
        try:
            with self.lock("events", timeout=1):
                path = self.directory / "operations.json"
                previous = read_json(path, [])
                if not isinstance(previous, list):
                    previous = []
                previous.append({"id": operation_id, "at": utc_now(), "operation": operation,
                                 "run_id": run_id, "phase": phase, "code": code})
                # A bounded structured log; no argv, environment, paths, or source text.
                atomic_json(path, previous[-200:])
        except (OSError, ValueError, StorageError, json.JSONDecodeError):
            pass
        return operation_id
