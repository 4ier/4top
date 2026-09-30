from dataclasses import replace
from types import SimpleNamespace

import pytest
from textual.widgets import Button, Input, OptionList, Select, Static

from fourtop.app import (
    Confirm,
    Details,
    DirectoryPrompt,
    FourtopApp,
    NewAgent,
    Preview,
    ProjectPicker,
    SessionList,
)
from fourtop.models import LaunchPlan
from fourtop.services import DemoManager
from fourtop.workspace import Pane


class _ViewOnlyManager:
    """Minimal non-demo manager: only the persisted view is read at construction."""

    demo = False
    remote = False
    host = None
    scope = "local"

    def __init__(self, view):
        self.config = SimpleNamespace(color="auto", refresh_seconds=1.0, history_refresh_seconds=5.0)
        self.store = SimpleNamespace(load_view=lambda: view)


class LocalDemo(DemoManager):
    """Demo rows behaving like this machine's own history: sessions can be opened."""

    demo = False
    store = SimpleNamespace(load_view=lambda: {}, save_view=lambda selected: None)

    @property
    def scope(self):
        return "local"


def status(app) -> str:
    return str(app.query_one("#status", Static).render())


def test_stored_selection_is_restored():
    assert FourtopApp(_ViewOnlyManager({})).selected_key is None
    assert FourtopApp(_ViewOnlyManager({"selected": "h_keep"})).selected_key == "local:h_keep"


@pytest.mark.asyncio
async def test_demo_keyboard_search_and_preview():
    app = FourtopApp(DemoManager())
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause(.2)
        assert len(app.shown) == 6
        assert "DEMO" in str(app.query_one("#top", Static).render())
        await pilot.press("slash")
        app.query_one("#query", Input).value = "中文"
        await pilot.pause()
        assert len(app.shown) == 1 and app.shown[0].agent == "claude"
        await pilot.press("enter", "space")
        await pilot.pause()
        assert isinstance(app.screen, Preview)
        assert "DEMO" in str(app.screen.query_one("#preview-title", Static).render())
        await pilot.press("escape", "escape", "q")
    assert app.return_value is None


@pytest.mark.asyncio
async def test_selection_survives_refresh_reorder_and_insertion():
    manager = DemoManager()
    app = FourtopApp(manager)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause(.2)
        await pilot.press("down")
        selected = app.current().key
        manager.rows.insert(0, replace(manager.rows[0], key="new-demo"))
        await app.refresh_rows()
        assert app.current().key == selected
        await pilot.resize_terminal(46, 16)
        await pilot.pause()
        assert app.current().key == selected
        await pilot.press("q")


@pytest.mark.asyncio
async def test_demo_mutations_are_disabled_and_raw_markup_not_rendered():
    manager = DemoManager()
    manager.rows[0] = replace(manager.rows[0], title="[red]literal[/red]\x1b]52;c;PAYLOAD\x07")
    app = FourtopApp(manager)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause(.2)
        listing = app.query_one(OptionList)
        prompt = listing.get_option("demo:demo_1").prompt
        assert "[red]literal[/red]" in prompt.plain and "\x1b" not in prompt.plain
        await pilot.press("n")
        assert not isinstance(app.screen, NewAgent)
        assert "read-only" in status(app)
        await pilot.press("i")
        assert isinstance(app.screen, Details)
        assert app.screen.query_one("#resume").disabled
        await pilot.press("escape", "q")


@pytest.mark.asyncio
async def test_confirmation_defaults_to_cancel():
    app = FourtopApp(DemoManager())
    outcome = []
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause(.2)
        app.push_screen(Confirm("Close", "Exact target?", destructive=True), outcome.append)
        await pilot.pause()
        assert app.focused.id == "cancel"
        await pilot.press("enter")
        assert outcome == [False]
        await pilot.press("q")


