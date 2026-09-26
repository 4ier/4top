#!/usr/bin/env python3
"""Gate a release before CI publishes it.

PyPI versions are immutable: a published wheel can be yanked but never replaced, so
every check here exists to stop a publish that cannot be taken back.

    scripts/check_release.py v0.2.0a4             # tag and built artifacts
    scripts/check_release.py v0.2.0a1 --decide    # also ask PyPI what is new

`session-ls` is not built here: it has its own repository and its own pipeline, and
arrives as an ordinary dependency. The check that matters most is therefore that a
published `session-ls` actually satisfies the requirement in this project's
metadata — the root wheel required `session-ls>=0.2.0` while PyPI only ever held
0.1.0, which would have published a package nobody could install.
"""
from __future__ import annotations

import argparse
import json
import sys
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import parse_wheel_filename
from packaging.version import InvalidVersion, Version

ROOT = Path(__file__).resolve().parents[1]
ROOT_PACKAGE = "4top"
DEPENDENCY_PACKAGE = "session-ls"
PYPI_JSON = "https://pypi.org/pypi/{name}/json"


def project(path: Path = ROOT / "pyproject.toml") -> dict:
    with path.open("rb") as handle:
        return tomllib.load(handle)["project"]


def parse(name: str, value: str) -> Version:
    try:
        return Version(value)
    except InvalidVersion:
        raise SystemExit(f"{name}: {value!r} is not a PEP 440 version") from None


def requirement(root: dict | None = None) -> SpecifierSet:
    """What this project needs from the separately published parser package."""
    for value in (root or project())["dependencies"]:
        parsed = Requirement(value)
        if parsed.name == DEPENDENCY_PACKAGE:
            return parsed.specifier
    raise SystemExit(f"{ROOT_PACKAGE} does not depend on {DEPENDENCY_PACKAGE}; "
                     f"this gate assumes it does")


def published_versions(name: str) -> set[str]:
    """Every version of a project on PyPI. Never guessed: an unreachable PyPI is an error."""
    try:
        with urllib.request.urlopen(PYPI_JSON.format(name=name), timeout=20) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return set()
        raise SystemExit(f"cannot ask PyPI about {name}: HTTP {exc.code}") from None
    except (OSError, ValueError) as exc:
        raise SystemExit(f"cannot ask PyPI about {name}: {type(exc).__name__}") from None
    releases = payload.get("releases")
    return set(releases) if isinstance(releases, dict) else set()


def artifact_versions(dist: Path) -> set[tuple[str, str]]:
    found = set()
    for wheel in sorted(dist.glob("*.whl")):
        name, version, _, _ = parse_wheel_filename(wheel.name)
        found.add((name.replace("_", "-"), str(version)))
    for sdist in sorted(dist.glob("*.tar.gz")):
        name, _, version = sdist.name[: -len(".tar.gz")].rpartition("-")
        found.add((name.replace("_", "-"), version))
    return found


def check(tag: str | None, dist: Path | None) -> tuple[list[str], dict]:
    """The checks that need no network."""
    problems: list[str] = []
    root = project()
    version = parse(ROOT_PACKAGE, root["version"])
    needs = requirement(root)

    if tag:
        expected = tag[1:] if tag.startswith("v") else tag
        if expected != root["version"]:
            problems.append(f"tag {tag} does not match the {ROOT_PACKAGE} version "
                            f"{root['version']}: tag the version you are releasing, or bump "
                            f"the version")

    if dist:
        expected_artifacts = {(ROOT_PACKAGE, str(version))}
        built = artifact_versions(dist)
        if built != expected_artifacts:
            problems.append(f"{dist} holds {sorted(built) or 'nothing'}, expected "
                            f"{sorted(expected_artifacts)}: build this project into that "
                            f"directory")

    return problems, {"version": version, "requirement": needs}


def dependency_problem(needs: SpecifierSet) -> str | None:
    """Is the requirement satisfiable by what PyPI already has?"""
    available = published_versions(DEPENDENCY_PACKAGE)
    satisfying = sorted((v for v in available if Version(v) in needs), key=Version)
    if satisfying:
        return None
    return (f"{ROOT_PACKAGE} requires {DEPENDENCY_PACKAGE}{needs}, but PyPI has "
            f"{sorted(available) or 'nothing'}: the published wheel would be uninstallable. "
            f"Release {DEPENDENCY_PACKAGE} first, from https://github.com/4ier/session-ls.")


def decide() -> int:
    """Report what a publish would upload, and refuse an unsatisfiable dependency.

    PyPI refuses an existing version and an uninstallable dependency, and refusing
    mid-run is worse than deciding here where the reason can be printed. stdout is
    exactly `key=value` lines so CI can append it to $GITHUB_OUTPUT.
    """
    root = project()
    needs = requirement(root)
    problem = dependency_problem(needs)
    if problem:
        print("release: " + problem, file=sys.stderr)
        return 1
    exists = root["version"] in published_versions(ROOT_PACKAGE)
    print(f"root={'exists' if exists else 'new'}")
    print(f"dependency=ok ({DEPENDENCY_PACKAGE}{needs} is available on PyPI)", file=sys.stderr)
    print(f"{ROOT_PACKAGE} {root['version']}: "
          f"{'already on PyPI, will be skipped' if exists else 'will be uploaded'}", file=sys.stderr)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("tag", nargs="?", help="Git tag being released, e.g. v0.2.0a1")
    parser.add_argument("--dist", type=Path, help="Directory holding the built artifacts")
    parser.add_argument("--decide", action="store_true",
                        help="Ask PyPI what is new, and require the dependency to be available")
    args = parser.parse_args()

    problems, info = check(args.tag, args.dist)
    for problem in problems:
        print("release: " + problem, file=sys.stderr)
    if problems:
        return 1

    if args.decide:
        return decide()

    problem = dependency_problem(info["requirement"])
    if problem:
        print("release: " + problem, file=sys.stderr)
        return 1
    print(f"release {ROOT_PACKAGE} {info['version']} with {DEPENDENCY_PACKAGE}"
          f"{info['requirement']} looks publishable")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
