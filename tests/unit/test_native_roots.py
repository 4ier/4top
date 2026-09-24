"""Native default configuration lookup must not be changed by history discovery."""
import json
from pathlib import Path

import pytest

from fourtop.agents import Drivers
from fourtop.config import ROOT_ENV, Config


@pytest.mark.parametrize("agent", ["claude", "codex", "pi"])
@pytest.mark.parametrize("profile", ["default", "environment", "configured", "explicit-default"])
def test_native_root_override_semantics(lab, agent, profile):
    env = dict(lab.env)
    key = ROOT_ENV[agent]
    env.pop(key, None)
    original = dict(env)
    default = lab.config.root(agent).path
    expected = default
    if profile == "environment":
        env[key] = str(lab.path / "custom-store")
        expected = env[key]
    elif profile in ("configured", "explicit-default"):
        env[key] = str(lab.path / "must-not-win")
        expected = default if profile == "explicit-default" else str(lab.path / "configured-store")
        lab.config_file.write_text(f"[agents.{agent}]\nroot = {json.dumps(expected)}\n")
    before = dict(env)
    config = Config.load(str(lab.config_file), environment=env)
    plan = Drivers(config, lab.manager.store.host_id).plan_new(agent, str(lab.path))
    assert plan.root == expected
    assert env == before, "Loading a profile must not mutate its caller's environment"
    if profile == "default":
        assert key not in plan.environment
        assert config.environment == original
    else:
        assert plan.environment[key] == expected


def test_inherited_relative_home_override_is_expanded(lab):
    env = {**lab.env, "CLAUDE_CONFIG_DIR": "~/custom-claude"}
    config = Config.load(str(lab.config_file), environment=env)
    expected = str(Path.home() / "custom-claude")
    assert config.root("claude").path == expected
    assert config.environment["CLAUDE_CONFIG_DIR"] == expected
