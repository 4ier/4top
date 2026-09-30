"""Agents kept on their host, against real tmux servers on private sockets.

The "device" is a tmux server of its own whose pane runs `4top attach`, the way the
panel's pane runs it over ssh. Killing that server is the dropped link: the client
goes, and the agent must not.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from conftest import eventually

from fourtop.resident import running

pytestmark = pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux is not installed")


@pytest.fixture
def host(lab):
    lab.env["FOURTOP_AGENTS_SOCKET"] = f"4top-agents-test-{uuid.uuid4().hex[:8]}"
    devices = []

    def device(*args):
        """A terminal on another machine: a tmux server whose pane runs 4top there."""
        name = f"4top-device-{uuid.uuid4().hex[:8]}"
        devices.append(name)
        command = [sys.executable, "-m", "fourtop", "--config", str(lab.config_file), *args]
        subprocess.run(["tmux", "-L", name, "-f", "/dev/null", "new-session", "-d", "-x", "120",
                        "-y", "30", "--", *command], check=True, env=lab.env)
        return name

    yield lab, device
    for name in [*devices, lab.env["FOURTOP_AGENTS_SOCKET"]]:
        subprocess.run(["tmux", "-L", name, "kill-server"], env=lab.env, capture_output=True)


def reports(lab):
    folder = lab.path / "reports"
    return [json.loads(path.read_text()) for path in folder.glob("*.json")] if folder.is_dir() else []


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def screen(name, env):
    return subprocess.run(["tmux", "-L", name, "capture-pane", "-p"], capture_output=True,
                          text=True, env=env).stdout


def test_an_attached_agent_outlives_its_link_and_is_attached_again(host):
    lab, device = host
    assert subprocess.run(lab.manager.new("pi", str(lab.path)).argv, cwd=str(lab.path), env=lab.env,
                          stdin=subprocess.DEVNULL, capture_output=True, timeout=30).returncode == 0
    (key,) = [record.key for record in lab.manager.history(force=True).records]
    first = device("attach", key)
    agent = eventually(lambda: [r for r in reports(lab) if "--session" in r["argv"]])[0]
    assert running(lab.env) == {key}, "the tmux session is named after the history key"
    assert json.loads(lab.cli("list", "--json").stdout)["resident"] is True

    subprocess.run(["tmux", "-L", first, "kill-server"], env=lab.env, check=True)  # the link drops
    assert alive(agent["pid"]) and running(lab.env) == {key}, "the agent does not go with it"

    second = device("attach", key)
    eventually(lambda: "COUNT=" in screen(second, lab.env))
    assert [r["pid"] for r in reports(lab) if "--session" in r["argv"]] == [agent["pid"]], \
        "attaching again shows the same process instead of starting another"

    subprocess.run(["tmux", "-L", second, "send-keys", "exit", "Enter"], env=lab.env, check=True)
    eventually(lambda: not alive(agent["pid"]))
    eventually(lambda: running(lab.env) == set())
    assert json.loads(lab.cli("list", "--json").stdout)["resident"] is False


def test_a_new_resident_agent_is_found_by_its_session_key(host):
    lab, device = host
    device("new", "pi", "--cwd", str(lab.path), "--resident")
    agent = eventually(lambda: reports(lab))[0]
    (record,) = eventually(lambda: lab.manager.history(force=True).records)
    assert record.native_id == agent["native_id"]
    assert running(lab.env) == {record.key}, "pi's preallocated id names the session"
    assert agent["cwd"] == str(lab.path)
    canary = hashlib.sha256(lab.env["TEST_CANARY"].encode()).hexdigest()
    assert agent["canary_hash"] == canary, "the environment reaches the kept agent"
    # The server now running was started with the first connection's environment;
    # a later one that differs still reaches its own agent.
    lab.env["TEST_CANARY"] = "a-later-connection"
    device("new", "pi", "--cwd", str(lab.path), "--resident")
    later = eventually(lambda: [r for r in reports(lab) if r["pid"] != agent["pid"]])[0]
    assert later["canary_hash"] == hashlib.sha256(b"a-later-connection").hexdigest()
    assert len(running(lab.env)) == 2


def test_tmux_is_found_where_package_managers_put_it(lab, monkeypatch, tmp_path):
    # ssh runs a command with the login's minimal PATH; Homebrew's tmux is not on it.
    from fourtop import workspace
    place = tmp_path / "brew"
    place.mkdir()
    (place / "tmux").write_text("#!/bin/sh\n")
    (place / "tmux").chmod(0o755)
    monkeypatch.setattr(workspace, "TMUX_PLACES", (str(place),))
    assert workspace.tmux_binary({"PATH": str(tmp_path / "empty")}) == str(place / "tmux")


def test_without_tmux_attach_is_a_plain_resume(lab, monkeypatch):
    from fourtop import workspace
    monkeypatch.setattr(workspace, "TMUX_PLACES", ())
    subprocess.run(lab.manager.new("pi", str(lab.path)).argv, cwd=str(lab.path), env=lab.env,
                   stdin=subprocess.DEVNULL, capture_output=True, timeout=30)
    (key,) = [record.key for record in lab.manager.history(force=True).records]
    lab.config.environment["PATH"] = str(lab.path / "bin")
    plan = lab.manager.attach(key)
    assert plan.argv[0].endswith("/pi") and "--session" in plan.argv


SCREENS = Path(__file__).parents[1] / "fixtures" / "screens"


def kept(lab, device, screen=None):
    """A new resident pi, optionally drawing a captured prompt; its key and report."""
    if screen:
        lab.env["FAKE_SCREEN"] = str(SCREENS / screen)
    device("new", "pi", "--cwd", str(lab.path), "--resident")
    agent = eventually(lambda: reports(lab))[0]
    (record,) = eventually(lambda: lab.manager.history(force=True).records)
    eventually(lambda: running(lab.env) == {record.key})
    return record.key, agent


def test_a_prompt_on_a_kept_agents_screen_needs_the_person_and_is_answered_once(host):
    lab, device = host
    key, agent = kept(lab, device, "claude-bash-permission.txt")
    row = eventually(lambda: [r for r in map(json.loads, lab.cli("list", "--json").stdout.splitlines())
                              if r["attention"]])[0]
    assert row["key"] == key and row["attention"] == "permission" and row["resident"]
    summary = json.loads(lab.cli("list", "--json", "--sync").stdout.splitlines()[-1])["sync"]
    assert summary["attention"] == {key: "permission"}, "carried by the sync trailer"

    peek = lab.cli("peek", key, "--json")
    assert peek.returncode == 0, peek.stderr
    screen = json.loads(peek.stdout)
    assert screen["prompt"] == "claude-permission" and "Do you want to proceed?" in "\n".join(screen["lines"])

    assert lab.cli("approve", key).returncode == 0
    eventually(lambda: [r for r in reports(lab) if r.get("typed") == "1"])
    eventually(lambda: "ANSWERED" in lab.cli("peek", key).stdout)
    again = lab.cli("approve", key)
    assert again.returncode == 4 and "nothing was pressed" in again.stderr, \
        "a stale tap never types into an agent that moved on"
    assert [r["typed"] for r in reports(lab) if r["pid"] == agent["pid"]] == ["1"]


def test_deny_presses_escape_and_send_pastes_then_enters(host):
    lab, device = host
    key, agent = kept(lab, device, "codex-exec-approval.txt")
    assert lab.cli("deny", key[:10]).returncode == 0, "a unique prefix is enough"
    eventually(lambda: [r for r in reports(lab) if r.get("typed") == "\x1b"])
    result = lab.cli("send", key, "--", "-a line that starts with a dash")
    assert result.returncode == 0, result.stderr
    typed = eventually(lambda: [r["typed"] for r in reports(lab) if r.get("typed", "").endswith("\n")])[0]
    assert typed == "\x1b-a line that starts with a dash\n"


def test_a_new_codex_agent_is_found_by_the_key_its_transcript_got(host):
    # Codex cannot be given a session id, so its tmux session starts as new-codex-….
    from datetime import datetime, timezone
    lab, device = host
    lab.env["FAKE_NOW"] = datetime.now(timezone.utc).isoformat()
    device("new", "codex", "--cwd", str(lab.path), "--resident")
    agent = eventually(lambda: reports(lab))[0]
    eventually(lambda: any(name.startswith("new-codex-") for name in running(lab.env)))
    row = json.loads(lab.cli("list", "--json").stdout)
    assert row["resident"] is True and running(lab.env) == {row["key"]}, \
        "the session is renamed after its history key"
    shown = device("attach", row["key"])
    eventually(lambda: "COUNT=" in screen(shown, lab.env))
    assert [r["pid"] for r in reports(lab)] == [agent["pid"]], "attach finds it; no second agent"


def test_talking_to_a_session_without_a_kept_agent_is_refused(host):
    lab, _ = host
    for command in (["peek", "h_0123456789"], ["send", "h_0123456789", "hi"], ["approve", "h_0123456789"]):
        result = lab.cli(*command)
        assert result.returncode == 3 and "runs on this host now" in result.stderr


def test_an_agent_with_no_transcript_yet_has_a_row_and_can_be_answered(host):
    # Claude writes nothing while it waits at its folder-trust dialog: the moment it
    # most needs the person, it would have no row at all.
    lab, device = host
    lab.env["FAKE_SCREEN"] = str(SCREENS / "claude-trust.txt")
    lab.env["FAKE_NO_TRANSCRIPT"] = "1"
    device("new", "pi", "--cwd", str(lab.path), "--prompt", "tidy the tests", "--resident")
    agent = eventually(lambda: reports(lab))[0]
    row = eventually(lambda: [r for r in map(json.loads, lab.cli("list", "--json").stdout.splitlines())
                              if r["resident"]])[0]
    assert row["title"] == "tidy the tests" and row["attention"] and row["cwd"] == str(lab.path)
    assert lab.cli("check", row["key"]).returncode == 0, "opening it attaches"
    assert lab.cli("approve", row["key"]).returncode == 0
    eventually(lambda: [r for r in reports(lab) if r["pid"] == agent["pid"] and r.get("typed")])
