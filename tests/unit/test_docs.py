"""The documentation gate.

A README that describes a design the code no longer has is not cosmetic: it is the
first thing a reader (or an agent) believes. These tests run the same checks CI
runs, so a stale document fails the suite instead of waiting to mislead someone.
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def run_checker() -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(ROOT / "scripts" / "check_docs.py")],
                          capture_output=True, text=True, timeout=120, cwd=ROOT)


def test_documentation_matches_the_code():
    result = run_checker()
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_checker_notices_a_stale_command(tmp_path, monkeypatch):
    # The gate is only worth having if it actually fails. Point it at a copy of the
    # tree whose README documents a command that does not exist.
    readme = ROOT / "README.md"
    original = readme.read_text(encoding="utf-8")
    try:
        readme.write_text(original + "\n```sh\n4top rerun --json\n```\n", encoding="utf-8")
        assert run_checker().returncode == 1
    finally:
        readme.write_text(original, encoding="utf-8")
