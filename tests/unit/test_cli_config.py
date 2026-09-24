import json

import pytest

from fourtop.agents import validate_extra
from fourtop.cli import main, parser
from fourtop.config import Config
from fourtop.errors import FourtopError
from fourtop.services import unique


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
    args = parser().parse_args(["--socket", "/tmp/a", "list", "--json"])
    assert args.socket == "/tmp/a"
    args = parser().parse_args(["list", "--socket", "/tmp/b"])
    assert args.socket == "/tmp/b"


def test_demo_never_loads_real_config(monkeypatch, capsys):
    monkeypatch.setattr(Config, "load", lambda *a, **k: (_ for _ in ()).throw(AssertionError("real config")))
    assert main(["--demo", "list", "--json"]) == 0
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert len(rows) == 6 and all(row["source"] == "DEMO" for row in rows)
    assert main(["--demo", "new", "codex", "--detach"]) == 4


def test_non_tty_ui_gives_explicit_error(lab, capsys):
    assert main(["--config", str(lab.config_file)]) == 5
    assert "list --json" in capsys.readouterr().err


def test_empty_list_and_script_confirmation(lab):
    result = lab.cli("list", "--json")
    assert result.returncode == 0 and not result.stdout
    assert lab.cli("resume", "h_missing", "--detach").returncode == 3


def test_unknown_config_options_fail(lab):
    lab.config_file.write_text('[runtime]\ntimeout=5\n')
    with pytest.raises(FourtopError):
        Config.load(str(lab.config_file), environment=lab.env)


@pytest.mark.parametrize("number", ["-1", "0", '"x"', "nan", "inf"])
def test_invalid_budgets_rejected(lab, number):
    lab.config_file.write_text('[runtime]\nstartup_handshake_seconds=' + number + '\n')
    with pytest.raises(FourtopError):
        Config.load(str(lab.config_file), environment=lab.env)


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
