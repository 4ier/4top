import concurrent.futures
import hashlib
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from conftest import eventually

from fourtop.errors import Conflict, FourtopError, Unavailable
from fourtop.services import Manager
from fourtop.tmux import process_identity

pytestmark = pytest.mark.tmux


def test_handoff_survives_cli_exit_without_environment_on_disk(tmux_lab):
    lab = tmux_lab
    result = lab.cli("new", "claude", "--cwd", str(lab.path), "--detach", "--", "--child")
    assert result.returncode == 0, result.stderr
    run = lab.manager.resolve_run(result.stdout.strip())
    report = lab.report(run)
    assert report["pid"] == run["pid"]
    assert report["canary_hash"] == hashlib.sha256(lab.env["TEST_CANARY"].encode()).hexdigest()
    identity = process_identity(run["pid"])
    child_identity = process_identity(report["child_pid"])
    assert identity and child_identity
    again = eventually(lambda: (value := lab.report(run))["count"] > report["count"] and value)
    assert again["nonce"] == report["nonce"] and process_identity(run["pid"]) == identity
    assert process_identity(report["child_pid"]) == child_identity
    assert lab.manager.tmux.observe(run)[0] == "LIVE"
    for path in lab.config.state_dir.rglob("*"):
        if path.is_file():
            assert lab.env["TEST_CANARY"].encode() not in path.read_bytes()
    start_command = lab.manager.tmux.command(["display-message", "-p", "-t", run["tmux"]["pane"], "#{pane_start_command}"]).stdout
    assert lab.env["TEST_CANARY"] not in start_command
    assert "--child" not in start_command


def test_panel_process_kill_does_not_stop_handed_off_agent(tmux_lab):
    lab = tmux_lab
    script = ("from fourtop.config import Config; from fourtop.services import Manager; import time,json; "
              f"m=Manager(Config.load({str(lab.config_file)!r})); "
              f"r=m.new('codex',{str(lab.path)!r}); print(r['run_id'],flush=True); time.sleep(60)")
    process = subprocess.Popen([sys.executable, "-c", script], env=lab.env, stdout=subprocess.PIPE, text=True)
    try:
        run_id = process.stdout.readline().strip()
        run = lab.manager.resolve_run(run_id)
        old = lab.report(run)
        process.kill()
        process.wait(timeout=5)
        assert lab.manager.tmux.observe(run)[0] == "LIVE"
        eventually(lambda: lab.report(run)["count"] > old["count"])
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        process.stdout.close()


def test_instant_exit_retains_status_and_preview(tmux_lab):
    lab = tmux_lab
    run = lab.manager.new("pi", str(lab.path), extra=("--fail",))
    state, pane, _ = eventually(lambda: (value := lab.manager.tmux.observe(run))[0] == "EXIT" and value)
    assert pane.exit_code == 7
    preview = lab.manager.tmux.preview(run)
    assert "INTENTIONAL EXIT 7" in preview
    assert "EXIT" in [row.state for row in lab.manager.snapshot().rows]


@pytest.mark.parametrize("agent", ["claude", "codex", "pi"])
def test_exact_native_resume_creates_new_process_and_rejects_duplicate(tmux_lab, agent):
    lab = tmux_lab
    first = lab.manager.new(agent, str(lab.path))
    first_report = lab.report(first)
    history = lab.manager.history(force=True).records[0]
    if agent == "codex":
        assert first["launch_history_key"] is None
        lab.manager.link(first["run_id"], history.key)
    with pytest.raises(Conflict):
        lab.manager.resume(history.key)
    lab.manager.terminate(first["run_id"])
    second = lab.manager.resume(history.key)
    second_report = lab.report(second)
    assert first["run_id"] != second["run_id"] and first["pid"] != second["pid"]
    assert first_report["native_id"] == second_report["native_id"]
    assert second["launch_history_key"] == history.key
    with pytest.raises(Conflict):
        lab.manager.resume(history.key)


