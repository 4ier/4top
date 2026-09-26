"""The release gate: PyPI versions are immutable, so this is the last chance to say no."""
import importlib.util
from pathlib import Path

from packaging.specifiers import SpecifierSet

ROOT = Path(__file__).resolve().parents[2]


def load():
    spec = importlib.util.spec_from_file_location("check_release", ROOT / "scripts" / "check_release.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_current_tree_passes_its_own_gate():
    problems, versions = load().check("v0.2.0a1", None)
    assert problems == [], problems
    assert str(versions["library"]) in versions["requirement"]


def test_a_tag_that_does_not_match_the_version_is_refused():
    problems, _ = load().check("v0.1.0", None)
    assert any("does not match" in problem for problem in problems), problems


def test_a_requirement_the_library_cannot_satisfy_is_refused(monkeypatch):
    # The real trap: the root required session-ls>=0.2.0 while PyPI only held 0.1.0,
    # so the root wheel would have been installable by nobody.
    module = load()
    monkeypatch.setattr(module, "library_requirement", lambda root: SpecifierSet(">=0.3"))
    problems, _ = module.check("v0.2.0a1", None)
    assert any("not installable" in problem for problem in problems), problems


def test_artifacts_must_be_the_versions_being_released(tmp_path):
    module = load()
    (tmp_path / "4top-0.2.0a1-py3-none-any.whl").write_text("")
    (tmp_path / "session_ls-0.2.0-py3-none-any.whl").write_text("")
    (tmp_path / "session_ls-0.2.0.tar.gz").write_text("")
    assert module.artifact_versions(tmp_path) == {("4top", "0.2.0a1"), ("session-ls", "0.2.0")}
    problems, _ = module.check("v0.2.0a1", tmp_path)
    assert problems == [], problems

    for leftover in tmp_path.glob("session_ls-*"):
        leftover.unlink()
    problems, _ = module.check("v0.2.0a1", tmp_path)
    assert any("expected" in problem for problem in problems), problems
