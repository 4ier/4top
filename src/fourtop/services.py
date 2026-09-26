"""One application service layer for both CLI and TUI.

4top reads native history and starts native processes. It does not own a process,
a terminal or a multiplexer, so nothing here tracks liveness.
"""
from __future__ import annotations

import os
import subprocess
import threading
import time
from pathlib import Path

from session_ls.api import (
    HistoryIndex,
    HistoryRecord,
    ScanResult,
    clean_text,
    excerpt,
    query_terms,
    search_full,
    utc_now,
)

from .agents import Drivers
from .config import Config, Host
from .errors import Conflict, FourtopError, Missing
from .hosts import remote_check, remote_preview, remote_search, remote_snapshot, ssh_argv
from .models import LaunchPlan, Session, Snapshot
from .state import StateStore


def unique(items, query: str, keys):
    if not query or len(query) < 4:
        raise Missing("Use at least four characters of a stable key / session identifier")
    exact = [item for item in items if query in keys(item)]
    matches = exact or [item for item in items if any(k.startswith(query) for k in keys(item) if k)]
    if not matches:
        raise Missing("No matching session; refresh the list and copy a stable key")
    if len(matches) != 1:
        raise Conflict("Identifier is ambiguous; use a complete key")
    return matches[0]


def row_for(record: HistoryRecord, host: str = "local") -> Session:
    return Session(record.key, record.agent, record.cwd, record.title, record.started, record.last,
                   host, record.file, record.status, tuple(record.problems), record.can_resume,
                   None, record)


def slice_rows(rows: list[Session], query: str = "", agent=None, project=None) -> list[Session]:
    terms = query_terms(query, tolerant=True)
    return [row for row in rows
            if (not agent or row.agent == agent)
            and (not project or project.casefold() in row.cwd.casefold())
            and all(term in "\n".join((row.title, row.cwd, row.agent, row.key)).casefold()
                    for term in terms)]


class Manager:
    """Sessions on this machine. Also the client for a remote host."""

    demo = False
    remote = False

    def __init__(self, config: Config, host: Host | None = None):
        self.config = config
        self.host = host
        self.store = StateStore(config.state_dir)
        self.drivers = Drivers(config, self.store.host_id)
        self._history = ScanResult()
        self._history_at = 0.0
        self._history_lock = threading.Lock()
        self.stop_event = threading.Event()
        if host is None:
            self.index = HistoryIndex(config.roots, config.cache_dir / "history.json",
                                      self.store.host_id, config.metadata_max_bytes,
                                      config.metadata_max_lines)
            self._remote = None
        else:
            self.remote = True
            self.index = None
            self._remote = Snapshot([], scope=host.name)
            self._remote_at = 0.0

    @property
    def scope(self) -> str:
        return "local" if self.host is None else self.host.name

    def history(self, force=False) -> ScanResult:
        with self._history_lock:
            if force or time.monotonic() - self._history_at >= self.config.history_refresh_seconds:
                scanned = self.index.scan(self.stop_event)
                if not scanned.cancelled:
                    self._history = scanned
                    self._history_at = time.monotonic()
            return self._history

    def snapshot(self, load_history=True) -> Snapshot:
        if self.host is not None:
            now = time.monotonic()
            if load_history or now - self._remote_at >= self.host.refresh_seconds:
                self._remote = remote_snapshot(self.config, self.host)
                self._remote_at = now
            return self._remote
        history = self.history() if load_history else self._history
        rows = [row_for(record) for record in history.records]
        return Snapshot(rows, list(dict.fromkeys(history.issues)), history.observed_at, "local")

    def search(self, query: str, full=False, cancel=None) -> Snapshot:
        if self.host is not None:
            return remote_search(self.config, self.host, query, full)
        snapshot = self.snapshot()
        if full:
            result = search_full(self.history().records, query, cancel)
            keys = {record.key for record in result.records}
            snapshot.rows = [row for row in snapshot.rows if row.key in keys]
            snapshot.issues.extend(result.issues)
            if result.cancelled:
                snapshot.issues.append("Full-content search cancelled; results are partial")
        else:
            snapshot.rows = slice_rows(snapshot.rows, query)
        return snapshot

    def resolve_row(self, query: str) -> Session:
        return unique(self.snapshot().rows, query, lambda row: (row.key,))

    def resolve_history(self, query: str) -> HistoryRecord:
        if self.host is not None:
            raise Missing("Remote sessions are resumed by the remote CLI, not resolved here")
        records = self.history(force=True).records
        return unique(records, query, lambda record: (record.key, record.native_id or ""))

    def preview(self, row: Session, cursor: int = 0):
        if self.host is not None:
            return f"{row.agent} · {row.host} (read-only)", \
                clean_text(remote_preview(self.config, self.host, row.key, cursor), multiline=True)[:2**18], None
        if row.record is None:
            row = self.resolve_row(row.key)
        if row.record is None:
            raise Missing("No readable native transcript is associated with this row")
        page = excerpt(row.record, cursor, max_lines=self.config.preview_max_lines)
        body = "\n\n".join(page.lines) or "No supported text messages in this page."
        if page.issues:
            body += "\n\n" + "\n".join(page.issues)
        return page.label, body, page.next_cursor

    def check(self, query: str) -> dict:
        """Would a resume work, and if not, why? Read-only: no process is planned.

        This exists so a refusal can be shown before the terminal is handed over.
        The same refusal raised during the hand-over flashes past under a panel that
        repaints immediately afterwards, which reads as "nothing happened".
        """
        if self.host is not None:
            return remote_check(self.config, self.host, query)
        record = self.resolve_history(query)
        executable, reason = None, None
        if record.agent == "cursor":
            reason = "cursor transcripts are read-only"
        elif not record.can_resume:
            reason = (f"the recorded directory is inferred or missing: {record.cwd or 'unknown'}"
                      if record.cwd_quality != "native" else
                      "no exact native identifier; 4top will not guess the latest session")
        else:
            try:
                executable = self.drivers.executable(record.agent)
            except FourtopError as exc:
                reason = str(exc)
        directory = Path(record.cwd).expanduser() if record.cwd else None
        cwd_missing = directory is None or not directory.is_dir()
        if reason is None and cwd_missing:
            reason = f"the recorded directory does not exist: {record.cwd or 'unknown'}"
        return {"key": record.key, "agent": record.agent, "host": self.scope,
                "native_id": record.native_id, "cwd": record.cwd,
                "cwd_quality": record.cwd_quality, "executable": executable,
                "cwd_missing": cwd_missing, "resumable": reason is None, "reason": reason,
                "status": record.status, "problems": list(record.problems)}

    def new(self, agent: str, cwd: str, extra: tuple[str, ...] = ()) -> LaunchPlan:
        if self.host is not None:
            raise Missing("Starting an agent on another host runs there; see remote_argv")
        return self.drivers.plan_new(agent, cwd, extra)

    def resume(self, query: str, cwd: str | None = None) -> LaunchPlan:
        if self.host is not None:
            raise Missing("Resuming a session on another host runs there; see remote_argv")
        return self.drivers.plan_resume(self.resolve_history(query), cwd)

    def remote_argv(self, args: list[str]) -> list[str]:
        if self.host is None:
            raise Missing("This is the local view; no remote command applies")
        return ssh_argv(self.config, self.host, args, tty=True)

    def run(self, plan: LaunchPlan) -> int:
        """Run a native agent in the current terminal and return when it exits."""
        return subprocess.call(plan.argv, cwd=plan.cwd, env=plan.environment)

    def hand_over(self, plan: LaunchPlan) -> None:
        """Replace this process with the native agent. Only for the CLI."""
        os.chdir(plan.cwd)
        os.execvpe(plan.executable, plan.argv, plan.environment)

    def close(self) -> None:
        self.stop_event.set()


