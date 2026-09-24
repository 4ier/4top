from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
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

    def report(self, run):
        path = self.path / "reports" / (str(run["pid"]) + ".json")
        return eventually(lambda: json.loads(path.read_text()) if path.exists() else None)


def eventually(fn, timeout=8):
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
    # Short, private Unix-socket paths are portable to macOS.
    with tempfile.TemporaryDirectory(prefix="4t-test-", dir="/tmp") as directory:
        root = Path(directory).resolve()
        home = root / "home"
        home.mkdir(mode=0o700)
        env = dict(os.environ)
        for key in ("TMUX", "TMUX_PANE", "CODEX_HOME", "CLAUDE_CONFIG_DIR", "PI_CODING_AGENT_DIR", "PYTHONPATH"):
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
        for key, value in env.items():
            if key in ("HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME", "XDG_CONFIG_HOME", "PATH", "TERM", "FAKE_REPORTS", "TEST_CANARY"):
                monkeypatch.setenv(key, value)
        config_file = root / "config.toml"
        config_file.write_text('[runtime]\nsocket = ' + json.dumps(str(root / "tmux.sock")) + '\n')
        config = Config.load(str(config_file), environment=env)
        manager = Manager(config)
        yield Lab(root, env, config, manager, config_file)
        manager.close()
        # TEST-ONLY cleanup: every socket in this directory belongs to this fixture.
        binary = shutil.which("tmux", path=env["PATH"])
        if binary:
            for sock in root.glob("*.sock"):
                subprocess.run([binary, "-S", str(sock), "kill-server"], capture_output=True,
                               env=env, timeout=5)


@pytest.fixture
def tmux_lab(lab):
    if not lab.manager.tmux.executable:
        if os.environ.get("FOURTOP_TEST_REQUIRE_TMUX"):
            pytest.fail("tmux is required; integration coverage must not be silently skipped")
        pytest.skip("tmux is required for real runtime integration tests")
    return lab
