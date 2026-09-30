"""One application service layer for both CLI and TUI.

4top reads native history and starts native processes. It does not own a process
or a terminal. Which agents run on this host is asked of tmux (fourtop.resident)
each time, so nothing here records liveness.
"""
from __future__ import annotations

import dataclasses
import hashlib
import os
import subprocess
import threading
import time
from dataclasses import replace
from pathlib import Path

from session_ls import _injected
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

from . import e2b, resident
from .agents import Drivers
from .config import Config, Host
from .errors import Conflict, FourtopError, Missing
from .hosts import (
    remote_check,
    remote_preview,
    remote_preview_tail,
    remote_search,
    remote_snapshot,
    rows_of,
    ssh_argv,
)
from .models import LaunchPlan, Session, Snapshot
from .state import StateStore
from .sync import SyncState


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
                   None, record, bool(getattr(record, "subagent", False)),
                   str(getattr(record, "activity", "") or ""),
                   str(getattr(record, "last_request", "") or ""),
                   str(getattr(record, "branch", "") or ""),
                   scripted=bool(getattr(record, "scripted", False)))


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
        self._running, self._running_at = set(), float("-inf")
        if host is None:
            self.index = HistoryIndex(config.roots, config.cache_dir / "history.json",
                                      self.store.host_id, config.metadata_max_bytes,
                                      config.metadata_max_lines)
            self._remote = None
        else:
            self.remote = True
            self.index = None
            name = hashlib.sha256(host.name.encode()).hexdigest()[:16]
            self.sync = SyncState(config.cache_dir / "remote" / f"{name}.json",
                                  identity=f"{host.ssh}\n{host.command}")
            self._remote = Snapshot([], scope=host.name)
            self._remote_at = 0.0
            if self.sync.load():
                self._remote = Snapshot(rows_of(host, self.sync.payloads.values()),
                                        scope=host.name, cached=True)

    @property
    def keeps_agents(self) -> bool:
        """The host keeps agents in its own tmux, so opening one there attaches."""
        return self.host is not None and self.sync.attach

    @property
    def scope(self) -> str:
        return "local" if self.host is None else self.host.name

    def due(self, at_least: float = 0.0) -> bool:
        """Whether the next snapshot would ask the host again (always cheap locally).
        ``at_least`` stretches the interval, e.g. while nobody is looking."""
        if self.host is None:
            return True
        return time.monotonic() - self._remote_at >= max(self.host.refresh_seconds, at_least)

    def cached_snapshot(self) -> Snapshot | None:
        """Rows from the last visit to this host, if any, to show while it is asked again."""
        return self._remote if self.host is not None and self._remote.cached else None

    def wake(self) -> None:
        """An action the person took wakes a sandbox, within its lifetime; refreshing
        never does (a paused sandbox shows its last rows)."""
        if self.host is not None and self.host.e2b:
            e2b.wake(self.config, self.host)

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
                # A failure waits for the next interval too, instead of retrying on
                # every tick of the panel.
                self._remote_at = now
                cloud = None
                if self.host.e2b:
                    from .cloud import describe
                    cloud = describe(self.config, e2b.info(self.config, self.host.e2b, self.host.name))
                if cloud and cloud["state"] != "running":
                    self._remote = dataclasses.replace(self._remote, paused=True, cloud=cloud)
                else:
                    self._remote = dataclasses.replace(
                        remote_snapshot(self.config, self.host, self.sync), cloud=cloud)
            return self._remote
        history = self.history() if load_history else self._history
        # The panel asks every second; rows are rebuilt only when the scan changed, and
        # tmux is asked which agents run here as often as history is scanned.
        if getattr(self, "_rows_of", None) is not history:
            self._rows_of, self._rows = history, [row_for(record) for record in history.records]
        now = time.monotonic()
        if load_history or now - self._running_at >= self.config.history_refresh_seconds:
            self._running, self._running_at = resident.running(self.config.environment), now
        rows = [replace(row, resident=True) if resident.session_name(row.key) in self._running
                else row for row in self._rows] if self._running else list(self._rows)
        return Snapshot(rows, list(dict.fromkeys(history.issues)), history.observed_at, "local")

    def search(self, query: str, full=False, cancel=None) -> Snapshot:
        if self.host is not None:
            if self._remote.paused and not full:
                return Snapshot(slice_rows(self._remote.rows, query), scope=self.host.name,
                                cached=True, paused=True)
            self.wake()
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
            self.wake()
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

    def preview_tail(self, row: Session, before: int | None = None):
        """The latest messages of a session, oldest first, and where the page of
        earlier ones ends (None at the start of the transcript).

        The opening of a transcript is mostly injected context (instructions,
        environment, attached files), so a preview starts from the end, where the
        conversation is, and pages backwards.
        """
        if self.host is not None:
            self.wake()
            return remote_preview_tail(self.config, self.host, row, before)
        if row.record is None:
            row = self.resolve_row(row.key)
        if row.record is None:
            raise Missing("No readable native transcript is associated with this row")
        end = os.path.getsize(row.record.file) if before is None else max(0, before)
        # Tool output dwarfs conversation in most transcripts, so keep reading
        # backwards until there is something to read or the budget is spent.
        lines, issues, start = [], [], end
        while start > 0 and len(lines) < PREVIEW_TAIL_MESSAGES and end - start < PREVIEW_TAIL_BUDGET:
            chunk_end, start = start, max(0, start - PREVIEW_TAIL_BYTES)
            page = excerpt(row.record, start, max_bytes=chunk_end - start, max_lines=10**6)
            lines = [line for line in page.lines if not _injected_line(line)] + lines
            # Starting mid-file cuts the first line in half; that is expected, not damage.
            # A partial or malformed line is a parser's note, not part of the
            # conversation a person is reading; it is left out of the preview.
            issues += [issue for issue in page.issues if "partial" not in issue.lower()
                       and "malformed" not in issue.lower()]
        lines = lines[-self.config.preview_max_lines:]
        body = "\n\n".join(lines) or "No conversation text in this part of the transcript."
        if issues:
            body += "\n\n" + "\n".join(dict.fromkeys(issues))
        label = f"{row.agent} · latest messages" + (" · Earlier loads more" if start else "")
        return label, body, start or None

    def check(self, query: str) -> dict:
        """Would a resume work, and if not, why? Read-only: no process is planned.

        This exists so a refusal can be shown before the terminal is handed over.
        The same refusal raised during the hand-over flashes past under a panel that
        repaints immediately afterwards, which reads as "nothing happened".
        """
        if self.host is not None:
            self.wake()
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
                # Resolving a path is not enough: the CLI has to actually run here. A
                # non-interactive ssh session, for instance, gives the remote 4top a
                # minimal PATH, and a launcher that needs node then fails at --help.
                capability = self.drivers.probe(record.agent)
                if not capability.resume:
                    reason = (f"{record.agent} at {executable} does not advertise a resume "
                              f"interface" + (f" ({capability.detail})" if capability.detail else ""))
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

    def attach(self, query: str, cwd: str | None = None) -> LaunchPlan:
        """Show the session's agent from this host's agent server, starting it there
        first if none is running. Without tmux this is a plain resume."""
        if self.host is not None:
            raise Missing("Attaching to a session on another host runs there; see remote_argv")
        record = self.resolve_history(query)
        name = resident.session_name(record.key)
        if name in resident.running(self.config.environment):
            plan = resident.attach(self.config, name, record.agent)
            if plan is not None:
                return plan
        return resident.keep(self.config, self.drivers.plan_resume(record, cwd), name)

    def keep(self, plan: LaunchPlan) -> LaunchPlan:
        """A planned agent, started in this host's agent server instead of this terminal."""
        return resident.keep(self.config, plan)

    def remote_argv(self, args: list[str]) -> list[str]:
        if self.host is None:
            raise Missing("This is the local view; no remote command applies")
        self.wake()
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


