"""A small, keyboard-first terminal UI. No process policy lives in widget callbacks.

Every configured machine is listed at once, one section each, local first. A
section shows a fixed number of sessions and pages through the rest, so a busy
machine cannot push the others off the screen. Inside 4top's tmux layout, opening
a session shows it beside the list and keeps it running when another is opened;
outside it, the session takes over this terminal until it exits.
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from rich.text import Text
from session_ls.api import clean_text, query_terms
from textual import on
from textual.app import App, ComposeResult, SuspendNotSupported
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, OptionList, Select, Static
from textual.widgets.option_list import Option

from .errors import FourtopError
from .models import Session, Snapshot, age
from .workspace import Workspace


def plain(value, *, multiline=False) -> Text:
    return Text(clean_text(str(value), multiline=multiline))


def project(cwd: str) -> str:
    return Path(cwd).name or cwd or "?"


def first_line(title: str) -> str:
    return " ".join(clean_text(title or "").split())


class Confirm(ModalScreen[bool]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, title: str, message: str, destructive=False, confirm="Confirm"):
        super().__init__()
        self.heading, self.message, self.destructive, self.label = title, message, destructive, confirm

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Static(plain(self.heading), classes="dialog-title")
            with VerticalScroll(classes="dialog-body"):
                yield Static(plain(self.message, multiline=True))
            with Horizontal(classes="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button(self.label, id="confirm", variant="error" if self.destructive else "primary")

    def on_mount(self):
        self.query_one("#cancel", Button).focus()  # Enter is not accidental approval.

    @on(Button.Pressed)
    def pressed(self, event: Button.Pressed):
        self.dismiss(event.button.id == "confirm")

    def action_cancel(self):
        self.dismiss(False)


class Info(ModalScreen[None]):
    BINDINGS = [("escape", "close", "Close"), ("q", "close", "Close"), ("question_mark", "close", "Close")]

    def __init__(self, title: str, message: str):
        super().__init__()
        self.heading, self.message = title, message

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Static(plain(self.heading), classes="dialog-title")
            with VerticalScroll(classes="dialog-body"):
                yield Static(plain(self.message, multiline=True))

    def action_close(self):
        self.dismiss(None)


class NewAgent(ModalScreen[tuple | None]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, cwd: str, error="", values=None, where="this machine"):
        super().__init__()
        self.values = values or ("codex", cwd)
        self.error, self.where = error, where

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Static(plain(f"New coding agent on {self.where}"), classes="dialog-title")
            yield Select([(agent, agent) for agent in ("codex", "claude", "pi")],
                         value=self.values[0], allow_blank=False, id="agent")
            yield Input(value=self.values[1], placeholder="Working directory", id="cwd")
            yield Static(plain(self.error or "Runs the original CLI with its own permissions "
                                             "and authentication."), id="form-error")
            with Horizontal(classes="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Start", id="start", variant="primary")

    @on(Input.Submitted, "#cwd")
    def submitted(self):
        self.query_one("#start", Button).press()

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
    """The latest messages first, like the end of a chat; Earlier pages back."""

    BINDINGS = [("escape", "close", "Close"), ("q", "close", "Close"), ("space", "close", "Close"),
                ("e", "earlier", "Earlier")]

    def __init__(self, manager, row: Session):
        super().__init__()
        self.manager, self.row = manager, row
        self.earlier: int | None = None
        self.parts: list[str] = []
        self.loading = False

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog preview-dialog"):
            yield Static("Loading the latest messages…", id="preview-title", classes="dialog-title")
            with VerticalScroll(id="preview-scroll"):
                yield Static("", id="preview-body")
            with Horizontal(classes="buttons"):
                yield Button("Earlier", id="earlier", disabled=True)
                yield Button("Close", id="close")

    async def on_mount(self):
        await self.load(None)

    async def load(self, before):
        if self.loading:
            return
        self.loading = True
        try:
            title, body, earlier = await asyncio.to_thread(self.manager.preview_tail, self.row, before)
            self.parts.insert(0, body)
            self.earlier = earlier
            self.query_one("#preview-title", Static).update(plain(title))
            self.query_one("#preview-body", Static).update(plain("\n\n".join(self.parts), multiline=True))
            self.query_one("#earlier", Button).disabled = earlier is None
            scroll = self.query_one("#preview-scroll", VerticalScroll)
            if before is None:
                self.call_after_refresh(scroll.scroll_end, animate=False)
            else:
                self.call_after_refresh(scroll.scroll_home, animate=False)
        except (FourtopError, OSError, ValueError) as exc:
            self.query_one("#preview-body", Static).update(plain(str(exc)))
        finally:
            self.loading = False

    @on(Button.Pressed)
    async def pressed(self, event: Button.Pressed):
        if event.button.id == "earlier":
            await self.action_earlier()
        elif event.button.id == "close":
            self.dismiss()

    async def action_earlier(self):
        if self.earlier is not None:
            await self.load(self.earlier)

    def action_close(self):
        self.dismiss()


class Details(ModalScreen[str | None]):
    BINDINGS = [("escape", "close", "Close"), ("i", "close", "Close")]

    def __init__(self, row: Session, demo=False):
        super().__init__()
        self.row, self.demo = row, demo

    def lines(self) -> list[str]:
        """Everything the screen shows. A missing directory is worth naming here:
        resuming from one is impossible, and the reason is not obvious from the path."""
        row = self.row
        directory = Path(row.cwd).expanduser() if row.cwd else None
        missing = "" if row.host != "local" or (directory and directory.is_dir()) else "  (missing)"
        lines = [f"Key: {row.key}", f"Agent: {row.agent}", f"Host: {row.host}",
                 f"Directory: {row.cwd}{missing}", f"Title: {row.title}",
                 f"Started: {row.started}", f"Last written: {row.last}",
                 f"Status: {row.status}", f"Source: {row.source}"]
        lines.extend(f"Problem: {problem}" for problem in row.problems)
        lines.append(row.issue or "")
        lines.append("Opening starts the agent again from its transcript: memory, shell "
                     "children and network state are not restored.")
        return lines

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog preview-dialog"):
            yield Static("Session details", classes="dialog-title")
            with VerticalScroll(classes="dialog-body"):
                yield Static(plain("\n".join(self.lines()), multiline=True))
            with Horizontal(classes="buttons"):
                yield Button("Close", id="close")
                yield Button("Open", id="resume", variant="primary",
                             disabled=self.demo or not self.row.can_resume)

    @on(Button.Pressed)
    def pressed(self, event: Button.Pressed):
        self.dismiss(None if event.button.id == "close" else event.button.id)

    def action_close(self):
        self.dismiss(None)


class ProjectPicker(ModalScreen[str | None]):
    BINDINGS = [("escape", "cancel", "Cancel"), ("p", "cancel", "Cancel")]

    def __init__(self, counts: list[tuple[str, int]], current: str | None):
        super().__init__()
        self.counts, self.current = counts, current

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Static("Show one project", classes="dialog-title")
            options = [Option(Text("All projects", style="bold"), id="")]
            options += [Option(Text.assemble(name, (f"  {count}", "dim")), id=name)
                        for name, count in self.counts]
            yield OptionList(*options, id="projects")

    def on_mount(self):
        projects = self.query_one("#projects", OptionList)
        projects.focus()
        ids = [""] + [name for name, _ in self.counts]
        projects.highlighted = ids.index(self.current) if self.current in ids else 0

    @on(OptionList.OptionSelected, "#projects")
    def chosen(self, event: OptionList.OptionSelected):
        self.dismiss(event.option.id or "")

    def action_cancel(self):
        self.dismiss(None)


class SessionList(OptionList):
    """The list itself. Only the wheel is changed: it moves the highlight, so Enter
    never acts on a row that has scrolled out of view."""

    def _on_mouse_scroll_down(self, event) -> None:
        event.stop()
        self.action_cursor_down()

    def _on_mouse_scroll_up(self, event) -> None:
        event.stop()
        self.action_cursor_up()


@dataclass
class Source:
    """One machine's section of the list."""

    manager: object
    snapshot: Snapshot = field(default_factory=lambda: Snapshot([]))
    loaded: bool = False
    stale: str = ""
    page: int = 0
    collapsed: bool = False
    refreshing: bool = False
    full_keys: set[str] | None = None

    @property
    def name(self) -> str:
        return self.manager.scope


