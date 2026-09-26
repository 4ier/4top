"""A small, keyboard-first terminal UI. No process policy lives in widget callbacks."""
from __future__ import annotations

import asyncio
import contextlib
import os
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from rich.text import Text
from session_ls.api import clean_text, query_terms
from textual import on
from textual.app import App, ComposeResult, SuspendNotSupported
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Input, OptionList, Select, Static
from textual.widgets.option_list import Option

from .errors import FourtopError
from .models import Session, Snapshot, age
from .services import Manager


def plain(value, *, multiline=False) -> Text:
    return Text(clean_text(str(value), multiline=multiline))



WHEEL_ROWS = 3


class SessionTable(DataTable):
    """A table whose wheel moves the highlight.

    Scrolling the viewport away from the highlight leaves Enter acting on a row
    that is off screen, so a wheel step moves the cursor and the view follows it.
    """

    def _wheel(self, rows: int) -> None:
        if not self.row_count:
            return
        target = max(0, min(self.row_count - 1, self.cursor_row + rows))
        if target != self.cursor_row:
            self.move_cursor(row=target, animate=False)

    def _on_mouse_scroll_down(self, event) -> None:
        event.stop()
        self._wheel(WHEEL_ROWS)

    def _on_mouse_scroll_up(self, event) -> None:
        event.stop()
        self._wheel(-WHEEL_ROWS)


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


class NewAgent(ModalScreen[tuple | None]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, cwd: str, error="", values=None):
        super().__init__()
        self.values = values or ("codex", cwd)
        self.error = error

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Static("New coding agent", classes="dialog-title")
            yield Select([(agent, agent) for agent in ("codex", "claude", "pi")],
                         value=self.values[0], allow_blank=False, id="agent")
            yield Input(value=self.values[1], placeholder="Working directory", id="cwd")
            yield Static(plain(self.error or "Runs the original CLI in this terminal, with its own "
                                             "permissions and authentication."), id="form-error")
            with Horizontal(classes="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Start", id="start", variant="primary")

    @on(Button.Pressed)
    def pressed(self, event: Button.Pressed):
        if event.button.id == "cancel":
            self.dismiss(None)
        elif event.button.id == "start":
            cwd = self.query_one("#cwd", Input).value
            if not cwd:
                self.query_one("#form-error", Static).update("A working directory is required.")
                return
            self.dismiss((str(self.query_one("#agent", Select).value), cwd))

    def action_cancel(self):
        self.dismiss(None)