@pytest.mark.asyncio
async def test_full_search_cancels_without_overwriting_newer_query():
    class SlowDemo(DemoManager):
        def search(self, query, full=False, cancel=None):
            if full:
                cancel.wait(2)
            return super().search(query)

    app = FourtopApp(SlowDemo())
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause(.2)
        await pilot.press("slash")
        app.query_one("#query", Input).value = "codex"
        await pilot.pause()
        await pilot.press("ctrl+f")
        await pilot.pause(.1)
        app.query_one("#query", Input).value = "claude"
        await pilot.pause(.2)
        assert app.sources[0].full_keys is None
        assert all(row.agent == "claude" for row in app.shown)
        await pilot.press("escape", "q")


@pytest.mark.asyncio
async def test_q_is_text_while_typing_in_search():
    app = FourtopApp(DemoManager())
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause(.2)
        await pilot.press("slash", "q", "p")
        assert app.query_one("#query", Input).value == "qp"
        assert not isinstance(app.screen, ProjectPicker)
        await pilot.press("escape", "q")


@pytest.mark.asyncio
async def test_unchanged_rows_are_not_rebuilt():
    # Rebuilding the whole list on every one-second tick is what makes a big store
    # feel laggy; an unchanged view must leave the options alone.
    manager = DemoManager()
    app = FourtopApp(manager)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause(.2)
        listing = app.query_one(OptionList)
        before = listing.get_option("demo:demo_1")
        app.render_list()
        assert listing.get_option("demo:demo_1") is before
        manager.rows[0] = replace(manager.rows[0], title="changed title")
        await app.refresh_rows()
        assert "changed title" in listing.get_option("demo:demo_1").prompt.plain
        await pilot.press("q")


@pytest.mark.asyncio
async def test_enter_opens_at_once_and_runs_the_plan(tmp_path):
    # No confirmation dialog: Enter checks the session with its host, then opens it.
    calls = []

    class Recording(LocalDemo):
        def resume(self, query, cwd=None):
            calls.append(("resume", query))
            return LaunchPlan("pi", "/fake/pi", ("/fake/pi",), str(tmp_path), {})

    manager = Recording()
    manager.rows = [replace(row, cwd=str(tmp_path)) for row in manager.rows]
    app = FourtopApp(manager)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause(.2)
        key = app.current().key
        await pilot.press("enter")
        await pilot.pause(.3)
        assert not isinstance(app.screen, Confirm)
        assert calls == [("resume", key)]
        # A headless driver cannot hand the terminal over; it must say so instead of crashing.
        assert "cannot hand over control" in status(app)
        await pilot.press("q")


class FakeWorkspace:
    def __init__(self):
        self.panes_by_tag, self.calls = {}, []

    def ensure_layout(self, width):
        self.calls.append(("layout", width))

    def fit(self, width):
        pass

    def panes(self):
        return dict(self.panes_by_tag)

    def reap(self):
        return []

    def poll(self):
        ended, self.ending = getattr(self, "ending", []), []
        for pane in ended:
            self.panes_by_tag.pop(pane.key, None)
        return dict(self.panes_by_tag), ended

    def stage(self):
        return "%1"

    def open(self, tag, argv, cwd, env, name):
        self.calls.append(("open", tag, tuple(argv), cwd))
        self.panes_by_tag[tag] = Pane(f"%{len(self.panes_by_tag) + 2}", tag, False, None)

    def show(self, pane):
        self.calls.append(("show", pane))

    def zoom_panel(self, on):
        self.calls.append(("zoom", on))

    def detach(self):
        self.calls.append(("detach",))

    def close_all(self):
        self.calls.append(("close_all",))


