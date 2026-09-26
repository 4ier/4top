"""Local diagnosis, never a live model request or an automatic repair."""
from __future__ import annotations

import os
import platform
import stat
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

from session_ls.api import clean_text

from . import __version__
from .errors import FourtopError


def revision(package_file: Path | None = None) -> str | None:
    """Which code is this? A checkout has a git revision, a deployed tree carries a
    REVISION file, and neither existing is reported as unknown rather than guessed.

    Two machines running different revisions is the failure this exists to surface:
    a remote that predates a command answers with a usage error instead.
    """
    here = (package_file or Path(__file__)).resolve()
    for parent in here.parents:
        if (parent / ".git").exists():
            try:
                result = subprocess.run(["git", "-C", str(parent), "rev-parse", "--short", "HEAD"],
                                        capture_output=True, text=True, timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                return None
            return result.stdout.strip()[:40] or None if result.returncode == 0 else None
    # <root>/lib/fourtop/__init__.py, so the deployed tree writes <root>/REVISION.
    for candidate in (here.parent, here.parents[1], here.parents[2]):
        try:
            value = (candidate / "REVISION").read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if value:
            return value[:40]
    return None


def diagnose(manager) -> dict:
    config = manager.config
    report = {"schema_version": 1, "4top": __version__, "revision": revision(),
              "python": platform.python_version(),
              "platform": sys.platform, "scope": manager.scope,
              "terminal": {"stdin_tty": sys.stdin.isatty(), "stdout_tty": sys.stdout.isatty()},
              "agents": {}, "hosts": {}, "issues": []}
    for agent in ("codex", "claude", "pi"):
        try:
            capability = asdict(manager.drivers.probe(agent))
            capability.pop("executable", None)  # Do not include private installation paths in diagnostics.
            capability["version"] = clean_text(capability["version"])
            capability.update(installed=True, evidence="installed help/version probe; not a real-agent smoke test")
        except (FourtopError, OSError) as exc:
            capability = {"installed": False, "evidence": type(exc).__name__}
        report["agents"][agent] = capability
    report["history_sources"] = []
    for root in config.roots:
        path = Path(root.path)
        exists = path.exists()
        report["history_sources"].append({"agent": root.agent, "exists": exists,
                                         "readable": exists and os.access(path, os.R_OK | os.X_OK)})
    report["hosts"] = {name: {"ssh": host.ssh, "command": host.command,
                              "refresh_seconds": host.refresh_seconds,
                              "timeout_seconds": host.timeout_seconds}
                       for name, host in config.hosts.items()}
    if manager.host is not None:
        from .hosts import remote_doctor
        try:
            report["remote"] = remote_doctor(config, manager.host)
        except FourtopError as exc:
            report["issues"].append(str(exc))
        local, remote = report["revision"], report["remote"].get("revision")
        if local and remote and local != remote:
            report["issues"].append(
                f"{manager.host.name} runs {remote} while this build is {local}: "
                f"update it with scripts/remote-update.sh {manager.host.name}")
    report["state"] = {"mode": oct(stat.S_IMODE(config.state_dir.stat().st_mode)),
                       "writable": os.access(config.state_dir, os.W_OK)}
    report["budgets"] = {"metadata_max_bytes": config.metadata_max_bytes,
                         "metadata_max_lines": config.metadata_max_lines,
                         "preview_max_lines": config.preview_max_lines,
                         "agent_runtime_limit": None}
    report["privacy"] = ("Local history, local state, ssh to configured hosts only; "
                         "no authentication files, model requests, or telemetry.")
    return report
