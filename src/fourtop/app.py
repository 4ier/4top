"""A small, keyboard-first terminal UI. No process policy lives in widget callbacks."""
from __future__ import annotations

import asyncio
import contextlib
import os
import threading
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from rich.text import Text
from session_ls.api import clean_text, query_terms
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Input, Select, Static

from .errors import FourtopError
from .models import Snapshot, ViewRow


def plain(value, *, multiline=False) -> Text:
    return Text(clean_text(str(value), multiline=multiline))


def age(value: str) -> str:
    try:
        started = datetime.fromisoformat(value.replace("Z", "+00:00"))
        delta = max(0, int((datetime.now(timezone.utc) - started).total_seconds()))
    except (ValueError, TypeError):
        return "—"
    if delta < 60:
        return f"{delta}s"
    if delta < 3600:
        return f"{delta // 60}m"
    if delta < 86400:
        return f"{delta // 3600}h{delta // 60 % 60:02d}m"
    return f"{delta // 86400}d"


class Confirm(ModalScreen[bool]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, title: str, message: str, destructive=False):
        super().__init__()
        self.heading, self.message, self.destructive = title, message, destructive

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Static(plain(self.heading), classes="dialog-title")
            with VerticalScroll(classes="dialog-body"):
                yield Static(plain(self.message, multiline=True))
            with Horizontal(classes="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Confirm", id="confirm", variant="error" if self.destructive else "primary")

    def on_mount(self):
        self.query_one("#cancel", Button).focus()  # Enter is not accidental approval.

    @on(Button.Pressed)
    def pressed(self, event: Button.Pressed):
        self.dismiss(event.button.id == "confirm")

    def action_cancel(self):
        self.dismiss(False)


class NewRuntime(ModalScreen[tuple | None]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, cwd: str, error="", values=None):
        super().__init__()
        self.values = values or ("codex", cwd, "")
        self.error = error

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Static("New coding agent", classes="dialog-title")
            yield Select([(agent, agent) for agent in ("codex", "claude", "pi")],
                         value=self.values[0], allow_blank=False, id="agent")
            yield Input(value=self.values[1], placeholder="Working directory", id="cwd")
            yield Input(value=self.values[2], placeholder="Optional name (stored locally)", id="name")
            yield Static(plain(self.error or "Starts the original CLI with its own permissions and authentication."), id="form-error")
            with Horizontal(classes="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Start & attach", id="start", variant="primary")

    @on(Button.Pressed)
    def pressed(self, event: Button.Pressed):
        if event.button.id == "cancel":
            self.dismiss(None)
        elif event.button.id == "start":
            cwd = self.query_one("#cwd", Input).value
            if not cwd:
                self.query_one("#form-error", Static).update("A working directory is required.")
                return
            self.dismiss((str(self.query_one("#agent", Select).value), cwd, self.query_one("#name", Input).value))

    def action_cancel(self):
        self.dismiss(None)


