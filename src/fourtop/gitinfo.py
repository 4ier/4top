"""Which repository a session belongs to, and what its working tree changed.

Grouping by directory name split one project in two whenever it had a linked
worktree (`4top` and `4top-resident`), so a row carries its repository: the main
worktree's root, which every linked worktree shares. That is read from the `.git`
entries on disk, without running git, because a listing has thousands of rows and
a repository does not move.

What the working tree changed is git's own answer (`diff --shortstat HEAD`, plus the
untracked count), asked only for sessions active in the last week, with the answers
kept in the cache directory. An answer is reused while the worktree's index, HEAD
and the latest session write in it are unchanged, for up to a minute: an agent that
edits files also writes its transcript, so its edits show at the next listing. Git
runs with optional locks off, so asking never contends with the agent's own git.
"""
from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import time
from pathlib import Path

from session_ls.storage import atomic_json, read_json

CACHE_SCHEMA = 1
TTL_SECONDS = 60.0
DEADLINE_SECONDS = 3.0  # for all of one listing's git calls together
SHORTSTAT = re.compile(r"(\d+) files? changed(?:, (\d+) insertions?\(\+\))?(?:, (\d+) deletions?\(-\))?")

_worktrees: dict[str, tuple[str, str] | None] = {}  # directory -> (worktree root, git dir)
_repos: dict[str, str] = {}


def _entry(directory: str) -> tuple[str, str] | None:
    """(worktree root, git dir) for the worktree containing ``directory``, or None."""
    if directory in _worktrees:
        return _worktrees[directory]
    found = None
    dot = os.path.join(directory, ".git")
    try:
        mode = os.stat(dot).st_mode
    except OSError:
        mode = None
    if mode is not None and stat.S_ISDIR(mode):
        found = (directory, dot)
    elif mode is not None and stat.S_ISREG(mode):
        # A linked worktree or a submodule: ".git" is a file naming the git dir.
        try:
            with open(dot, encoding="utf-8") as handle:
                line = handle.readline(4096).strip()
        except (OSError, UnicodeDecodeError):
            line = ""
        if line.startswith("gitdir:"):
            found = (directory, os.path.normpath(os.path.join(directory, line[7:].strip())))
    else:
        parent = os.path.dirname(directory)
        found = _entry(parent) if parent and parent != directory else None
    _worktrees[directory] = found
    return found


def worktree(cwd: str) -> tuple[str, str] | None:
    # A directory that is gone belongs to no repository, rather than to whatever
    # repository happens to contain its parent.
    if not cwd or not os.path.isabs(cwd) or not os.path.isdir(cwd):
        return None
    return _entry(os.path.normpath(cwd))


def repo_of(cwd: str) -> str:
    """The repository ``cwd`` belongs to: its main worktree's root, "" outside git."""
    if cwd in _repos:
        return _repos[cwd]
    found, result = worktree(cwd), ""
    if found:
        root, git_dir = found
        result = root
        try:
            with open(os.path.join(git_dir, "commondir"), encoding="utf-8") as handle:
                common = os.path.normpath(os.path.join(git_dir, handle.readline(4096).strip()))
            # A linked worktree: its repository is where the shared .git lives.
            if os.path.basename(common) == ".git":
                result = os.path.dirname(common)
        except (OSError, UnicodeDecodeError):
            pass  # a main worktree or a submodule: its own root
    _repos[cwd] = result
    return result


def _state(git_dir: str) -> list:
    """What must be unchanged for an earlier answer to still be right."""
    def mtime(name):
        try:
            return os.stat(os.path.join(git_dir, name)).st_mtime_ns
        except OSError:
            return 0
    try:
        with open(os.path.join(git_dir, "HEAD"), encoding="utf-8") as handle:
            head = handle.readline(4096).strip()
    except (OSError, UnicodeDecodeError):
        head = ""
    return [mtime("index"), head, mtime("logs/HEAD")]


