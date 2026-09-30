"""What a host says about its sessions beyond their transcripts: prompts on a kept
agent's screen, the person's names and mutes, the repository and its changes.

The screens in tests/fixtures/screens were captured from the real CLIs (Claude Code
2.1.280 on macOS; Codex 0.157.1 on macOS and 0.158.0 on Linux; Pi 0.87.0) with
`tmux capture-pane -p`, in a throwaway directory, asking for a harmless command
without any permission bypass.
"""
import json
import subprocess
from pathlib import Path

import pytest

from fourtop import gitinfo, hosts
from fourtop.config import Host
from fourtop.errors import FourtopError, Unavailable
from fourtop.marks import Marks, muted
from fourtop.models import Session
from fourtop.prompts import PROMPTS, attention, detect
from fourtop.services import Manager

SCREENS = Path(__file__).parents[1] / "fixtures" / "screens"
EXPECTED = {
    "claude-bash-permission.txt": "claude-permission",
    "claude-write-permission.txt": "claude-permission",
    "claude-trust.txt": "claude-trust",
    "claude-question.txt": "claude-question",
    "codex-exec-approval.txt": "codex-approval",
    "codex-exec-approval-2.txt": "codex-approval",
    "codex-patch-approval.txt": "codex-approval",
    "codex-trust.txt": "codex-trust",
}


@pytest.mark.parametrize("name", sorted(path.name for path in SCREENS.glob("*.txt")))
def test_each_captured_screen_is_read_as_what_it_shows(name):
    prompt = detect((SCREENS / name).read_text(encoding="utf-8"))
    assert (prompt.name if prompt else None) == EXPECTED.get(name)


def test_every_prompt_shape_has_a_captured_screen():
    assert {prompt.name for prompt in PROMPTS} == set(EXPECTED.values())


def test_only_permission_prompts_can_be_approved_or_denied():
    for prompt in PROMPTS:
        assert bool(prompt.approve) == bool(prompt.deny) == (prompt.kind == "permission")
    assert attention((SCREENS / "claude-question.txt").read_text()) == "question"


def test_a_prompt_quoted_in_the_conversation_is_not_a_prompt():
    # The same words scrolled up, with the idle input box drawn below them.
    prompt = (SCREENS / "claude-bash-permission.txt").read_text()
    idle = (SCREENS / "claude-idle.txt").read_text()
    assert detect(prompt + "\n" + idle) is None


# ----- repository and working-tree changes ---------------------------------------


def git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                   env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@t", "HOME": str(cwd), "PATH": "/usr/bin:/bin:/opt/homebrew/bin"})


@pytest.fixture
def repo(tmp_path):
    gitinfo._worktrees.clear()
    gitinfo._repos.clear()
    main = tmp_path / "project"
    (main / "src").mkdir(parents=True)
    git("init", "-q", "-b", "main", cwd=main)
    (main / "a.txt").write_text("one\n")
    git("add", "a.txt", cwd=main)
    git("commit", "-q", "-m", "first", cwd=main)
    git("worktree", "add", "-q", str(tmp_path / "project-feature"), "-b", "feature", cwd=main)
    yield main
    gitinfo._worktrees.clear()
    gitinfo._repos.clear()


def test_worktrees_and_subdirectories_belong_to_one_repository(repo, tmp_path):
    assert gitinfo.repo_of(str(repo)) == str(repo)
    assert gitinfo.repo_of(str(repo / "src")) == str(repo)
    assert gitinfo.repo_of(str(tmp_path / "project-feature")) == str(repo), \
        "a linked worktree is its main worktree's repository, not a project of its own"
    (tmp_path / "plain").mkdir()
    assert gitinfo.repo_of(str(tmp_path / "plain")) == ""
    assert gitinfo.repo_of(str(repo / "gone")) == "", "a directory that is gone is in no repository"


