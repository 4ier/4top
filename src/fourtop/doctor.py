"""Local diagnosis, never a live model request or an automatic repair."""
from __future__ import annotations

import os
import platform
import stat
import sys
from dataclasses import asdict

from session_ls.api import clean_text

from . import __version__
from .errors import FourtopError


def diagnose(manager) -> dict:
    config = manager.config
    report = {"schema_version": 1, "4top": __version__, "python": platform.python_version(),
              "platform": sys.platform, "terminal": {"stdin_tty": sys.stdin.isatty(),
              "stdout_tty": sys.stdout.isatty(), "inside_tmux": manager.tmux.is_inside()},
              "agents": {}, "issues": []}
    observed = manager.tmux.snapshot()
    report["tmux"] = {"installed": bool(manager.tmux.executable), "reachable": observed.available,
                      "running_server": bool(observed.server), "panes": len(observed.panes)}
    if manager.tmux.executable:
        try:
            report["tmux"]["version"] = clean_text(manager.tmux.command(["-V"]).stdout.strip())
            if observed.server:
                value = manager.tmux.command(["show-options", "-sv", "exit-unattached"], check=False)
                if value.stdout.strip() == "on":
                    report["issues"].append("tmux exit-unattached is on: detached work cannot be retained")
        except FourtopError as exc:
            report["issues"].append(str(exc))
    if observed.issue:
        report["issues"].append(observed.issue)
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
        from pathlib import Path
        path = Path(root.path)
        exists = path.exists()
        report["history_sources"].append({"agent": root.agent, "exists": exists,
                                         "readable": exists and os.access(path, os.R_OK | os.X_OK)})
    report["state"] = {"mode": oct(stat.S_IMODE(config.state_dir.stat().st_mode)),
                       "writable": os.access(config.state_dir, os.W_OK)}
    runs, issues = manager.store.list()
    report["state"]["runs"] = len(runs)
    report["issues"].extend(issues)
    report["budgets"] = {"startup_handshake_seconds": config.startup_seconds,
                         "control_timeout_seconds": config.control_seconds,
                         "metadata_max_bytes": config.metadata_max_bytes,
                         "metadata_max_lines": config.metadata_max_lines,
                         "preview_max_lines": config.preview_max_lines,
                         "agent_runtime_limit": None}
    report["privacy"] = "Local only; no authentication files, model requests, or telemetry. Review before sharing."
    return report
