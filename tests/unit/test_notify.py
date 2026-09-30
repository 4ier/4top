"""Push notifications: what a hook says, where it goes, and wiring that comes out clean."""
import json
import os
import subprocess
import sys
import threading
import time
import tomllib
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from fourtop import notify
from fourtop.cli import main
from fourtop.config import Config
from fourtop.errors import FourtopError

SRC = Path(__file__).resolve().parents[2] / "src"


@pytest.fixture
def ntfy():
    """A local stand-in for an ntfy server that keeps what it was sent."""
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            received.append({"path": self.path, "json": json.loads(body)})
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    server.url = f"http://127.0.0.1:{server.server_port}/4top-testtopic"
    server.received = received
    yield server
    server.shutdown()


def configured(lab, url, extra=""):
    return lab.write_config(f'[notify]\nurl = "{url}"\n{extra}')


def claude_session(lab, native, cwd, request="make the build green"):
    root = Path(lab.config.root("claude").path) / "projects/test"
    root.mkdir(parents=True, exist_ok=True)
    file = root / (native + ".jsonl")
    file.write_text(json.dumps({"type": "user", "sessionId": native, "cwd": str(cwd),
                                "timestamp": "2026-09-30T00:00:00Z",
                                "message": {"role": "user", "content": request}}) + "\n")
    return file


NATIVE = "6f0c2d7e-1111-4a2b-9c3d-000000000001"


def test_notify_configuration_is_validated(lab):
    assert lab.write_config("").notify_url == ""
    config = configured(lab, "https://ntfy.sh/4top-abc", 'events = ["needs-you"]\n')
    assert config.notify_url == "https://ntfy.sh/4top-abc" and config.notify_events == ("needs-you",)
    for bad in ('[notify]\nurl = "ntfy.sh/topic"\n', '[notify]\nurl = "https://ntfy.sh/"\n',
                '[notify]\nurl = "https://ntfy.sh/a b"\n', '[notify]\nevents = ["done"]\n',
                '[notify]\nurl = "https://ntfy.sh/t"\nevents = ["sometimes"]\n',
                '[notify]\nurl = "https://ntfy.sh/t"\nsound = true\n'):
        with pytest.raises(FourtopError):
            lab.write_config(bad)


def test_publish_sends_json_so_any_title_survives(ntfy):
    assert notify.publish(ntfy.url, "mac · 中文项目", "Done", 4, ["x"]) == 200
    sent = ntfy.received[0]
    assert sent["path"] == "/"
    assert sent["json"] == {"topic": "4top-testtopic", "title": "mac · 中文项目", "message": "Done",
                            "priority": 4, "tags": ["x"]}


def test_hook_payloads_become_states():
    base = {"session_id": NATIVE, "transcript_path": "/t.jsonl", "cwd": "/w/app"}
    event = notify.parse("claude", {**base, "hook_event_name": "Notification",
                                    "notification_type": "permission_prompt",
                                    "message": "Claude needs your permission to use Bash"})
    assert (event.state, event.kind, event.native_id, event.detail) == (
        "needs-you", "permission", NATIVE, "Claude needs your permission to use Bash")
    assert notify.parse("claude", {**base, "hook_event_name": "Notification",
                                   "notification_type": "elicitation_dialog"}).kind == "question"
    assert notify.parse("claude", {**base, "hook_event_name": "Notification",
                                   "notification_type": "idle_prompt"}) is None
    assert notify.parse("claude", {**base, "hook_event_name": "Stop",
                                   "last_assistant_message": "All green."}).state == "done"
    assert notify.parse("claude", {**base, "hook_event_name": "Stop", "agent_id": "sub"}) is None
    assert notify.parse("claude", {**base, "hook_event_name": "StopFailure",
                                   "error_type": "rate_limit"}).detail == "rate_limit"
    # As Claude Code 2.1.280 really sends it (captured on ubuntu, not logged in).
    assert notify.parse("claude", {**base, "hook_event_name": "StopFailure",
                                   "error": "authentication_failed",
                                   "last_assistant_message": "Failed to authenticate."}).detail \
        == "authentication_failed — Failed to authenticate."
    codex = notify.parse("codex", {"type": "agent-turn-complete", "thread-id": NATIVE, "cwd": "/w",
                                   "input-messages": ["first", "latest ask"],
                                   "last-assistant-message": "Done it."})
    assert (codex.state, codex.request, codex.detail) == ("done", "latest ask", "Done it.")
    assert notify.parse("codex", {"type": "something-else"}) is None
    assert notify.parse("pi", {"event": "prompt", "title": "Pick one"}).state == "needs-you"
    assert notify.parse("pi", {"event": "prompt", "title": "", "kind": "custom"}) is None
    assert notify.parse("pi", {"event": "prompt", "title": ""}) is None   # an older extension
    assert notify.parse("pi", {"event": "error", "error": "429"}).detail == "429"
    assert notify.parse("pi", "not a dict") is None


