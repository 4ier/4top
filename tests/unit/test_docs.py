"""The documentation gate.

A README that describes a design the code no longer has is not cosmetic: it is the
first thing a reader (or an agent) believes. These tests run the same script CI and
the pre-commit hook run, so a stale document fails the suite instead of quietly
misleading the next change.
"""
import importlib.util
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load_checker():
    spec = importlib.util.spec_from_file_location("check_docs", ROOT / "scripts" / "check_docs.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_checker() -> subprocess.CompletedProcess:
    """Run it the way CI and the hook do: as a program, not as an import."""
    return subprocess.run([sys.executable, str(ROOT / "scripts" / "check_docs.py")],
                          capture_output=True, text=True, timeout=120, cwd=ROOT)


def test_documentation_matches_the_code():
    result = run_checker()
    assert result.returncode == 0, result.stdout + result.stderr


def test_invocations_come_from_code_blocks_not_from_prose():
    # The first version of this checker scanned prose and reported 137 problems,
    # most of them sentences like "4top never installs an agent".
    checker = load_checker()
    assert checker.invocations("4top never installs or authenticates agents for you") == []
    assert checker.invocations("Because 4top owns no process, ...") == []
    assert checker.invocations("```sh\n4top --host box list --json   # view one host\n```") == \
        [["4top", "--host", "box", "list", "--json"]]
    assert checker.invocations("Run `4top preview h_x`") == [["4top", "preview", "h_x"]]


def test_the_checker_notices_a_stale_command(tmp_path, monkeypatch):
    # A gate that cannot fail is worse than no gate, so prove it fails: give the
    # checker a README that documents a command the CLI does not have.
    checker = load_checker()
    stale = tmp_path / "README.md"
    stale.write_text((ROOT / "README.md").read_text(encoding="utf-8")
                     + "\n```sh\n4top rerun --json\n```\n", encoding="utf-8")
    real = checker.tracked_files
    monkeypatch.setattr(checker, "tracked_files",
                        lambda: [stale] + [path for path in real() if path.name != "README.md"])
    problems = checker.reported()
    assert any("rerun" in problem for problem in problems), problems


def test_the_checker_notices_removed_vocabulary(tmp_path, monkeypatch):
    checker = load_checker()
    stale = tmp_path / "docs.md"
    stale.write_text("Run 4top inside tmux.\n", encoding="utf-8")
    monkeypatch.setattr(checker, "tracked_files", lambda: [stale])
    problems = checker.reported()
    assert any("removed vocabulary" in problem for problem in problems), problems


def test_the_checker_explains_itself_without_the_project_environment():
    # The hook may fall back to a bare interpreter; that must not look like a crash.
    result = subprocess.run([sys.executable, "-S", str(ROOT / "scripts" / "check_docs.py")],
                            capture_output=True, text=True, timeout=60, cwd="/")
    assert result.returncode in (0, 2), result.stdout + result.stderr
    assert "Traceback" not in result.stderr, result.stderr