class LinkHistory(ModalScreen[str | None]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Static("Confirm a history association", classes="dialog-title")
            yield Static("Paste an exact history key from the history view or `4top list --json`. "
                         "The agent and source profile must match. This does not change native context.")
            yield Input(placeholder="h_… or exact native ID", id="history-key")
            with Horizontal(classes="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Review association", id="link", variant="primary")

    @on(Button.Pressed)
    def pressed(self, event: Button.Pressed):
        self.dismiss(self.query_one("#history-key", Input).value if event.button.id == "link" else None)

    def action_cancel(self):
        self.dismiss(None)


class Preview(ModalScreen):
    BINDINGS = [("escape", "close", "Close"), ("q", "close", "Close")]

    def __init__(self, manager, row: ViewRow):
        super().__init__()
        self.manager, self.row = manager, row
        self.cursor = 0
        self.loading = False

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog preview-dialog"):
            yield Static("Loading read-only preview…", id="preview-title", classes="dialog-title")
            with VerticalScroll(id="preview-scroll"):
                yield Static("", id="preview-body")
            with Horizontal(classes="buttons"):
                yield Button("Close", id="close")
                yield Button("Next page", id="next", disabled=True)

    async def on_mount(self):
        await self.load_page()

    async def load_page(self):
        if self.loading:
            return
        self.loading = True
        try:
            title, body, cursor = await asyncio.to_thread(self.manager.preview, self.row, self.cursor)
            self.query_one("#preview-title", Static).update(plain(title))
            self.query_one("#preview-body", Static).update(plain(body, multiline=True))
            self.cursor = cursor
            self.query_one("#next", Button).disabled = cursor is None
            self.query_one("#preview-scroll", VerticalScroll).scroll_home(animate=False)
        except (FourtopError, OSError, ValueError) as exc:
            self.query_one("#preview-body", Static).update(plain(str(exc)))
        finally:
            self.loading = False

    @on(Button.Pressed)
    async def pressed(self, event: Button.Pressed):
        if event.button.id == "next" and self.cursor is not None:
            await self.load_page()
        elif event.button.id == "close":
            self.dismiss()

    def action_close(self):
        self.dismiss()


class Details(ModalScreen[str | None]):
    BINDINGS = [("escape", "close", "Close")]

    def __init__(self, row: ViewRow, demo=False):
        super().__init__()
        self.row, self.demo = row, demo

    def compose(self) -> ComposeResult:
        row = self.row
        with Vertical(classes="dialog preview-dialog"):
            yield Static("Work details", classes="dialog-title")
            with VerticalScroll(classes="dialog-body"):
                yield Static(plain("\n".join((
                    f"Key: {row.key}", f"State: {row.state}{' (STALE)' if row.stale else ''}",
                    f"Agent: {row.agent}", f"Directory: {row.cwd}", f"Title: {row.title}",
                    f"Run: {row.run_id or '—'}", f"Launch / user association: {row.history_key or 'unlinked'}",
                    f"Binding: {row.binding}", "Current native context: not continuously observed",
                    f"PID: {row.pid or '—'} · connected clients: {row.clients}",
                    f"Observed: {row.observed_at}", f"Source: {row.source}",
                    row.issue or "", "Multiple clients share tmux's view. No other client is detached.",
                    "HIST does not prove that an externally launched agent has exited.",
                )), multiline=True))
            with Horizontal(classes="buttons"):
                yield Button("Close", id="close")
                yield Button("Link", id="link", disabled=self.demo or not row.can_attach)
                yield Button("Dismiss", id="dismiss", disabled=self.demo or row.state not in ("EXIT", "MISSING"))
                yield Button("Terminate…", id="terminate", variant="error",
                             disabled=self.demo or not row.run_id or row.state not in ("LIVE", "EXIT"))

    @on(Button.Pressed)
    def pressed(self, event: Button.Pressed):
        self.dismiss(None if event.button.id == "close" else event.button.id)

    def action_close(self):
        self.dismiss(None)


class FourtopApp(App[tuple | None]):
    TITLE = "4top"
    ENABLE_COMMAND_PALETTE = False
    BINDINGS = [
        Binding("q", "quit", "Quit"), Binding("ctrl+c", "quit", "Quit", priority=True),
        Binding("slash", "search", "Search"), Binding("escape", "clear_search", "Clear"),
        Binding("h", "history", "History"), Binding("n", "new", "New"),
        Binding("space", "preview", "Preview"), Binding("i", "details", "Details"),
        Binding("ctrl+f", "full_search", "Full content"), Binding("r", "refresh", "Refresh"),
        Binding("question_mark", "help", "Help"),
    ]
    CSS = """
    Screen { background: $background; color: $text; }
    #brand { height: 2; padding: 0 1; text-style: bold; background: $boost; }
    #counts { height: 1; padding: 0 1; color: $text-muted; }
    #query { height: 3; margin: 0 1; display: none; }
    #table { height: 1fr; margin: 1 0 0 0; }
    DataTable > .datatable--header { background: $boost; text-style: bold; }
    #selection { height: 3; padding: 0 1; border-top: solid $primary; }
    #status { height: 2; padding: 0 1; color: $text-muted; }
    #keys { height: 1; padding: 0 1; background: $boost; }
    ModalScreen { align: center middle; background: $background 80%; }
    .dialog { width: 76; max-width: 96%; height: auto; max-height: 90%;
              border: round $primary; background: $surface; padding: 1 2; }
    .dialog-title { text-style: bold; margin-bottom: 1; }
    .dialog-body { height: auto; max-height: 18; margin-bottom: 1; }
    .dialog Input, .dialog Select { margin-bottom: 1; }
    .buttons { height: auto; align-horizontal: right; margin-top: 1; }
    .buttons Button { min-width: 10; margin-left: 1; }
    .preview-dialog { width: 100; height: 85%; }
    #preview-scroll { height: 1fr; }
    #form-error { color: $text-muted; }
    """

    def __init__(self, manager, *, no_color=False):
        from textual.filter import NoColor
        super().__init__(ansi_color=True)
        self.manager = manager
        requested_no_color = no_color or getattr(manager.config, "color", "auto") == "none"
        if requested_no_color and not self.no_color:
            self._filters.append(NoColor())
            self.no_color = True
        self.snapshot_data = Snapshot([])
        self.shown: list[ViewRow] = []
        self.show_history = True
        self.selected_key = None
        self._refreshing = self._history_loading = self._launching = False
        self._columns = []
        self._keys = []
        self._cell_values = {}
        self._full_keys: set[str] | None = None
        self._full_issues: list[str] = []
        self._full_cancel = threading.Event()
        self._search_generation = 0
        self._full_running = False
        self._fourtop_closing = False
        self._status_message = ""
        if not manager.demo:
            view = manager.store.load_view()
            # Schema 1 wrote the old false-by-default before the user could choose,
            # so it carries no intent; honor a stored value only from schema 2 on.
            if view.get("schema_version", 1) >= 2:
                self.show_history = bool(view.get("history", True))
            self.selected_key = view.get("selected")

    def compose(self) -> ComposeResult:
        label = "4top  /  Your coding agents, one terminal."
        if self.manager.demo:
            label += "  [DEMO]"
        yield Static(plain(label), id="brand")
        yield Static("Opening local view…", id="counts")
        yield Input(placeholder="Search titles, directories, agents or IDs · Ctrl-F: full content · Esc: clear", id="query")
        yield DataTable(id="table", cursor_type="row", show_row_labels=False, zebra_stripes=True)
        yield Static("", id="selection")
        yield Static("", id="status")
        yield Static("/ search   Enter open   h history   n new   Space preview   i details   ? help   q quit", id="keys")

    async def on_mount(self):
        self._layout_columns()
        self.query_one("#table", DataTable).focus()
        self.set_interval(self.manager.config.refresh_seconds, self.refresh_runtime)
        self.set_interval(self.manager.config.history_refresh_seconds, self.refresh_history)
        self.run_worker(self.refresh_history())
        await self.refresh_runtime()

    def on_resize(self, event):
        if self.is_mounted:
            self._layout_columns(event.size.width)
            self.render_rows()

    def _layout_columns(self, width=None):
        width = width if width is not None else self.size.width
        if width < 60:
            columns = [("state", "STATE", 7), ("agent", "AGENT", 7), ("title", "TITLE", max(12, width - 21))]
        else:
            columns = [("state", "STATE", 7), ("agent", "AGENT", 7)]
            if width >= 100:
                columns.extend([("pid", "PID", 7), ("age", "AGE", 7)])
            columns.append(("project", "PROJECT", 18 if width >= 80 else 13))
            used = sum(item[2] + 2 for item in columns)
            columns.append(("title", "TITLE", max(12, width - used - 3)))
        if columns == self._columns:
            return
        self._columns = columns
        table = self.query_one("#table", DataTable)
        table.clear(columns=True)
        for key, name, column_width in columns:
            table.add_column(name, width=column_width, key=key)
        self._keys, self._cell_values = [], {}
        self._row_signatures = {}

    async def refresh_history(self):
        if self._history_loading or self._fourtop_closing:
            return
        self._history_loading = True
        try:
            await asyncio.to_thread(self.manager.history)
            await self.refresh_runtime()
        except (FourtopError, OSError, ValueError) as exc:
            self.set_status("History unavailable: " + str(exc))
        finally:
            self._history_loading = False

    async def refresh_runtime(self):
        if self._refreshing or self._fourtop_closing:
            return
        self._refreshing = True
        try:
            self.snapshot_data = await asyncio.to_thread(self.manager.snapshot, False)
            if not self._fourtop_closing:
                self.render_rows()
        except (FourtopError, OSError, ValueError) as exc:
            # Keep the previous view; don't turn a failed source into an empty screen.
            from dataclasses import replace
            self.snapshot_data.rows = [replace(row, stale=True, can_attach=False, can_resume=False)
                                       for row in self.snapshot_data.rows]
            self.set_status("STALE: " + str(exc))
            self.render_rows()
        finally:
            self._refreshing = False

    def render_rows(self):
        if self._fourtop_closing:
            return
        query = self.query_one("#query", Input).value
        terms = query_terms(query, tolerant=True)
        rows = self.snapshot_data.rows
        has_runtime = any(row.run_id for row in rows)
        if self._full_keys is not None:
            rows = [row for row in rows if row.key in self._full_keys]
        elif terms:
            rows = [row for row in rows if all(term in "\n".join((
                row.title, row.cwd, row.agent, row.key, row.run_id or "", row.history_key or ""
            )).casefold() for term in terms)]
        elif not self.show_history and has_runtime:
            rows = [row for row in rows if row.run_id]
        self.shown = rows
        keys = [row.key for row in rows]
        table = self.query_one("#table", DataTable)
        rebuild = keys != self._keys
        if rebuild:
            table.clear()
            self._cell_values.clear()
            self._row_signatures.clear()
        for row in rows:
            # Only "age" changes on its own, and only for managed runs, so a row
            # whose inputs are unchanged needs no cell rebuild. History rows are
            # the bulk of the table; re-texting all of them every refresh is what
            # makes a large store feel laggy.
            signature = None if row.run_id else (row.state, row.stale, row.pid,
                                                 row.agent, row.cwd, row.title)
            if not rebuild and signature is not None and self._row_signatures.get(row.key) == signature:
                continue
            values = {"state": row.state + ("*" if row.stale else ""), "agent": row.agent,
                      "pid": str(row.pid or "—"), "age": age(row.created_at) if row.run_id else "—",
                      "project": Path(row.cwd).name or row.cwd or "unknown", "title": row.title or "(untitled)"}
            cells = []
            for column, _, _ in self._columns:
                text = plain(values[column])
                if column == "state" and not self.no_color:
                    text.stylize({"LIVE": "green", "START": "yellow", "EXIT": "bright_black",
                                  "UNKNOWN": "yellow", "MISSING": "yellow", "HIST": "cyan"}.get(row.state, ""))
                cells.append(text)
            if rebuild:
                table.add_row(*cells, key=row.key)
            else:
                for (column, _, _), cell in zip(self._columns, cells, strict=True):
                    if self._cell_values.get((row.key, column)) != cell:
                        table.update_cell(row.key, column, cell)
            for (column, _, _), cell in zip(self._columns, cells, strict=True):
                self._cell_values[row.key, column] = cell
            self._row_signatures[row.key] = signature
        self._keys = keys
        if keys:
            index = keys.index(self.selected_key) if self.selected_key in keys else min(table.cursor_row, len(keys)-1)
            table.move_cursor(row=max(0, index), animate=False)
            self.selected_key = keys[max(0, index)]
        counts = Counter(row.state for row in self.snapshot_data.rows)
        scope = "FULL SEARCH" if self._full_keys is not None else "ALL HISTORY" if self.show_history or not has_runtime else "RUNTIMES"
        label = f"{counts['LIVE']} live · {counts['EXIT']} exited · {counts['HIST']} history  /  {scope}  /  {len(rows)} shown"
        if self.manager.demo:
            label = "SYNTHETIC DEMO · no real data or processes  /  " + label
        self.query_one("#counts", Static).update(plain(label))
        self.show_selection()
        issues = self._full_issues + self.snapshot_data.issues
        message = self._status_message or (" · ".join(issues[:2]) if issues else "HIST = no verified live association. q closes only this panel.")
        if self._full_running:
            message = "Searching decoded raw records… Esc cancels. Runtime refresh remains active."
        elif not rows and not terms:
            message = "No work found. Press n to start an agent, or try 4top --demo. " + message
        self.query_one("#status", Static).update(plain(message))

    def current(self) -> ViewRow | None:
        table = self.query_one("#table", DataTable)
        index = table.cursor_row
        return self.shown[index] if 0 <= index < len(self.shown) else None

    def show_selection(self):
        row = self.current()
        if not row:
            self.query_one("#selection", Static).update("No selection. Clear search or enable history.")
            return
        verb = "preview only" if self.manager.demo else "attach to original process" if row.can_attach else "review native resume" if row.can_resume else "inspect details"
        info = f"{row.agent} · {row.cwd}\nEnter: {verb} · {row.key} · {row.binding}"
        self.query_one("#selection", Static).update(plain(info, multiline=True))

    @on(DataTable.RowHighlighted)
    def highlighted(self, event):
        if event.row_key.value in self._keys:
            self.selected_key = event.row_key.value
        self.show_selection()

    @on(DataTable.RowSelected)
    async def selected(self, event):
        await self.open_current()

    @on(Input.Changed, "#query")
    def search_changed(self):
        self._full_cancel.set()
        self._search_generation += 1
        self._full_keys = None
        self._full_issues = []
        self._status_message = ""
        self.render_rows()

    @on(Input.Submitted, "#query")
    def search_submitted(self):
        self.query_one("#table", DataTable).focus()

    def set_status(self, message):
        self._status_message = clean_text(message)
        if self.is_mounted and not self._fourtop_closing:
            self.query_one("#status", Static).update(plain(message))

    def action_search(self):
        query = self.query_one("#query", Input)
        query.display = True
        query.focus()

    def action_clear_search(self):
        self._full_cancel.set()
        self._search_generation += 1
        self._full_keys, self._full_issues = None, []
        query = self.query_one("#query", Input)
        query.value = ""
        query.display = False
        self.query_one("#table", DataTable).focus()
        self.render_rows()

    def action_history(self):
        self.show_history = not self.show_history
        self.render_rows()

    def action_refresh(self):
        self._status_message = ""
        self.run_worker(self.refresh_history())
        self.run_worker(self.refresh_runtime())

    def action_full_search(self):
        query = self.query_one("#query", Input).value
        if not query.strip():
            self.action_search()
            self.set_status("Enter a literal query before starting full-content search.")
            return
        if self._full_running:
            self._full_cancel.set()
            self.set_status("Cancelling the previous full-content scan; press Ctrl-F again when it finishes.")
            return
        self._full_cancel = threading.Event()
        self._search_generation += 1
        generation = self._search_generation
        self.run_worker(self._do_full_search(query, generation))

    async def _do_full_search(self, query, generation):
        self._full_running = True
        self.render_rows()
        try:
            result = await asyncio.to_thread(self.manager.search, query, True, self._full_cancel)
            if generation == self._search_generation and not self._fourtop_closing:
                self._full_keys = {row.key for row in result.rows}
                self._full_issues = result.issues
        except (FourtopError, OSError, ValueError) as exc:
            self.set_status("Full-content search failed: " + str(exc))
        finally:
            self._full_running = False
            if not self._fourtop_closing:
                self.render_rows()

    def action_preview(self):
        row = self.current()
        if row:
            self.push_screen(Preview(self.manager, row))

    def action_new(self):
        if self.manager.demo:
            self.set_status("DEMO is read-only. No agent or tmux process will be started.")
        elif self._launching:
            self.set_status("A startup handoff is already in progress in this panel.")
        else:
            self.push_screen(NewRuntime(os.getcwd()), self._new_result)

    def _new_result(self, values):
        if values:
            self.run_worker(self._start(values))

    async def _start(self, values):
        self._launching = True
        self.set_status("Starting native agent; waiting for a verified terminal handoff…")
        try:
            run = await asyncio.to_thread(self.manager.new, *values)
            self.selected_key = "r_" + run["run_id"]
            await self.refresh_runtime()
            self._attach_runtime(run["run_id"])
        except (FourtopError, OSError, ValueError) as exc:
            self.push_screen(NewRuntime(values[1], clean_text(str(exc)), values), self._new_result)
        finally:
            self._launching = False

    def _attach_runtime(self, run_id):
        self._save_view()
        if self.manager.tmux.is_inside():
            self.exit(("attach", run_id))
            return
        try:
            with self.suspend():
                self.manager.attach(run_id)
            self.set_status("Returned from tmux. The original process was not restarted.")
        except (FourtopError, OSError, ValueError) as exc:
            self.set_status(str(exc))
        self.run_worker(self.refresh_runtime())

    async def open_current(self):
        row = self.current()
        if not row:
            return
        if self.manager.demo:
            self.action_preview()
        elif row.can_attach and row.run_id and not row.stale:
            self._attach_runtime(row.run_id)
        elif row.can_resume and row.history_key and not row.stale:
            message = (f"This creates a NEW {row.agent} process.\nDirectory: {row.cwd}\n"
                       f"History: {row.history_key}\nNative ID: {row.history.native_id if row.history else 'unknown'}\n"
                       "Current native configuration applies; original launch flags are not replayed.\n"
                       "Native execution may modify files or incur model costs.")
            self.push_screen(Confirm("Resume native history", message),
                             lambda answer: self.run_worker(self._resume(row.history_key)) if answer else None)
        else:
            self.action_details()

    async def _resume(self, history_key):
        if self._launching:
            self.set_status("A launch is already in progress in this panel.")
            return
        self._launching = True
        try:
            run = await asyncio.to_thread(self.manager.resume, history_key)
            self.selected_key = "r_" + run["run_id"]
            await self.refresh_runtime()
            self._attach_runtime(run["run_id"])
        except (FourtopError, OSError, ValueError) as exc:
            self.set_status(str(exc))
        finally:
            self._launching = False

    def action_details(self):
        row = self.current()
        if row:
            self.push_screen(Details(row, self.manager.demo), lambda action: self._detail_action(row, action))

    def _detail_action(self, row, action):
        if not action or not row.run_id or self.manager.demo:
            return
        if action == "link":
            self.push_screen(LinkHistory(), lambda key: self._confirm_link(row, key))
            return
        message = f"{row.agent} · {row.cwd}\nRun: {row.run_id}\n"
        message += ("Close this exact managed pane? In-flight writes may be interrupted. "
                    "Attach and exit natively for graceful shutdown." if action == "terminate" else
                    "Hide this exited runtime? Native history and project files are left untouched.")
        self.push_screen(Confirm(action.capitalize() + " runtime", message, action == "terminate"),
                         lambda answer: self.run_worker(self._mutate(action, row.run_id)) if answer else None)

    def _confirm_link(self, row, key):
        if not key:
            return
        self.push_screen(Confirm("Link history", f"Runtime: {row.run_id}\nHistory: {key}\n"
                                 "You confirm this association. Current context will still be marked unobserved."),
                         lambda answer: self.run_worker(self._mutate("link", row.run_id, key)) if answer else None)

    async def _mutate(self, action, *args):
        try:
            await asyncio.to_thread(getattr(self.manager, action), *args)
            self.set_status(action.capitalize() + " completed.")
            await self.refresh_runtime()
        except (FourtopError, OSError, ValueError) as exc:
            self.set_status(str(exc))

    def action_help(self):
        self.push_screen(Confirm("4top keys & safety", "\n".join((
            "↑ / ↓: select   Enter: attach or review native resume", "/: search metadata   Ctrl-F: explicit full-content search",
            "h: include history   Space: read-only preview   i: details / safe actions",
            "n: new agent   r: refresh   q / Ctrl-C: close only the panel",
            "Esc: close dialog, cancel full search or clear query",
            "Attach preserves a process; Resume creates a new one.",
            "Use your tmux prefix followed by d to detach. Custom bindings are not changed.",
            "Inside tmux: the panel exits before switching your exact client; use tmux's previous-session action to return.",
            "No automatic process restart, model calls, or telemetry.",
        ))))

    def _save_view(self):
        if not self.manager.demo:
            with contextlib.suppress(OSError, FourtopError):
                self.manager.store.save_view(self.selected_key, self.show_history)

    def action_quit(self):
        self._save_view()
        self._fourtop_closing = True
        self._full_cancel.set()
        self.manager.close()
        self.exit(None)

    def on_unmount(self):
        self._fourtop_closing = True
        self._full_cancel.set()
        self.manager.close()
