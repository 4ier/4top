#!/usr/bin/env python3
"""Install built wheels into a new venv; never inspect the caller's agent stores."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import venv
import zipfile
from pathlib import Path


def run(argv, env, cwd):
    result = subprocess.run(argv, env=env, cwd=cwd, capture_output=True, text=True, timeout=180)
    if result.returncode:
        raise RuntimeError(f"Command failed ({result.returncode}): {argv}\n{result.stdout}\n{result.stderr}")
    return result.stdout


def main():
    directory = Path(sys.argv[1] if len(sys.argv) > 1 else "dist").resolve()
    wheels = sorted(directory.glob("*.whl"))
    if [wheel.name for wheel in wheels if wheel.name.startswith("4top-")] == []:
        raise SystemExit("Expected a freshly built 4top wheel")
    wheels = [wheel for wheel in wheels if wheel.name.startswith("4top-")]
    if len(wheels) != 1:
        raise SystemExit(f"Expected exactly one 4top wheel, found {[w.name for w in wheels]}")
    with zipfile.ZipFile(next(p for p in wheels if p.name.startswith("4top-"))) as archive:
        if any(name.startswith("session_ls/") for name in archive.namelist()):
            raise SystemExit("4top must not vendor or overwrite the separate session-ls package")
    with tempfile.TemporaryDirectory(prefix="4top-install-") as temporary:
        root = Path(temporary)
        home = root / "home"
        home.mkdir()
        env = {key: value for key, value in os.environ.items() if key not in {
            "PYTHONPATH", "TMUX", "TMUX_PANE", "CLAUDE_CONFIG_DIR", "CODEX_HOME", "PI_CODING_AGENT_DIR"}}
        env.update(HOME=str(home), XDG_CONFIG_HOME=str(home / "config"),
                   XDG_STATE_HOME=str(home / "state"), XDG_CACHE_HOME=str(home / "cache"),
                   NO_COLOR="1", PIP_DISABLE_PIP_VERSION_CHECK="1")
        target = root / "venv"
        venv.EnvBuilder(with_pip=True).create(target)
        python = str(target / "bin/python")
        run([python, "-m", "pip", "install", *map(str, wheels)], env, root)
        version = run([str(target / "bin/4top"), "--version"], env, root).strip()
        assert version == "4top 0.2.0a2", version
        demo = run([str(target / "bin/4top"), "--demo", "list", "--json"], env, root)
        rows = [json.loads(line) for line in demo.splitlines()]
        assert len(rows) == 6 and all(row["schema_version"] == 2 for row in rows)
        assert not (home / "state").exists(), "Demo wrote user state"
        # session-ls is a dependency now, resolved from PyPI rather than built here:
        # its own console script existing proves the dependency was installed.
        legacy = run([str(target / "bin/session-ls"), "--json"], env, root)
        assert not legacy.strip(), "Isolated empty HOME must not discover caller history"
        no_ui = run([python, "-c", "import sys, session_ls; assert 'textual' not in sys.modules"], env, root)
        assert no_ui == ""
        installed = json.loads(run([python, "-m", "pip", "list", "--format=json"], env, root))
        print(json.dumps({"fresh_venv": True, "version": version, "demo_rows": len(rows),
                          "legacy_empty_home": True, "root_wheel_owns_core": False,
                          "row_schema": 2,
                          "installed": installed}, indent=2))


if __name__ == "__main__":
    main()