def test_same_cwd_codex_runs_are_never_auto_bound(tmux_lab):
    lab = tmux_lab
    runs = [lab.manager.new("codex", str(lab.path)) for _ in range(2)]
    for run in runs:
        lab.report(run)
    lab.manager.history(force=True)
    rows = lab.manager.snapshot().rows
    assert len([row for row in rows if row.state == "LIVE"]) == 2
    assert len([row for row in rows if row.state == "HIST"]) == 2
    assert all(run["launch_history_key"] is None for run in runs)


def test_concurrent_resume_serializes_across_cli_processes(tmux_lab):
    lab = tmux_lab
    run = lab.manager.new("claude", str(lab.path), extra=("--fail",))
    lab.report(run)
    eventually(lambda: lab.manager.tmux.observe(run)[0] == "EXIT")
    key = lab.manager.history(force=True).records[0].key
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: lab.cli("resume", key, "--yes", "--detach"), range(2)))
    assert sorted(result.returncode for result in results) == [0, 4], [r.stderr for r in results]
    assert sum(row.state == "LIVE" for row in lab.manager.snapshot().rows) == 1


def test_registered_other_socket_blocks_duplicate(tmux_lab):
    lab = tmux_lab
    run = lab.manager.new("claude", str(lab.path))
    lab.report(run)
    other = Manager(replace(lab.config, socket=str(lab.path / "second.sock")))
    try:
        with pytest.raises(Conflict):
            other.resume(run["launch_history_key"])
    finally:
        other.close()


def test_server_restart_and_reused_pane_id_never_target_unrelated_pane(tmux_lab):
    lab = tmux_lab
    run = lab.manager.new("codex", str(lab.path))
    lab.report(run)
    # This is the fixture's isolated server, not an application recovery operation.
    lab.manager.tmux.command(["kill-server"])
    eventually(lambda: process_identity(run["pid"]) != run["process_identity"])
    lab.manager.tmux.command(["new-session", "-d", "-s", "unrelated", "/bin/sleep", "60"], no_create=False)
    with pytest.raises(Unavailable):
        lab.manager.terminate(run["run_id"])
    snapshot = lab.manager.tmux.snapshot()
    assert len(snapshot.panes) == 1 and not snapshot.panes[0].dead


def test_rename_move_split_and_respawn(tmux_lab):
    lab = tmux_lab
    run = lab.manager.new("codex", str(lab.path))
    lab.report(run)
    backend = lab.manager.tmux
    pane = backend.verify(run)
    backend.command(["rename-session", "-t", pane.session, "renamed-session"])
    assert backend.observe(run)[0] == "LIVE"
    backend.command(["split-window", "-d", "-t", pane.pane, "/bin/sleep", "60"])
    assert backend.observe(run)[0] == "LIVE"
    backend.command(["break-pane", "-d", "-s", pane.pane])
    assert backend.observe(run)[0] == "LIVE"
    backend.command(["respawn-pane", "-k", "-t", pane.pane, "/bin/sleep", "60"])
    assert backend.observe(run)[0] == "UNKNOWN"
    with pytest.raises(Unavailable):
        backend.terminate(run)


def test_changed_marker_is_unknown_not_safe_to_resume(tmux_lab):
    lab = tmux_lab
    run = lab.manager.new("claude", str(lab.path))
    lab.report(run)
    lab.manager.tmux.command(["set-option", "-p", "-t", run["tmux"]["pane"], "@4top_run_id", "changed"])
    assert lab.manager.tmux.observe(run)[0] == "UNKNOWN"
    with pytest.raises(Unavailable):
        lab.manager.resume(run["launch_history_key"])


