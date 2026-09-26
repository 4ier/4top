#!/usr/bin/env python3
"""Fail when the documentation stops describing the code.

Reading a repository is how an agent builds its model of it, so a stale README is
not cosmetic: it becomes a wrong assumption in the next change. Every check here
compares shipped behaviour with what the docs claim about it.

Run directly (`python scripts/check_docs.py`), or through the test suite and CI,
which is what makes it refuse a change rather than merely mention it.
"""
from __future__ import annotations

import re
import shlex
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

# Vocabulary from a design that no longer exists. Leaving it in the tree costs a
# reader attention on every pass, and git history is where the lesson belongs.
# Upper-case tokens are matched case-sensitively so ordinary prose stays readable.
SUPERSEDED = (r"tmux", r"byobu", r"\bpty\b", r"\bpane\b", r"handoff", r"--socket",
              r"--client", r"--detach", r"unix socket",
              r"\bLIVE\b", r"\bHIST\b", r"\bMISSING\b", r"\bUNKNOWN\b", r"\bEXIT\b")
# Files that must name the removed vocabulary in order to look for it.
EXEMPT = {"scripts/check_docs.py", "tests/unit/test_docs.py"}

KEY_NOTATION = {
    "slash": "/", "question_mark": "?", "escape": "Esc", "space": "Space", "enter": "Enter",
    "up": "↑", "down": "↓", "ctrl+f": "Ctrl-F", "ctrl+c": "Ctrl-C",
    "q": "q", "n": "n", "r": "r", "i": "i", "H": "H",
}
FLAGS_WITH_VALUE = {"--host", "--config"}
FLAGS_BARE = {"--json", "--full", "--yes", "--no-color", "--demo", "--version", "--agent",
              "--project", "--query", "--cursor", "--cwd"}
SKIP_DIRS = {".git", ".venv", "dist", "build", "__pycache__", ".pytest_cache", ".ruff_cache"}


def tracked_files() -> list[Path]:
    try:
        listed = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True,
                                check=True).stdout.split()
        if listed:
            return [ROOT / name for name in listed]
    except (OSError, subprocess.CalledProcessError):
        pass
    return [path for path in ROOT.rglob("*")
            if path.is_file() and not any(part in SKIP_DIRS for part in path.parts)]


def invocations(text: str) -> list[list[str]]:
    """Command lines that claim to be `4top` invocations, not prose about 4top."""
    lines = []
    for block in re.findall(r"```[a-z]*\n(.*?)```", text, re.DOTALL):
        lines.extend(block.splitlines())
    lines.extend(re.findall(r"`(4top[^`]*)`", text))
    commands = []
    for line in lines:
        line = line.strip().lstrip("$").split(" #")[0].strip()
        if not line.startswith("4top"):
            continue
        try:
            commands.append(shlex.split(line))
        except ValueError:
            continue
    return commands


def reported() -> list[str]:
    """Every problem found, one line each. Empty means the docs match the code."""
    from fourtop import __version__
    from fourtop.app import FourtopApp
    from fourtop.cli import COMMANDS
    from fourtop.config import Host

    problems = []
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    chinese = (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")
    known = {name for name, _ in COMMANDS}
    texts = {path: path.read_text(encoding="utf-8", errors="replace") for path in tracked_files()}

    # Every command the CLI offers is documented, and every documented invocation is
    # a command that exists.
    for name in sorted(known):
        if not any(name in invocation for invocation in invocations(readme)):
            problems.append(f"README.md never shows `4top {name}`")
    for path, text in texts.items():
        if path.suffix != ".md" or str(path.relative_to(ROOT)) in EXEMPT:
            continue
        for invocation in invocations(text):
            words, index = invocation[1:], 0
            while index < len(words):
                word = words[index]
                if word in FLAGS_WITH_VALUE:
                    index += 2
                    continue
                if word in FLAGS_BARE:
                    index += 1
                    continue
                if word in known or word == "--":
                    break
                problems.append(f"{path.relative_to(ROOT)}: `{' '.join(invocation)}` "
                                f"uses unknown command `{word}`")
                break

    # Every key the panel binds is in both key references.
    for binding in FourtopApp.BINDINGS:
        notation = KEY_NOTATION.get(binding.key)
        if notation and f"`{notation}`" not in readme:
            problems.append(f"README.md does not document the `{notation}` key")
        if notation and f"`{notation}`" not in chinese:
            problems.append(f"README.zh-CN.md does not document the `{notation}` key")

    # Documented remote options, configuration sections and version stay honest.
    for name in Host.__dataclass_fields__:
        if name not in ("name", "ad_hoc") and f"{name} =" not in readme:
            problems.append(f"README.md does not show the hosts.{name} option")
    for section in ("ui", "history", "agents", "hosts"):
        if f"[{section}" not in readme:
            problems.append(f"README.md does not show the [{section}] section")
    if __version__ not in readme:
        problems.append("README.md does not state the current version")

    # Relative links resolve.
    for path, text in texts.items():
        if path.suffix != ".md":
            continue
        for target in re.findall(r"\]\((?!https?:|#)([^)]+)\)", text):
            if not (path.parent / target.split("#")[0]).exists():
                problems.append(f"{path.relative_to(ROOT)}: link target {target} does not exist")

    # No vocabulary from a design that no longer exists.
    for path, text in texts.items():
        relative = str(path.relative_to(ROOT))
        if relative in EXEMPT or path.suffix not in (".md", ".py", ".toml", ".yml", ".yaml", ".in", ".cfg"):
            continue
        for word in SUPERSEDED:
            for match in re.finditer(word, text):
                line = text.count("\n", 0, match.start()) + 1
                problems.append(f"{relative}:{line}: removed vocabulary {match.group(0)!r}")
    return problems


def main() -> int:
    try:
        problems = reported()
    except ImportError as exc:
        print(f"docs: cannot inspect the code ({exc}); run this with the project environment",
              file=sys.stderr)
        return 2
    for problem in problems:
        print("docs: " + problem, file=sys.stderr)
    if problems:
        print(f"\n{len(problems)} documentation problem(s). The code is the source of truth: "
              "update README.md and README.zh-CN.md, then docs/.", file=sys.stderr)
        return 1
    print("documentation matches the code")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