def parse(shortstat: str, untracked: int) -> dict:
    match = SHORTSTAT.search(shortstat)
    files, insertions, deletions = (int(value or 0) for value in match.groups()) if match else (0, 0, 0)
    return {"files": files, "insertions": insertions, "deletions": deletions,
            "untracked": untracked, "dirty": bool(files or untracked)}


class Changes:
    """Working-tree changes per worktree, cached on disk between listings."""

    def __init__(self, cache: Path, env: dict[str, str]):
        self.cache = cache
        self.env = {**env, "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"}
        self.git = shutil.which("git", path=env.get("PATH", os.defpath))
        self.entries: dict | None = None

    def _load(self) -> dict:
        if self.entries is None:
            try:
                value = read_json(self.cache, {})
            except (OSError, ValueError):
                value = {}
            ok = isinstance(value, dict) and value.get("schema_version") == CACHE_SCHEMA
            self.entries = value.get("entries", {}) if ok and isinstance(value.get("entries"), dict) else {}
        return self.entries

    def of(self, latest: dict[str, str]) -> dict[str, dict]:
        """``{cwd: changes}`` for directories whose sessions were last written at
        ``latest[cwd]``. A directory outside git, or one git did not answer for in
        time and never answered for before, gets no entry."""
        if self.git is None or not latest:
            return {}
        entries, now = self._load(), time.time()
        trees: dict[str, tuple[str, str]] = {}  # worktree root -> (git dir, latest write)
        for cwd, last in latest.items():
            found = worktree(cwd)
            if found and (found[0] not in trees or trees[found[0]][1] < last):
                trees[found[0]] = (found[1], last)
        answers, stale, asked = {}, {}, {}
        for root, (git_dir, last) in trees.items():
            key = [*_state(git_dir), last]
            entry = entries.get(root)
            if isinstance(entry, dict) and isinstance(entry.get("changes"), dict):
                if entry.get("key") == key and now - float(entry.get("at", 0)) < TTL_SECONDS:
                    answers[root] = entry["changes"]
                    continue
                stale[root] = entry["changes"]
            asked[root] = key
        fresh = self._ask(list(asked)) if asked else {}
        for root in asked:
            if root in fresh:
                answers[root] = fresh[root]
                entries[root] = {"key": asked[root], "at": now, "changes": fresh[root]}
            else:
                answers[root] = stale.get(root)
        if fresh:
            for root in [r for r, e in entries.items()
                         if not isinstance(e, dict) or now - float(e.get("at", 0)) > 7 * 86400]:
                del entries[root]
            try:
                atomic_json(self.cache, {"schema_version": CACHE_SCHEMA, "entries": entries})
            except OSError:
                pass  # an unwritten cache only costs the next listing the same git calls
        result = {}
        for cwd in latest:
            found = worktree(cwd)
            if found and answers.get(found[0]) is not None:
                result[cwd] = answers[found[0]]
        return result

    def _ask(self, roots: list[str]) -> dict[str, dict]:
        """Run git in every worktree at once and wait, together, up to the deadline."""
        running = {}
        for root in roots:
            try:
                running[root] = [subprocess.Popen(
                    [self.git, "-C", root, *args], stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, env=self.env)
                    for args in (("diff", "--shortstat", "HEAD", "--"),
                                 ("ls-files", "-z", "--others", "--exclude-standard",
                                  "--directory", "--no-empty-directory"))]
            except OSError:
                continue
        deadline, answers = time.monotonic() + DEADLINE_SECONDS, {}
        for root, (diff, untracked) in running.items():
            try:
                diff_out = diff.communicate(timeout=max(0.01, deadline - time.monotonic()))[0]
                files_out = untracked.communicate(timeout=max(0.01, deadline - time.monotonic()))[0]
            except subprocess.TimeoutExpired:
                for process in (diff, untracked):
                    process.kill()
                    process.communicate()
                continue
            if diff.returncode == 0 and untracked.returncode == 0:
                count = sum(1 for name in files_out.split(b"\0") if name)
                answers[root] = parse(diff_out.decode("utf-8", "replace"), count)
        return answers