def test_target_changed_after_verify_is_not_killed(tmux_lab, monkeypatch):
    lab = tmux_lab
    run = lab.manager.new("codex", str(lab.path))
    lab.report(run)
    backend = lab.manager.tmux
    original = backend.verify
    def replace_before_action(record, allow_dead=False):
        pane = original(record, allow_dead)
        backend.command(["set-option", "-p", "-t", pane.pane, "@4top_run_id", "changed-after-confirm"])
        return pane
    monkeypatch.setattr(backend, "verify", replace_before_action)
    with pytest.raises(Unavailable):
        backend.terminate(run)
    assert not backend.snapshot().panes[0].dead


def test_special_cwd_and_argv_are_data(tmux_lab):
    lab = tmux_lab
    folder = lab.path / "项目 '; $(touch GOTCHA);\n-new"
    folder.mkdir()
    prompt = "literal; $(touch GOTCHA); ' \n --session-id-is-not-an-option"
    run = lab.manager.new("codex", str(folder), extra=(prompt,))
    report = lab.report(run)
    assert report["cwd"] == str(folder) and report["argv"] == [prompt]
    assert not (folder / "GOTCHA").exists()
    for path in lab.config.state_dir.rglob("*.json"):
        assert prompt not in path.read_text()


def test_stale_environment_replaced_in_memory(tmux_lab):
    lab = tmux_lab
    backend = lab.manager.tmux
    old_env = {**lab.env, "TEST_CANARY": "old", "PATH": "/usr/bin:/bin"}
    subprocess.run([backend.executable, "-S", backend.socket, "new-session", "-d", "-s", "old",
                    "/bin/sleep", "60"], env=old_env, check=True, capture_output=True)
    run = lab.manager.new("claude", str(lab.path))
    report = lab.report(run)
    assert report["canary_hash"] == hashlib.sha256(lab.env["TEST_CANARY"].encode()).hexdigest()
    assert report["pane"] == run["tmux"]["pane"]


def test_history_is_never_modified_by_search_preview_or_dismiss(tmux_lab):
    lab = tmux_lab
    run = lab.manager.new("claude", str(lab.path), extra=("--fail",))
    report = lab.report(run)
    eventually(lambda: lab.manager.tmux.observe(run)[0] == "EXIT")
    source = Path(report["source"])
    before = source.read_bytes()
    lab.manager.search("中文", full=True)
    lab.manager.preview(lab.manager.snapshot().rows[0])
    lab.manager.dismiss(run["run_id"])
    assert source.read_bytes() == before
    assert all(row.run_id != run["run_id"] for row in lab.manager.snapshot().rows)


def test_attach_non_tty_never_launches_replacement(tmux_lab):
    lab = tmux_lab
    run = lab.manager.new("codex", str(lab.path))
    lab.report(run)
    result = lab.cli("attach", run["run_id"])
    assert result.returncode == 5 and "terminal" in result.stderr
    assert len(lab.manager.store.list()[0]) == 1


def test_source_query_failure_is_stale_not_zero(tmux_lab, monkeypatch):
    lab = tmux_lab
    run = lab.manager.new("claude", str(lab.path))
    lab.report(run)
    from fourtop.models import TmuxSnapshot
    monkeypatch.setattr(lab.manager.tmux, "snapshot", lambda: TmuxSnapshot((), "", "now", False, "permission denied"))
    snapshot = lab.manager.snapshot()
    assert snapshot.rows[0].state == "UNKNOWN" and snapshot.rows[0].stale
    with pytest.raises(Unavailable):
        lab.manager.resume(run["launch_history_key"])