def test_a_hook_names_host_and_project_and_the_latest_request(lab, ntfy):
    project = lab.path / "shop-api"
    project.mkdir()
    claude_session(lab, NATIVE, project)
    config = configured(lab, ntfy.url)
    payload = {"hook_event_name": "Notification", "notification_type": "permission_prompt",
               "session_id": NATIVE, "cwd": str(project), "message": "Allow Bash(rm -rf build)?"}
    assert notify.deliver(config, "claude", payload, manager=lab.manager)
    sent = ntfy.received[-1]["json"]
    assert sent["title"] == f"{notify.host_name()} · shop-api"
    assert sent["message"] == ("Needs you (permission): Allow Bash(rm -rf build)?\n"
                               "› make the build green")
    assert sent["priority"] == 4 and sent["tags"] == ["raised_hand", "claude"]


def test_a_worktree_is_named_after_its_repository(tmp_path):
    repo = tmp_path / "shop"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t",
                    "commit", "-q", "--allow-empty", "-m", "init"], check=True)
    subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", str(tmp_path / "shop-fix")],
                   check=True)
    assert notify.repo_name(str(repo)) == "shop"
    assert notify.repo_name(str(tmp_path / "shop-fix")) == "shop"
    assert notify.repo_name(str(tmp_path)) == tmp_path.name


def test_repeats_and_bursts_are_dropped_but_needing_you_is_not(lab, ntfy):
    claude_session(lab, NATIVE, lab.path)
    config = configured(lab, ntfy.url)
    clock = [1000.0]
    stop = {"hook_event_name": "Stop", "session_id": NATIVE, "last_assistant_message": "done"}
    ask = {"hook_event_name": "Notification", "notification_type": "permission_prompt",
           "session_id": NATIVE, "message": "Allow?"}

    def send(payload):
        return notify.deliver(config, "claude", payload, manager=lab.manager, now=lambda: clock[0])
    assert send(stop)
    clock[0] += 5
    assert send(ask)          # newly needs you: through, even this soon
    clock[0] += 5
    assert not send(ask)      # the same again
    clock[0] += 60
    assert send(stop)
    assert not send(stop)
    clock[0] += notify.REPEAT_SECONDS + 1
    assert send(stop)
    assert len(ntfy.received) == 4


def test_a_muted_session_or_project_sends_nothing(lab, ntfy):
    claude_session(lab, NATIVE, lab.path)
    config = configured(lab, ntfy.url)
    stop = {"hook_event_name": "Stop", "session_id": NATIVE, "last_assistant_message": "done"}
    (record,) = lab.manager.history(force=True).records
    lab.manager.mute(record.key)
    assert not notify.deliver(config, "claude", stop, manager=lab.manager)
    lab.manager.mute(record.key, False)
    lab.manager.mute_project(str(lab.path))
    assert not notify.deliver(config, "claude", stop, manager=lab.manager)
    assert not notify.deliver(config, "claude", {**stop, "session_id": "unknown", "cwd": str(lab.path)},
                              manager=lab.manager), "a session not found yet is muted by its directory"
    lab.manager.mute_project(str(lab.path), False)
    assert notify.deliver(config, "claude", stop, manager=lab.manager)
    assert len(ntfy.received) == 1


def test_events_not_chosen_are_not_sent(lab, ntfy):
    config = configured(lab, ntfy.url, 'events = ["needs-you"]\n')
    assert not notify.deliver(config, "claude", {"hook_event_name": "Stop", "session_id": NATIVE},
                              manager=lab.manager)
    assert ntfy.received == []


def test_a_hook_never_fails_the_agent(lab, monkeypatch, capsys):
    monkeypatch.setattr(notify, "_read_stdin", lambda: "{not json")
    assert notify.from_hook(str(lab.config_file), "claude", (), detach=False) == 0
    lab.config_file.write_text("[notify]\nurl = 3\n")   # even with a broken configuration
    monkeypatch.setattr(notify, "_read_stdin", lambda: '{"hook_event_name": "Stop"}')
    assert notify.from_hook(str(lab.config_file), "claude", (), detach=False) == 0
    assert capsys.readouterr().out == ""


def test_an_unreachable_server_is_swallowed_quickly(lab, monkeypatch):
    config = configured(lab, "http://127.0.0.1:9/4top-nowhere")
    monkeypatch.setattr(notify, "_read_stdin", lambda: json.dumps(
        {"hook_event_name": "Stop", "session_id": NATIVE, "last_assistant_message": "x"}))
    started = time.monotonic()
    assert notify.from_hook(config.config_path, "claude", (), detach=False) == 0
    assert time.monotonic() - started < notify.TIMEOUT + 2


