"""Cloud tasks against a fake E2B (tests/fake_e2b.py).

The sandbox's side runs for real, locally: the scripts 4top sends over ssh run in
``sh`` with a home of the sandbox's own, git clones from and pushes to a local bare
repository, and the agent runs in a real tmux server on a private socket. Only the
network is fake: E2B's API, ssh, and the agent itself (a script).
"""
import json
import os
import shutil
import subprocess
import sys
import time
import uuid

import pytest
from conftest import eventually
from fake_e2b import KEY, FakeE2B, iso

from fourtop import cloud, e2b
from fourtop.cli import parser
from fourtop.errors import Conflict, Dependency, Missing, Unavailable

pytestmark = pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux is not installed")

GH = "ghs_github_secret_value"
CLAUDE = "claude-secret-123"

AGENT = """#!/bin/sh
printf '%s\\n' "$@" > "$HOME/agent-args"
echo "token:${CLAUDE_CODE_OAUTH_TOKEN#claude-secret-} gh:${GH_TOKEN:-none}" > "$HOME/agent-env"
case "$*" in *change*) echo work > DONE_BY_AGENT.txt ;; esac
exec sleep 600
"""
# 4top inside the sandbox: `attach` exists, and `list` knows the agent's session.
FOURTOP = """#!/bin/sh
case "$1" in
attach) exit 0 ;;
list) printf '{"key": "h_fake", "cwd": "%s", "agent": "claude", "started": "x"}\\n' "{directory}" ;;
esac
"""


def git(*args, cwd=None):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


@pytest.fixture
def sky(lab, monkeypatch):
    """A fake E2B, a repository to work on, and sandboxes that are local shells."""
    fake = FakeE2B(lab.path / "sandboxes")
    origin = lab.path / "origin.git"
    seed = lab.path / "seed"
    git("init", "-q", "--bare", "-b", "main", str(origin))
    git("init", "-q", "-b", "main", str(seed))
    (seed / "README").write_text("hello\n")
    git("add", "-A", cwd=seed)
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init", cwd=seed)
    git("push", "-q", str(origin), "main", cwd=seed)
    work = lab.path / "work"
    monkeypatch.setattr(cloud, "WORK", str(work))
    fakebin = lab.path / "sandbox-bin"
    fakebin.mkdir()
    for name, text in (("claude", AGENT), ("4top", FOURTOP.replace("{directory}", str(work / "origin")))):
        (fakebin / name).write_text(text)
        (fakebin / name).chmod(0o755)
    (fakebin / "python3").symlink_to(sys.executable)
    socket = f"4top-agents-test-{uuid.uuid4().hex[:8]}"
    path = os.pathsep.join([str(fakebin), os.path.dirname(shutil.which("git")),
                            os.path.dirname(shutil.which("tmux")), "/usr/bin", "/bin"])

    def local(config, host, script, stdin=b"", *, timeout=600, check=True):
        # Only an awake sandbox answers.
        if fake.sandboxes.get(host.e2b, {}).get("state") != "running":
            raise Unavailable(f"{host.name}: not running")
        env = {"HOME": str(fake.homes / host.e2b), "PATH": path, "FOURTOP_AGENTS_SOCKET": socket,
               "TERM": "xterm-256color"}
        result = subprocess.run(["sh", "-c", cloud.PRELUDE + script], input=stdin, env=env,
                                capture_output=True, timeout=60)
        if check and result.returncode:
            raise Unavailable(f"{host.name}: exit {result.returncode} · {result.stderr.decode()[-300:]}")
        return result

    monkeypatch.setattr(cloud, "run_in", local)
    (lab.path / "home/.ssh").mkdir()
    (lab.path / "home/.ssh/id_ed25519.pub").write_text("ssh-ed25519 AAAA me@tablet\n")
    lab.env.update(FOURTOP_E2B_API=fake.url, FOURTOP_E2B_ENVD=fake.url, E2B_API_KEY=KEY,
                   GH_TOKEN=GH, CLAUDE_CODE_OAUTH_TOKEN=CLAUDE)
    config = lab.write_config("")
    yield lab, fake, config, origin, socket
    subprocess.run(["tmux", "-L", socket, "kill-server"], capture_output=True)
    fake.close()


def request(origin, prompt="make a change", **kwargs):
    return cloud.Request("claude", prompt, f"file://{origin}", **kwargs)


def files_under(*folders):
    for folder in folders:
        for path in folder.rglob("*"):
            if path.is_file() and ".git" not in path.parts:
                yield path


