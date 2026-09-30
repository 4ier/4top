"""Incremental remote refresh: only changed rows travel, and the merged view is
proven equal to the host's own rows by a digest, or fetched again in full."""
import io
import json
from contextlib import redirect_stdout

import pytest

from fourtop import hosts
from fourtop.cli import _display_sync
from fourtop.config import Host
from fourtop.errors import Unavailable
from fourtop.models import ROW_SCHEMA, Session, Snapshot
from fourtop.sync import SyncState, changed_since, trailer

HOST = Host(name="venus", ssh="me@venus")


def row(key, last, title="work", status="available"):
    return Session(key, "pi", "/srv/app", title, "2026-09-01T00:00:00+00:00", last,
                   status=status)


class FakeHost:
    """Answers `list` the way the real CLI does, through the real `_display_sync`."""

    def __init__(self, rows, syncs=True):
        self.rows, self.syncs, self.calls = list(rows), syncs, []

    def __call__(self, config, host, args):
        self.calls.append(args)
        if "--sync" in args and not self.syncs:
            return 2, "", "4top: error: unrecognized arguments: --sync"
        out = io.StringIO()
        with redirect_stdout(out):
            if "--sync" in args:
                since = args[args.index("--since") + 1] if "--since" in args else ""
                _display_sync(Snapshot(self.rows), since)
            else:
                for item in self.rows:
                    print(json.dumps(item.json()))
        return 0, out.getvalue(), ""


@pytest.fixture
def fake(monkeypatch):
    def install(rows, **kwargs):
        host = FakeHost(rows, **kwargs)
        monkeypatch.setattr(hosts, "run_remote", host)
        return host
    return install


def keys(snapshot):
    return [item.key for item in snapshot.rows]


def test_only_rows_written_since_the_cursor_travel(fake, tmp_path):
    host = fake([row("a", "2026-09-28T10:00:00+00:00"), row("b", "2026-09-27T10:00:00+00:00")])
    sync = SyncState(tmp_path / "venus.json", "me@venus")
    assert keys(hosts.remote_snapshot(None, HOST, sync)) == ["a", "b"]
    assert "--since" not in host.calls[0]

    host.rows[1] = row("b", "2026-09-28T11:00:00+00:00", title="more work")
    snapshot = hosts.remote_snapshot(None, HOST, sync)
    assert host.calls[1][-2:] == ["--since", "2026-09-28T10:00:00+00:00"]
    assert keys(snapshot) == ["b", "a"] and snapshot.rows[0].title == "more work"
    assert len(host.calls) == 2  # no full refetch was needed


def test_an_unchanged_host_sends_nothing_but_its_summary(fake, tmp_path):
    fake([row("a", "2026-09-28T10:00:00+00:00")])
    sync = SyncState(tmp_path / "venus.json", "me@venus")
    hosts.remote_snapshot(None, HOST, sync)
    out = io.StringIO()
    with redirect_stdout(out):
        _display_sync(Snapshot([row("b", "2026-09-27T00:00:00+00:00")]), "2026-09-28T00:00:00+00:00")
    lines = out.getvalue().splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["sync"]["count"] == 1


@pytest.mark.parametrize("change", ["deleted", "status", "older"])
def test_a_change_the_cursor_cannot_see_forces_one_full_fetch(fake, tmp_path, change):
    # A deleted transcript, a directory that went missing, or a row whose `last`
    # moved back never shows up after the cursor. The digest notices instead.
    host = fake([row("a", "2026-09-28T10:00:00+00:00"), row("b", "2026-09-27T10:00:00+00:00")])
    sync = SyncState(tmp_path / "venus.json", "me@venus")
    hosts.remote_snapshot(None, HOST, sync)
    if change == "deleted":
        del host.rows[1]
    elif change == "status":
        host.rows[1] = row("b", "2026-09-27T10:00:00+00:00", status="cwd-missing")
    else:
        host.rows.append(row("c", "2026-01-01T00:00:00+00:00"))
    snapshot = hosts.remote_snapshot(None, HOST, sync)
    assert {item.key: item.status for item in snapshot.rows} == {
        item.key: item.status for item in host.rows}
    assert "--since" in host.calls[1] and "--since" not in host.calls[2]


def test_a_host_that_disagrees_with_itself_is_not_trusted(monkeypatch, tmp_path):
    line = json.dumps(row("a", "2026-09-28T10:00:00+00:00").json())
    bad = json.dumps({"schema_version": ROW_SCHEMA,
                      "sync": {**trailer([]), "count": 1}})
    monkeypatch.setattr(hosts, "run_remote", lambda *a: (0, line + "\n" + bad + "\n", ""))
    with pytest.raises(Unavailable, match="own summary"):
        hosts.remote_snapshot(None, HOST, SyncState(None))


def test_an_older_host_gets_a_plain_listing_and_is_not_asked_again(fake, tmp_path):
    host = fake([row("a", "2026-09-28T10:00:00+00:00")], syncs=False)
    sync = SyncState(tmp_path / "venus.json", "me@venus")
    assert keys(hosts.remote_snapshot(None, HOST, sync)) == ["a"]
    assert keys(hosts.remote_snapshot(None, HOST, sync)) == ["a"]
    assert host.calls == [["list", "--json", "--sync"], ["list", "--json"], ["list", "--json"]]


def test_the_last_rows_are_on_disk_for_the_next_start(fake, tmp_path):
    fake([row("a", "2026-09-28T10:00:00+00:00")])
    cache = tmp_path / "remote" / "venus.json"
    hosts.remote_snapshot(None, HOST, SyncState(cache, "me@venus"))

    again = SyncState(cache, "me@venus")
    assert again.load() and list(again.payloads) == ["a"]
    assert again.cursor == "2026-09-28T10:00:00+00:00"
    # Rows cached for one destination are never shown for another.
    assert not SyncState(cache, "me@mars").load()