@pytest.mark.parametrize("phase", ["before-pane", "plan-transfer", "grant", "after-grant"])
def test_handoff_faults_never_blindly_restart_or_persist_secrets(tmux_lab, monkeypatch, phase):
    import fourtop.tmux as module
    lab = tmux_lab
    backend = lab.manager.tmux
    if phase == "before-pane":
        original = backend.command
        def command(args, **kwargs):
            if args[0] == "new-session":
                raise Unavailable("injected before create")
            return original(args, **kwargs)
        monkeypatch.setattr(backend, "command", command)
    else:
        original = module.send_frame
        def send(sock, payload):
            if phase == "plan-transfer" and "environment" in payload:
                raise OSError("injected before plan transfer")
            if phase == "grant" and "go" in payload:
                raise OSError("injected before execution grant")
            if phase == "after-grant" and "go" in payload:
                original(sock, payload)
                raise OSError("injected after execution grant")
            return original(sock, payload)
        monkeypatch.setattr(module, "send_frame", send)
    with pytest.raises(FourtopError):
        lab.manager.new("claude", str(lab.path))
    runs, issues = lab.manager.store.list()
    assert len(runs) == 1 and not issues
    run = runs[0]
    if phase != "after-grant":
        eventually(lambda: backend.observe(run)[0] in ("EXIT", "MISSING"))
        assert not (lab.path / "reports").exists()
    else:
        # Execution permission might have arrived. Never infer absence from the parent error.
        state = eventually(lambda: (value := backend.observe(run)[0]) in ("LIVE", "EXIT", "MISSING") and value)
        if state == "LIVE":
            lab.report(run)  # Native transcript is created after execution handoff.
            with pytest.raises(Conflict):
                lab.manager.resume(run["launch_history_key"])
    for file in lab.config.state_dir.rglob("*.json"):
        assert lab.env["TEST_CANARY"].encode() not in file.read_bytes()


def test_missing_cwd_or_binary_does_not_create_runtime(tmux_lab):
    lab = tmux_lab
    for operation in (
        lambda: lab.manager.new("codex", str(lab.path / "missing")),
        lambda: lab.manager.new("cursor", str(lab.path)),
    ):
        with pytest.raises(FourtopError):
            operation()
    lab.config.executables["codex"] = "4top-nonexistent-binary"
    with pytest.raises(FourtopError):
        lab.manager.new("codex", str(lab.path))
    assert lab.manager.store.list()[0] == []


def test_launch_binding_does_not_claim_current_native_context(tmux_lab):
    lab = tmux_lab
    run = lab.manager.new("claude", str(lab.path))
    lab.report(run)
    assert run["current_history_key"] is None
    # Native /new can switch context invisibly to 4top; launch association is immutable.
    lab.manager.history(force=True)
    row = next(row for row in lab.manager.snapshot().rows if row.run_id == run["run_id"])
    assert row.binding == "explicit-launch"
    assert lab.manager.store.get(run["run_id"])["current_history_key"] is None


def test_closed_terminal_without_exit_evidence_blocks_resume(tmux_lab, monkeypatch):
    lab = tmux_lab
    run = lab.manager.new("claude", str(lab.path))
    lab.report(run)
    key = lab.manager.history(force=True).records[0].key
    snapshot = lab.manager.tmux.snapshot()
    pending = replace(snapshot, panes=tuple(
        replace(pane, dead=True, exit_code=None, exit_signal=None)
        if pane.run_id == run["run_id"] else pane for pane in snapshot.panes))
    monkeypatch.setattr(lab.manager.tmux, "snapshot", lambda: pending)
    assert lab.manager.tmux.observe(run)[0] == "UNKNOWN"
    with pytest.raises(Unavailable, match="cannot be verified"):
        lab.manager.resume(key)


def test_signalled_exit_retains_signal_evidence(tmux_lab):
    import os
    import signal

    lab = tmux_lab
    run = lab.manager.new("pi", str(lab.path))
    lab.report(run)
    assert process_identity(run["pid"]) == run["process_identity"]
    os.kill(run["pid"], signal.SIGKILL)  # Only this isolated test's exact process.
    _, pane, _ = eventually(lambda: (value := lab.manager.tmux.observe(run))[0] == "EXIT" and value)
    assert pane.exit_code is None
    assert pane.exit_signal.upper() in {"KILL", "SIGKILL"}