def test_a_task_runs_from_the_repository_url_alone(sky):
    lab, fake, config, origin, socket = sky
    task = cloud.new(config, request(origin, ref="main"))
    assert (task["name"], task["state"], task["branch"]) == ("origin", "running", "4top/origin")
    assert task["lifetime_left"] == 3600 and task["usd_per_hour"] == pytest.approx(0.1332)
    created = fake.asked("POST", "/sandboxes")[0][2]
    # Paused, never killed, at the end of its lifetime; and never woken by traffic.
    assert created["timeout"] == 3600 and created["autoPause"] and created["autoResume"] == {"enabled": False}
    assert created["metadata"]["fourtop_repo"] == f"file://{origin}"
    home = fake.homes / "sbx001"
    assert (home / ".ssh/authorized_keys").read_text() == "ssh-ed25519 AAAA me@tablet\n"
    directory = lab.path / "work/origin"
    eventually(lambda: (home / "agent-env").is_file())
    # Cloned in the sandbox, on the task's branch; the agent got its prompt and its
    # sign-in, and never the GitHub token.
    assert git("rev-parse", "--abbrev-ref", "HEAD", cwd=directory).strip() == "4top/origin"
    assert (home / "agent-args").read_text().split("\n")[:2] == ["--dangerously-skip-permissions",
                                                                 "make a change"]
    assert (home / "agent-env").read_text().strip() == "token:123 gh:none"
    claude = json.loads((home / ".claude.json").read_text())
    assert claude["projects"][str(directory)]["hasTrustDialogAccepted"]
    # The session is named after its history key, so `4top attach h_fake` finds it.
    eventually(lambda: "h_fake" in subprocess.run(["tmux", "-L", socket, "ls"], capture_output=True,
                                                  text=True).stdout, timeout=15)
    assert (home / ".4top-task/session").read_text() == "h_fake"
    # No secret was written anywhere in the sandbox, nor the E2B key.
    for path in files_under(home, directory):
        text = path.read_bytes()
        assert GH.encode() not in text and CLAUDE.encode() not in text and KEY.encode() not in text, path


def test_done_pushes_the_branch_keeps_a_snapshot_and_ends_the_sandbox(sky):
    lab, fake, config, origin, socket = sky
    cloud.new(config, request(origin))
    eventually(lambda: (lab.path / "work/origin/DONE_BY_AGENT.txt").is_file())
    result = cloud.done(config, "origin")
    assert result["branch"] == "4top/origin" and len(result["commit"]) == 40
    assert git("--git-dir", str(origin), "show", "4top/origin:DONE_BY_AGENT.txt") == "work\n"
    assert git("--git-dir", str(origin), "log", "-1", "--format=%s", "4top/origin").startswith("4top: make a change")
    assert result["snapshot"].startswith("team/fourtop-task-origin-") and fake.sandboxes == {}
    # The agent, and every copy of its credentials in memory, was gone before the snapshot.
    assert subprocess.run(["tmux", "-L", socket, "ls"], capture_output=True).returncode != 0
    rows, _ = cloud.tasks(config)
    assert [(row["name"], row["state"], row["branch"]) for row in rows] == [("origin", "done", "4top/origin")]
    # Removing a finished task deletes its snapshot.
    assert cloud.remove(config, "origin").startswith("snapshot") and fake.snapshots == {}


def test_a_task_that_changed_nothing_pushes_nothing(sky):
    lab, fake, config, origin, _ = sky
    cloud.new(config, request(origin, prompt="look around"))
    eventually(lambda: (fake.homes / "sbx001/agent-env").is_file())
    result = cloud.done(config, "origin", snapshot=False)
    assert result["branch"] == "" and result["snapshot"] == "" and fake.snapshots == {}
    assert "4top/origin" not in git("--git-dir", str(origin), "branch")


def test_rm_refuses_to_discard_work_not_brought_home(sky):
    lab, fake, config, origin, _ = sky
    cloud.new(config, request(origin))
    eventually(lambda: (lab.path / "work/origin/DONE_BY_AGENT.txt").is_file())
    fake.pause("sbx001")
    with pytest.raises(Conflict, match="1 changed files and 0 commits are not on 4top/origin"):
        cloud.remove(config, "origin")
    assert "sbx001" in fake.sandboxes  # woken to look, and kept
    assert cloud.remove(config, "origin", discard=True) == "sandbox sbx001" and fake.sandboxes == {}


def test_rm_of_a_task_with_nothing_to_lose_needs_no_flag(sky):
    lab, fake, config, origin, _ = sky
    cloud.new(config, request(origin, prompt="look around"))
    eventually(lambda: (fake.homes / "sbx001/agent-env").is_file())
    assert cloud.remove(config, "origin") == "sandbox sbx001"


