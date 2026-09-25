import json
import os
import stat

import pytest

from fourtop.config import Config
from fourtop.errors import Unavailable
from fourtop.hosts import parse_rows, remote_search, remote_snapshot, ssh_argv
from fourtop.models import ROW_SCHEMA, Session

FAKE_SSH = '''#!{python}
import json, os, sys
mode = os.environ.get("FAKE_SSH_MODE", "rows")
if mode == "argv":
    open(os.environ["FAKE_SSH_ROWS"], "w", encoding="utf-8").write(json.dumps(sys.argv[1:]))
    raise SystemExit(0)
if mode == "fail":
    print("ssh: connect to host venus port 22: Connection refused", file=sys.stderr)
    raise SystemExit(255)
print(open(os.environ["FAKE_SSH_ROWS"], encoding="utf-8").read(), end="")
raise SystemExit(int(os.environ.get("FAKE_SSH_EXIT", "0")))
'''


def payload(**overrides):
    row = {"key": "h_abc", "agent": "pi", "host": "local", "cwd": "/srv/app", "title": "fix retry",
           "started": "2026-09-24T00:00:00+00:00", "last": "2026-09-25T00:00:00+00:00",
           "source": "/remote/.pi/agent/sessions/x/y.jsonl", "status": "available",
           "problems": [], "can_resume": True, "issue": None, "schema_version": ROW_SCHEMA}
    row.update(overrides)
    return json.dumps(row, ensure_ascii=False) + "\n"


@pytest.fixture
def remote(lab):
    """A configured host plus a fake ssh, so no network is involved."""
    binary = lab.path / "bin" / "ssh"
    binary.write_text(FAKE_SSH.format(python=os.sys.executable))
    binary.chmod(0o700)
    rows = lab.path / "rows.jsonl"
    rows.write_text(payload())

    def load(mode="rows", rows_file=None, exit_code=0, path=None):
        lab.config_file.write_text('[hosts.venus]\nssh = "me@venus"\n')
        environment = {**lab.env, "FAKE_SSH_MODE": mode, "FAKE_SSH_EXIT": str(exit_code),
                       "FAKE_SSH_ROWS": str(rows_file or rows)}
        if path is not None:
            environment["PATH"] = path
        config = Config.load(str(lab.config_file), environment=environment)
        return config, config.resolve_host("venus")

    return load, rows


def test_ssh_argv_quotes_the_remote_command_for_a_shell(lab):
    host = lab.config.resolve_host("me@venus")
    argv = ssh_argv(lab.config, host, ["search", "--json", "two words"])
    assert argv[0] == "ssh"
    assert "BatchMode=yes" in argv and "-o" in argv
    assert argv[-2] == "me@venus"
    # One argv element: the remote shell parses the quoting, not the local shell.
    assert argv[-1] == "4top search --json 'two words'"
    assert ssh_argv(lab.config, host, ["list"], tty=True)[-3] == "-t"
    assert ssh_argv(lab.config, host, ["list"])[-1] == "4top list"


def test_parse_rows_relabels_host(remote):
    load, _ = remote
    config, host = load()
    rows = parse_rows(host, payload())
    assert len(rows) == 1
    row = rows[0]
    assert isinstance(row, Session)
    assert row.host == "venus", "a remote row is never filed under the local host"
    assert row.key == "h_abc" and row.cwd == "/srv/app" and row.can_resume


@pytest.mark.parametrize("body,message", [
    (payload(schema_version=1), "schema"),
    ("not json\n", "not JSON"),
    (json.dumps({"key": "h_abc", "schema_version": ROW_SCHEMA}) + "\n", "missing required fields"),
    ("[1, 2]\n", "not an object"),
])
def test_bad_remote_rows_are_refused_not_guessed(lab, body, message):
    host = lab.config.resolve_host("me@venus")
    with pytest.raises(Unavailable, match=message):
        parse_rows(host, body)