def test_changes_are_gits_answer_and_are_reused_until_something_moves(repo, tmp_path, monkeypatch):
    changes = gitinfo.Changes(tmp_path / "cache" / "git.json", {"PATH": "/usr/bin:/bin:/opt/homebrew/bin"})
    last = "2026-09-30T00:00:00+00:00"
    assert changes.of({str(repo): last}) == {str(repo): {
        "files": 0, "insertions": 0, "deletions": 0, "untracked": 0, "dirty": False}}

    (repo / "a.txt").write_text("one\ntwo\nthree\n")
    (repo / "new.txt").write_text("x\n")
    asked = []
    real = changes._ask
    monkeypatch.setattr(changes, "_ask", lambda roots: asked.append(roots) or real(roots))
    assert changes.of({str(repo): last})[str(repo)]["files"] == 0, "same index, HEAD and write: cached"
    assert asked == []
    # The agent that edited the file also wrote its transcript.
    later = "2026-09-30T00:01:00+00:00"
    found = changes.of({str(repo / "src"): later, str(repo): later})
    assert found[str(repo)] == {"files": 1, "insertions": 2, "deletions": 0, "untracked": 1, "dirty": True}
    assert found[str(repo / "src")] == found[str(repo)], "a subdirectory shares its worktree's answer"
    assert asked[-1] == [str(repo)], "one git call per worktree, not per directory"

    again = gitinfo.Changes(tmp_path / "cache" / "git.json", {"PATH": "/usr/bin:/bin:/opt/homebrew/bin"})
    monkeypatch.setattr(again, "_ask", lambda roots: pytest.fail("the next listing reads the cache"))
    assert again.of({str(repo): later})[str(repo)]["files"] == 1


def test_a_directory_outside_git_has_no_changes(tmp_path):
    (tmp_path / "plain").mkdir()
    changes = gitinfo.Changes(tmp_path / "git.json", {"PATH": "/usr/bin:/bin"})
    assert changes.of({str(tmp_path / "plain"): "2026-09-30T00:00:00+00:00"}) == {}


def test_shortstat_parsing():
    assert gitinfo.parse(" 1 file changed, 1 deletion(-)\n", 0) == {
        "files": 1, "insertions": 0, "deletions": 1, "untracked": 0, "dirty": True}
    assert gitinfo.parse("", 2)["dirty"] is True


# ----- names and mutes ------------------------------------------------------------


def test_names_and_mutes_are_kept_on_the_host(lab):
    marks = Marks(lab.manager.store)
    marks.label("h_one", "  the  migration ")
    marks.mute("h_two")
    marks.mute_project("/srv/bots")
    value = marks.load()
    assert value["labels"] == {"h_one": "the migration"} and value["muted"] == ["h_two"]
    assert muted(value, "h_two", "/elsewhere", "")
    assert muted(value, "h_x", "/srv/bots/worker", "") and muted(value, "h_x", "/tmp/w", "/srv/bots")
    assert not muted(value, "h_x", "/srv/botsmith", "")
    marks.label("h_one", "")
    marks.mute("h_two", False)
    marks.mute_project("/srv/bots/", False)
    assert marks.load() == {"schema_version": 1, "labels": {}, "muted": [], "projects": []}


def test_a_marks_file_of_another_schema_is_not_replaced(lab):
    path = lab.manager.store.directory / "marks.json"
    path.write_text(json.dumps({"schema_version": 99, "labels": {"k": "v"}}))
    with pytest.raises(Unavailable):
        Marks(lab.manager.store).label("h_one", "x")
    assert json.loads(path.read_text())["schema_version"] == 99


def test_label_mute_and_projects_from_the_command_line(lab):
    import subprocess as sp
    sp.run(lab.manager.new("pi", str(lab.path)).argv, cwd=str(lab.path), env=lab.env,
           stdin=sp.DEVNULL, capture_output=True, timeout=30)
    (key,) = [record.key for record in lab.manager.history(force=True).records]
    assert lab.cli("label", key[:8], "--", "-nightly bot").returncode == 0
    assert lab.cli("mute", "--project", str(lab.path)).returncode == 0
    row = json.loads(lab.cli("list", "--json").stdout)
    assert row["label"] == "-nightly bot" and row["muted"] is True
    assert lab.cli("unmute", "--project", str(lab.path)).returncode == 0
    assert lab.cli("mute").returncode == 2, "a key or --project is required"
    assert json.loads(lab.cli("list", "--json").stdout)["muted"] is False
    projects = [json.loads(line) for line in lab.cli("projects", "--json").stdout.splitlines()]
    assert [(p["path"], p["sessions"], p["agents"]) for p in projects] == [(str(lab.path), 1, ["pi"])]


def test_a_new_agent_takes_its_first_request_as_text(lab):
    result = lab.cli("new", "pi", "--cwd", str(lab.path), "--prompt", "-review the diff",
                     stdin=subprocess.DEVNULL)
    assert result.returncode == 0, result.stderr
    (report,) = [json.loads(path.read_text()) for path in (lab.path / "reports").glob("*.json")]
    assert report["argv"][-2:] == ["--", "-review the diff"]
    with pytest.raises(FourtopError):
        lab.manager.dispatch("pi", str(lab.path), "  ")


# ----- the same, asked of a remote host ---------------------------------------------