def test_the_hook_command_returns_at_once(lab, ntfy):
    claude_session(lab, NATIVE, lab.path)
    configured(lab, ntfy.url)
    env = {**lab.env, "PYTHONPATH": str(SRC)}
    payload = json.dumps({"hook_event_name": "Stop", "session_id": NATIVE,
                          "last_assistant_message": "Shipped."})
    started = time.monotonic()
    run = subprocess.run([sys.executable, "-m", "fourtop", "--config", str(lab.config_file),
                          "notify", "--from-hook", "claude"], input=payload, env=env,
                         capture_output=True, text=True, timeout=20)
    assert run.returncode == 0 and run.stdout == ""
    assert time.monotonic() - started < 5
    deadline = time.monotonic() + 15
    while not ntfy.received and time.monotonic() < deadline:
        time.sleep(0.1)
    assert ntfy.received and ntfy.received[0]["json"]["message"].startswith("Done: Shipped.")


def test_codex_keeps_calling_the_notify_program_it_had(lab, tmp_path):
    marker = tmp_path / "called"
    previous = [sys.executable, "-c",
                f"import sys; open({str(marker)!r}, 'w').write(sys.argv[-1])"]
    payload = json.dumps({"type": "agent-turn-complete", "thread-id": NATIVE})
    assert notify.from_hook(str(lab.config_file), "codex", (*previous, payload), detach=False) == 0
    deadline = time.monotonic() + 10
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert json.loads(marker.read_text())["thread-id"] == NATIVE


CLAUDE_SETTINGS = {
    "model": "opus",
    "hooks": {
        "Stop": [{"hooks": [{"type": "command", "command": "their-stop-hook", "timeout": 10}]}],
        "PreToolUse": [{"matcher": "*", "hooks": [{"type": "command", "command": "guard"}]}],
    },
}


def test_claude_hooks_merge_and_come_out_exactly(tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps(CLAUDE_SETTINGS, indent=4))
    settings.chmod(0o640)
    argv = ["/opt/my tools/4top", "notify", "--from-hook", "claude"]
    notify.claude(settings, argv)
    wired = json.loads(settings.read_text())
    assert wired["model"] == "opus" and wired["hooks"]["PreToolUse"] == CLAUDE_SETTINGS["hooks"]["PreToolUse"]
    stop = wired["hooks"]["Stop"]
    assert stop[0] == CLAUDE_SETTINGS["hooks"]["Stop"][0]
    assert stop[1]["hooks"][0] == {"type": "command", "command": "'/opt/my tools/4top' notify "
                                   "--from-hook claude", "async": True, "timeout": 10}
    assert wired["hooks"]["Notification"][0]["matcher"] == notify.CLAUDE_WAITING
    assert "StopFailure" in wired["hooks"]
    assert settings.stat().st_mode & 0o777 == 0o640
    backup = tmp_path / "settings.json.4top-backup"
    assert json.loads(backup.read_text()) == CLAUDE_SETTINGS
    before = settings.read_text()
    assert "already" in notify.claude(settings, argv)       # idempotent
    assert settings.read_text() == before
    notify.claude(settings, None)
    assert json.loads(settings.read_text()) == CLAUDE_SETTINGS
    assert "already" in notify.claude(settings, None)


def test_claude_settings_are_created_and_a_broken_file_is_left_alone(tmp_path):
    settings = tmp_path / "claude" / "settings.json"
    notify.claude(settings, ["/bin/4top", "notify", "--from-hook", "claude"])
    assert set(json.loads(settings.read_text())["hooks"]) == set(notify.CLAUDE_EVENTS)
    notify.claude(settings, None)
    assert json.loads(settings.read_text()) == {}
    settings.write_text("{broken")
    with pytest.raises(FourtopError):
        notify.claude(settings, ["/bin/4top"])
    assert settings.read_text() == "{broken"


CODEX_CONFIG = '''model = "gpt-5"
notify = [
  "/Applications/Other.app/notifier",   # a comment
  "turn-ended",
]
approval_policy = "never"

[projects."/w"]
trust_level = "trusted"
'''