@pytest.mark.asyncio
async def test_in_the_layout_a_session_opens_beside_the_list_and_stays_open(tmp_path):
    class Recording(LocalDemo):
        def resume(self, query, cwd=None):
            return LaunchPlan("pi", "/fake/pi", ("/fake/pi", "--session", query), str(tmp_path), {})

    manager = Recording()
    manager.rows = [replace(row, cwd=str(tmp_path)) for row in manager.rows]
    workspace = FakeWorkspace()
    app = FourtopApp(manager, workspace=workspace)
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause(.3)
        tag = f"local:{app.current().key}"
        await pilot.press("enter")
        await pilot.pause(.3)
        assert ("open", tag, ("/fake/pi", "--session", app.current().key), str(tmp_path)) in workspace.calls
        assert tag in app.opened
        assert app.query_one(OptionList).get_option(tag).prompt.plain.startswith("● ")
        # Opening it again shows the running one instead of starting a second.
        await pilot.press("enter")
        await pilot.pause(.2)
        assert [call[0] for call in workspace.calls].count("open") == 1
        assert workspace.calls[-1][0] == "show"
        # q leaves the agents running and detaches; the panel stays alive in tmux.
        await pilot.press("q")
        await pilot.pause()
        assert workspace.calls[-1] == ("detach",)
        assert app.is_running
        await pilot.press("Q")
        await pilot.pause()
        assert isinstance(app.screen, Confirm)
        app.screen.query_one("#confirm").press()
        await pilot.pause()
    assert ("close_all",) in workspace.calls


@pytest.mark.asyncio
async def test_cursor_rows_are_read_only():
    app = FourtopApp(DemoManager())
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause(.2)
        row = next(value for value in app.shown if value.agent == "cursor")
        assert not row.can_resume
        assert "read-only" in app.query_one(OptionList).get_option(f"demo:{row.key}").prompt.plain
        await pilot.press("q")


@pytest.mark.asyncio
async def test_rows_follow_their_changes():
    manager = DemoManager()
    app = FourtopApp(manager)
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause(.2)
        assert "api-service" in app.query_one(OptionList).get_option("demo:demo_1").prompt.plain
        manager.rows[0] = replace(manager.rows[0], cwd="/demo/renamed-project")
        await app.refresh_rows()
        assert "renamed-project" in app.query_one(OptionList).get_option("demo:demo_1").prompt.plain
        await pilot.press("q")


@pytest.mark.asyncio
async def test_status_says_nothing_when_there_is_nothing():
    app = FourtopApp(DemoManager())
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause(.2)
        assert not app.query_one("#status", Static).display
        assert "open" in str(app.query_one("#keys", Static).render())
        await pilot.press("q")


@pytest.mark.asyncio
async def test_wheel_moves_the_highlight_with_the_view():
    from textual.events import MouseScrollDown, MouseScrollUp

    app = FourtopApp(DemoManager())
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause(.2)
        listing = app.query_one(SessionList)
        first = listing.highlighted
        listing._on_mouse_scroll_down(MouseScrollDown(listing, 0, 0, 0, 1, 0, False, False, False))
        await pilot.pause()
        assert listing.highlighted == first + 1
        assert app.selected_key == app._ids[first + 1]
        listing._on_mouse_scroll_up(MouseScrollUp(listing, 0, 0, 0, 1, 0, False, False, False))
        assert listing.highlighted == first
        await pilot.press("q")


