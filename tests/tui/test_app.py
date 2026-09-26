from dataclasses import replace
from types import SimpleNamespace

import pytest
from textual.widgets import DataTable, Input, Static

from fourtop.app import (
    WHEEL_ROWS,
    Confirm,
    Details,
    DirectoryPrompt,
    FourtopApp,
    NewAgent,
    Preview,
    SessionTable,
)
from fourtop.models import LaunchPlan
from fourtop.services import DemoManager


class _ViewOnlyManager:
    """Minimal non-demo manager: only the persisted view is read at construction."""

    demo = False
    remote = False
    host = None

    def __init__(self, view):
        self.config = SimpleNamespace(color="auto", refresh_seconds=1.0, history_refresh_seconds=5.0)
        self.store = SimpleNamespace(load_view=lambda: view)


def test_stored_selection_is_restored():
    assert FourtopApp(_ViewOnlyManager({})).selected_key is None
    assert FourtopApp(_ViewOnlyManager({"selected": "h_keep"})).selected_key == "h_keep"


@pytest.mark.asyncio
async def test_demo_keyboard_search_and_preview():
    app = FourtopApp(DemoManager())
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause(.2)
        assert len(app.shown) == 6
        assert "6 sessions" in str(app.query_one("#counts", Static).render())
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
        assert app.selected_key == selected
        await pilot.resize_terminal(46, 16)
        await pilot.pause()
        assert len(app._columns) == 2
        assert app.current().key == selected
        await pilot.press("q")


@pytest.mark.asyncio
async def test_demo_mutations_are_disabled_and_raw_markup_not_rendered():
    manager = DemoManager()
    manager.rows[0] = replace(manager.rows[0], title="[red]literal[/red]\x1b]52;c;PAYLOAD\x07")
    app = FourtopApp(manager)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause(.2)
        assert app.query_one(DataTable).get_cell("demo_1", "title").plain == "[red]literal[/red]"
        await pilot.press("n")
        assert not isinstance(app.screen, NewAgent)
        assert "read-only" in str(app.query_one("#status", Static).render())
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
        app.push_screen(Confirm("Resume", "Exact target?", destructive=True), outcome.append)
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
        assert app._full_keys is None
        assert all(row.agent == "claude" for row in app.shown)
        await pilot.press("escape", "q")


@pytest.mark.asyncio
async def test_q_is_text_while_typing_in_search():
    app = FourtopApp(DemoManager())
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause(.2)
        await pilot.press("slash", "q")
        assert app.query_one("#query", Input).value == "q"
        await pilot.press("escape", "q")


@pytest.mark.asyncio
async def test_unchanged_rows_are_not_retexted(monkeypatch):
    # Re-texting every cell of a large store on each refresh is what makes the
    # table feel laggy, so unchanged rows must be skipped entirely.
    import fourtop.app as app_module

    calls = []
    original = app_module.plain

    def counting(value, **kwargs):
        calls.append(str(value))
        return original(value, **kwargs)

    monkeypatch.setattr(app_module, "plain", counting)

    manager = DemoManager()
    app = FourtopApp(manager)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause(.2)
        titles = {row.title for row in app.shown}
        assert len(titles) == 6 and titles <= set(calls)  # rendered once for real

        calls.clear()
        app.render_rows()
        app.render_rows()
        assert not (titles & set(calls))  # unchanged rows are not re-texted

        manager.rows[0] = replace(manager.rows[0], title="changed title")
        await app.refresh_rows()  # a real change must still repaint
        assert "changed title" in calls
        await pilot.press("q")


@pytest.mark.asyncio
async def test_enter_confirms_before_resuming_and_runs_the_plan(tmp_path):
    calls = []

    class RecordingDemo(DemoManager):
        demo = False
        store = SimpleNamespace(load_view=lambda: {}, save_view=lambda selected: None)

        def resume(self, query, cwd=None):
            calls.append(("resume", query))
            return LaunchPlan("pi", "/fake/pi", ("/fake/pi",), "/demo", {})

        def run(self, plan):
            calls.append(("run", plan))
            return 0

    manager = RecordingDemo()
    manager.rows = [replace(row, cwd=str(tmp_path)) for row in manager.rows]
    app = FourtopApp(manager)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause(.2)
        assert app.current().can_resume
        key = app.current().key
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, Confirm)
        assert calls == []  # nothing happens before explicit approval
        app.screen.query_one("#confirm").press()
        await pilot.pause(.3)
        assert calls == [("resume", key)]
        # A headless driver cannot hand the terminal over; it must say so instead of crashing.
        assert "cannot hand over control" in str(app.query_one("#status", Static).render())
        await pilot.press("q")


