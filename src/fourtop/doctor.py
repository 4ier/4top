"""Local diagnosis, never a live model request or an automatic repair."""
from __future__ import annotations

import os
import platform
import stat
import sys
from dataclasses import asdict
from pathlib import Path

from session_ls.api import clean_text

from . import __version__
from .errors import FourtopError


def diagnose(manager) -> dict:
    config = manager.config
    report = {"schema_version": 1, "4top": __version__, "python": platform.python_version(),
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
    report["state"] = {"mode": oct(stat.S_IMODE(config.state_dir.stat().st_mode)),
                       "writable": os.access(config.state_dir, os.W_OK)}
    report["budgets"] = {"metadata_max_bytes": config.metadata_max_bytes,
                         "metadata_max_lines": config.metadata_max_lines,
                         "preview_max_lines": config.preview_max_lines,
                         "agent_runtime_limit": None}
    report["privacy"] = ("Local history, local state, ssh to configured hosts only; "
                         "no authentication files, model requests, or telemetry.")
    return report