HOST = Host(name="venus", ssh="me@venus")


class Remote:
    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def __call__(self, config, host, args):
        self.calls.append(args)
        return self.answers.get(args[0], (0, "", ""))


@pytest.fixture
def remote(lab, monkeypatch):
    def install(answers=None):
        fake = Remote(answers or {})
        monkeypatch.setattr("fourtop.services.run_remote", fake)
        return Manager(lab.config, HOST), fake
    return install


def test_a_remote_session_is_talked_to_through_its_hosts_cli(remote):
    screen = {"key": "h_k", "session": "h_k", "attention": "permission", "prompt": "codex-approval",
              "lines": ["Would you like to run the following command?", "\x1b[31mred\x1b[0m"]}
    manager, fake = remote({"peek": (0, json.dumps(screen) + "\n", ""),
                            "projects": (0, json.dumps({"path": "/srv/app", "sessions": 2}) + "\n", "")})
    row = Session("h_k", "codex", "/srv/app", "t", "", "", host="venus")
    assert manager.peek(row).endswith("\nred"), "the host's screen, controls stripped"
    manager.send(row, "-go on")
    manager.approve(row)
    manager.deny(row)
    manager.label(row, "")
    manager.label(row, "deploy")
    manager.mute(row, True)
    manager.mute_project("/srv/app", False)
    assert manager.projects() == [{"path": "/srv/app", "sessions": 2}]
    assert fake.calls == [
        ["peek", "h_k", "--lines", "40", "--json"], ["send", "h_k", "--", "-go on"],
        ["approve", "h_k"], ["deny", "h_k"], ["label", "h_k"], ["label", "h_k", "--", "deploy"],
        ["mute", "h_k"], ["unmute", "--project", "/srv/app"], ["projects", "--json"]]


def test_a_remote_refusal_keeps_its_words_and_its_code(remote):
    manager, _ = remote({
        "approve": (4, "", "4top: No permission prompt on this agent's screen now; nothing was pressed\n"),
        "peek": (2, "", "4top: error: argument command: invalid choice: 'peek'\n"),
        "send": (255, "", "ssh: connect to host venus: Connection refused\n")})
    row = Session("h_k", "codex", "/srv/app", "t", "", "", host="venus")
    with pytest.raises(FourtopError, match="nothing was pressed") as refused:
        manager.approve(row)
    assert refused.value.code == 4
    with pytest.raises(FourtopError, match="cannot peek yet"):
        manager.peek(row)
    with pytest.raises(Unavailable, match="ssh exit 255"):
        manager.send(row, "hi")


def test_a_placeholder_codex_session_is_named_only_when_unambiguous(monkeypatch):
    from types import SimpleNamespace

    from fourtop import resident
    renamed = []
    monkeypatch.setattr(resident, "tmux_binary", lambda env: "tmux")
    monkeypatch.setattr(resident, "_tmux", lambda tmux, env, *args: renamed.append(args)
                        or SimpleNamespace(returncode=0))
    rows = [Session("h_old", "codex", "/srv/app", "", "2026-09-30T00:00:00+00:00", ""),
            Session("h_new", "codex", "/srv/app", "", "2026-09-30T00:10:03+00:00", ""),
            Session("h_later", "codex", "/srv/app", "", "2026-09-30T00:20:00+00:00", ""),
            Session("h_pi", "pi", "/srv/app", "", "2026-09-30T00:10:01+00:00", "")]
    created = 1790727000.0  # 2026-09-30T00:10:00Z
    monkeypatch.setattr(resident, "unnamed", lambda env: [("new-codex-1", created, "/srv/app")])
    assert resident.adopt({}, rows, set()) == {"new-codex-1": "h_new"}, \
        "the earliest Codex session that began after the placeholder did"
    assert renamed == [("rename-session", "-t", "=new-codex-1", "h_new")]
    assert resident.adopt({}, rows, {"h_new"}) == {"new-codex-1": "h_later"}, \
        "a session whose agent is already running is not taken twice"
    monkeypatch.setattr(resident, "unnamed", lambda env: [("new-codex-1", created, "/srv/app"),
                                                          ("new-codex-2", created, "/srv/app")])
    assert resident.adopt({}, rows, set()) == {}, "two placeholders in one directory: no guess"


def test_changes_from_a_host_are_numbers_and_a_flag():
    assert hosts.changes_from({"files": 2, "insertions": "9", "deletions": -1, "dirty": True,
                               "evil": "x"}) == {"files": 2, "dirty": True}
    assert hosts.changes_from("nope") == {}