def test_remote_snapshot_returns_rows_and_scope(remote):
    load, rows = remote
    rows.write_text(payload() + payload(key="h_def", agent="claude"))
    config, host = load(exit_code=6)  # 6 means "some source reported an issue"
    snapshot = remote_snapshot(config, host)
    assert [row.key for row in snapshot.rows] == ["h_abc", "h_def"]
    assert snapshot.scope == "venus"
    assert all(row.host == "venus" for row in snapshot.rows)


def test_ssh_failure_is_an_explicit_error(remote):
    load, _ = remote
    config, host = load(mode="fail")
    with pytest.raises(Unavailable, match="ssh exit 255"):
        remote_snapshot(config, host)


def test_missing_ssh_binary_is_reported(remote, lab):
    load, _ = remote
    empty = lab.path / "empty"
    empty.mkdir(mode=0o700, exist_ok=True)
    config, host = load(path=str(empty))
    with pytest.raises(Unavailable, match="ssh is not installed"):
        remote_snapshot(config, host)


def test_remote_search_passes_the_query_as_one_argument(remote, lab):
    load, _ = remote
    captured = lab.path / "captured.json"
    config, host = load(mode="argv", rows_file=captured)
    remote_search(config, host, 'retry "database timeout"', full=True)
    argv = json.loads(captured.read_text())
    assert argv[-2] == "me@venus"
    assert argv[-1] == "4top search --json --full 'retry \"database timeout\"'"


def test_host_name_is_used_not_the_ssh_destination(lab):
    config = lab.write_config('[hosts.build-box]\nssh = "root@10.0.0.9"\ncommand = "/opt/4top"\n')
    host = config.resolve_host("build-box")
    assert (host.name, host.ssh, host.command) == ("build-box", "root@10.0.0.9", "/opt/4top")
    argv = ssh_argv(config, host, ["list", "--json"])
    assert "root@10.0.0.9" in argv and argv[-1] == "/opt/4top list --json"


def test_unconfigured_target_is_used_verbatim_as_an_ssh_destination(lab):
    host = lab.config.resolve_host("me@10.0.0.4")
    assert host.ad_hoc and host.ssh == "me@10.0.0.4" and host.command == "4top"
    assert "--host me@10.0.0.4" not in ssh_argv(lab.config, host, ["list"])


def test_remote_preview_and_doctor_use_the_same_transport(remote, lab):
    load, rows = remote
    rows.write_text("page one\n")
    config, host = load()
    from fourtop.hosts import remote_preview
    assert remote_preview(config, host, "h_abc") == "page one\n"
    rows.write_text(json.dumps({"schema_version": 1, "4top": "0.1.0a2", "issues": ["x"]}))
    from fourtop.hosts import remote_doctor
    report = remote_doctor(config, host)
    assert report["host"] == "venus" and report["issues"] == ["x"]


def test_control_path_lives_in_private_state(lab):
    host = lab.config.resolve_host("me@venus")
    argv = ssh_argv(lab.config, host, ["list"])
    control = next(value for value in argv if value.startswith("ControlPath="))
    assert control.endswith("/%C")
    assert str(lab.config.state_dir) in control
    assert stat.S_IMODE((lab.config.state_dir / "ssh").stat().st_mode) == 0o700


@pytest.mark.asyncio
async def test_remote_scope_panel_shows_remote_rows(remote):
    from textual.widgets import DataTable, Static

    from fourtop.app import FourtopApp
    from fourtop.services import Manager

    load, rows = remote
    config, host = load()
    manager = Manager(config, host)
    app = FourtopApp(manager)
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause(3)
        assert app.manager.remote
        assert [row.host for row in app.shown] == ["venus"]
        assert "venus" in str(app.query_one("#counts", Static).render())
        assert app.query_one(DataTable).get_cell("h_abc", "agent").plain == "pi"
        await pilot.press("q")
    manager.close()
