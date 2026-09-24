"""One application service layer for both CLI and TUI."""
from __future__ import annotations

import threading
import time
from dataclasses import replace

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
from .config import Config
from .errors import Conflict, Missing, Unavailable
from .models import Snapshot, ViewRow
from .state import StateStore
from .tmux import Tmux


def associated_keys(run: dict) -> set[str]:
    return {value for value in (run.get("launch_history_key"), run.get("user_history_key")) if value}


def unique(items, query: str, keys):
    if not query or len(query) < 4:
        raise Missing("Use at least four characters of a stable key / run identifier")
    exact = [item for item in items if query in keys(item)]
    matches = exact or [item for item in items if any(k.startswith(query) for k in keys(item) if k)]
    if not matches:
        raise Missing("No matching identifier; refresh the list and copy a stable key")
    if len(matches) != 1:
        raise Conflict("Identifier is ambiguous; use a complete key or select a specific runtime")
    return matches[0]


class Manager:
    demo = False

    def __init__(self, config: Config):
        self.config = config
        self.store = StateStore(config.state_dir)
        self.tmux = Tmux(config)
        self.drivers = Drivers(config, self.store.host_id)
        self.index = HistoryIndex(config.roots, config.cache_dir / "history.json", self.store.host_id,
                                  config.metadata_max_bytes, config.metadata_max_lines)
        self._history = ScanResult()
        self._history_at = 0.0
        self._history_lock = threading.Lock()
        self.stop_event = threading.Event()

    @property
    def location(self) -> str:
        return self.tmux.socket

    def history(self, force=False) -> ScanResult:
        with self._history_lock:
            if force or time.monotonic() - self._history_at >= self.config.history_refresh_seconds:
                scanned = self.index.scan(self.stop_event)
                if not scanned.cancelled:
                    self._history = scanned
                    self._history_at = time.monotonic()
            return self._history

    def snapshot(self, load_history=True) -> Snapshot:
        history = self.history() if load_history else self._history
        by_key = {record.key: record for record in history.records}
        runs, issues = self.store.list()
        issues = list(issues) + list(history.issues)
        observed = self.tmux.snapshot()
        if not observed.available:
            issues.append(observed.issue or "tmux is unavailable; history-only mode")
        rows = []
        merged = set()
        selected_runs = [run for run in runs if not run.get("dismissed")
                         and (not run.get("tmux") or run["tmux"]["socket"] == self.tmux.socket)]
        known = {run["run_id"] for run in runs}
        for run in selected_runs:
            key = run.get("user_history_key") or run.get("launch_history_key")
            native = by_key.get(key)
            state, pane, issue = self.tmux.observe(run, observed)
            if run.get("launch_phase") == "failed" and not run.get("tmux"):
                state, issue = "EXIT", "Startup failed before native execution"
            if native:
                merged.add(native.key)
            title = run["name"] or (native.title if native else "") or f"new {run['agent']} work"
            can_resume = state in ("EXIT", "MISSING") and native is not None and native.can_resume
            rows.append(ViewRow(
                "r_" + run["run_id"], state, run["agent"], run["cwd"], title,
                run["created_at"], native.last if native else "", run["run_id"], key,
                pane.pid if pane and not pane.dead else None, pane.clients if pane else 0,
                run.get("binding", "none"), observed.observed_at, not observed.available,
                state == "LIVE", can_resume, self.tmux.socket, issue, native,
            ))
        for pane in observed.panes:
            if pane.host_id == self.store.host_id and pane.run_id and pane.run_id not in known:
                issues.append("Marked terminal has no matching state file; inspect it with native tmux")
        for native in history.records:
            if native.key in merged:
                continue
            rows.append(ViewRow(native.key, "HIST", native.agent, native.cwd, native.title,
                                native.started, native.last, history_key=native.key,
                                observed_at=history.observed_at, can_resume=native.can_resume,
                                source=native.file, history=native))
        # Immutable timestamps avoid rows jumping as agents produce output.
        rows.sort(key=lambda row: row.created_at if row.run_id else row.last, reverse=True)
        rows.sort(key=lambda row: 0 if row.state == "LIVE" else 1 if row.state == "START" else
                  3 if row.state == "HIST" else 2)
        counts = {}
        for row in rows:
            if row.state in ("LIVE", "START") and row.history_key:
                counts[row.history_key] = counts.get(row.history_key, 0) + 1
        rows = [replace(row, issue="Multiple live runtimes share this launch history")
                if counts.get(row.history_key, 0) > 1 else row for row in rows]
        return Snapshot(rows, list(dict.fromkeys(issues)), observed.observed_at)

    def resolve_run(self, query: str) -> dict:
        records, _ = self.store.list()
        return unique(records, query, lambda run: (run["run_id"], "r_" + run["run_id"]))

    def resolve_history(self, query: str) -> HistoryRecord:
        records = self.history(force=True).records
        return unique(records, query, lambda record: (record.key, record.native_id or ""))

    def resolve_row(self, query: str) -> ViewRow:
        return unique(self.snapshot().rows, query, lambda row:
                      (row.key, row.run_id or "", row.history_key or ""))

    def _tmux_for(self, run: dict) -> Tmux:
        socket = (run.get("tmux") or {}).get("socket")
        return self.tmux if not socket or socket == self.tmux.socket else Tmux(self.config, socket)

    def new(self, agent: str, cwd: str, name: str = "", extra: tuple[str, ...] = ()) -> dict:
        plan = self.drivers.plan_new(agent, cwd, name, extra)
        if plan.history_key:
            with self.store.lock("history:" + plan.history_key, self.config.startup_seconds):
                return self.tmux.start(plan, self.store)
        return self.tmux.start(plan, self.store)

    def _check_conflicts(self, history_key: str, exclude: str | None = None) -> None:
        runs, issues = self.store.list()
        if issues:
            raise Unavailable("Some runtime metadata is unreadable; cannot safely exclude duplicate execution")
        for run in runs:
            if run["run_id"] == exclude or history_key not in associated_keys(run):
                continue
            if run.get("launch_phase") == "failed" and not run.get("tmux"):
                continue  # No native process received permission to execute.
            state, _, _ = self._tmux_for(run).observe(run)
            if state in ("LIVE", "START"):
                raise Conflict(f"History already has an active runtime: {run['run_id']}. Use attach.")
            if state == "UNKNOWN":
                raise Unavailable("An associated runtime cannot be verified; inspect it before resuming")

    def resume(self, query: str, cwd: str | None = None) -> dict:
        native = self.resolve_history(query)
        with self.store.lock("history:" + native.key, self.config.startup_seconds):
            # Refresh under the same lock used for the reservation, including other registered sockets.
            native = self.resolve_history(native.key)
            self._check_conflicts(native.key)
            plan = self.drivers.plan_resume(native, cwd)
            return self.tmux.start(plan, self.store)

    def attach(self, query: str, client: str | None = None) -> int:
        run = self.resolve_run(query)
        target = self._tmux_for(run)
        result = target.attach(run, client)
        self.store.event("attach", run["run_id"], "returned", result)
        return result

    def link(self, run_query: str, history_query: str) -> dict:
        native = self.resolve_history(history_query)
        run = self.resolve_run(run_query)
        with self.store.lock("history:" + native.key):
            with self.store.lock("run:" + run["run_id"]):
                run = self.store.get(run["run_id"])
                self._tmux_for(run).verify(run)
                if run["agent"] != native.agent or run["root"] != native.root:
                    raise Conflict("Agent and source profile must match before linking")
                self._check_conflicts(native.key, exclude=run["run_id"])
                # Preserve the immutable original launch association.
                run.update(user_history_key=native.key, binding="explicit-user",
                           binding_observed_at=utc_now(), current_history_key=None)
                self.store.save(run)
        self.store.event("link", run["run_id"], "confirmed")
        return run

    def terminate(self, query: str) -> dict:
        run = self.resolve_run(query)
        with self.store.lock("run:" + run["run_id"]):
            run = self.store.get(run["run_id"])
            self._tmux_for(run).terminate(run)
            run["terminated_at"] = utc_now()
            self.store.save(run)
        self.store.event("terminate", run["run_id"], "requested")
        return run

    def dismiss(self, query: str) -> dict:
        run = self.resolve_run(query)
        with self.store.lock("run:" + run["run_id"]):
            run = self.store.get(run["run_id"])
            state, _, _ = self._tmux_for(run).observe(run)
            if state not in ("EXIT", "MISSING") and not (
                    run["launch_phase"] == "failed" and not run.get("tmux")):
                raise Conflict("Only confirmed exited / missing runtimes can be dismissed")
            run["dismissed"] = True
            self.store.save(run)
        return run

    def preview(self, row: ViewRow, cursor: int = 0):
        if row.run_id and row.state in ("LIVE", "EXIT"):
            run = self.resolve_run(row.run_id)
            if run.get("tmux"):
                value = self._tmux_for(run).preview(run, self.config.preview_max_lines)
                return "Live terminal snapshot (read-only)", clean_text(value, multiline=True)[:2**18], None
        if row.history:
            page = excerpt(row.history, cursor, max_lines=self.config.preview_max_lines)
            body = "\n\n".join(page.lines) or "No supported text messages in this page."
            if page.issues:
                body += "\n\n" + "\n".join(page.issues)
            return page.label, body, page.next_cursor
        raise Missing("No readable pane or native transcript is associated with this row")

    def search(self, query: str, full=False, cancel=None) -> Snapshot:
        snapshot = self.snapshot()
        terms = query_terms(query)
        full_result = None
        if full:
            full_result = search_full(self.history().records, query, cancel)
            keys = {record.key for record in full_result.records}
            snapshot.rows = [row for row in snapshot.rows if row.history_key in keys]
            snapshot.issues.extend(full_result.issues)
            if full_result.cancelled:
                snapshot.issues.append("Full-content search cancelled; results are partial")
        else:
            snapshot.rows = [row for row in snapshot.rows if all(term in "\n".join((
                row.title, row.cwd, row.agent, row.key, row.run_id or "", row.history_key or ""
            )).casefold() for term in terms)]
        return snapshot

    def close(self) -> None:
        self.stop_event.set()


