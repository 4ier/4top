"""Real pseudo-terminal tests. These are not Textual's headless driver."""
import shlex
import sys
from dataclasses import replace

import pexpect
import pytest
from conftest import eventually

from fourtop.errors import Conflict
from fourtop.tmux import Tmux, process_identity

pytestmark = [pytest.mark.tmux, pytest.mark.pty]


def cli_pty(lab, *args):
    return pexpect.spawn(sys.executable, ["-m", "fourtop", "--config", str(lab.config_file), *args],
                         env=lab.env, encoding="utf-8", codec_errors="replace", timeout=12,
                         dimensions=(30, 110))


def tmux_client(lab, session):
    backend = lab.manager.tmux
    return pexpect.spawn(backend.executable, ["-S", backend.socket, "attach-session", "-t", session],
                         env=lab.env, encoding="utf-8", codec_errors="replace", timeout=12,
                         dimensions=(30, 110))


def detach(child):
    child.sendcontrol("b")
    child.send("d")


def test_external_attach_detach_preserves_identity_and_resize(tmux_lab):
    lab = tmux_lab
    run = lab.manager.new("codex", str(lab.path))
    before = lab.report(run)
    identity = process_identity(run["pid"])
    child = cli_pty(lab, "attach", run["run_id"])
    try:
        child.expect("COUNT=")
        child.sendline("ping")
        child.expect("ECHO ping")
        child.setwinsize(35, 120)
        child.expect("RESIZED")
        detach(child)
        child.expect(pexpect.EOF)
        child.close()
        assert child.exitstatus == 0
    finally:
        child.close(force=True)
    assert process_identity(run["pid"]) == identity
    after = eventually(lambda: (value := lab.report(run))["count"] > before["count"] and value)
    assert after["nonce"] == before["nonce"]
    assert len(lab.manager.store.list()[0]) == 1


def test_tui_returns_to_same_selection_after_detach(tmux_lab):
    lab = tmux_lab
    run = lab.manager.new("codex", str(lab.path), "pty continuity")
    report = lab.report(run)
    child = cli_pty(lab)
    try:
        child.expect("pty continuity")
        child.send("\r")
        child.expect("COUNT=")
        detach(child)
        child.expect("Returned from tmux")
        child.send("q")
        child.expect(pexpect.EOF)
        child.close()
        assert child.exitstatus == 0
    finally:
        child.close(force=True)
    assert lab.manager.store.load_view()["selected"] == "r_" + run["run_id"]
    assert lab.manager.tmux.observe(run)[0] == "LIVE"
    assert lab.report(run)["nonce"] == report["nonce"]


def test_two_clients_are_not_detached_by_attach(tmux_lab):
    lab = tmux_lab
    run = lab.manager.new("claude", str(lab.path))
    lab.report(run)
    first, second = cli_pty(lab, "attach", run["run_id"]), cli_pty(lab, "attach", run["run_id"])
    try:
        first.expect("COUNT=")
        second.expect("COUNT=")
        assert eventually(lambda: lab.manager.tmux.verify(run).clients == 2)
        detach(second)
        second.expect(pexpect.EOF)
        assert first.isalive()
        assert eventually(lambda: lab.manager.tmux.verify(run).clients == 1)
        detach(first)
        first.expect(pexpect.EOF)
    finally:
        first.close(force=True)
        second.close(force=True)


def test_inside_tmux_switches_one_exact_client_and_no_nesting(tmux_lab):
    lab = tmux_lab
    run = lab.manager.new("codex", str(lab.path), "switch target")
    lab.report(run)
    backend = lab.manager.tmux
    backend.command(["new-session", "-d", "-s", "caller", "/bin/sh"])
    child = tmux_client(lab, "caller")
    try:
        child.sendline(shlex.join([sys.executable, "-m", "fourtop", "--config", str(lab.config_file)]))
        child.expect("switch target")
        child.send("\r")
        child.expect("COUNT=")
        assert eventually(lambda: backend.verify(run).clients == 1)
        clients = backend.command(["list-clients", "-F", "#{session_id}"]).stdout.splitlines()
        assert clients == [backend.verify(run).session]
        detach(child)
        child.expect(pexpect.EOF)
    finally:
        child.close(force=True)


def test_shared_calling_session_requires_explicit_client(tmux_lab):
    lab = tmux_lab
    run = lab.manager.new("codex", str(lab.path))
    lab.report(run)
    backend = lab.manager.tmux
    backend.command(["new-session", "-d", "-s", "caller", "/bin/sh"])
    response = backend.command(["display-message", "-p", "-t", "caller", "#{pane_id}\t#{pid}\t#{session_id}"])
    pane_id, server_pid, caller_session = response.stdout.strip().split("\t")
    first, second = tmux_client(lab, "caller"), tmux_client(lab, "caller")
    try:
        def clients():
            result = backend.command(["list-clients", "-F", "#{client_tty}\t#{session_id}"]).stdout.splitlines()
            return [line.split("\t") for line in result]
        eventually(lambda: len(clients()) == 2)
        inside_env = {**lab.env, "TMUX": f"{backend.socket},{server_pid},0", "TMUX_PANE": pane_id}
        inside = Tmux(replace(lab.config, environment=inside_env))
        with pytest.raises(Conflict):
            inside.attach(run)
        before = clients()
        assert all(session == caller_session for tty, session in before)
        inside.attach(run, before[0][0])
        after = clients()
        mapping = dict(after)
        assert mapping[before[0][0]] == backend.verify(run).session
        assert mapping[before[1][0]] == caller_session
        assert first.isalive() and second.isalive()
    finally:
        first.close(force=True)
        second.close(force=True)


def test_cross_socket_inside_attach_fails_explicitly(tmux_lab):
    lab = tmux_lab
    run = lab.manager.new("codex", str(lab.path))
    lab.report(run)
    env = {**lab.env, "TMUX": f"{lab.path / 'elsewhere.sock'},123,0", "TMUX_PANE": "%0"}
    backend = Tmux(replace(lab.config, environment=env))
    with pytest.raises(Conflict, match="Cross-socket"):
        backend.attach(run)
