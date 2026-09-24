"""A closed PTY is not a reaped child; fail closed until tmux reports an exit."""
import subprocess

import pytest

from fourtop.models import Pane, TmuxSnapshot


@pytest.mark.parametrize("code,signal,state", [
    (0, None, "EXIT"), (7, None, "EXIT"),
    (None, "KILL", "EXIT"), (None, None, "UNKNOWN"),
])
def test_dead_terminal_needs_exit_evidence(lab, code, signal, state):
    run = {"run_id": "test-run", "host_id": "test-host", "pid": 123,
           "process_identity": "original", "tmux": {"server": "test-server", "pane": "%0"}}
    pane = Pane("$0", "@0", "%0", 123, True, code, 0,
                "test-run", "test-host", "claude", True, signal)
    snapshot = TmuxSnapshot((pane,), "test-server", "2026-09-24T00:00:00Z")
    observed, actual, issue = lab.manager.tmux.observe(run, snapshot)
    assert observed == state and actual == pane
    assert bool(issue) == (state == "UNKNOWN")


@pytest.mark.parametrize("code,signal,expected_code,expected_signal", [
    ("7", "", 7, None), ("", "KILL", None, "KILL"),
])
def test_snapshot_keeps_exit_code_and_signal_separate(lab, monkeypatch, code, signal,
                                                    expected_code, expected_signal):
    backend = lab.manager.tmux
    backend.executable = "/synthetic/tmux"
    line = "\t".join(["$0", "@0", "%0", "123", "1", code, "0", "run", "host",
                      "claude", "1", "456", signal]) + "\n"
    monkeypatch.setattr(backend, "command", lambda *a, **k:
                        subprocess.CompletedProcess([], 0, line, ""))
    monkeypatch.setattr(backend, "server_identity", lambda pid: "server")
    snapshot = backend.snapshot()
    assert snapshot.available and len(snapshot.panes) == 1
    assert snapshot.panes[0].exit_code == expected_code
    assert snapshot.panes[0].exit_signal == expected_signal
