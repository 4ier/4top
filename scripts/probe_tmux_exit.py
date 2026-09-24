#!/usr/bin/env python3
"""Credential-free tmux exit diagnostic, for development/CI bug reports only."""
import json
import os
import runpy
import signal
import time
from pathlib import Path

import pytest

scope = runpy.run_path(str(Path(__file__).resolve().parents[1] / "tests/conftest.py"))
patch = pytest.MonkeyPatch()
fixture = scope["lab"].__wrapped__(patch)
lab = next(fixture)
try:
    for mode in ("fast-exit", "signal-exit"):
        run = lab.manager.new("claude", str(lab.path), extra=("--fail",) if mode == "fast-exit" else ())
        if mode == "signal-exit":
            lab.report(run)
            os.kill(run["pid"], signal.SIGKILL)
        for delay in (.05, .25, 1):
            time.sleep(delay)
            raw = lab.manager.tmux.command(["display-message", "-p", "-t", run["tmux"]["pane"],
                "dead=#{pane_dead}|status=#{pane_dead_status}|signal=#{pane_dead_signal}|time=#{pane_dead_time}|pid=#{pane_pid}"])
            status = lab.manager.tmux.observe(run)
            proc = Path(f"/proc/{run['pid']}/stat")
            fields = proc.read_text().rsplit(")", 1)[1].split() if proc.exists() else []
            process = {"state": fields[0], "parent_pid": fields[1],
                       "kernel_exit_status": fields[49] if len(fields) > 49 else None} if fields else None
            # Only the disposable synthetic process's status and its empty project output.
            print(json.dumps({"mode":mode,"delay":delay,"tmux_raw":raw.stdout,
                "state":status[0],"issue":status[2],
                "pane":str(status[1]),
                "process_present":proc.exists(), "kernel_process":process,
                "capture":lab.manager.tmux.command(["capture-pane","-p","-t",run["tmux"]["pane"]]).stdout.strip()}))
finally:
    try:
        next(fixture)
    except StopIteration:
        pass
    patch.undo()