def test_a_failed_start_leaves_no_sandbox(sky):
    lab, fake, config, origin, _ = sky
    with pytest.raises(Unavailable):
        cloud.new(config, cloud.Request("claude", "x", f"file://{lab.path}/missing.git"))
    assert fake.sandboxes == {} and fake.asked("DELETE", "/sandboxes/sbx001")


def test_missing_credentials_create_nothing(sky):
    lab, fake, config, origin, _ = sky
    config.environment.pop("CLAUDE_CODE_OAUTH_TOKEN")
    with pytest.raises(Dependency, match="Claude credentials"):
        cloud.new(config, request(origin))
    config.environment["CLAUDE_CODE_OAUTH_TOKEN"] = CLAUDE
    config.environment.pop("GH_TOKEN")
    config.environment["PATH"] = "/nonexistent"  # no `gh` to ask either
    with pytest.raises(Dependency, match="GitHub token"):
        cloud.new(config, cloud.Request("claude", "x", "octo/app"))
    assert not fake.asked("POST", "/sandboxes")


def test_the_daily_budget_counts_every_device_and_refuses_before_creating(sky):
    lab, fake, config, origin, _ = sky
    now = time.time()
    # Three hours of a 2 vCPU / 2 GiB sandbox earlier today, from another device:
    # $0.40. Someone else's sandbox in the same project does not count.
    other = fake.add({"fourtop": "1", "fourtop_name": "earlier"}, started=max(e2b.start_of_day(), now - 4 * 3600))
    fake.event({**other, "_since": now - 10800 - 60}, "killed", now - 60)
    del fake.sandboxes[other["sandboxID"]]
    stranger = fake.add({"user": "someone"}, started=now - 36000)
    fake.event(stranger, "killed", now)
    del fake.sandboxes[stranger["sandboxID"]]
    spent = cloud.budget(config)["spent_today_usd"]
    assert spent == pytest.approx(10800 * e2b.rate(2, 2048), rel=0.01)
    config = lab.write_config("[cloud]\ndaily_budget_usd = 0.5\n")  # 0.40 + up to 0.27 > 0.5
    with pytest.raises(Conflict, match="daily_budget_usd"):
        cloud.new(config, request(origin))
    assert not fake.asked("POST", "/sandboxes")
    config = lab.write_config("[cloud]\ndaily_budget_usd = 0.5\nmax_minutes = 30\n")  # up to 0.07
    assert cloud.new(config, request(origin))["lifetime_left"] == 1800


def test_cost_so_far_is_the_stretches_e2b_recorded(lab, sky):
    _, fake, config, *_ = sky
    now = int(time.time())
    record = fake.add({"fourtop": "1", "fourtop_task": "1", "fourtop_name": "t",
                       "fourtop_deadline": str(int(now + 3000))}, started=now - 1000)
    fake.pause(record["sandboxID"], at=now - 400)  # ran 600 s
    record.update(state="running", _since=now - 100, startedAt=iso(now - 100))  # resumed
    row = cloud.describe(config, fake.public(record), now)
    assert row["running_seconds"] == pytest.approx(700, abs=2)
    assert row["cost_usd"] == pytest.approx(700 * e2b.rate(2, 2048), abs=1e-4)
    assert row["lifetime_left"] == 3000 and row["estimate"] and row["state"] == "running"


def test_waking_stays_within_the_lifetime(sky):
    _, fake, config, *_ = sky
    now = int(time.time())
    live = fake.add({"fourtop": "1", "fourtop_name": "a", "fourtop_deadline": str(int(now + 900))},
                    state="paused")
    host = cloud.sandbox_host(live["sandboxID"], "a")
    e2b.wake(config, host, now=now)
    assert fake.asked("POST", "/v2/sandboxes")[-1][2] == {"timeout": 900}
    over = fake.add({"fourtop": "1", "fourtop_name": "b", "fourtop_deadline": str(int(now - 5))},
                    state="paused")
    host = cloud.sandbox_host(over["sandboxID"], "b")
    with pytest.raises(Conflict, match="used its lifetime"):
        e2b.wake(config, host, now=now)
    assert fake.sandboxes[over["sandboxID"]]["state"] == "paused"
    e2b.wake(config, host, grace=True, now=now)  # bringing work home is always allowed
    assert fake.asked("POST", "/v2/sandboxes")[-1][2] == {"timeout": e2b.GRACE_SECONDS}


def test_expired_snapshots_are_pruned_and_fresh_ones_kept(sky):
    _, fake, config, *_ = sky
    old, fresh = int(time.time() - 8 * 86400), int(time.time() - 86400)
    fake.snapshots.update({f"team/fourtop-task-a-{old}:default": "x",
                           f"team/fourtop-task-b-{fresh}:default": "y",
                           "team/someone-elses:default": "z"})
    assert cloud.prune(config) == [f"team/fourtop-task-a-{old}:default"]
    assert sorted(fake.snapshots) == [f"team/fourtop-task-b-{fresh}:default", "team/someone-elses:default"]