class DemoManager:
    """Read-only sample data. Does not construct Config, StateStore, tmux, or history readers."""
    demo = True
    location = "DEMO · synthetic data · no real processes"

    def __init__(self):
        from types import SimpleNamespace
        self.config = SimpleNamespace(refresh_seconds=1.0, history_refresh_seconds=5.0)
        now = utc_now()
        self.rows = [
            ViewRow("demo_1", "LIVE", "codex", "/demo/api-service", "fix retry handling", now, now,
                    "demo-run-1", pid=18421, binding="explicit-launch", observed_at=now, source="DEMO"),
            ViewRow("demo_2", "LIVE", "claude", "/demo/web-client", "改善中文搜索体验", now, now,
                    "demo-run-2", pid=19702, clients=1, binding="explicit-launch", observed_at=now, source="DEMO"),
            ViewRow("demo_3", "LIVE", "pi", "/demo/infra-tools", "inspect migration", now, now,
                    "demo-run-3", pid=20144, observed_at=now, source="DEMO"),
            ViewRow("demo_4", "EXIT", "claude", "/demo/api-service", "investigate timeout", now, now,
                    "demo-run-4", source="DEMO"),
            ViewRow("demo_5", "HIST", "codex", "/demo/session-ls", "improve history parsing", now, now, source="DEMO"),
            ViewRow("demo_6", "HIST", "cursor", "/demo/site", "polish documentation", now, now, source="DEMO"),
        ]
        from datetime import datetime, timedelta, timezone
        self.rows = [replace(row, created_at=(datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat())
                     for row, minutes in zip(self.rows, (24, 11, 5, 125, 1440, 2880), strict=True)]

    def snapshot(self, load_history=True):
        return Snapshot(list(self.rows))

    def history(self, force=False):
        return ScanResult()

    def preview(self, row, cursor=0):
        return "DEMO — synthetic terminal preview", (
            "$ agent\n\nWorking directory: " + row.cwd + "\n\n"
            "user: " + row.title + "\nassistant: Reviewing the changes and running tests.\n\n"
            "No real history is read. No agent is started. Escape returns to 4top."
        ), None

    def search(self, query, full=False, cancel=None):
        terms = query_terms(query, tolerant=True)
        return Snapshot([row for row in self.rows if all(term in
            (row.title + " " + row.agent + " " + row.cwd).casefold() for term in terms)])

    def close(self):
        pass
