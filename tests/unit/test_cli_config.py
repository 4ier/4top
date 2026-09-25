import json
import subprocess
import sys
from pathlib import Path

import pytest

from fourtop.agents import validate_extra
from fourtop.cli import main, parser
from fourtop.config import Config
from fourtop.errors import FourtopError
from fourtop.services import unique

SRC = Path(__file__).resolve().parents[2] / "src"


@pytest.mark.parametrize("agent,args", [
    ("claude", ("--session-id=bad",)), ("claude", ("-rUUID",)),
    ("codex", ("-C/path",)), ("codex", ("resume",)),
    ("pi", ("--no-session",)), ("pi", ("--session", "bad")),
])
def test_reserved_native_arguments(agent, args):
    with pytest.raises(FourtopError):
        validate_extra(agent, args)


def test_model_and_prompt_are_preserved():
    validate_extra("codex", ("--model", "some-model", "literal; $(do-not-execute)"))


def test_globals_before_and_after_commands():
    args = parser().parse_args(["--host", "venus", "list", "--json"])
    assert args.host == "venus"
    args = parser().parse_args(["list", "--host", "mars"])
    assert args.host == "mars"


def test_demo_never_loads_real_config(monkeypatch, capsys):
    monkeypatch.setattr(Config, "load", lambda *a, **k: (_ for _ in ()).throw(AssertionError("real config")))
    assert main(["--demo", "list", "--json"]) == 0
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert len(rows) == 6 and all(row["source"] == "DEMO" for row in rows)
    assert main(["--demo", "new", "codex"]) == 4


def test_non_tty_ui_gives_explicit_error(lab, capsys):
    assert main(["--config", str(lab.config_file)]) == 5
    assert "list --json" in capsys.readouterr().err


def test_empty_list_and_script_confirmation(lab):
    result = lab.cli("list", "--json")
    assert result.returncode == 0 and not result.stdout
    assert lab.cli("resume", "h_missing", "--yes").returncode == 3


def test_unknown_config_options_fail(lab):
    lab.config_file.write_text('[ui]\ntimeout=5\n')
    with pytest.raises(FourtopError):
        Config.load(str(lab.config_file), environment=lab.env)


@pytest.mark.parametrize("number", ["-1", "0", '"x"', "nan", "inf"])
def test_invalid_budgets_rejected(lab, number):
    lab.config_file.write_text('[history]\nmetadata_max_lines=' + number + '\n')
    with pytest.raises(FourtopError):
        Config.load(str(lab.config_file), environment=lab.env)


def test_hosts_are_configured_and_validated(lab):
    config = lab.write_config('[hosts.venus]\nssh = "me@venus"\ncommand = "/opt/4top/bin/4top"\n'
                              'refresh_seconds = 30\n')
    host = config.resolve_host("venus")
    assert (host.ssh, host.command, host.refresh_seconds) == ("me@venus", "/opt/4top/bin/4top", 30)
    assert config.resolve_host(None) is None
    ad_hoc = config.resolve_host("me@mars")
    assert ad_hoc.ad_hoc and ad_hoc.ssh == "me@mars"
    assert ad_hoc.command == "4top"


@pytest.mark.parametrize("option", [
    'ssh = ""',
    'ssh = "-oProxyCommand=boom"',
    'ssh = "me@venus extra"',
    'command = "4top --json"',
    'ssh = "me@venus"\ntimeout = 1',
])
def test_unsafe_host_options_are_refused(lab, option):
    with pytest.raises(FourtopError):
        lab.write_config("[hosts.venus]\n" + option + "\n")


def test_agent_profiles_do_not_cross(lab):
    report = lab.manager.drivers.probe("claude")
    assert report.allocate_id and report.resume and "fake-claude" in report.version
    plan = lab.manager.drivers.plan_new("claude", str(lab.path))
    assert plan.native_id and plan.history_key
    assert "CLAUDE_CONFIG_DIR" not in plan.environment
    assert "synthetic-canary" not in repr(plan)


def test_unique_keys_reject_ambiguous_prefixes():
    values = ["aaaa-one", "aaaa-two"]
    with pytest.raises(FourtopError):
        unique(values, "aaaa", lambda x: [x])
    assert unique(values, "aaaa-one", lambda x: [x]) == "aaaa-one"


def test_native_subcommands_after_global_options_are_not_new_sessions():
    from fourtop.agents import validate_extra
    from fourtop.errors import FourtopError
    with pytest.raises(FourtopError):
        validate_extra("codex", ("--model", "test-model", "resume", "1234"))


def test_human_table_keeps_every_column(capsys):
    from session_ls.api import utc_now

    from fourtop.cli import _display
    from fourtop.models import Session, Snapshot

    row = Session("h_abcdef1234567890", "claude", "/srv/app", "中文标题 with a trailing tail",
                  "2026-09-24T00:00:00+00:00", utc_now())
    assert _display(Snapshot([row], scope="local")) == 0
    out = capsys.readouterr().out
    for name in ("AGENT", "UPDATED", "PROJECT", "TITLE", "KEY"):
        assert name in out, name
    assert "claude" in out and row.key in out and "/srv/app" in out
    assert "1 sessions · local" in out


def test_issue_text_makes_the_command_exit_six(capsys):
    from fourtop.cli import _display
    from fourtop.models import Snapshot

    assert _display(Snapshot([], ["venus: ssh exit 255"], scope="venus")) == 6
    assert "ssh exit 255" in capsys.readouterr().err


def test_human_table_needs_no_ui_stack(lab):
    # The non-interactive interface must print a table where only the standard
    # library is installed, so it may not reach for the terminal UI stack.
    code = ("import sys; sys.modules['rich'] = None; sys.modules['textual'] = None;"
            f"from fourtop.cli import main; raise SystemExit(main(['--config', r'{lab.config_file}', 'list']))")
    result = subprocess.run([sys.executable, "-c", code], env={**lab.env, "PYTHONPATH": str(SRC)},
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert "AGENT" in result.stdout and "UPDATED" in result.stdout
