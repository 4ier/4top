import json

import pytest

from fourtop import e2b
from fourtop.errors import Dependency, FourtopError
from fourtop.hosts import ssh_argv
from fourtop.services import Manager


@pytest.fixture
def sandbox(lab, monkeypatch):
    """A sandbox host whose E2B state is scripted, and a log of what was asked."""
    calls, state = [], {"now": "paused"}
    monkeypatch.setattr(e2b, "state", lambda config, host: calls.append("state") or state["now"])
    monkeypatch.setattr(e2b, "wake", lambda config, host, seconds=e2b.KEEP_SECONDS: calls.append("wake"))
    config = lab.write_config('[hosts.box]\ne2b = "abc123"\n')
    return Manager(config, config.resolve_host("box")), calls, state


def test_an_e2b_host_is_ssh_through_the_sandbox_websocket(lab):
    config = lab.write_config('[hosts.box]\ne2b = "abc123"\n')
    host = config.resolve_host("box")
    assert (host.ssh, host.e2b) == ("user@abc123", "abc123")
    argv = ssh_argv(config, host, ["list", "--json"])
    assert f"ProxyCommand={e2b.PROXY}" in argv and "StrictHostKeyChecking=accept-new" in argv
    assert "ProxyCommand" not in " ".join(ssh_argv(lab.config, lab.config.resolve_host("me@venus"), []))


@pytest.mark.parametrize("text", ['e2b = "abc"\nssh = "me@venus"', 'e2b = "Not An ID"', "e2b = 3"])
def test_an_e2b_host_takes_one_sandbox_id(lab, text):
    with pytest.raises(FourtopError):
        lab.write_config(f"[hosts.box]\n{text}\n")


def test_refreshing_never_wakes_a_paused_sandbox(sandbox, monkeypatch):
    manager, calls, _ = sandbox
    monkeypatch.setattr("fourtop.services.remote_snapshot",
                        lambda *args: pytest.fail("a paused sandbox was polled"))
    for _ in range(3):
        snapshot = manager.snapshot(True)
    assert snapshot.paused and calls == ["state"] * 3


def test_a_running_sandbox_is_polled_like_any_host(sandbox, monkeypatch):
    manager, calls, state = sandbox
    state["now"] = "running"
    monkeypatch.setattr("fourtop.services.remote_snapshot", lambda *args: "rows")
    assert manager.snapshot(True) == "rows" and calls == ["state"]


def test_an_action_wakes_the_sandbox_first(sandbox, monkeypatch):
    manager, calls, _ = sandbox
    monkeypatch.setattr("fourtop.services.remote_check", lambda *args: calls.append("check") or {})
    manager.check("h_key")
    manager.remote_argv(["resume", "h_key"])
    assert calls == ["wake", "check", "wake"]


def test_search_of_a_paused_sandbox_reads_its_last_rows(sandbox, monkeypatch):
    manager, calls, _ = sandbox
    manager.snapshot(True)
    monkeypatch.setattr("fourtop.services.remote_search", lambda *args: pytest.fail("woken to search"))
    result = manager.search("anything")
    assert result.paused and result.rows == [] and "wake" not in calls


def test_an_open_agent_keeps_its_sandbox_awake_once_a_minute(sandbox):
    manager, calls, _ = sandbox
    manager.keep_awake()
    manager.keep_awake()
    assert calls == ["wake"]


def test_the_api_key_comes_from_the_environment_or_the_e2b_login(tmp_path):
    assert e2b.api_key({"E2B_API_KEY": "e2b_env", "HOME": str(tmp_path)}) == "e2b_env"
    with pytest.raises(Dependency):
        e2b.api_key({"HOME": str(tmp_path)})
    (tmp_path / ".e2b").mkdir()
    (tmp_path / ".e2b/config.json").write_text(json.dumps({"projectApiKey": "e2b_login"}))
    assert e2b.api_key({"HOME": str(tmp_path)}) == "e2b_login"
