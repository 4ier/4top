#!/usr/bin/env python3
"""Gate a release before CI publishes it.

PyPI versions are immutable: a published wheel can be yanked but never replaced, so
every check here exists to stop a publish that cannot be taken back.

    scripts/check_release.py v0.2.0a1             # tag, versions and built artifacts
    scripts/check_release.py v0.2.0a1 --decide    # also report what is new on PyPI

The dependency check is the one that matters most. The root wheel requires
`session-ls>=0.2.0`, and PyPI only ever held 0.1.0, so publishing the root alone
would have produced a package nobody could install. A release is therefore only
allowed when the requirement is satisfied by PyPI already or by this same run.
"""
from __future__ import annotations

import argparse
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
LIBRARY_PACKAGE = "session-ls"
LIBRARY_PYPROJECT = ROOT / "packages" / "session-ls" / "pyproject.toml"
PYPI_JSON = "https://pypi.org/pypi/{name}/{version}/json"


def project(path: Path) -> dict:
    with path.open("rb") as handle:
        return tomllib.load(handle)["project"]


def parse(name: str, value: str) -> Version:
    try:
        return Version(value)
    except InvalidVersion:
        raise SystemExit(f"{name}: {value!r} is not a PEP 440 version") from None


def published(name: str, version: str) -> bool:
    try:
        with urllib.request.urlopen(PYPI_JSON.format(name=name, version=version), timeout=20):
            return True
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return False
        raise SystemExit(f"cannot ask PyPI about {name} {version}: HTTP {exc.code}") from None
    except OSError as exc:
        # Never guess here: "unknown" and "not published" have opposite consequences.
        raise SystemExit(f"cannot ask PyPI about {name} {version}: {type(exc).__name__}") from None


def library_requirement(root: dict) -> SpecifierSet:
    for value in root["dependencies"]:
        requirement = Requirement(value)
        if requirement.name == LIBRARY_PACKAGE:
            return requirement.specifier
    raise SystemExit(f"{ROOT_PACKAGE} does not depend on {LIBRARY_PACKAGE}; this gate assumes it does")


def artifact_versions(dist: Path) -> set[tuple[str, str]]:
    found = set()
    for wheel in sorted(dist.glob("*.whl")):
        name, version, _, _ = parse_wheel_filename(wheel.name)
        found.add((name.replace("_", "-"), str(version)))
    for sdist in sorted(dist.glob("*.tar.gz")):
        stem = sdist.name[: -len(".tar.gz")]
        name, _, version = stem.rpartition("-")
        found.add((name.replace("_", "-"), version))
    return found


def check(tag: str | None, dist: Path | None) -> tuple[list[str], dict]:
    problems: list[str] = []
    root = project(ROOT / "pyproject.toml")
    library = project(LIBRARY_PYPROJECT)
    root_version = parse(ROOT_PACKAGE, root["version"])
    library_version = parse(LIBRARY_PACKAGE, library["version"])

    if tag:
        expected = tag[1:] if tag.startswith("v") else tag
        if expected != root["version"]:
            problems.append(f"tag {tag} does not match the {ROOT_PACKAGE} version {root['version']}: "
                            f"tag the version you are releasing, or bump the version")

    requirement = library_requirement(root)
    if library_version not in requirement:
        problems.append(f"{ROOT_PACKAGE} {root_version} requires {LIBRARY_PACKAGE}{requirement}, "
                        f"but this tree builds {library_version}: the pair is not installable")

    if dist:
        expected_artifacts = {(ROOT_PACKAGE, str(root_version)), (LIBRARY_PACKAGE, str(library_version))}
        built = artifact_versions(dist)
        if built != expected_artifacts:
            problems.append(f"{dist} holds {sorted(built) or 'nothing'}, expected "
                            f"{sorted(expected_artifacts)}: build both packages into this directory")

    return problems, {"root": root_version, "library": library_version, "requirement": requirement}


def decide() -> int:
    """Report what a publish would upload, so CI can skip versions that exist.

    PyPI refuses an existing version, and refusing mid-run is worse than deciding
    here where the reason can be printed.
    """
    root = project(ROOT / "pyproject.toml")
    library = project(LIBRARY_PYPROJECT)
    for package, info in ((LIBRARY_PACKAGE, library), (ROOT_PACKAGE, root)):
        exists = published(package, info["version"])
        key = package.replace("-", "_")
        print(f"{key}={'exists' if exists else 'new'}")
        print(f"  {package} {info['version']}: "
              f"{'already on PyPI, will be skipped' if exists else 'will be uploaded'}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("tag", nargs="?", help="Git tag being released, e.g. v0.2.0a1")
    parser.add_argument("--dist", type=Path, help="Directory holding the built artifacts")
    parser.add_argument("--decide", action="store_true", help="Print what would be uploaded")
    args = parser.parse_args()

    if args.decide:
        problems, _ = check(args.tag, args.dist)
        if problems:
            for problem in problems:
                print("release: " + problem, file=sys.stderr)
            return 1
        return decide()

    problems, versions = check(args.tag, args.dist)
    for problem in problems:
        print("release: " + problem, file=sys.stderr)
    if problems:
        return 1
    print(f"release {ROOT_PACKAGE} {versions['root']} with {LIBRARY_PACKAGE} {versions['library']} "
          f"(requirement {versions['requirement']}) looks publishable")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