PREVIEW_TAIL_BYTES = 2**18
PREVIEW_TAIL_MESSAGES = 10
PREVIEW_TAIL_BUDGET = 2**21


def _injected_line(line: str) -> bool:
    """A user message that is context an agent or a bridge injected, not typed."""
    role, _, text = line.partition(": ")
    return role == "user" and _injected(text)


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
            ("demo_1", "codex", "/demo/api-service", "fix retry handling", 1),
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
        # A working session, one waiting for its person, and one that stopped mid-turn.
        from dataclasses import replace
        states = {"demo_1": ("working", "now add jitter to the backoff", "fix/retry-jitter"),
                  "demo_2": ("waiting", "", "feat/cjk-search"),
                  "demo_4": ("working", "check the p99 after the pool change", "")}
        self.rows = [replace(row, activity=states[row.key][0], last_request=states[row.key][1],
                             branch=states[row.key][2]) if row.key in states else row
                     for row in self.rows]
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

    def preview_tail(self, row, before=None):
        label, body, _ = self.preview(row)
        return label, body, None

    def preview(self, row, cursor=0):
        return "DEMO — synthetic terminal preview", (
            "$ agent\n\nWorking directory: " + row.cwd + "\n\n"
            "user: " + row.title + "\nassistant: Reviewing the changes and running tests.\n\n"
            "No real history is read. No agent is started. Escape returns to 4top."
        ), None

    def close(self):
        pass
