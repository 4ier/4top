"""A sandbox as a host: ssh through its websocket, never woken by refreshing, opened
like any host that keeps agents. Against the fake E2B in tests/fake_e2b.py."""
import json
import time

import pytest
from fake_e2b import KEY, FakeE2B

from fourtop import e2b
from fourtop.errors import Dependency, FourtopError
from fourtop.hosts import ssh_argv
from fourtop.services import Manager


# One task's events as E2B's API returned them on 2026-09-30 (fields trimmed): it ran,
# was paused, resumed, checkpointed by `done`, and killed. The stretch between the
# resume and the checkpoint is in no event's execution.
def lifecycle(kind, at, execution=None):
    data = {"sandbox_metadata": {"fourtop": "1", "fourtop_name": "e2e"}}
    if execution:
        data["execution"] = dict(zip(("execution_time", "started_at", "vcpu_count", "memory_mb"),
                                     (*execution, 2, 2048)))
    return {"type": f"sandbox.lifecycle.{kind}", "timestamp": at, "eventData": data}


E2E = [
    lifecycle("killed", "2026-09-30T00:25:26.312Z", (848, "2026-09-30T00:25:25Z")),
    lifecycle("checkpointed", "2026-09-30T00:25:25.401Z"),
    lifecycle("updated", "2026-09-30T00:25:18.101Z"),
    lifecycle("resumed", "2026-09-30T00:24:59.679909183Z"),
    lifecycle("paused", "2026-09-30T00:24:45.388Z", (90193, "2026-09-30T00:23:15Z")),
    lifecycle("created", "2026-09-30T00:23:15.177692147Z"),
]


@pytest.fixture
def sandbox(lab):
    """A configured sandbox host, paused, behind a fake E2B."""
    fake = FakeE2B(lab.path / "sandboxes")
    record = fake.add({"fourtop": "1", "fourtop_name": "box",
                       "fourtop_deadline": str(int(time.time() + 3600))}, state="paused")
    lab.env.update(FOURTOP_E2B_API=fake.url, E2B_API_KEY=KEY)
    config = lab.write_config(f'[hosts.box]\ne2b = "{record["sandboxID"]}"\n')
    yield Manager(config, config.resolve_host("box")), fake, record
    fake.close()


def test_an_e2b_host_is_ssh_through_the_sandbox_websocket(lab):
    config = lab.write_config('[hosts.box]\ne2b = "abc123"\n')
    host = config.resolve_host("box")
    assert (host.ssh, host.e2b) == ("user@abc123", "abc123")
    argv = ssh_argv(config, host, ["list", "--json"])
    assert "ProxyCommand=websocat --binary -B 65536 - wss://8081-%h.e2b.app" in argv
    assert "StrictHostKeyChecking=accept-new" in argv
    assert "ProxyCommand" not in " ".join(ssh_argv(lab.config, lab.config.resolve_host("me@venus"), []))


@pytest.mark.parametrize("text", ['e2b = "abc"\nssh = "me@venus"', 'e2b = "Not An ID"', "e2b = 3"])
def test_an_e2b_host_takes_one_sandbox_id(lab, text):
    with pytest.raises(FourtopError):
        lab.write_config(f"[hosts.box]\n{text}\n")


def test_refreshing_never_wakes_a_paused_sandbox(sandbox, monkeypatch):
    manager, fake, _ = sandbox
    monkeypatch.setattr("fourtop.services.remote_snapshot",
                        lambda *args: pytest.fail("a paused sandbox was polled"))
    for _ in range(3):
        snapshot = manager.snapshot(True)
    assert snapshot.paused and snapshot.cloud["state"] == "paused"
    assert not fake.asked("POST", "/v2/sandboxes") and not fake.asked("POST", "/sandboxes")
    # The section header's data: what it cost and how long it may still run.
    assert snapshot.cloud["name"] == "box" and 3590 <= snapshot.cloud["lifetime_left"] <= 3600
    assert snapshot.cloud["cost_usd"] == 0 and snapshot.cloud["estimate"]


def test_a_running_sandbox_is_polled_like_any_host(sandbox, monkeypatch):
    manager, fake, record = sandbox
    fake.sandboxes[record["sandboxID"]]["state"] = "running"
    monkeypatch.setattr("fourtop.services.remote_snapshot", lambda *args: e2b_snapshot())
    snapshot = manager.snapshot(True)
    assert snapshot.scope == "box" and snapshot.cloud["state"] == "running" and not snapshot.paused


def e2b_snapshot():
    from fourtop.models import Snapshot
    return Snapshot([], scope="box")


def test_an_action_wakes_the_sandbox_until_its_deadline(sandbox, monkeypatch):
    manager, fake, record = sandbox
    monkeypatch.setattr("fourtop.services.remote_check", lambda *args: {})
    manager.check("h_key")
    timeout = fake.asked("POST", "/v2/sandboxes")[-1][2]["timeout"]
    assert 3590 <= timeout <= 3600 and fake.sandboxes[record["sandboxID"]]["state"] == "running"


def test_opening_a_row_is_the_hosts_own_attach(sandbox):
    """No keeper of the sandbox's own: `4top attach KEY` on the host, as for any host,
    so opening session B can never show agent A."""
    manager, *_ = sandbox
    argv = manager.remote_argv(["attach", "h_b"])
    assert argv[0] == "ssh" and argv[-1] == "4top attach h_b" and "-t" in argv


def test_search_of_a_paused_sandbox_reads_its_last_rows(sandbox, monkeypatch):
    manager, fake, _ = sandbox
    manager.snapshot(True)
    monkeypatch.setattr("fourtop.services.remote_search", lambda *args: pytest.fail("woken to search"))
    result = manager.search("anything")
    assert result.paused and result.rows == [] and not fake.asked("POST", "/v2/sandboxes")


def test_what_a_task_ran_is_rebuilt_from_its_events():
    found = e2b.stretches(E2E)
    assert [round(length, 1) for _, length, _, _ in found] == [90.2, 25.7, 0.8]
    assert found[0][0] == e2b.when("2026-09-30T00:23:15Z") and {f[2:] for f in found} == {(2.0, 2048.0)}
    # Running now, with the resume not yet among the events: from the listing's start.
    start = e2b.when("2026-09-30T00:30:00Z")
    [(begin, length, *_)] = e2b.stretches([], (2, 2048), running_since=start, now=start + 60)
    assert (begin, length) == (start, 60)
    assert sum(length for _, length, _, _ in found) * e2b.rate(2, 2048) == pytest.approx(0.0043, abs=1e-4)


def test_the_api_key_comes_from_the_environment_or_the_e2b_login(tmp_path):
    assert e2b.api_key({"E2B_API_KEY": "e2b_env", "HOME": str(tmp_path)}) == "e2b_env"
    with pytest.raises(Dependency):
        e2b.api_key({"HOME": str(tmp_path)})
    (tmp_path / ".e2b").mkdir()
    (tmp_path / ".e2b/config.json").write_text(json.dumps({"projectApiKey": "e2b_login"}))
    assert e2b.api_key({"HOME": str(tmp_path)}) == "e2b_login"
