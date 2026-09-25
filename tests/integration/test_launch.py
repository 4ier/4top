"""4top starts a native CLI. It owns no pane, so these tests use real processes only."""
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

from fourtop.models import LaunchPlan

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"


def latest_report(lab, timeout=10):
    reports = lab.path / "reports"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        found = sorted(reports.glob("*.json")) if reports.is_dir() else []
        if found:
            return json.loads(found[-1].read_text())
        time.sleep(.05)
    raise AssertionError("the native process wrote no report")


def test_new_plan_runs_the_original_cli_in_the_requested_directory(lab):
    plan = lab.manager.new("pi", str(lab.path))
    assert plan.argv[0].endswith("/pi")
    assert "--session-id" in plan.argv, "pi advertises an exact session id, so it is preallocated"
    result = subprocess.run(plan.argv, cwd=plan.cwd, env=plan.environment,
                            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    report = latest_report(lab)
    assert report["cwd"] == str(lab.path)
    assert report["native_id"] == plan.native_id
    assert report["term"] == "xterm-256color"
    assert not any("tmux" in value or "byobu" in value for value in plan.argv), \
        "4top never routes an agent through a multiplexer"
    assert report["argv"] == list(plan.argv[1:])
    canary = lab.env["TEST_CANARY"].encode()
    assert report["canary_hash"] == hashlib.sha256(canary).hexdigest()
    assert not any(lab.env["TEST_CANARY"] in value for value in plan.argv)
    on_disk = [path.name for path in Path(lab.config.state_dir).rglob("*")
               if path.is_file() and canary in path.read_bytes()]
    assert on_disk == [], "environment values are never written to local state"


def test_hand_over_replaces_4top_with_the_native_cli(lab):
    code = (
        "import sys; from fourtop.config import Config; from fourtop.services import Manager;"
        f"config = Config.load(r'{lab.config_file}'); manager = Manager(config);"
        f"manager.hand_over(manager.new('pi', r'{lab.path}'))"
    )
    result = subprocess.run([sys.executable, "-c", code], cwd=str(lab.path), capture_output=True,
                            text=True, timeout=30, stdin=subprocess.DEVNULL,
                            env={**lab.env, "PYTHONPATH": str(SRC)})
    assert result.returncode == 0, result.stdout + result.stderr
    report = latest_report(lab)
    assert report["agent"] == "pi" and report["cwd"] == str(lab.path)


def test_resume_plan_targets_the_exact_session_file(lab):
    assert subprocess.run(lab.manager.new("pi", str(lab.path)).argv, cwd=str(lab.path),
                          env=lab.env, stdin=subprocess.DEVNULL, capture_output=True,
                          timeout=30).returncode == 0
    records = lab.manager.history(force=True).records
    assert len(records) == 1
    plan = lab.manager.resume(records[0].key)
    assert plan.argv == (plan.executable, "--session", records[0].file)
    assert plan.cwd == str(lab.path)
    assert plan.argv[0].endswith("/pi")


def test_resume_requires_an_exact_identifier_and_a_known_directory(lab):
    record_file = lab.path / "claude-record.jsonl"
    record_file.write_text(json.dumps({
        "type": "user", "sessionId": "11111111-2222-3333-4444-555555555555",
        "cwd": str(lab.path), "timestamp": "2026-09-24T00:00:00Z",
        "message": {"role": "user", "content": "hello"}}) + "\n")
    record = lab.manager.history(force=True).records
    assert record == [], "a file outside the configured roots is never adopted"


def test_claude_and_codex_resume_argv_are_exact(lab, tmp_path):
    native = "11111111-2222-3333-4444-555555555555"
    claude_root = Path(lab.config.root("claude").path)
    target = claude_root / "projects/test" / (native + ".jsonl")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({
        "type": "user", "sessionId": native, "cwd": str(lab.path),
        "timestamp": "2026-09-24T00:00:00Z",
        "message": {"role": "user", "content": "keep me"}}) + "\n")
    plan = lab.manager.resume(native)
    assert plan.argv == (plan.executable, "--resume", native)
    assert plan.argv[0].endswith("/claude")

    codex_native = "99999999-8888-7777-6666-555555555555"
    codex_root = Path(lab.config.root("codex").path)
    codex_file = codex_root / "sessions/2026/09/24" / ("rollout-2026-09-24-" + codex_native + ".jsonl")
    codex_file.parent.mkdir(parents=True, exist_ok=True)
    codex_file.write_text(json.dumps({"type": "session_meta", "payload": {
        "id": codex_native, "cwd": str(lab.path), "timestamp": "2026-09-24T00:00:00Z"}}) + "\n" + json.dumps(
        {"type": "response_item", "payload": {"role": "user",
         "content": [{"type": "input_text", "text": "keep me"}]}}) + "\n")
    plan = lab.manager.resume(codex_native)
    assert plan.argv == (plan.executable, "resume", codex_native)


def test_demo_never_plans_a_process():
    from fourtop.services import DemoManager
    manager = DemoManager()
    try:
        manager.new("pi", "/tmp")
    except AttributeError:
        return  # the demo object has no launch API at all
    raise AssertionError("DEMO must not be able to plan a native process")


def test_plan_passes_the_caller_environment_without_relocating_config(lab):
    plan: LaunchPlan = lab.manager.new("claude", str(lab.path))
    assert plan.environment["TEST_CANARY"] == lab.env["TEST_CANARY"]
    # An absent override is meaningful: setting it would move Claude's config lookup.
    assert "CLAUDE_CONFIG_DIR" not in plan.environment