class DirectoryPrompt(ModalScreen[str | None]):
    """The recorded directory is gone; ask for the one to resume in."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, recorded: str, initial: str, validate: bool = True):
        # A remote directory cannot be checked from here: the remote CLI validates it
        # when it runs, so an unvalidated prompt still cannot start anything wrong.
        super().__init__()
        self.recorded, self.initial, self.validate = recorded, initial, validate

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Static("Choose a working directory", classes="dialog-title")
            yield Static(plain(f"The recorded directory no longer exists:\n{self.recorded}\n\n"
                               "Resuming runs there, and 4top never creates a directory."))
            yield Input(value=self.initial, placeholder="Absolute path", id="cwd")
            yield Static("", id="form-error")
            with Horizontal(classes="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Use this directory", id="use", variant="primary")

    def on_mount(self):
        self.query_one("#cwd", Input).focus()

    @on(Input.Submitted, "#cwd")
    def submitted(self, event: Input.Submitted):
        self.query_one("#use", Button).press()

    @on(Button.Pressed)
    def pressed(self, event: Button.Pressed):
        if event.button.id == "cancel":
            self.dismiss(None)
            return
        value = self.query_one("#cwd", Input).value.strip()
        candidate = Path(value).expanduser() if value else None
        if candidate is None:
            self.query_one("#form-error", Static).update("A directory is required.")
            return
        if self.validate and not candidate.is_dir():
            self.query_one("#form-error", Static).update("Not an existing directory.")
            return
        self.dismiss(str(candidate))

    def action_cancel(self):
        self.dismiss(None)


class Preview(ModalScreen):
    BINDINGS = [("escape", "close", "Close"), ("q", "close", "Close")]

    def __init__(self, manager, row: Session):
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

    def __init__(self, row: Session, demo=False):
        super().__init__()
        self.row, self.demo = row, demo

    def lines(self) -> list[str]:
        """Everything the screen shows. A missing directory is worth naming here:
        resuming from one is impossible, and the reason is not obvious from the path."""
        row = self.row
        directory = Path(row.cwd).expanduser() if row.cwd else None
        missing = "" if directory and directory.is_dir() else "  (missing)"
        lines = [f"Key: {row.key}", f"Agent: {row.agent}", f"Host: {row.host}",
                 f"Directory: {row.cwd}{missing}", f"Title: {row.title}",
                 f"Started: {row.started}", f"Last written: {row.last}",
                 f"Status: {row.status}", f"Source: {row.source}"]
        lines.extend(f"Problem: {problem}" for problem in row.problems)
        lines.append(row.issue or "")
        lines.append("Resume starts a new process: memory, shell children and network "
                     "state are not restored.")
        return lines

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog preview-dialog"):
            yield Static("Session details", classes="dialog-title")
            with VerticalScroll(classes="dialog-body"):
                yield Static(plain("\n".join(self.lines()), multiline=True))
            with Horizontal(classes="buttons"):
                yield Button("Close", id="close")
                yield Button("Resume…", id="resume", variant="primary",
                             disabled=self.demo or not self.row.can_resume)

    @on(Button.Pressed)
    def pressed(self, event: Button.Pressed):
        self.dismiss(None if event.button.id == "close" else event.button.id)

    def action_close(self):
        self.dismiss(None)


class HostPicker(ModalScreen[str | None]):
    """Pick which machine the panel is looking at. Local is always first."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, current: str, choices: list[tuple[str, str]]):
        super().__init__()
        self.current, self.choices = current, choices

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Static("Switch host", classes="dialog-title")
            with VerticalScroll(classes="dialog-body"):
                yield OptionList(*[Option(label, id=value) for value, label in self.choices], id="hosts")
            with Horizontal(classes="buttons"):
                yield Button("Cancel", id="cancel")

    def on_mount(self):
        options = self.query_one("#hosts", OptionList)
        options.focus()
        for index, (value, _) in enumerate(self.choices):
            if value == self.current:
                options.highlighted = index
                break

    @on(OptionList.OptionSelected, "#hosts")
    def chosen(self, event: OptionList.OptionSelected):
        self.dismiss(event.option.id)

    @on(Button.Pressed)
    def pressed(self, event: Button.Pressed):
        self.dismiss(None)

    def action_cancel(self):
        self.dismiss(None)


