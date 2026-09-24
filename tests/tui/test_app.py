from dataclasses import replace

import pytest
from textual.widgets import DataTable, Input, Static

from fourtop.app import Confirm, Details, FourtopApp, NewRuntime, Preview
from fourtop.services import DemoManager


@pytest.mark.asyncio
async def test_demo_keyboard_search_history_and_preview():
    app = FourtopApp(DemoManager())
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause(.2)
        assert len(app.shown) == 4
        await pilot.press("h")
        assert len(app.shown) == 6
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
        manager.rows.insert(0, replace(manager.rows[0], key="new-demo", run_id="new-demo-run"))
        await app.refresh_runtime()
        assert app.current().key == selected
        assert app.selected_key == selected
        await pilot.resize_terminal(46, 16)
        await pilot.pause()
        assert len(app._columns) == 3
        assert app.current().key == selected
        await pilot.press("q")


@pytest.mark.asyncio
async def test_demo_mutations_are_disabled_and_raw_markup_not_rendered():
    manager = DemoManager()
    manager.rows[0] = replace(manager.rows[0], title="[red]literal[/red]\x1b]52;c;PAYLOAD\x07")
    app = FourtopApp(manager)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause(.2)
        cell = app.query_one(DataTable).get_cell("demo_1", "title")
        assert cell.plain == "[red]literal[/red]"
        await pilot.press("n")
        assert not isinstance(app.screen, NewRuntime)
        assert "read-only" in str(app.query_one("#status", Static).render())
        await pilot.press("i")
        assert isinstance(app.screen, Details)
        assert app.screen.query_one("#terminate").disabled
        await pilot.press("escape", "q")


@pytest.mark.asyncio
async def test_confirmation_defaults_to_cancel():
    app = FourtopApp(DemoManager())
    outcome = []
    async with app.run_test(size=(80,24)) as pilot:
        await pilot.pause(.2)
        app.push_screen(Confirm("Terminate", "Exact target?", destructive=True), outcome.append)
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
    async with app.run_test(size=(100,30)) as pilot:
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
    async with app.run_test(size=(80,24)) as pilot:
        await pilot.pause(.2)
        await pilot.press("slash", "q")
        assert app.query_one("#query", Input).value == "q"
        await pilot.press("escape", "q")