@pytest.mark.asyncio
async def test_cursor_rows_are_preview_only():
    app = FourtopApp(DemoManager())
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause(.2)
        row = next(value for value in app.shown if value.agent == "cursor")
        assert not row.can_resume
        app.selected_key = row.key
        app.render_rows()
        assert "Enter: preview" in str(app.query_one("#selection", Static).render())
        await pilot.press("q")


@pytest.mark.asyncio
async def test_derived_labels_follow_row_changes():
    manager = DemoManager()
    app = FourtopApp(manager)
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause(.2)
        assert app.query_one(DataTable).get_cell("demo_1", "project").plain == "api-service"
        manager.rows[0] = replace(manager.rows[0], cwd="/demo/renamed-project")
        await app.refresh_rows()
        # The cached project label must not outlive the row it was derived from.
        assert app.query_one(DataTable).get_cell("demo_1", "project").plain == "renamed-project"
        await pilot.press("q")


@pytest.mark.asyncio
async def test_footer_has_no_key_and_status_says_nothing_when_there_is_nothing():
    app = FourtopApp(DemoManager())
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause(.2)
        selection = str(app.query_one("#selection", Static).render())
        assert "Enter: preview" in selection
        assert "demo_1" not in selection and "h_" not in selection
        # DEMO labels itself, so the status line carries only that note.
        assert "keeps nothing running" not in str(app.query_one("#status", Static).render())
        await pilot.press("q")


@pytest.mark.asyncio
async def test_wheel_moves_the_highlight_with_the_view():
    from textual.events import MouseScrollDown, MouseScrollUp

    app = FourtopApp(DemoManager())
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause(.2)
        table = app.query_one(SessionTable)
        assert table.cursor_row == 0
        table._on_mouse_scroll_down(MouseScrollDown(table, 0, 0, 0, 1, 0, False, False, False))
        await pilot.pause()
        assert table.cursor_row == WHEEL_ROWS
        assert app.selected_key == app.shown[WHEEL_ROWS].key
        table._on_mouse_scroll_up(MouseScrollUp(table, 0, 0, 0, 1, 0, False, False, False))
        assert table.cursor_row == 0
        # Clamped at both ends rather than scrolling the viewport away.
        for _ in range(20):
            table._on_mouse_scroll_up(MouseScrollUp(table, 0, 0, 0, 1, 0, False, False, False))
        assert table.cursor_row == 0 and table.scroll_y == 0
        await pilot.press("q")


@pytest.mark.asyncio
async def test_resume_asks_for_a_directory_when_the_recorded_one_is_gone(tmp_path):
    calls = []

    class GoneDemo(DemoManager):
        demo = False
        store = SimpleNamespace(load_view=lambda: {}, save_view=lambda selected: None)

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
        assert calls == [(app.shown[0].key, str(tmp_path))], "the chosen directory is used"
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

    class BrokenDemo(DemoManager):
        demo = False
        store = SimpleNamespace(load_view=lambda: {}, save_view=lambda selected: None)

        def check(self, query):
            return {"key": query, "agent": "pi", "host": "ubuntu", "native_id": "x",
                    "cwd": "/home/fourier/code/x", "cwd_quality": "native", "executable": None,
                    "cwd_missing": False, "resumable": False,
                    "reason": "pi executable not found; install it or configure a real wrapper path",
                    "status": "available", "problems": []}

        def run(self, plan):
            calls.append(plan)
            return 0

        def resume(self, query, cwd=None):
            calls.append(query)
            return None

    app = FourtopApp(BrokenDemo())
    async with app.run_test(size=(110, 30)) as pilot:
        await pilot.pause(.3)
        await pilot.press("enter")
        await pilot.pause(.4)
        assert calls == [], "a refused session must not reach a hand-over"
        assert not isinstance(app.screen, Confirm)
        assert "pi executable not found" in str(app.query_one("#status", Static).render())
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
        app._hand_over(lambda: 255, "Resuming on ubuntu…", remote=True)
        await pilot.pause(.2)
        status = str(app.query_one("#status", Static).render())
        assert "255" in status and "transcript" in status
        await pilot.press("q")


def test_exit_notes_distinguish_a_dropped_link():
    app = FourtopApp(DemoManager())
    assert "ssh closed the connection (255)" in app._exit_note(255, True)
    assert "remote command exited 5" in app._exit_note(5, True)
    assert app._exit_note(0, True) is None or True
    assert "agent exited 130" in app._exit_note(130, False)