def test_equal_timestamps_are_sent_again_rather_than_missed():
    rows = [row("a", "2026-09-28T10:00:00+00:00").json()]
    assert changed_since(rows, "2026-09-28T10:00:00+00:00") == rows
    assert changed_since(rows, "2026-09-28T10:00:00.000001+00:00") == []


def test_the_cli_speaks_sync(lab):
    result = lab.cli("list", "--json", "--sync")
    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout.splitlines()[-1])["sync"]
    assert summary["count"] == 0 and summary["version"] == 1


def test_an_agent_starting_or_exiting_costs_no_full_fetch(fake, tmp_path):
    # A resident agent starts or exits without writing its transcript, so the cursor
    # cannot see it; the trailer's list of running sessions carries the change.
    from dataclasses import replace
    host = fake([row("a", "2026-09-28T10:00:00+00:00"), row("b", "2026-09-27T10:00:00+00:00")])
    sync = SyncState(tmp_path / "venus.json", "me@venus")
    hosts.remote_snapshot(None, HOST, sync)
    assert sync.attach, "a host whose trailer lists its agents has `attach`"
    for running in (True, False):
        host.rows[1] = replace(host.rows[1], resident=running)
        host.calls.clear()
        snapshot = hosts.remote_snapshot(None, HOST, sync)
        assert [item.resident for item in snapshot.rows] == [False, running]
        assert len(host.calls) == 1 and "--since" in host.calls[0], "still incremental"
    again = SyncState(tmp_path / "venus.json", "me@venus")
    assert again.load() and again.attach, "remembered for the next start"


@pytest.mark.parametrize("change", [
    {"attention": "permission"}, {"attention": "question"}, {"label": "the migration"},
    {"muted": True}])
def test_a_prompt_a_name_or_a_mute_costs_no_full_fetch(fake, tmp_path, change):
    # An agent reaching a prompt, or the person naming or muting a session, writes no
    # transcript: the trailer carries these for every row, like `resident`.
    from dataclasses import replace
    host = fake([row("a", "2026-09-28T10:00:00+00:00"), row("b", "2026-09-27T10:00:00+00:00")])
    sync = SyncState(tmp_path / "venus.json", "me@venus")
    hosts.remote_snapshot(None, HOST, sync)
    (field, _), = change.items()
    unchanged = getattr(host.rows[1], field)
    for value in (replace(host.rows[1], **change), host.rows[1]):
        host.rows[1] = value
        host.calls.clear()
        snapshot = hosts.remote_snapshot(None, HOST, sync)
        assert [getattr(item, field) for item in snapshot.rows] == [unchanged, getattr(value, field)]
        assert len(host.calls) == 1 and "--since" in host.calls[0], "still incremental"


def test_a_host_without_these_trailer_fields_keeps_its_rows_as_sent():
    # An older host has no `attention` in its trailer: rows are not reset to defaults.
    from fourtop.sync import apply
    merged = {"a": {"key": "a", "attention": "permission", "label": "x"}}
    apply(merged, {"resident": []})
    assert merged["a"]["attention"] == "permission" and merged["a"]["label"] == "x"
    apply(merged, {"attention": {}, "labels": {"a": "y"}, "muted": ["a"]})
    assert merged["a"] == {"key": "a", "attention": "", "label": "y", "muted": True}


def test_an_unreadable_trailer_field_is_refused(monkeypatch):
    line = json.dumps({"schema_version": ROW_SCHEMA, "sync": {**trailer([]), "attention": ["a"]}})
    monkeypatch.setattr(hosts, "run_remote", lambda *a: (0, line + "\n", ""))
    with pytest.raises(Unavailable, match="unreadable sync summary"):
        hosts.remote_snapshot(None, HOST, SyncState(None))


def test_an_older_host_is_not_asked_to_attach(fake, tmp_path):
    fake([row("a", "2026-09-28T10:00:00+00:00")], syncs=False)
    sync = SyncState(tmp_path / "venus.json", "me@venus")
    hosts.remote_snapshot(None, HOST, sync)
    assert not sync.attach



def test_an_unchanged_refresh_does_not_write_the_cache(fake, tmp_path, monkeypatch):
    fake([row("a", "2026-09-28T10:00:00+00:00"), row("b", "2026-09-27T10:00:00+00:00")])
    sync = SyncState(tmp_path / "venus.json", "me@venus")
    hosts.remote_snapshot(None, HOST, sync)
    saved = []
    monkeypatch.setattr(SyncState, "save", lambda self: saved.append(1))
    hosts.remote_snapshot(None, HOST, sync)
    assert saved == [], "the row at the cursor came again, unchanged"


def test_rows_that_did_not_change_are_not_fingerprinted_again(monkeypatch):
    from fourtop import sync as sync_module
    rows = {"a": row("a", "2026-09-28T10:00:00+00:00").json(), "b": row("b", "2026-09-27T10:00:00+00:00").json()}
    sync = SyncState(None)
    first = sync.fingerprints(rows)
    printed = []
    real = sync_module.fingerprint
    monkeypatch.setattr(sync_module, "fingerprint", lambda p: printed.append(p["key"]) or real(p))
    changed = {**rows, "b": {**rows["b"], "title": "more"}}
    again = sync.fingerprints(changed)
    assert printed == ["b"] and again["a"] == first["a"] and again["b"] != first["b"]
