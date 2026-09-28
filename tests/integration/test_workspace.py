"""The tmux layout against a real tmux server on a private socket."""
import os
import shutil
import subprocess
import time
import uuid

import pytest

from fourtop.workspace import Workspace

pytestmark = pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux is not installed")


@pytest.fixture
def layout():
    env = {**os.environ, "FOURTOP_TMUX_SOCKET": f"4top-test-{uuid.uuid4().hex[:8]}"}
    tmux = ["tmux", "-L", env["FOURTOP_TMUX_SOCKET"]]
    subprocess.run([*tmux, "-f", "/dev/null", "new-session", "-d", "-s", "4top", "-x", "160",
                    "-y", "40", "sleep 600"], check=True, env=env)
    panel = subprocess.run([*tmux, "list-panes", "-F", "#{pane_id}"], capture_output=True,
                           text=True, env=env).stdout.strip()
    workspace = Workspace("tmux", panel, env)
    workspace.ensure_layout(160)
    yield workspace, tmux, env
    subprocess.run([*tmux, "kill-server"], env=env, capture_output=True)


def stage_key(tmux, env, panel):
    out = subprocess.run([*tmux, "list-panes", "-t", panel, "-F", "#{pane_id}\t#{@fourtop-key}"],
                         capture_output=True, text=True, env=env).stdout
    return [line.split("\t")[1] for line in out.splitlines() if not line.startswith(panel + "\t")][0]


def test_sessions_open_beside_the_list_and_keep_running(layout, tmp_path):
    workspace, tmux, env = layout
    first = workspace.open("local:a", ["sh", "-c", "sleep 600"], str(tmp_path), {}, "a")
    assert stage_key(tmux, env, workspace.panel) == "local:a"
    workspace.open("local:b", ["sh", "-c", "sleep 600"], str(tmp_path), {}, "b")
    assert stage_key(tmux, env, workspace.panel) == "local:b"
    assert set(workspace.panes()) == {"local:a", "local:b"}, "a keeps running in the background"
    assert workspace.open("local:a", [], str(tmp_path), {}, "a") == first, "shown, not restarted"
    assert stage_key(tmux, env, workspace.panel) == "local:a"


def test_an_exited_agent_is_reaped_and_the_stage_cleared(layout, tmp_path):
    workspace, tmux, env = layout
    workspace.open("venus:x", ["sh", "-c", "exit 3"], str(tmp_path), {}, "x")
    deadline = time.monotonic() + 5
    while not any(pane.dead for pane in workspace.panes().values()) and time.monotonic() < deadline:
        time.sleep(.05)
    ended = workspace.reap()
    assert [(pane.key, pane.status) for pane in ended] == [("venus:x", 3)]
    assert workspace.panes() == {}
    assert stage_key(tmux, env, workspace.panel) == "", "the placeholder is back"


def test_the_environment_reaches_the_agent(layout, tmp_path):
    workspace, tmux, env = layout
    out = tmp_path / "env.txt"
    workspace.open("local:e", ["sh", "-c", f'echo "$CODEX_HOME" > {out}; sleep 600'],
                   str(tmp_path), {**os.environ, "CODEX_HOME": "/custom/codex"}, "e")
    deadline = time.monotonic() + 5
    while not out.exists() and time.monotonic() < deadline:
        time.sleep(.05)
    assert out.read_text().strip() == "/custom/codex"


def test_a_layout_that_outlived_its_list_gets_the_list_back(layout, tmp_path):
    from fourtop.workspace import revive
    workspace, tmux, env = layout
    workspace.open("local:a", ["sh", "-c", "sleep 600"], str(tmp_path), {}, "a")
    subprocess.run([*tmux, "respawn-pane", "-k", "-t", workspace.panel, "true"], env=env)
    deadline = time.monotonic() + 5

    def dead():
        return subprocess.run([*tmux, "display", "-p", "-t", workspace.panel, "#{pane_dead}"],
                              capture_output=True, text=True, env=env).stdout.strip()
    while dead() != "1" and time.monotonic() < deadline:
        time.sleep(.05)
    assert dead() == "1", "the list exited and its pane was kept"
    revive("tmux", env["FOURTOP_TMUX_SOCKET"], ["sh", "-c", "sleep 600"], env)
    assert dead() == "0", "the list runs again in the same place"
    assert "local:a" in workspace.panes(), "the agent was never touched"


def test_placeholders_never_pile_up(layout, tmp_path):
    workspace, tmux, env = layout
    workspace.open("local:a", ["sh", "-c", "sleep 600"], str(tmp_path), {}, "a")
    # The agent on the stage is killed from outside: its window's placeholder waits.
    subprocess.run([*tmux, "kill-pane", "-t", workspace.panes()["local:a"].id], env=env)
    workspace.ensure_layout(160)
    workspace.ensure_layout(160)

    def placeholders():
        out = subprocess.run([*tmux, "list-panes", "-a", "-F", "#{@fourtop-stage}"],
                             capture_output=True, text=True, env=env).stdout
        return out.split().count("1")
    assert placeholders() == 1
    assert stage_key(tmux, env, workspace.panel) == ""


def test_fitting_the_layout_leaves_a_zoomed_list_zoomed(layout):
    # Zooming the list to read a preview resizes the panel, whose resize handler
    # fits the layout; in tmux any pane resize unzooms, so the zoom undid itself.
    workspace, tmux, env = layout
    workspace.zoom_panel(True)
    workspace.fit(160)

    def zoomed():
        return subprocess.run([*tmux, "display", "-p", "-t", workspace.panel, "#{window_zoomed_flag}"],
                              capture_output=True, text=True, env=env).stdout.strip()
    assert zoomed() == "1"
    workspace.zoom_panel(False)
    assert zoomed() == "0"