def test_codex_notify_chains_the_previous_program_and_restores_it(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text(CODEX_CONFIG)
    argv = ["/bin/4top", "notify", "--from-hook", "codex"]
    assert "previous notify still runs" in notify.codex(config, argv)
    data = tomllib.loads(config.read_text())
    assert data["notify"] == [*argv, "--", "/Applications/Other.app/notifier", "turn-ended"]
    assert data["projects"] == {"/w": {"trust_level": "trusted"}} and data["model"] == "gpt-5"
    assert "already" in notify.codex(config, argv)
    notify.codex(config, None)
    assert tomllib.loads(config.read_text()) == tomllib.loads(CODEX_CONFIG)


def test_codex_without_a_notify_gets_one_and_loses_it(tmp_path):
    config = tmp_path / "config.toml"
    original = '[projects."/w"]\ntrust_level = "trusted"\n'
    config.write_text(original)
    notify.codex(config, ["/bin/4top", "notify", "--from-hook", "codex"])
    assert tomllib.loads(config.read_text())["notify"][-1] == "--"   # Codex's JSON goes after
    notify.codex(config, None)
    assert config.read_text() == original


def test_pi_extension_is_ours_alone(tmp_path):
    extensions = tmp_path / "extensions"
    argv = ["/bin/4top", "notify", "--from-hook", "pi"]
    notify.pi(extensions, argv)
    source = (extensions / notify.PI_EXTENSION).read_text()
    assert json.dumps(argv) in source and "agent_settled" in source and "ui_prompt_start" in source
    assert "already" in notify.pi(extensions, argv)
    notify.pi(extensions, None)
    assert not (extensions / notify.PI_EXTENSION).exists()
    (extensions / notify.PI_EXTENSION).write_text("// someone else's\n")
    with pytest.raises(FourtopError):
        notify.pi(extensions, argv)


def test_the_hook_names_4top_absolutely_even_off_path(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["/src/fourtop/__main__.py"])
    local = tmp_path / ".local/bin/4top"
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "PYTHONPATH": "/src"}
    assert notify.launcher(env) == ["/usr/bin/env", "PYTHONPATH=/src", sys.executable, "-m", "fourtop"]
    local.parent.mkdir(parents=True)
    local.write_text("#!/bin/sh\n")
    local.chmod(0o755)
    assert notify.launcher(env) == [str(local)]    # a wrapper there, as on a git checkout
    monkeypatch.setattr(sys, "argv", ["/opt/uv/bin/4top"])
    assert notify.launcher(env) == [str(local)]    # not executable at that path: skipped


def test_install_records_a_random_topic_and_wires_the_agents_present(lab, capsys):
    home = Path(lab.env["HOME"])
    (home / ".claude").mkdir()
    (home / ".codex").mkdir()
    lab.config_file.write_text('[ui]\ncolor = "none"\n')
    assert main(["--config", str(lab.config_file), "notify", "--install"]) == 0
    out = capsys.readouterr().out
    config = Config.load(str(lab.config_file), environment=lab.env)
    assert config.notify_url.startswith("https://ntfy.sh/4top-") and config.color == "none"
    assert config.notify_url.rpartition("/")[2] in out
    assert "claude:" in out and "codex:" in out and "pi:" not in out
    hook = json.loads((home / ".claude/settings.json").read_text())["hooks"]["Stop"][0]["hooks"][0]
    assert hook["command"].endswith(f"--config {lab.config_file} notify --from-hook claude")
    codex = tomllib.loads((home / ".codex/config.toml").read_text())["notify"]
    assert os.path.isabs(codex[0]) and codex[-3:] == ["--from-hook", "codex", "--"]
    # A second install keeps the topic; uninstall leaves the agents as they were.
    assert main(["--config", str(lab.config_file), "notify", "--install"]) == 0
    assert Config.load(str(lab.config_file), environment=lab.env).notify_url == config.notify_url
    assert main(["--config", str(lab.config_file), "notify", "--uninstall", "--agent", "codex"]) == 0
    assert "notify" not in tomllib.loads((home / ".codex/config.toml").read_text())
    assert "hooks" in json.loads((home / ".claude/settings.json").read_text())
    assert main(["--config", str(lab.config_file), "notify", "--uninstall"]) == 0
    assert json.loads((home / ".claude/settings.json").read_text()) == {}
    assert "notify" not in tomllib.loads((home / ".codex/config.toml").read_text())


def test_test_message_reports_delivery_and_failure(lab, ntfy, capsys):
    configured(lab, ntfy.url)
    assert main(["--config", str(lab.config_file), "notify", "--test"]) == 0
    assert ntfy.received[0]["json"]["message"].startswith("Test")
    lab.config_file.write_text('[notify]\nurl = "http://127.0.0.1:9/4top-nowhere"\n')
    assert main(["--config", str(lab.config_file), "notify", "--test"]) == 6
    lab.config_file.write_text("")
    assert main(["--config", str(lab.config_file), "notify", "--test"]) == 3



def test_a_named_machine_is_named_in_the_title(lab, ntfy):
    notify.test(configured(lab, ntfy.url, 'name = "mac"\n'))
    assert ntfy.received[0]["json"]["title"] == "mac · 4top"
