"""The release gate: PyPI versions are immutable, so this is the last chance to say no."""
import importlib.util
import tomllib
from pathlib import Path

from packaging.specifiers import SpecifierSet

ROOT = Path(__file__).resolve().parents[2]


def load():
    spec = importlib.util.spec_from_file_location("check_release", ROOT / "scripts" / "check_release.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def current_version() -> str:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)["project"]["version"]


def test_the_current_tree_passes_the_checks_that_need_no_network():
    # The tag is derived from the version so bumping the version does not break this.
    problems, info = load().check("v" + current_version(), None)
    assert problems == [], problems
    assert str(info["version"]) == current_version()


def test_a_tag_that_does_not_match_the_version_is_refused():
    problems, _ = load().check("v0.0.0", None)
    assert any("does not match" in problem for problem in problems), problems


def test_artifacts_must_be_the_versions_being_released(tmp_path):
    module = load()
    version = current_version()
    (tmp_path / f"4top-{version}-py3-none-any.whl").write_text("")
    (tmp_path / f"4top-{version}.tar.gz").write_text("")
    problems, _ = module.check("v" + version, tmp_path)
    assert problems == [], problems

    for built in tmp_path.glob("*"):
        built.unlink()
    problems, _ = module.check("v" + version, tmp_path)
    assert any("expected" in problem for problem in problems), problems


def test_an_unsatisfiable_dependency_is_refused(monkeypatch):
    # The real trap: the root required session-ls>=0.2.0 while PyPI only held 0.1.0,
    # so the published wheel would have been installable by nobody. session-ls is not
    # built here any more, so this is asked of PyPI rather than of a local package.
    module = load()
    monkeypatch.setattr(module, "published_versions", lambda name: {"0.1.0"})
    problem = module.dependency_problem(SpecifierSet(">=0.2.0,<0.3"))
    assert problem and "uninstallable" in problem

    monkeypatch.setattr(module, "published_versions", lambda name: {"0.2.0", "0.2.1"})
    assert module.dependency_problem(SpecifierSet(">=0.2.0,<0.3")) is None


def test_decide_refuses_before_it_reports(monkeypatch, capsys):
    module = load()
    monkeypatch.setattr(module, "published_versions", lambda name: set())
    assert module.decide() == 1
    assert "uninstallable" in capsys.readouterr().err
