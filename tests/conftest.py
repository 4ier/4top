from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import pytest

from fourtop.config import Config
from fourtop.services import Manager


@dataclass
class Lab:
    path: Path
    env: dict
    config: Config
    manager: Manager
    config_file: Path

    def cli(self, *args, **kwargs):
        return subprocess.run([sys.executable, "-m", "fourtop", "--config", str(self.config_file), *args],
                              env=self.env, capture_output=True, text=True, timeout=20, **kwargs)

    def write_config(self, text: str) -> Config:
        self.config_file.write_text(text)
        return Config.load(str(self.config_file), environment=self.env)

    def report(self, run):
        path = self.path / "reports" / (str(run["pid"]) + ".json")
        return eventually(lambda: json.loads(path.read_text()) if path.exists() else None)


def eventually(fn, timeout=8):
    import time
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        try:
            result = fn()
            if result:
                return result
        except (OSError, ValueError, AssertionError) as exc:
            last_error = exc
        time.sleep(.05)
    raise AssertionError(f"Condition did not become true: {last_error}")


@pytest.fixture
def lab(monkeypatch):
    with tempfile.TemporaryDirectory(prefix="4t-test-", dir="/tmp") as directory:
        root = Path(directory).resolve()
        home = root / "home"
        home.mkdir(mode=0o700)
        env = dict(os.environ)
        for key in ("CODEX_HOME", "CLAUDE_CONFIG_DIR", "PI_CODING_AGENT_DIR", "PYTHONPATH"):
            env.pop(key, None)
            monkeypatch.delenv(key, raising=False)
        env.update(HOME=str(home), XDG_STATE_HOME=str(root / "state"),
                   XDG_CACHE_HOME=str(root / "cache"), XDG_CONFIG_HOME=str(root / "config"),
                   TERM="xterm-256color", FAKE_REPORTS=str(root / "reports"),
                   TEST_CANARY="synthetic-canary-not-a-real-secret")
        bin_dir = root / "bin"
        bin_dir.mkdir(mode=0o700)
        source = Path(__file__).parent / "fixtures/fake_agent.py"
        for agent in ("codex", "claude", "pi"):
            path = bin_dir / agent
            path.write_text("#!" + sys.executable + "\n" + source.read_text())
            path.chmod(0o700)
        env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", os.defpath)
        for key in ("HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME", "XDG_CONFIG_HOME", "PATH",
                    "TERM", "FAKE_REPORTS", "TEST_CANARY"):
            monkeypatch.setenv(key, env[key])
        config_file = root / "config.toml"
        config_file.write_text("")
        config = Config.load(str(config_file), environment=env)
        manager = Manager(config)
        yield Lab(root, env, config, manager, config_file)
        manager.close()