@pytest.mark.asyncio
async def test_a_machine_pages_through_its_sessions():
    manager = DemoManager()
    manager.rows = [replace(manager.rows[0], key=f"demo_{i}", title=f"task {i}") for i in range(30)]
    app = FourtopApp(manager)
    async with app.run_test(size=(80, 16)) as pilot:
        await pilot.pause(.2)
        size = app.page_size()
        assert len(app.shown) == size < 30
        first = [row.key for row in app.shown]
        await pilot.press("right_square_bracket")
        await pilot.pause()
        assert [row.key for row in app.shown] == [f"demo_{i}" for i in range(size, 2 * size)]
        assert f"2/{-(-30 // size)}" in app._header(app.sources[0], 30, -(-30 // size), 70).plain
        await pilot.press("left_square_bracket")
        await pilot.pause()
        assert [row.key for row in app.shown] == first
        await pilot.press("q")


@pytest.mark.asyncio
async def test_project_filter_and_folding():
    app = FourtopApp(DemoManager())
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause(.2)
        await pilot.press("p")
        await pilot.pause()
        assert isinstance(app.screen, ProjectPicker)
        app.dismiss_value = None
        app.screen.dismiss("api-service")
        await pilot.pause()
        assert {row.cwd for row in app.shown} == {"/demo/api-service"}
        assert "project api-service" in str(app.query_one("#top", Static).render())
        await pilot.press("escape")
        await pilot.pause()
        assert len(app.shown) == 6
        # f folds the machine under the cursor; its name is not a row Enter can hit.
        assert app.query_one(OptionList).get_option_at_index(0).disabled
        await pilot.press("f")
        await pilot.pause()
        assert app.shown == [] and app.sources[0].collapsed
        await pilot.press("q")


@pytest.mark.asyncio
async def test_resume_asks_for_a_directory_when_the_recorded_one_is_gone(tmp_path):
    calls = []

    class GoneDemo(LocalDemo):
        def check(self, query):
            # What a real host would report for a session whose directory is gone.
            return {"key": query, "agent": "pi", "host": "local", "native_id": "x",
                    "cwd": "/definitely/not/here", "cwd_quality": "native", "executable": "/fake/pi",
                    "cwd_missing": True, "resumable": False,
                    "reason": "the recorded directory does not exist: /definitely/not/here",
                    "status": "available", "problems": []}

        def resume(self, query, cwd=None):
            calls.append((query, cwd))
            return LaunchPlan("pi", "/fake/pi", ("/fake/pi",), cwd or "/gone", {})

    manager = GoneDemo()
    manager.rows = [replace(row, cwd="/definitely/not/here") for row in manager.rows]
    app = FourtopApp(manager)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause(.2)
        key = app.current().key
        await pilot.press("enter")
        await pilot.pause(.2)
        assert isinstance(app.screen, DirectoryPrompt)
        app.screen.query_one("#cwd", Input).value = "/nope"
        app.screen.query_one("#use").press()
        await pilot.pause(.1)
        assert isinstance(app.screen, DirectoryPrompt), "a missing path must not be accepted"
        app.screen.query_one("#cwd", Input).value = str(tmp_path)
        app.screen.query_one("#use").press()
        await pilot.pause(.3)
        assert calls == [(key, str(tmp_path))], "the chosen directory is used"
        await pilot.press("q")


def test_details_marks_a_directory_that_is_gone():
    from fourtop.models import Session

    row = Session("h_x", "pi", "/gone/away", "t", "2026-01-01T00:00:00+00:00",
                  "2026-01-01T00:00:00+00:00")
    assert "(missing)" in "\n".join(Details(row).lines())


@pytest.mark.asyncio
async def test_a_refused_preflight_is_shown_and_nothing_is_started():
    # The failure that used to flash past under a repainted panel.
    calls = []

    class BrokenDemo(LocalDemo):
        def check(self, query):
            return {"key": query, "agent": "pi", "host": "ubuntu", "native_id": "x",
                    "cwd": "/home/fourier/code/x", "cwd_quality": "native", "executable": None,
                    "cwd_missing": False, "resumable": False,
                    "reason": "pi executable not found; install it or configure a real wrapper path",
                    "status": "available", "problems": []}

        def resume(self, query, cwd=None):
            calls.append(query)
            return None

    app = FourtopApp(BrokenDemo())
    async with app.run_test(size=(110, 30)) as pilot:
        await pilot.pause(.3)
        await pilot.press("enter")
        await pilot.pause(.4)
        assert calls == [], "a refused session must not be started"
        assert "pi executable not found" in status(app)
        assert not app.busy
        await pilot.press("q")


@pytest.mark.asyncio
async def test_a_failed_hand_over_reports_its_exit_code():
    from contextlib import contextmanager

    app = FourtopApp(DemoManager())
    async with app.run_test(size=(110, 30)) as pilot:
        await pilot.pause(.2)

        @contextmanager
        def fake_suspend():
            yield  # a headless driver cannot really suspend; run the action anyway

        app.suspend = fake_suspend
        app._hand_over(lambda: 255, "Opening on ubuntu…", remote=True)
        await pilot.pause(.2)
        text = status(app)
        assert "255" in text and "transcript" in text
        await pilot.press("q")


def test_exit_notes_distinguish_a_dropped_link():
    app = FourtopApp(DemoManager())
    assert "ssh closed the connection (255)" in app._exit_note(255, True)
    assert "remote command exited 5" in app._exit_note(5, True)
    assert "agent exited 130" in app._exit_note(130, False)


@pytest.mark.asyncio
async def test_sessions_agents_started_for_themselves_are_hidden_until_asked():
    manager = DemoManager()
    manager.rows[1] = replace(manager.rows[1], subagent=True)
    app = FourtopApp(manager)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause(.2)
        assert manager.rows[1].key not in {row.key for row in app.shown}
        await pilot.press("A")
        await pilot.pause()
        assert manager.rows[1].key in {row.key for row in app.shown}
        assert "with subagents" in str(app.query_one("#top", Static).render())
        await pilot.press("q")


@pytest.mark.asyncio
async def test_a_tap_selects_and_a_second_tap_opens(tmp_path, monkeypatch):
    # On a phone a tap is how you select; opening on the first touch started
    # sessions nobody meant to open. Real clicks, through Textual's dispatch:
    # OptionList's own handler runs too unless prevented, which a direct call to
    # the override never showed.
    import asyncio as _asyncio
    opened = []
    # Termux's doubled event arrives within milliseconds; a loaded CI runner can take
    # longer than the real window between two simulated clicks, so the test widens it.
    monkeypatch.setattr(SessionList, "DUPLICATE_TAP", 1.0)

    class Recording(LocalDemo):
        def resume(self, query, cwd=None):
            opened.append(query)
            return LaunchPlan("pi", "/fake/pi", ("/fake/pi",), str(tmp_path), {})

    manager = Recording()
    manager.rows = [replace(row, cwd=str(tmp_path)) for row in manager.rows]
    app = FourtopApp(manager, workspace=FakeWorkspace())
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause(.2)
        listing = app.query_one(SessionList)
        first = listing.highlighted
        # Row 2 is two lines tall, below the header and the first row.
        offset = (10, 1 + 2 * 2)
        await pilot.click(SessionList, offset=offset)
        await pilot.click(SessionList, offset=offset)  # Termux: one touch, twice
        await pilot.pause(.2)
        assert listing.highlighted != first and opened == []
        await _asyncio.sleep(SessionList.DUPLICATE_TAP + .05)
        await pilot.click(SessionList, offset=offset)
        await pilot.pause(.4)
        assert opened == [app._ids[listing.highlighted].split(":", 1)[1]]
        await pilot.press("Q")
        await pilot.pause()
        if isinstance(app.screen, Confirm):
            app.screen.query_one("#confirm").press()


@pytest.mark.asyncio
async def test_the_list_says_when_the_agent_has_the_keyboard():
    from textual.events import AppBlur, AppFocus

    app = FourtopApp(DemoManager())
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause(.2)
        app.post_message(AppBlur())
        await pilot.pause()
        assert app.screen.has_class("-away")
        assert "typing goes to the agent" in str(app.query_one("#keys", Static).render())
        app.post_message(AppFocus())
        await pilot.pause()
        assert not app.screen.has_class("-away")
        assert "open" in str(app.query_one("#keys", Static).render())
        await pilot.press("q")


@pytest.mark.asyncio
async def test_a_session_written_a_moment_ago_is_marked_active():
    from datetime import datetime, timezone

    manager = DemoManager()
    manager.rows[2] = replace(manager.rows[2], last=datetime.now(timezone.utc).isoformat())
    app = FourtopApp(manager)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause(.2)
        assert "active now" in app.query_one(OptionList).get_option("demo:demo_3").prompt.plain
        await pilot.press("q")


@pytest.mark.asyncio
async def test_rows_say_what_each_session_is_doing_and_was_last_asked():
    from datetime import datetime, timedelta, timezone

    from fourtop.app import state

    app = FourtopApp(DemoManager())
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause(.2)
        text = {key: app.query_one(OptionList).get_option(f"demo:{key}").prompt.plain
                for key in ("demo_1", "demo_2", "demo_4", "demo_5")}
        assert "⟳ working" in text["demo_1"] and "fix/retry-jitter" in text["demo_1"]
        assert "› now add jitter to the backoff" in text["demo_1"], "the latest request, not the first"
        assert "✓ done" in text["demo_2"], "a finished turn is done, not a question"
        assert "✗ stopped" in text["demo_4"], "working, but silent for two hours"
        assert "working" not in text["demo_5"] and "›" not in text["demo_5"]
        await pilot.press("q")
    old = DemoManager().rows[0]
    old = replace(old, last=(datetime.now(timezone.utc) - timedelta(days=3)).isoformat())
    assert state(old) == "", "a days-old session is history, not a task in flight"


@pytest.mark.asyncio
async def test_rows_hold_still_while_the_person_is_acting():
    # Sessions at work jump to the top as they write; a row moving under a finger
    # changes what the tap lands on. A refresh waits until input has paused.
    manager = DemoManager()
    app = FourtopApp(manager)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause(.2)
        before = [row.key for row in app.shown]
        await pilot.press("down")
        manager.rows.insert(0, replace(manager.rows[3], key="jumped"))
        await app.refresh_rows()
        assert [row.key for row in app.shown] == before, "held while acting"
        await pilot.pause(3.3)
        assert app.shown[0].key == "jumped", "and applied once the person is still"
        await pilot.press("q")


@pytest.mark.asyncio
async def test_reading_screens_take_the_whole_window_in_the_layout():
    workspace = FakeWorkspace()
    app = FourtopApp(DemoManager(), workspace=workspace)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause(.2)
        await pilot.press("space")
        await pilot.pause(.2)
        assert isinstance(app.screen, Preview) and ("zoom", True) in workspace.calls
        await pilot.press("escape")
        await pilot.pause(.2)
        assert workspace.calls[-1] == ("zoom", False)
        await pilot.press("Q")
        await pilot.pause()


@pytest.mark.asyncio
async def test_a_host_that_keeps_agents_is_attached_and_an_older_one_resumed(tmp_path):
    class Remote(LocalDemo):
        remote = True
        keeps_agents = True

        def check(self, query):
            return {"key": query, "resumable": True, "cwd_missing": False}

        def remote_argv(self, args):
            return ["ssh", "venus", *args]

    for keeps, verb in ((True, ("attach",)), (False, ("resume",))):
        manager = Remote()
        manager.keeps_agents = keeps
        workspace = FakeWorkspace()
        app = FourtopApp(manager, workspace=workspace)
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause(.3)
            key = app.current().key
            await pilot.press("enter")
            await pilot.pause(.3)
            opened = [call for call in workspace.calls if call[0] == "open"]
            assert opened and opened[0][2][:4] == ("ssh", "venus", *verb, key)
            await pilot.press("q")


@pytest.mark.asyncio
async def test_an_agent_running_on_its_host_is_marked_and_never_called_stopped(tmp_path):
    from datetime import datetime, timedelta, timezone

    from fourtop.app import state

    hour_ago = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    manager = LocalDemo()
    manager.rows[0] = replace(manager.rows[0], activity="working", last=hour_ago)
    assert state(manager.rows[0]) == "✗ stopped", "silent for an hour, as far as the file says"
    manager.rows[0] = replace(manager.rows[0], resident=True)
    assert state(manager.rows[0]) == "⟳ working", "its host says it runs: a long tool call"
    assert state(replace(manager.rows[0], activity="", last=week_ago)) == "running"
    app = FourtopApp(manager)
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause(.3)
        prompt = app.query_one(OptionList).get_option(f"local:{manager.rows[0].key}").prompt.plain
        assert prompt.startswith("○ ")
        await pilot.press("q")


@pytest.mark.asyncio
async def test_a_local_agent_kept_on_this_host_is_attached_not_started_again(tmp_path):
    calls = []

    class Kept(LocalDemo):
        def check(self, query):
            return {"key": query, "resumable": True, "cwd_missing": False}

        def attach(self, query, cwd=None):
            calls.append(("attach", query))
            return LaunchPlan("pi", "/usr/bin/tmux", ("/usr/bin/tmux", "attach"), str(tmp_path), {})

        def resume(self, query, cwd=None):
            calls.append(("resume", query))
            return LaunchPlan("pi", "/fake/pi", ("/fake/pi",), str(tmp_path), {})

    manager = Kept()
    manager.rows = [replace(row, resident=True) for row in manager.rows]
    app = FourtopApp(manager, workspace=FakeWorkspace())
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause(.3)
        await pilot.press("enter")
        await pilot.pause(.3)
        assert calls == [("attach", app.current().key)]
        await pilot.press("q")


@pytest.mark.asyncio
async def test_a_killed_link_to_a_host_that_keeps_agents_is_a_disconnect_not_an_end():
    # ssh exits 255 when the link drops and has no status when Android or Termux
    # kills it; either way the agent keeps running in its host's tmux. Only the
    # agent's own exit, which comes back as its code, ends it.
    class Remote(LocalDemo):
        remote = True
        keeps_agents = True

        @property
        def scope(self):
            return "venus"

    for status, word in ((None, "Disconnected"), (255, "Disconnected"), (0, "Ended")):
        workspace = FakeWorkspace()
        app = FourtopApp(Remote(), workspace=workspace)
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause(.2)
            workspace.ending = [Pane("%9", "venus:demo_1", True, status)]
            await app.poll_workspace()
            assert word in status_text(app), (status, status_text(app))
            await pilot.press("q")


def status_text(app) -> str:
    return str(app.query_one("#status", Static).render())


@pytest.mark.asyncio
async def test_now_puts_what_needs_the_person_first_across_machines():
    from datetime import datetime, timedelta, timezone

    from fourtop.app import FourtopApp

    now = datetime.now(timezone.utc)
    class Ubuntu(LocalDemo):
        scope = "ubuntu"

    here, there = LocalDemo(), Ubuntu()
    old = (now - timedelta(days=30)).isoformat()
    here.rows = [replace(here.rows[0], key="a", activity="waiting", last=now.isoformat()),
                 replace(here.rows[1], key="b", activity="", last=old, title="ancient")]
    there.rows = [replace(there.rows[0], key="c", attention="permission", host="ubuntu",
                          last=(now - timedelta(hours=3)).isoformat()),
                  replace(there.rows[1], key="d", activity="working", host="ubuntu",
                          last=now.isoformat(), muted=True)]
    app = FourtopApp(here, hosts=[there])
    app.view = "now"
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(.3)
        keys = [row.key for row in app.shown]
        assert keys == ["c", "a"], "needs-you first, done next; muted and month-old rows left out"
        headers = [app.query_one(OptionList).get_option(i).prompt.plain for i in app._ids if i.startswith("h:")]
        assert headers[0].startswith("‼ needs you") and headers[1].startswith("✓ done")
        await pilot.press("slash")
        app.query_one("#query", Input).value = "ancient"
        await pilot.pause()
        assert [row.key for row in app.shown] == ["b"], "search reaches the whole history"
        await pilot.press("escape", "g")
        await pilot.pause()
        assert app.view == "machines" and {row.key for row in app.shown} >= {"a", "c"}
        await pilot.press("q")


@pytest.mark.asyncio
async def test_a_finished_session_looked_at_is_no_longer_news():
    from datetime import datetime, timedelta, timezone

    from fourtop.app import FourtopApp

    now = datetime.now(timezone.utc)
    saved = {}
    here = LocalDemo()
    here.store = SimpleNamespace(load_view=lambda: {}, save_view=lambda selected: None,
                                 load_seen=lambda: {}, save_seen=saved.update)
    finished = (now - timedelta(hours=1)).isoformat()
    here.rows = [replace(here.rows[0], key="a", activity="waiting", last=finished)]
    app = FourtopApp(here)
    app.view = "now"
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause(.3)
        headers = lambda: [app.query_one(OptionList).get_option(i).prompt.plain  # noqa: E731
                           for i in app._ids if i.startswith("h:")]
        assert headers()[0].startswith("✓ done")
        source, row = app._row_and_source()
        app.mark_seen(source, row)
        await pilot.pause()
        assert headers()[0].startswith("· this week") and saved == {"local:a": finished}
        # The agent writes again: it is news again.
        here.rows = [replace(row, last=now.isoformat())]
        await app.refresh_rows()
        await pilot.pause()
        assert headers()[0].startswith("✓ done")
        await pilot.press("q")


@pytest.mark.asyncio
async def test_a_task_can_start_in_the_cloud(monkeypatch):
    from types import SimpleNamespace as Namespace

    from fourtop import cloud
    from fourtop.app import Dispatch
    from fourtop.config import Cloud

    started = []
    monkeypatch.setattr(cloud, "tasks", lambda config: ([{"repo": "octo/app"}], []))
    monkeypatch.setattr(cloud, "new", lambda config, request: started.append(request) or {"name": "app"})
    monkeypatch.setattr(cloud, "open_argv", lambda config, name: ["ssh", "sandbox", name])
    manager = LocalDemo()
    manager.config = Namespace(**vars(manager.config), cloud=Cloud(enabled=True))
    workspace = FakeWorkspace()
    app = FourtopApp(manager, workspace=workspace)
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause(.2)
        await pilot.press("n")
        await pilot.pause(.2)
        dialog = app.screen
        assert isinstance(dialog, Dispatch)
        dialog.query_one("#host", Select).value = "cloud"
        await pilot.pause(.3)
        assert dialog.query_one("#cwd", Input).value == "octo/app", "earlier tasks' repositories first"
        dialog.query_one("#prompt", Input).value = "fix the flaky test"
        dialog.query_one("#start", Button).press()
        await pilot.pause(.3)
        assert started == [cloud.Request("claude", "fix the flaky test", "octo/app")]
        assert [list(call[2]) for call in workspace.calls if call[0] == "open"] == [["ssh", "sandbox", "app"]]
        await pilot.press("q")


@pytest.mark.asyncio
async def test_peeking_a_session_with_no_agent_shows_its_latest_messages():
    from fourtop.app import Peek

    app = FourtopApp(LocalDemo())
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause(.2)
        await pilot.press("v")
        await pilot.pause(.5)
        assert isinstance(app.screen, Peek)
        body = str(app.screen.query_one("#peek-body", Static).render())
        assert "its latest messages" in body, body
        await pilot.press("escape", "q")


@pytest.mark.asyncio
async def test_down_from_the_search_box_goes_into_the_results():
    app = FourtopApp(DemoManager())
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause(.2)
        await pilot.press("slash")
        app.query_one("#query", Input).value = "中文"
        await pilot.pause()
        await pilot.press("down")
        await pilot.pause()
        assert app.focused is app.query_one("#list", OptionList)
        await pilot.press("q")