class DemoManager:
    """Read-only sample data. Reads no configuration, history or state."""

    demo = True
    remote = False
    location = "DEMO · synthetic data · no real processes"

    def __init__(self):
        from types import SimpleNamespace
        self.config = SimpleNamespace(refresh_seconds=1.0, history_refresh_seconds=5.0)
        self.host = None
        now = utc_now()
        from datetime import datetime, timedelta, timezone
        rows = [
            ("demo_1", "codex", "/demo/api-service", "fix retry handling", 24),
            ("demo_2", "claude", "/demo/web-client", "改善中文搜索体验", 11),
            ("demo_3", "pi", "/demo/infra-tools", "inspect migration", 5),
            ("demo_4", "claude", "/demo/api-service", "investigate timeout", 125),
            ("demo_5", "codex", "/demo/session-ls", "improve history parsing", 1440),
            ("demo_6", "cursor", "/demo/site", "polish documentation", 2880),
        ]
        self.rows = [
            Session(key, agent, cwd, title, started, started, "local", "DEMO",
                    "available", (), agent != "cursor", None)
            for key, agent, cwd, title, minutes in rows
            for started in [(datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()]
        ]
        self._now = now

    @property
    def scope(self) -> str:
        return "demo"

    def snapshot(self, load_history=True):
        return Snapshot(list(self.rows), [], scope="demo")

    def history(self, force=False):
        return ScanResult()

    def search(self, query, full=False, cancel=None):
        return Snapshot(slice_rows(self.rows, query), [], scope="demo")

    def resolve_row(self, query: str):
        return unique(self.rows, query, lambda row: (row.key,))

    def check(self, query: str):
        row = self.resolve_row(query)
        return {"key": row.key, "agent": row.agent, "host": "demo", "native_id": None,
                "cwd": row.cwd, "cwd_quality": "native", "executable": None,
                "cwd_missing": False, "resumable": row.can_resume,
                "reason": None if row.can_resume else "DEMO is read-only",
                "status": row.status, "problems": []}

    def preview(self, row, cursor=0):
        return "DEMO — synthetic terminal preview", (
            "$ agent\n\nWorking directory: " + row.cwd + "\n\n"
            "user: " + row.title + "\nassistant: Reviewing the changes and running tests.\n\n"
            "No real history is read. No agent is started. Escape returns to 4top."
        ), None

    def close(self):
        pass