def test_ls_json_is_the_data_a_panel_shows(sky):
    lab, fake, config, origin, _ = sky
    cloud.new(config, request(origin, prompt="look around"))
    run = lab.cli("cloud", "ls", "--json")
    assert run.returncode == 0, run.stderr
    lines = [json.loads(line) for line in run.stdout.splitlines()]
    assert lines[0]["name"] == "origin" and lines[0]["state"] == "running"
    assert {"cost_usd", "lifetime_left", "deadline", "usd_per_hour", "prompt", "repo"} <= set(lines[0])
    assert set(lines[-1]["budget"]) >= {"spent_today_usd", "daily_budget_usd", "left_usd"}
    hosts = cloud.hosts(config)
    assert [(host.name, host.e2b, row["state"]) for host, row in hosts] == [("origin", "sbx001", "running")]
    # What the panel adds as sections: one listing call, nothing per sandbox.
    before = len(fake.log)
    assert [(host.name, host.e2b) for host in cloud.sources(config)] == [("origin", "sbx001")]
    assert [entry[1] for entry in fake.log[before:]] == ["/v2/sandboxes"]
    fake.close()
    assert cloud.sources(config) == []  # E2B unreachable: no sections, no error


def test_github_urls_are_cloned_over_https():
    assert cloud.clone_url("git@github.com:octo/app.git") == "https://github.com/octo/app.git"
    assert cloud.clone_url("octo/app") == "https://github.com/octo/app.git"
    assert cloud.clone_url("https://github.com/octo/app") == "https://github.com/octo/app.git"
    assert cloud.clone_url("https://gitlab.com/octo/app.git") == "https://gitlab.com/octo/app.git"
    assert cloud.github("file:///tmp/x/origin.git") is None


@pytest.mark.parametrize("argv", [
    ["cloud", "new", "claude", "fix the flaky test", "--repo", "octo/app", "--ref", "main"],
    ["cloud", "ls", "--json"], ["cloud", "open", "n"], ["cloud", "pause", "n"],
    ["cloud", "done", "n", "--pr"], ["cloud", "rm", "n", "--discard"]])
def test_every_cloud_verb_parses(argv):
    assert parser().parse_args(argv).verb == argv[1]


def test_the_cloud_section_is_validated(lab):
    config = lab.write_config('[cloud]\ntemplate = "4top:v2"\nmax_minutes = 45\n')
    assert (config.cloud.template, config.cloud.max_minutes, config.cloud.daily_budget_usd) == ("4top:v2", 45, 5.0)
    # Only a [cloud] section makes the panel ask E2B for tasks to list.
    assert config.cloud.enabled and not lab.write_config("").cloud.enabled
    for text in ("max_minutes = 0", 'template = "a b"', "surprise = 1"):
        with pytest.raises(Exception):
            lab.write_config(f"[cloud]\n{text}\n")


def test_an_unknown_task_is_missing(sky):
    _, _, config, *_ = sky
    with pytest.raises(Missing):
        cloud.done(config, "nope")


def test_a_lifetime_longer_than_the_plan_allows_is_said_plainly(sky):
    lab, fake, _, origin, _ = sky
    config = lab.write_config("[cloud]\nmax_minutes = 120\ndaily_budget_usd = 50\n")
    with pytest.raises(Conflict, match=r"120-minute sandbox .*1 hours.*max_minutes"):
        cloud.new(config, request(origin))
    assert fake.sandboxes == {}


def test_a_pull_request_goes_from_the_task_branch_to_its_ref(monkeypatch, lab):
    asked = []

    def answer(req, label, timeout):
        asked.append((req.get_method(), req.full_url, json.loads(req.data) if req.data else None,
                      req.get_header("Authorization")))
        return {"default_branch": "trunk"} if req.get_method() == "GET" else {"html_url": "https://x/pull/7"}

    monkeypatch.setattr(e2b, "_request", answer)
    task = {"repo": "https://github.com/octo/app.git", "ref": "", "branch": "4top/app", "name": "app",
            "agent": "claude", "prompt": "fix the flaky test"}
    assert cloud.pull_request(lab.config, task, "tok") == "https://x/pull/7"
    assert asked[0][:2] == ("GET", "https://api.github.com/repos/octo/app")
    method, url, body, auth = asked[1]
    assert (method, url, auth) == ("POST", "https://api.github.com/repos/octo/app/pulls", "Bearer tok")
    assert (body["head"], body["base"], body["title"]) == ("4top/app", "trunk", "fix the flaky test")
