"""What the person said about sessions on this host: names, and what to keep quiet.

A name and a mute belong with the history they describe, so they live on the host
that owns the sessions and reach every panel with its rows, as `label` and `muted`.
They are the person's own words, kept in one small private file
(`$XDG_STATE_HOME/4top/marks.json`); nothing here is inferred.

A project is muted by path: a session is muted when its repository or its directory
is that path or lies under it, so a bot's sessions stay quiet as new ones appear.
"""
from __future__ import annotations

import os
from pathlib import Path

from session_ls.storage import atomic_json, read_json

from .errors import FourtopError, Unavailable

SCHEMA = 1
LABEL_LIMIT = 80


class Marks:
    def __init__(self, store):
        self.store = store
        self.path = Path(store.directory) / "marks.json"

    def _read(self) -> dict:
        try:
            value = read_json(self.path, None)
        except (OSError, ValueError):
            raise Unavailable("Unreadable marks.json; refusing to replace it") from None
        if value is None:
            return {"schema_version": SCHEMA, "labels": {}, "muted": [], "projects": []}
        if not isinstance(value, dict) or value.get("schema_version") != SCHEMA:
            raise Unavailable("marks.json has another schema; refusing to replace it")
        return {"schema_version": SCHEMA,
                "labels": {str(k): str(v) for k, v in dict(value.get("labels") or {}).items()},
                "muted": [str(k) for k in value.get("muted") or []],
                "projects": [str(p) for p in value.get("projects") or []]}

    def load(self) -> dict:
        """The marks for a listing. A damaged file marks nothing rather than failing it."""
        try:
            return self._read()
        except (FourtopError, TypeError, ValueError):
            return {"schema_version": SCHEMA, "labels": {}, "muted": [], "projects": []}

    def _change(self, edit) -> None:
        with self.store.lock("marks"):
            value = self._read()
            edit(value)
            atomic_json(self.path, value)

    def label(self, key: str, name: str) -> None:
        name = " ".join(name.split())[:LABEL_LIMIT]

        def edit(value):
            if name:
                value["labels"][key] = name
            else:
                value["labels"].pop(key, None)
        self._change(edit)

    def mute(self, key: str, on: bool = True) -> None:
        def edit(value):
            value["muted"] = [k for k in value["muted"] if k != key] + ([key] if on else [])
        self._change(edit)

    def mute_project(self, path: str, on: bool = True) -> str:
        path = project_path(path)

        def edit(value):
            value["projects"] = [p for p in value["projects"] if p != path] + ([path] if on else [])
        self._change(edit)
        return path


def project_path(path: str) -> str:
    if not path or "\x00" in path:
        raise FourtopError("A project directory is required", 2)
    value = os.path.expanduser(path)
    if not os.path.isabs(value):
        value = os.path.abspath(value)
    return os.path.normpath(value)


def muted(marks: dict, key: str, cwd: str, repo: str) -> bool:
    if key in marks["muted"]:
        return True
    for project in marks["projects"]:
        for path in (repo, cwd):
            if path and (path == project or path.startswith(project.rstrip("/") + "/")):
                return True
    return False
