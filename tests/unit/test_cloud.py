import json
import subprocess

import pytest

from fourtop import cloud, e2b
from fourtop.cli import parser
from fourtop.services import Manager


def git(path, *args):
    subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    path = tmp_path / "My App"
    path.mkdir()
    git(path, "init", "-q")
    (path / ".gitignore").write_text(".venv/\n*.log\n")
    (path / "main.py").write_text("print(1)\n")
    git(path, "add", "-A")
    git(path, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")
    return path


def test_a_project_is_named_and_its_checkpoint_follows_its_dependencies(repo):
    first = cloud.project(str(repo / "."))
    assert (first.name, first.installer) == ("my-app", None)
    (repo / "uv.lock").write_text("v1")
    locked = cloud.project(str(repo))
    assert locked.installer == "uv sync" and locked.checkpoint != first.checkpoint
    (repo / "main.py").write_text("print(2)\n")  # code is synced, not baked in
    assert cloud.project(str(repo)).checkpoint == locked.checkpoint
    (repo / "uv.lock").write_text("v2")
    assert cloud.project(str(repo)).checkpoint != locked.checkpoint


def test_only_what_git_would_keep_travels(repo):
    (repo / ".venv").mkdir()
    (repo / ".venv/lib").write_text("installed")
    (repo / "debug.log").write_text("noise")
    (repo / "notes.md").write_text("untracked but not ignored")
    (repo / "main.py").unlink()  # tracked, deleted here: nothing to send
    assert cloud.tree_files(str(repo)) == [".gitignore", "notes.md"]


def test_the_fingerprint_moves_with_any_change_here(repo):
    before = cloud.fingerprint(str(repo))
    assert cloud.fingerprint(str(repo)) == before
    (repo / "notes.md").write_text("x")
    assert cloud.fingerprint(str(repo)) != before


def test_a_worktree_brings_the_repository_it_points_to(repo, tmp_path):
    assert cloud.git_common(str(repo)) is None
    git(repo, "worktree", "add", "-q", str(tmp_path / "wt"))
    assert cloud.git_common(str(tmp_path / "wt")) == str((repo / ".git").resolve())


def test_credentials_are_this_machines_own(lab, repo):
    home = lab.path / "home"
    (home / ".ssh").mkdir()
    (home / ".ssh/id_ed25519.pub").write_text("ssh-ed25519 AAAA me\n")
    (home / ".config/claude-code").mkdir(parents=True)
    (home / ".config/claude-code/oauth-token").write_text("tok\n")
    config = lab.write_config('[agents.claude]\nargs = ["--dangerously-skip-permissions"]\n')
    config.environment["E2B_API_KEY"] = "e2b_test"
    files = cloud.credentials(config, str(repo))
    assert files[".ssh/authorized_keys"] == b"ssh-ed25519 AAAA me\n"
    assert files[".ssh/environment"] == b"CLAUDE_CODE_OAUTH_TOKEN=tok\n"
    assert b"--dangerously-skip-permissions" in files[".config/4top/config.toml"]
    settings = json.loads(files[".claude/settings.json"])
    assert "4top-sandbox checkpoint" in json.dumps(settings["hooks"]["Stop"])
    # Only the user's own answer to the bypass warning is carried, never assumed.
    assert "skipDangerousModePermissionPrompt" not in settings
    assert f'[projects."{repo}"]' in files[".codex/config.toml"].decode()
    assert json.loads(files[".claude.json"])["projects"][str(repo)]["hasTrustDialogAccepted"]


def test_without_an_ssh_key_nothing_is_created(lab, repo):
    with pytest.raises(cloud.Dependency):
        cloud.credentials(lab.config, str(repo))


def test_a_sandbox_agent_is_attached_in_its_own_tmux_and_reattached(lab):
    host = cloud.sandbox_host("abc123", "box")
    argv = cloud.agent_argv(lab.config, host, ["resume", "h_key", "--yes"])
    assert argv[:3] == ["sh", "-c", cloud.REATTACH] and "255" in cloud.REATTACH
    assert argv[-1] == "tmux new-session -A -s agent 4top resume h_key --yes"
    assert cloud.agent_argv(lab.config, host, None)[-1] == "tmux attach-session -t agent"


def test_an_e2b_host_opens_through_the_sandbox_tmux(lab, monkeypatch):
    monkeypatch.setattr(e2b, "wake", lambda *args, **kwargs: None)
    argv = Manager(lab.config, cloud.sandbox_host("abc123", "box")).remote_argv(["resume", "k"])
    assert argv[0] == "sh" and argv[-1].startswith("tmux new-session -A -s agent")


def test_discovery_skips_builders_and_names_by_metadata(lab, monkeypatch):
    monkeypatch.setattr(e2b, "tagged", lambda config: [
        {"sandboxID": "b1", "metadata": {"fourtop": "1", "fourtop_name": "app-setup",
                                         "fourtop_role": "builder"}},
        {"sandboxID": "s2", "metadata": {"fourtop": "1", "fourtop_name": "zeta"}},
        {"sandboxID": "s1", "metadata": {"fourtop": "1", "fourtop_name": "alpha"}}])
    found = cloud.discover(lab.config)
    assert [(host.name, host.e2b, host.ssh) for host, _ in found] == [
        ("alpha", "s1", "user@s1"), ("zeta", "s2", "user@s2")]
    assert cloud.find(lab.config, "s2")[0].name == "zeta"
    with pytest.raises(cloud.Missing):
        cloud.find(lab.config, "nope")


def test_forks_are_named_after_their_parent_without_collisions(lab, monkeypatch):
    monkeypatch.setattr(cloud, "discover", lambda config: [(cloud.sandbox_host("x", "app-1"), {})])
    assert cloud._unique(lab.config, "app-1") == "app-1-2"
    assert cloud._unique(lab.config, "app-2") == "app-2"


@pytest.mark.parametrize("argv", [
    ["cloud", "new", "claude", "--name", "n"], ["cloud", "fork", "n", "-n", "3"],
    ["cloud", "race", "fix it", "--agents", "claude,codex"], ["cloud", "take", "n"],
    ["cloud", "rewind", "n"], ["cloud", "rewind", "n", "2"], ["cloud", "up", "h_key"],
    ["cloud", "home", "n"], ["cloud", "ls", "--json"], ["cloud", "rm", "a", "b"]])
def test_every_cloud_verb_parses(argv):
    assert parser().parse_args(argv).verb == argv[1]