class FourtopApp(App[tuple | None]):
    TITLE = "4top"
    ENABLE_COMMAND_PALETTE = False
    BINDINGS = [
        Binding("q", "quit", "Quit"), Binding("ctrl+c", "quit", "Quit", priority=True),
        Binding("slash", "search", "Search"), Binding("escape", "clear_search", "Clear"),
        Binding("n", "new", "New"),
        Binding("space", "preview", "Preview"), Binding("i", "details", "Details"),
        Binding("ctrl+f", "full_search", "Full content"), Binding("r", "refresh", "Refresh"),
        Binding("H", "switch_host", "Host"), Binding("question_mark", "help", "Help"),
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
        self.shown: list[Session] = []
        self.selected_key = None
        self.stale = False
        self._refreshing = self._history_loading = self._launching = False
        self._columns = []
        self._keys = []
        self._cell_values = {}
        self._row_signatures = {}
        self._derived = {}
        self._full_keys: set[str] | None = None
        self._full_issues: list[str] = []
        self._full_cancel = threading.Event()
        self._search_generation = 0
        self._full_running = False
        self._fourtop_closing = False
        self._status_message = ""
        if not manager.demo:
            self.selected_key = manager.store.load_view().get("selected")

    def compose(self) -> ComposeResult:
        label = "4top  /  Your coding agents, one terminal."
        if self.manager.demo:
            label += "  [DEMO]"
        yield Static(plain(label), id="brand")
        yield Static("Opening the view…", id="counts")
        yield Input(placeholder="Search titles, directories, agents or keys · Ctrl-F: full content · Esc: clear", id="query")
        yield SessionTable(id="table", cursor_type="row", show_row_labels=False, zebra_stripes=True)
        yield Static("", id="selection")
        yield Static("", id="status")
        yield Static("/ search   Enter resume   n new   H host   Space preview   i details   ? help   q quit", id="keys")

    async def on_mount(self):
        self._layout_columns()
        self.query_one("#table", DataTable).focus()
        self.set_interval(self.manager.config.refresh_seconds, self.refresh_rows)
        if not self.manager.remote:
            self.set_interval(self.manager.config.history_refresh_seconds, self.refresh_history)
            self.run_worker(self.refresh_history())
        await self.refresh_rows()

    def on_resize(self, event):
        if self.is_mounted:
            self._layout_columns(event.size.width)
            self.render_rows()

    def _layout_columns(self, width=None):
        width = width if width is not None else self.size.width
        if width < 60:
            columns = [("agent", "AGENT", 7), ("title", "TITLE", max(12, width - 10))]
        else:
            columns = [("agent", "AGENT", 7), ("project", "PROJECT", 18 if width >= 80 else 13)]
            if width >= 100:
                columns.append(("age", "UPDATED", 9))
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
        self._derived = {}

    async def refresh_history(self):
        if self._history_loading or self._fourtop_closing or self.manager.remote:
            return
        self._history_loading = True
        try:
            await asyncio.to_thread(self.manager.history)
            await self.refresh_rows()
        except (FourtopError, OSError, ValueError) as exc:
            self.set_status("History unavailable: " + str(exc))
        finally:
            self._history_loading = False

    async def refresh_rows(self):
        if self._refreshing or self._fourtop_closing:
            return
        self._refreshing = True
        manager = self.manager
        try:
            snapshot = await asyncio.to_thread(manager.snapshot, False)
            if manager is not self.manager or self._fourtop_closing:
                return  # The host changed while this was in flight; its rows are not ours.
            self.snapshot_data = snapshot
            self.stale = False
            self.render_rows()
        except (FourtopError, OSError, ValueError) as exc:
            if manager is not self.manager or self._fourtop_closing:
                return
            # Keep the previous view; a failed source is not an empty machine.
            self.stale = True
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
        if self._full_keys is not None:
            rows = [row for row in rows if row.key in self._full_keys]
        elif terms:
            rows = [row for row in rows if all(term in "\n".join(
                (row.title, row.cwd, row.agent, row.key)).casefold() for term in terms)]
        self.shown = rows
        keys = [row.key for row in rows]
        table = self.query_one("#table", DataTable)
        rebuild = keys != self._keys
        if rebuild:
            table.clear()
            self._cell_values.clear()
            self._row_signatures.clear()
            self._derived.clear()
        # Deriving a label or a project name for every row on every tick dominates the
        # refresh cost of a large store, so derived values are cached until the row,
        # or the coarse age bucket, changes.
        now = datetime.now(timezone.utc)
        seconds = int(now.timestamp())
        recent_cutoff = (now - timedelta(hours=1)).isoformat()
        for row in rows:
            bucket = seconds if row.last > recent_cutoff else seconds // 60
            cached = self._derived.get(row.key)
            if (cached is None or cached[0] != row.last or cached[1] != bucket
                    or cached[2] != row.cwd):
                cached = self._derived[row.key] = (
                    row.last, bucket, row.cwd, age(row.last),
                    Path(row.cwd).name or row.cwd or "unknown")
            values = {"agent": row.agent, "project": cached[4], "age": cached[3],
                      "title": row.title or "(untitled)"}
            signature = (row.agent, row.cwd, row.title, row.status, cached[3])
            if not rebuild and self._row_signatures.get(row.key) == signature:
                continue
            cells = [plain(values[column]) for column, _, _ in self._columns]
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
            index = keys.index(self.selected_key) if self.selected_key in keys else min(table.cursor_row, len(keys) - 1)
            table.move_cursor(row=max(0, index), animate=False)
            self.selected_key = keys[max(0, index)]
        scope = "FULL SEARCH" if self._full_keys is not None else "REMOTE" if self.manager.remote else "LOCAL"
        total = len(self.snapshot_data.rows)
        label = f"{total} sessions · {self.snapshot_data.scope} · {scope} · {len(rows)} shown"
        if self.stale:
            label += " · STALE"
        if self.manager.demo:
            label = "SYNTHETIC DEMO · no real data or processes  /  " + label
        self.query_one("#counts", Static).update(plain(label))
        self.show_selection()
        issues = self._full_issues + self.snapshot_data.issues
        message = self._status_message or " · ".join(issues[:2])
        if self._full_running:
            message = "Searching decoded raw records… Esc cancels. The table stays usable."
        elif not rows and not terms:
            message = "No sessions found. Press n to start an agent, or try 4top --demo. " + message
        self.query_one("#status", Static).update(plain(message))

    def current(self) -> Session | None:
        table = self.query_one("#table", DataTable)
        index = table.cursor_row
        return self.shown[index] if 0 <= index < len(self.shown) else None

    def show_selection(self):
        row = self.current()
        if not row:
            self.query_one("#selection", Static).update("No selection. Clear the search or press r.")
            return
        verb = ("preview" if self.manager.demo else
                f"resume on {row.host}" if self.manager.remote else
                "resume" if row.can_resume else "details")
        info = f"{row.agent} · {row.cwd}\nEnter: {verb}"
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

    def action_refresh(self):
        self._status_message = ""
        if not self.manager.remote:
            self.run_worker(self.refresh_history())
        self.run_worker(self.refresh_rows())

    def action_switch_host(self):
        if self.manager.demo:
            self.set_status("DEMO is a single synthetic view; it has no hosts.")
            return
        hosts = getattr(self.manager.config, "hosts", {}) or {}
        if not hosts:
            self.set_status("No [hosts.NAME] entries are configured. See docs/remote-design.md.")
            return
        choices = [("", "local · this machine")]
        choices += [(name, f"{name} · {host.ssh}") for name, host in sorted(hosts.items())]
        current = self.manager.scope if self.manager.remote else ""
        self.push_screen(HostPicker(current, choices), self._switch_to)

    def _switch_to(self, name):
        """Replace the whole view with another machine's. Rows, selection and any
        running search belong to the old scope and are dropped."""
        if name is None:
            return
        config = self.manager.config
        target = config.resolve_host(name or None)
        if (target is not None) == self.manager.remote:
            return  # Already looking at that scope.
        previous, self.manager = self.manager, Manager(config, target)
        previous.close()
        # Let the new scope refresh immediately instead of waiting for the tick that
        # the previous scope's request would otherwise block.
        self._refreshing = self._history_loading = False
        self.snapshot_data = Snapshot([])
        self.shown = []
        self.selected_key = None
        self.stale = False
        self._status_message = ""
        self._full_cancel.set()
        self._full_keys, self._full_issues = None, []
        query = self.query_one("#query", Input)
        query.value = ""
        query.display = False
        self.query_one("#table", DataTable).focus()
        self.set_status(f"Switching to {self.manager.scope}…")
        if not self.manager.remote:
            self.run_worker(self.refresh_history())
        self.run_worker(self.refresh_rows())

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
            self.set_status("DEMO is read-only. No agent will be started.")
        elif self.manager.remote:
            self.set_status(f"Start agents on {self.manager.scope} with "
                            f"`4top --host {self.manager.scope} new AGENT`.")
        elif self._launching:
            self.set_status("A launch is already in progress in this panel.")
        else:
            self.push_screen(NewAgent(os.getcwd()), self._new_result)

    def _new_result(self, values):
        if values:
            self.run_worker(self._start(values))

    async def _start(self, values):
        self._launching = True
        try:
            plan = await asyncio.to_thread(self.manager.new, *values)
        except (FourtopError, OSError, ValueError) as exc:
            self._launching = False
            self.push_screen(NewAgent(values[1], clean_text(str(exc)), values), self._new_result)
            return
        self._launching = False
        self._hand_over(lambda: self.manager.run(plan), f"Starting {plan.agent} in this terminal…",
                        f"Run `4top new {plan.agent}` instead.")

    def action_details(self):
        row = self.current()
        if row:
            self.push_screen(Details(row, self.manager.demo), lambda action: self._detail_action(row, action))

    def _detail_action(self, row, action):
        if action != "resume" or self.manager.demo:
            return
        self.run_worker(self._prepare_resume(row))

    async def open_current(self):
        row = self.current()
        if not row:
            return
        if self.manager.demo:
            self.action_preview()
        elif row.can_resume:
            await self._prepare_resume(row)
        else:
            self.action_details()

    async def _prepare_resume(self, row: Session):
        """Ask the machine that owns the session whether a resume is possible there.

        A refusal now is visible in the panel. The same refusal raised during the
        terminal hand-over flashes past under a panel that repaints immediately
        afterwards, which reads as "nothing happened".
        """
        if self._launching:
            self.set_status("A launch is already in progress in this panel.")
            return
        self._launching = True
        try:
            report = await asyncio.to_thread(self.manager.check, row.key)
        except (FourtopError, OSError, ValueError) as exc:
            self.set_status(f"Cannot check {row.key}: {exc}")
            return
        finally:
            self._launching = False
        if report["resumable"] is None:
            # An older remote cannot answer; say so and let the hand-over decide.
            self.set_status(f"{report['reason']}; resuming without a preflight.")
            self._confirm_resume(row)
            return
        if report["resumable"]:
            self._confirm_resume(row)
            return
        if report.get("cwd_missing"):
            self.push_screen(DirectoryPrompt(row.cwd, os.getcwd(), validate=not self.manager.remote),
                             lambda answer: self.run_worker(self._resume(row, answer)) if answer else None)
            return
        self.set_status(f"{row.agent} on {self.manager.scope}: {report['reason']}")

    def _confirm_resume(self, row: Session, cwd: str | None = None):
        if self._launching:
            self.set_status("A launch is already in progress in this panel.")
            return
        if self.manager.remote:
            message = (f"This runs on {row.host} through ssh and creates a NEW {row.agent} process there.\n"
                       f"Directory: {cwd or row.cwd}\nSession: {row.key}\n"
                       "The remote CLI uses its current native configuration.")
            self.push_screen(Confirm("Resume on " + row.host, message),
                             lambda answer: self.run_worker(self._resume(row)) if answer else None)
            return
        native = row.record.native_id if row.record else None
        message = (f"This creates a NEW {row.agent} process.\nDirectory: {cwd or row.cwd}\n"
                   f"Session: {row.key}\nNative ID: {native or 'exact source path'}\n"
                   "Current native configuration applies; original launch flags are not replayed.\n"
                   "Native execution may modify files or incur model costs.")
        self.push_screen(Confirm("Resume session", message),
                         lambda answer: self.run_worker(self._resume(row, cwd)) if answer else None)

    async def _resume(self, row: Session, cwd: str | None = None):
        self._launching = True
        try:
            if self.manager.remote:
                remote = ["resume", row.key, "--yes"] + (["--cwd", cwd] if cwd else [])
                command = self.manager.remote_argv(remote)
                self._launching = False
                self._hand_over(lambda: subprocess.call(command), f"Resuming on {row.host}…",
                                f"Run `4top --host {row.host} resume {row.key}` instead.", remote=True)
                return
            plan = await asyncio.to_thread(self.manager.resume, row.key, cwd)
        except (FourtopError, OSError, ValueError) as exc:
            self._launching = False
            self.set_status(str(exc))
            return
        self._launching = False
        self._hand_over(lambda: self.manager.run(plan), f"Resuming {plan.agent}…",
                        f"Run `4top resume {row.key}` instead.")

    def _hand_over(self, action, message: str, fallback: str = "", remote: bool = False):
        self.set_status(message)
        self._save_view()
        started, code = time.monotonic(), None
        try:
            with self.suspend():
                code = action()
        except SuspendNotSupported:
            self.set_status(f"This terminal cannot hand over control. {fallback}".strip())
        except (FourtopError, OSError, ValueError) as exc:
            self.set_status(str(exc))
        # A command that fails while the panel is suspended prints under a screen that
        # is repainted immediately, so the failure has to be reported here or not at all.
        if code and (remote or time.monotonic() - started < 2.0):
            self.set_status(self._exit_note(int(code), remote))
        self.run_worker(self.refresh_rows())

    @staticmethod
    def _exit_note(code: int, remote: bool) -> str:
        if remote and code == 255:
            return ("ssh closed the connection (255). The session is unchanged in its transcript "
                    "on that host: resume it again when the link is back.")
        if remote:
            return f"the remote command exited {code}; the session is unchanged in its transcript."
        return f"the agent exited {code}."

    def action_help(self):
        self.push_screen(Confirm("4top keys & safety", "\n".join((
            "↑ / ↓: select   Enter: review native resume   /: search metadata",
            "Ctrl-F: explicit full-content search   Space: read-only preview   i: details",
            "n: new agent   H: switch host   r: refresh   q / Ctrl-C: close only the panel",
            "Esc: close dialog, cancel full search or clear the query",
            "Resume always starts a new process from the transcript.",
            "Deep search reads only approved local sources and executes nothing.",
            "No automatic restart, model calls, or telemetry.",
        ))))

    def _save_view(self):
        # A remote key belongs to another machine's history, so the local view keeps
        # only the local selection.
        if not self.manager.demo and not self.manager.remote:
            with contextlib.suppress(OSError, FourtopError):
                self.manager.store.save_view(self.selected_key)

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