class FourtopApp(App[tuple | None]):
    TITLE = "4top"
    ENABLE_COMMAND_PALETTE = False
    BINDINGS = [
        Binding("q", "quit", "Quit"), Binding("ctrl+c", "quit", "Quit", priority=True),
        Binding("Q", "close_all", "Quit all"),
        Binding("slash", "search", "Search"), Binding("escape", "clear_search", "Clear"),
        Binding("n", "new", "New"), Binding("p", "project", "Project"),
        Binding("space", "preview", "Preview"), Binding("i", "details", "Details"),
        Binding("right_square_bracket", "page(1)", "Next page"),
        Binding("left_square_bracket", "page(-1)", "Previous page"),
        Binding("right", "stage", "Agent"), Binding("f", "fold", "Fold"),
        Binding("a", "subagents", "Subagents"),
        Binding("ctrl+f", "full_search", "Full content"), Binding("r", "refresh", "Refresh"),
        Binding("question_mark", "help", "Help"),
    ]
    CSS = """
    Screen { background: $background; color: $text; }
    #top { height: 1; padding: 0 1; background: $boost; }
    #query { height: 1; border: none; padding: 0 1; margin: 0; display: none; background: $boost; }
    #query:focus { border: none; }
    #list { height: 1fr; border: none; padding: 0; background: $background; scrollbar-size-vertical: 1; }
    #list:focus { border: none; }
    #list > .option-list--option-highlighted { background: $primary 30%; text-style: none; }
    #list:focus > .option-list--option-highlighted { background: $primary 45%; }
    #status { height: auto; max-height: 2; padding: 0 1; color: $warning; }
    #keys { height: 1; padding: 0 1; color: $text-muted; background: $boost; }
    ModalScreen { align: center middle; background: $background 70%; }
    .dialog { width: 76; max-width: 96%; height: auto; max-height: 90%;
              border: round $primary; background: $surface; padding: 1 2; }
    .dialog-title { text-style: bold; margin-bottom: 1; }
    .dialog-body { height: auto; max-height: 18; margin-bottom: 1; }
    .dialog Input, .dialog Select { margin-bottom: 1; }
    .dialog OptionList { height: auto; max-height: 20; }
    .buttons { height: auto; align-horizontal: right; margin-top: 1; }
    .buttons Button { min-width: 10; margin-left: 1; }
    .preview-dialog { width: 100; height: 85%; }
    #preview-scroll { height: 1fr; }
    #form-error { color: $text-muted; }
    """

    def __init__(self, manager, *, no_color=False, hosts=None, workspace: Workspace | None = None):
        from textual.filter import NoColor
        super().__init__(ansi_color=True)
        managers = [manager, *(hosts or [])]
        self.sources = [Source(m) for m in managers]
        self.workspace = workspace
        requested_no_color = no_color or getattr(manager.config, "color", "auto") == "none"
        if requested_no_color and not self.no_color:
            self._filters.append(NoColor())
            self.no_color = True
        self.selected_key = None  # "host:key" of the highlighted row
        # Until the user moves, the highlight follows the top of the list: sections
        # arrive at different times, and the first to arrive is not the first shown.
        self._following_top = True
        self._placed: str | None = None
        self.project_filter: str | None = None
        self.show_subagents = False
        self.update_notice = None
        self.opened: dict[str, object] = {}  # tag -> tmux pane, as tmux reports it
        self.busy: dict[str, str] = {}  # tag -> what is happening to it right now
        self._signature = None
        self._ids: list[str] = []
        self._rows: dict[str, tuple[Source, Session]] = {}
        self._history_loading = False
        self._launching = False
        self._full_cancel = threading.Event()
        self._search_generation = 0
        self._full_running = False
        self._fourtop_closing = False
        self._status_message = ""
        if not manager.demo:
            saved = manager.store.load_view().get("selected")
            self.selected_key = f"local:{saved}" if saved else None

    # ----- compatibility: the first source is "the" manager -------------------------

    @property
    def manager(self):
        return self.sources[0].manager

    @property
    def shown(self) -> list[Session]:
        return [row for _, row in self._rows.values()]

    # ----- layout ----------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Static("", id="top")
        yield Input(placeholder="search titles, directories, agents · Ctrl-F full content · Esc clear",
                    id="query")
        yield SessionList(id="list")
        yield Static("", id="status")
        yield Static("", id="keys")

    async def on_mount(self):
        self.query_one("#list", OptionList).focus()
        if self.workspace:
            with contextlib.suppress(FourtopError):
                await asyncio.to_thread(self.workspace.ensure_layout, self.size.width)
            self.set_interval(1.0, self.poll_workspace)
        for source in self.sources:
            cached = getattr(source.manager, "cached_snapshot", lambda: None)()
            if cached is not None:
                source.snapshot, source.loaded = cached, True
        self.render_list()
        self.set_interval(self.manager.config.refresh_seconds, self.refresh_rows)
        local = self._local()
        if local is not None:
            self.set_interval(self.manager.config.history_refresh_seconds, self.refresh_history)
            self.run_worker(self.refresh_history())
        if not self.manager.demo and getattr(self.manager.config, "update_check", False):
            self.run_worker(self._check_update())
        await self.refresh_rows()

    async def _check_update(self):
        from .update import check
        self.update_notice = await asyncio.to_thread(check, self.manager.config.cache_dir)
        if self.update_notice:
            self._signature = None
            self.render_list()

    def on_resize(self, event):
        if self.is_mounted:
            self._signature = None
            self.render_list()
            if self.workspace:
                self.run_worker(asyncio.to_thread(self.workspace.fit, event.size.width))

    def _local(self) -> Source | None:
        return next((s for s in self.sources if not s.manager.remote and not s.manager.demo), None)

    # ----- data --------------------------------------------------------------------

    async def refresh_history(self):
        local = self._local()
        if local is None or self._history_loading or self._fourtop_closing:
            return
        self._history_loading = True
        try:
            await asyncio.to_thread(local.manager.history)
            await self._refresh(local)
        except (FourtopError, OSError, ValueError) as exc:
            self.set_status("History unavailable: " + str(exc))
        finally:
            self._history_loading = False

    async def refresh_rows(self):
        await asyncio.gather(*(self._refresh(source) for source in self.sources))

    async def _refresh(self, source: Source):
        if source.refreshing or self._fourtop_closing:
            return
        source.refreshing = True
        manager = source.manager
        try:
            snapshot = await asyncio.to_thread(manager.snapshot, False)
            if source not in self.sources or self._fourtop_closing:
                return
            source.snapshot, source.loaded, source.stale = snapshot, True, ""
        except (FourtopError, OSError, ValueError) as exc:
            if self._fourtop_closing:
                return
            # Keep the previous rows; a failed source is not an empty machine.
            source.stale, source.loaded = clean_text(str(exc)), True
        finally:
            source.refreshing = False
        self.render_list()

    async def poll_workspace(self):
        if not self.workspace or self._fourtop_closing:
            return
        try:
            ended = await asyncio.to_thread(self.workspace.reap)
            panes = await asyncio.to_thread(self.workspace.panes)
        except FourtopError as exc:
            self.set_status(str(exc))
            return
        if ended:
            names = ", ".join(self._label(pane.key) for pane in ended)
            self.set_status(f"Ended: {names}. Its transcript is unchanged; Enter opens it again.")
        if set(panes) != set(self.opened):
            self.opened = panes
            self.render_list()

    def _label(self, tag: str) -> str:
        found = self._find(tag)
        return first_line(found[1].title)[:24] if found else tag.split(":", 1)[-1][:12]

    def _find(self, tag: str) -> tuple[Source, Session] | None:
        if tag in self._rows:
            return self._rows[tag]
        host, _, key = tag.partition(":")
        for source in self.sources:
            if source.name == host:
                for row in source.snapshot.rows:
                    if row.key == key:
                        return source, row
        return None

    # ----- rendering ---------------------------------------------------------------

    def _visible(self, source: Source) -> list[Session]:
        terms = query_terms(self.query_one("#query", Input).value, tolerant=True)
        rows = source.snapshot.rows
        if source.full_keys is not None:
            rows = [row for row in rows if row.key in source.full_keys]
        elif terms:
            rows = [row for row in rows if all(term in "\n".join(
                (row.title, row.cwd, row.agent, row.key)).casefold() for term in terms)]
        if self.project_filter:
            rows = [row for row in rows if project(row.cwd) == self.project_filter]
        if self.show_subagents:
            return rows
        return [row for row in rows if not row.subagent]

    def page_sizes(self, visible: dict[int, list[Session]]) -> dict[int, int]:
        """Rows per machine. A machine with few sessions takes only what it needs and
        the rest is shared by the others, so no screen space sits empty."""
        configured = getattr(self.manager.config, "rows_per_host", 0) or 0
        if configured:
            return {index: configured for index in visible}
        height = self.query_one("#list", OptionList).size.height or (self.size.height - 3)
        expanded = [i for i, s in enumerate(self.sources) if not s.collapsed]
        left = max(2, (height - len(self.sources)) // 2)
        sizes = {}
        pending = sorted(expanded, key=lambda i: len(visible[i]))
        while pending:
            share = max(2, left // len(pending))
            index = pending[0]
            if max(1, len(visible[index])) <= share:
                sizes[index] = max(1, len(visible[index]))
                left -= sizes[index]
                pending.pop(0)
                continue
            for index in pending:
                sizes[index] = share
            break
        return {index: sizes.get(index, 2) for index in visible}

    def page_size(self) -> int:
        """Rows per page of the section under the cursor."""
        index = self.sources.index(self.current_source())
        return self.page_sizes({i: self._visible(s) for i, s in enumerate(self.sources)})[index]

    def render_list(self):
        if self._fourtop_closing or not self.is_mounted:
            return
        listing = self.query_one("#list", OptionList)
        width = max(20, (listing.size.width or self.size.width) - 2)
        every = {index: self._visible(source) for index, source in enumerate(self.sources)}
        sizes = self.page_sizes(every)
        options, rows, signature = [], {}, [width, tuple(sizes.items()), tuple(self.opened),
                                            tuple(self.busy.items())]
        for index, source in enumerate(self.sources):
            visible, size = every[index], sizes[index]
            pages = max(1, -(-len(visible) // size))
            source.page = min(source.page, pages - 1)
            header = self._header(source, len(visible), pages, width)
            options.append(Option(header, id=f"h:{index}", disabled=True))
            signature.append((header.plain, source.collapsed))
            if source.collapsed:
                continue
            page = visible[source.page * size:(source.page + 1) * size]
            if not page and source.loaded and not source.snapshot.rows and not source.stale:
                continue  # an empty machine is said by its header ("· 0"); no row needed
            if not page:
                note = ("loading…" if not source.loaded else
                        "no match" if len(source.snapshot.rows) else "no sessions")
                options.append(Option(Text("   " + note, style="dim italic"), id=f"e:{index}",
                                      disabled=True))
                signature.append(note)
            for row in page:
                tag = f"{source.name}:{row.key}"
                rows[tag] = (source, row)
                options.append(Option(self._row(row, tag, width), id=tag))
                signature.append((tag, row.title, row.last, row.cwd, row.agent, row.can_resume))
        signature = tuple(signature)
        self._rows = rows
        if signature != self._signature:
            self._signature = signature
            highlighted = listing.highlighted
            listing.clear_options()
            listing.add_options(options)
            self._ids = [option.id for option in options]
            target = self.selected_key if self.selected_key in self._ids else None
            if target is not None:
                self._following_top = False
            elif self._following_top:
                highlighted = None
            if target is None and highlighted is not None and self._ids:
                target = self._ids[min(highlighted, len(self._ids) - 1)]
                if target.startswith("e:"):
                    target = self._ids[max(0, self._ids.index(target) - 1)]
            if target is None:
                target = next((i for i in self._ids if not i.startswith(("h:", "e:"))), None)
            if target is not None:
                self._placed = target
                listing.highlighted = self._ids.index(target)
        self._render_chrome(width)

    def _header(self, source: Source, count: int, pages: int, width: int) -> Text:
        arrow = "▸" if source.collapsed else "▾"
        left = Text.assemble((f"{arrow} {source.name}", "bold"), (f" · {count}", "dim"))
        if source.stale:
            left.append(" · unreachable", "bold red")
        elif not source.loaded or source.refreshing and not source.snapshot.rows:
            left.append(" · loading", "dim italic")
        elif source.snapshot.cached:
            left.append(" · cached", "dim italic")
        if source.snapshot.issues:
            left.append(" · partial", "yellow")
        right = Text(f"{source.page + 1}/{pages} [ ]" if pages > 1 and not source.collapsed else "",
                     style="dim")
        gap = max(1, width - left.cell_len - right.cell_len)
        return Text.assemble(left, " " * gap, right)

    def _row(self, row: Session, tag: str, width: int) -> Text:
        opened = tag in self.opened
        title = first_line(row.title)
        head = Text()
        head.append("● " if opened else "  ", style="bold green")
        head.append(title or "(untitled)", style="bold" if opened else "" if title else "dim italic")
        head.truncate(width, overflow="ellipsis")
        meta = [row.agent, project(row.cwd), age(row.last)]
        if not row.can_resume:
            meta.append("read-only")
        busy = self.busy.get(tag)
        foot = Text("    ")
        if busy:
            foot.append(busy + " · ", style="bold yellow")
        foot.append(" · ".join(meta), style="dim")
        foot.truncate(width, overflow="ellipsis")
        return Text("\n").join([head, foot])

    def _render_chrome(self, width: int):
        filters = []
        if self.project_filter:
            filters.append(f"project {self.project_filter}")
        query = self.query_one("#query", Input).value.strip()
        if query and not self.query_one("#query", Input).display:
            filters.append(f"“{query}”")
        if any(source.full_keys is not None for source in self.sources):
            filters.append("full content")
        if self.show_subagents:
            filters.append("with subagents")
        top = Text("4top", style="bold")
        if self.manager.demo:
            top.append("  DEMO · synthetic data, no real processes", style="bold yellow")
        if filters:
            top.append("  " + " · ".join(filters) + "  (Esc clears)", style="cyan")
        if self._full_running:
            top.append("  searching…", style="dim italic")
        elif any(s.refreshing and not s.loaded for s in self.sources):
            top.append("  ⟳", style="dim")
        if self.update_notice:
            top.append("  ↑ " + self.update_notice.text(), style="bold magenta")
        top.truncate(width, overflow="ellipsis")
        self.query_one("#top", Static).update(top)
        keys = ["⏎ open", "/ search", "p project", "space preview", "[ ] page", "n new"]
        if self.workspace:
            keys.insert(1, "→ agent")
            keys.append("q detach")
        else:
            keys.append("q quit")
        keys.append("? help")
        line, used = [], 0
        for item in keys:
            if used + len(item) + 3 > width:
                break
            line.append(item)
            used += len(item) + 3
        self.query_one("#keys", Static).update(Text("   ".join(line)))
        message = self._status_message
        if not message:
            issues = [f"{s.name}: {s.stale}" for s in self.sources if s.stale]
            issues += [issue for s in self.sources for issue in s.snapshot.issues][:2]
            message = " · ".join(issues[:2])
        status = self.query_one("#status", Static)
        status.update(plain(message))
        status.display = bool(message)

    def set_status(self, message):
        self._status_message = clean_text(message)
        if self.is_mounted and not self._fourtop_closing:
            status = self.query_one("#status", Static)
            status.update(plain(self._status_message))
            status.display = bool(self._status_message)

    # ----- selection -------------------------------------------------------------

    def _highlighted_id(self) -> str | None:
        index = self.query_one("#list", OptionList).highlighted
        return self._ids[index] if index is not None and 0 <= index < len(self._ids) else None

    def current(self) -> Session | None:
        found = self._rows.get(self._highlighted_id() or "")
        return found[1] if found else None

    def current_source(self) -> Source:
        ident = self._highlighted_id() or ""
        if ident in self._rows:
            return self._rows[ident][0]
        if ident[:2] in ("h:", "e:"):
            return self.sources[int(ident[2:])]
        return self.sources[0]

    @on(OptionList.OptionHighlighted, "#list")
    def highlighted(self, event):
        ident = event.option.id
        if ident == self._placed:
            self._placed = None  # the list placed it there, not the user
            return
        self._following_top = False
        if ident in self._rows:
            self.selected_key = ident

    @on(OptionList.OptionSelected, "#list")
    async def selected(self, event):
        await self.open_current()

    def action_fold(self):
        """Fold or unfold the machine under the cursor (its name stays listed)."""
        source = self.current_source()
        source.collapsed = not source.collapsed
        self._following_top = not any(not s.collapsed and self._visible(s) for s in self.sources)
        self.render_list()

    # ----- search and filters ----------------------------------------------------

    def action_search(self):
        query = self.query_one("#query", Input)
        query.display = True
        query.focus()

    @on(Input.Changed, "#query")
    def search_changed(self):
        self._full_cancel.set()
        self._search_generation += 1
        for source in self.sources:
            source.full_keys = None
            source.page = 0
        self._status_message = ""
        self._following_top, self.selected_key = True, None
        self.render_list()

    @on(Input.Submitted, "#query")
    def search_submitted(self):
        query = self.query_one("#query", Input)
        query.display = False
        self.query_one("#list", OptionList).focus()
        self.render_list()

    def action_clear_search(self):
        query = self.query_one("#query", Input)
        if self._full_running:
            self._full_cancel.set()
            self.set_status("Full-content search cancelled.")
        query.value = ""
        query.display = False
        self.project_filter = None
        for source in self.sources:
            source.full_keys = None
        self._status_message = ""
        self.query_one("#list", OptionList).focus()
        self.render_list()

    def action_subagents(self):
        """Show or hide sessions that agents started for themselves."""
        if isinstance(self.focused, Input):
            return
        self.show_subagents = not self.show_subagents
        for source in self.sources:
            source.page = 0
        self.render_list()

    def action_project(self):
        if isinstance(self.focused, Input):
            return
        counts: dict[str, int] = {}
        for source in self.sources:
            for row in source.snapshot.rows:
                counts[project(row.cwd)] = counts.get(project(row.cwd), 0) + 1
        ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        self.push_screen(ProjectPicker(ranked, self.project_filter), self._project_chosen)

    def _project_chosen(self, name):
        if name is None:
            return
        self.project_filter = name or None
        for source in self.sources:
            source.page = 0
        self.render_list()

    def action_page(self, step: int):
        source = self.current_source()
        source.page = max(0, source.page + step)
        self.selected_key = None
        self.render_list()
        index = self._ids.index(f"h:{self.sources.index(source)}")
        listing = self.query_one("#list", OptionList)
        listing.highlighted = min(index + 1, len(self._ids) - 1)

    def action_refresh(self):
        self._status_message = ""
        if self._local() is not None:
            self.run_worker(self.refresh_history())
        self.run_worker(self.refresh_rows())

    def action_full_search(self):
        query = self.query_one("#query", Input).value
        if not query.strip():
            self.action_search()
            self.set_status("Type a literal query first, then Ctrl-F searches full content.")
            return
        if self._full_running:
            self._full_cancel.set()
            self.set_status("Cancelling the previous full-content scan; press Ctrl-F again when it finishes.")
            return
        self._full_cancel = threading.Event()
        self._search_generation += 1
        self.run_worker(self._do_full_search(query, self._search_generation))

    async def _do_full_search(self, query, generation):
        self._full_running = True
        self.render_list()

        async def one(source):
            try:
                result = await asyncio.to_thread(source.manager.search, query, True, self._full_cancel)
            except (FourtopError, OSError, ValueError) as exc:
                self.set_status(f"{source.name}: full-content search failed: {exc}")
                return
            if generation == self._search_generation and not self._fourtop_closing:
                source.full_keys = {row.key for row in result.rows}
                source.page = 0
        try:
            await asyncio.gather(*(one(source) for source in self.sources))
        finally:
            self._full_running = False
            if not self._fourtop_closing:
                self._signature = None
                self.render_list()

    # ----- actions on a session --------------------------------------------------

    def action_preview(self):
        row = self.current()
        if row:
            self.push_screen(Preview(self.current_source().manager, row))

    def action_details(self):
        row = self.current()
        if row:
            source = self.current_source()
            self.push_screen(Details(row, self.manager.demo),
                             lambda action: self._detail_action(source, row, action))

    def _detail_action(self, source, row, action):
        if action == "resume" and not self.manager.demo:
            self.run_worker(self._open(source, row))

    def action_stage(self):
        if self.workspace and not isinstance(self.focused, Input):
            self.run_worker(asyncio.to_thread(self.workspace.show, self.workspace.stage()))

    async def open_current(self):
        row = self.current()
        if not row:
            return
        source = self.current_source()
        if self.manager.demo:
            self.action_preview()
        elif row.can_resume:
            await self._open(source, row)
        else:
            self.action_details()

    async def _open(self, source: Source, row: Session, cwd: str | None = None):
        """Open a session: show it if it is already running, otherwise ask the machine
        that owns it whether it can resume, and start it. A refusal is shown on the
        row itself, never after the terminal has been handed over."""
        tag = f"{source.name}:{row.key}"
        if tag in self.busy:
            return
        pane = self.opened.get(tag)
        if pane is not None and self.workspace:
            await asyncio.to_thread(self.workspace.show, pane.id)
            return
        if self._launching and not self.workspace:
            self.set_status("A launch is already in progress in this panel.")
            return
        manager = source.manager
        self._busy(tag, "checking")
        try:
            report = await asyncio.to_thread(manager.check, row.key)
        except (FourtopError, OSError, ValueError) as exc:
            self._busy(tag, None)
            self.set_status(f"Cannot check {first_line(row.title)[:30] or row.key}: {exc}")
            return
        if cwd and report.get("cwd_missing"):
            report = {**report, "resumable": True}  # a directory was chosen; the plan checks it
        if report["resumable"] is False:
            self._busy(tag, None)
            if report.get("cwd_missing") and not cwd:
                self.push_screen(
                    DirectoryPrompt(row.cwd, os.getcwd(), validate=not manager.remote),
                    lambda answer: self.run_worker(self._open(source, row, answer)) if answer else None)
            else:
                self.set_status(f"{row.agent} on {source.name}: {report['reason']}")
            return
        self._busy(tag, "starting")
        try:
            if manager.remote:
                remote = ["resume", row.key, "--yes"] + (["--cwd", cwd] if cwd else [])
                argv, directory, env = manager.remote_argv(remote), str(Path.home()), {}
            else:
                plan = await asyncio.to_thread(manager.resume, row.key, cwd)
                argv, directory, env = list(plan.argv), plan.cwd, plan.environment
        except (FourtopError, OSError, ValueError) as exc:
            self._busy(tag, None)
            self.set_status(str(exc))
            return
        name = first_line(row.title)[:24] or row.agent
        await self._launch(tag, argv, directory, env, name, remote=manager.remote)

    async def _launch(self, tag, argv, directory, env, name, remote=False):
        if self.workspace:
            try:
                await asyncio.to_thread(self.workspace.open, tag, argv, directory, env, name)
                self.opened = await asyncio.to_thread(self.workspace.panes)
            except FourtopError as exc:
                self.set_status(str(exc))
            finally:
                self._busy(tag, None)
            return
        self._busy(tag, None)
        self._launching = True
        try:
            self._hand_over(lambda: subprocess.call(argv, cwd=directory, env=env or None),
                            f"Opening {name}…", remote=remote)
        finally:
            self._launching = False

    def _busy(self, tag: str, what: str | None):
        if what is None:
            self.busy.pop(tag, None)
        else:
            self.busy[tag] = what + "…"
        self.render_list()

    def action_new(self):
        source = self.current_source()
        if self.manager.demo:
            self.set_status("DEMO is read-only. No agent will be started.")
            return
        row = self.current()
        cwd = row.cwd if row and source is self.current_source() else os.getcwd()
        if not source.manager.remote and not (row and Path(cwd).is_dir()):
            cwd = os.getcwd()
        self.push_screen(NewAgent(cwd, where=source.name),
                         lambda values: self._new_result(source, values))

    def _new_result(self, source, values):
        if values:
            self.run_worker(self._start(source, values))

    async def _start(self, source, values):
        agent, cwd = values
        manager = source.manager
        try:
            if manager.remote:
                argv = manager.remote_argv(["new", agent, "--cwd", cwd, "--yes"])
                directory, env = str(Path.home()), {}
            else:
                plan = await asyncio.to_thread(manager.new, agent, cwd)
                argv, directory, env = list(plan.argv), plan.cwd, plan.environment
        except (FourtopError, OSError, ValueError) as exc:
            self.push_screen(NewAgent(cwd, clean_text(str(exc)), values, source.name),
                             lambda again: self._new_result(source, again))
            return
        tag = f"{source.name}:new-{int(time.time())}"
        await self._launch(tag, argv, directory, env, f"new {agent}", remote=manager.remote)

    def _hand_over(self, action, message: str, remote: bool = False):
        self.set_status(message)
        self._save_view()
        started, code = time.monotonic(), None
        try:
            with self.suspend():
                code = action()
        except SuspendNotSupported:
            self.set_status("This terminal cannot hand over control; run 4top in a real terminal.")
        except (FourtopError, OSError, ValueError) as exc:
            self.set_status(str(exc))
        else:
            self.set_status("")
        # A command that fails while the panel is suspended prints under a screen that
        # is repainted immediately, so the failure has to be reported here or not at all.
        if code and (remote or time.monotonic() - started < 2.0):
            self.set_status(self._exit_note(int(code), remote))
        self.run_worker(self.refresh_rows())

    @staticmethod
    def _exit_note(code: int, remote: bool) -> str:
        if remote and code == 255:
            return ("ssh closed the connection (255). The session is unchanged in its transcript "
                    "on that host: open it again when the link is back.")
        if remote:
            return f"the remote command exited {code}; the session is unchanged in its transcript."
        return f"the agent exited {code}."

    def action_help(self):
        layout = ("Alt-← / Alt-→ or → : move between the list and the agent.\n"
                  "Opened sessions keep running when you open another; ● marks them.\n"
                  "q detaches: agents keep running, and 4top reattaches.  Q closes everything.\n"
                  if self.workspace else
                  "Enter runs the agent in this terminal; the list returns when it exits.\n"
                  "Install tmux to keep the list beside the agent.\n")
        self.push_screen(Info("4top", "\n".join((
            "↑ ↓  select      Enter  open      Space  preview      i  details",
            "/  search        p  project        [ ]  page within a machine",
            "Ctrl-F  full-content search         n  new agent on the selected machine",
            "f  fold the machine under the cursor.  a  show sessions agents started for",
            "themselves (hidden by default).  r  refresh.  Esc  clear filters.",
            "",
            layout,
            "Opening starts the agent again from its transcript.",
            "4top makes no model calls and sends no telemetry.",
        ))))

    def _save_view(self):
        # A remote key belongs to another machine's history, so only a local selection
        # is kept.
        if self.manager.demo or not self.selected_key or not self.selected_key.startswith("local:"):
            return
        with contextlib.suppress(OSError, FourtopError, AttributeError):
            self.manager.store.save_view(self.selected_key.split(":", 1)[1])

    def action_quit(self):
        if self.workspace and not self._fourtop_closing:
            self._save_view()
            with contextlib.suppress(FourtopError):
                self.workspace.detach()
            return
        self._close_panel()

    def action_close_all(self):
        if not self.workspace:
            self._close_panel()
            return
        count = len(self.opened)
        if not count:
            self._close_panel(close=True)
            return
        self.push_screen(Confirm("Close everything", f"{count} open session(s) will be stopped. "
                                 "Their transcripts are kept and can be opened again.",
                                 destructive=True, confirm="Close all"),
                         lambda yes: self._close_panel(close=True) if yes else None)

    def _close_panel(self, close=False):
        self._save_view()
        self._fourtop_closing = True
        self._full_cancel.set()
        for source in self.sources:
            source.manager.close()
        if close and self.workspace:
            with contextlib.suppress(FourtopError):
                self.workspace.close_all()
        self.exit(None)
