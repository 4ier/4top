"""Exact kernel exit evidence; no signals, guessed identity or masked success."""
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from fourtop.models import Pane, TmuxSnapshot
from fourtop.tmux import linux_zombie_exit


def make_proc(root: Path, state="Z", status="1792", start="4242", pid=123):
    fields = ["0"] * 50
    fields[0], fields[19], fields[49] = state, start, status
    path = root / "123/stat"
    path.parent.mkdir(parents=True, exist_ok=True)
    # A legal comm containing spaces and parentheses must not shift field offsets.
    path.write_text(f"{pid} (agent ) name) " + " ".join(fields) + "\n")
    (root / "123/status").write_text("Uid:\t" + "\t".join([str(os.getuid())] * 4) + "\n")
    boot = root / "sys/kernel/random/boot_id"
    boot.parent.mkdir(parents=True, exist_ok=True)
    boot.write_text("test-boot\n")
    return "linux:test-boot:123:4242"


@pytest.mark.parametrize("state,status,expected", [
    ("Z", "1792", (7, None)), ("Z", "9", (None, "9")),
    ("Z", "139", (None, "11")), ("Z", "0", (None, None)),
    ("R", "1792", None), ("S", "1792", None), ("T", "1792", None),
    ("Z", "-1", None), ("Z", "65536", None), ("Z", "bad", None),
])
def test_kernel_wait_status_requires_zombie(tmp_path, state, status, expected):
    identity = make_proc(tmp_path, state, status)
    assert linux_zombie_exit(123, identity, tmp_path) == expected


@pytest.mark.parametrize("identity", [None, "", "ps:123:other", "linux:other-boot:123:4242",
                                      "linux:test-boot:124:4242", "linux:test-boot:123:4243"])
def test_kernel_exit_rejects_missing_or_reused_identity(tmp_path, identity):
    make_proc(tmp_path)
    assert linux_zombie_exit(123, identity, tmp_path) is None


@pytest.mark.parametrize("damage", ["missing", "truncated", "other-pid", "wrong-owner", "missing-uid", "partial-uid"])
def test_kernel_exit_fails_closed_on_untrusted_proc(tmp_path, monkeypatch, damage):
    identity = make_proc(tmp_path)
    path = tmp_path / "123/stat"
    if damage == "missing":
        path.unlink()
    elif damage == "truncated":
        path.write_text("123 (agent) Z 1")
    elif damage == "other-pid":
        make_proc(tmp_path, pid=124)
    elif damage == "wrong-owner":
        (tmp_path / "123/status").write_text("Uid:\t" + "\t".join([str(os.getuid() + 1)] * 4))
    elif damage == "missing-uid":
        (tmp_path / "123/status").write_text("State:\tZ\n")
    else:
        (tmp_path / "123/status").write_text(f"Uid:\t{os.getuid()}\n")
    assert linux_zombie_exit(123, identity, tmp_path) is None


def test_verified_zombie_can_close_the_exit_evidence_gap(lab, monkeypatch):
    import fourtop.tmux as module
    run = {"run_id": "test-run", "host_id": "test-host", "pid": 123,
           "process_identity": "linux:boot:123:42",
           "tmux": {"server": "test-server", "pane": "%0"}}
    pane = Pane("$0", "@0", "%0", 123, True, None, 0,
                "test-run", "test-host", "claude", True)
    snapshot = TmuxSnapshot((pane,), "test-server", "now")
    monkeypatch.setattr(module, "sys", SimpleNamespace(platform="linux"))
    def evidence(pid, identity):
        assert (pid, identity) == (123, "linux:boot:123:42")
        return 7, None
    monkeypatch.setattr(module, "linux_zombie_exit", evidence)
    state, observed, issue = lab.manager.tmux.observe(run, snapshot)
    assert state == "EXIT" and observed.exit_code == 7
    assert "Linux kernel" in issue


def test_proc_inode_owner_is_not_process_identity(tmp_path, monkeypatch):
    import fourtop.tmux as module
    identity = make_proc(tmp_path)
    # Changing inode ownership metadata cannot override the actual Uid fields.
    monkeypatch.setattr(module.os, "fstat", lambda fd: SimpleNamespace(st_uid=0))
    assert linux_zombie_exit(123, identity, tmp_path) == (7, None)
