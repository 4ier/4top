# 4top

**Your coding agents, one terminal.**

Find your work. Resume an exact native session. Reach your other machines over SSH.
A keyboard-first terminal dashboard built on **session-ls**, with no extra 4top
daemon, account, model calls, or telemetry.

[中文](README.zh-CN.md) · [Compatibility](docs/compatibility.md) · [Remote hosts](docs/remote-design.md) · [Phone or tablet](docs/mobile.md) · [Acceptance](docs/validation/README.md)

![4top synthetic demo — no real user history](docs/demo/demo.svg)

> **0.2.0a6 — alpha.** With tmux installed, 4top keeps its list beside the agents it
> opens; it records nothing about them and asks tmux what is open. Codex **0.155.1**
> passed an authenticated exact-resume smoke check against an earlier build;
> Claude Code, Pi and other native versions are not certified.
> [Evidence](docs/validation/macos-0.1.0a2.md). It is on PyPI as a pre-release: `uv tool install 4top`.

## Install

macOS or Linux; **Python 3.11+**. No multiplexer is required. Current validation
records, rather than this minimum target, determine which versions were tested.
Windows users need a Linux environment such as WSL; native Windows is unsupported.

```sh
uv tool install 4top            # alpha, so a pre-release
pip install --pre 4top          # pip needs --pre for a pre-release
```

`session-ls`, the parser this builds on, is an ordinary dependency on PyPI. A pip
mirror may lag behind for a new project: if `4top` seems not to exist, add
`--index-url https://pypi.org/simple`, or wait for the mirror to sync.

```sh
4top --demo                     # isolated synthetic demo, no real history
```

### From this checkout

```sh
git clone https://github.com/4ier/4top.git
cd 4top
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/4top --demo
```

`session-ls`, the parser this builds on, is a separate project with its own
repository and pipeline ([4ier/session-ls](https://github.com/4ier/session-ls)); a
checkout installs it from PyPI like any other dependency. The root wheel contains
only `fourtop`, and `session-ls` keeps its small, stdlib-only CLI.

```sh
. .venv/bin/activate
4top                       # browse every session on this machine
4top new codex             # start an agent here, in this terminal
4top --host build-box      # view another machine over ssh
```

4top never installs or authenticates agents for you. Install the original Claude
Code, Codex, or Pi CLI separately and keep its existing authentication flow.
The panel also reads Cursor transcripts, but does not launch or resume Cursor.

## The daily loop

Open `4top` and every machine is listed at once: this one first, then each
configured host, most recent sessions first, each machine with its own page. Select
a session and press **Enter**. The machine that owns it checks that it can resume
there, and the agent opens. There is no confirmation dialog, and a refusal (a
missing CLI, a directory that is gone) appears in the list instead.

With **tmux** installed, 4top runs in a tmux server of its own and opens each agent
beside the list, like files beside an editor's file tree. Opening another session
keeps the first one running in the background; `●` marks the open ones, and Enter
on one shows it again. `Alt-←` / `Alt-→` (or `→` in the list, or a click) move
between the list and the agent. On a narrow screen, such as a phone, the focused
side fills the screen. **`q` detaches**: the agents keep running, and running `4top`
again brings everything back. `Q` closes them all.

Without tmux, or with `layout = "plain"`, Enter runs the agent in this terminal and
the list returns when it exits.

| Key | Action |
| --- | --- |
| `↑` / `↓`, `Enter` | Select, open (or show, if it is already open) |
| `→`, `Alt-←` / `Alt-→` | Move to the agent / between list and agent (tmux layout) |
| `[` / `]` | Previous / next page of the machine under the cursor |
| `/`, `Enter`, `Esc` | Search metadata, return to the list, clear search and filters |
| `p`, `f`, `a` | Show one project; fold the machine under the cursor; show subagent sessions |
| `Ctrl-F` | Explicit literal full-content search; `Esc` cancels |
| `Space`, `i` | Latest messages (read-only; `e` for earlier), details |
| `n`, `r`, `?` | New agent on the selected machine, refresh, help |
| `q`, `Q`, `Ctrl-C` | Detach (tmux) or quit; close all agents and quit |

Each row says what the session is doing, from the end of its transcript: `⟳ working`
while the agent is mid-turn, `▶ your turn` once it has handed the turn back, and
`✗ stopped` for a turn that went silent for ten minutes. A third line, `› …`, is
your latest request when it differs from how the session began, and the git branch
joins the project. Badges are shown for the last day only; older sessions are history.

Sessions an agent started for itself (Codex's approval reviewer, spawned workers)
are hidden until you press `a`. The preview opens at the latest messages, where the
conversation is, rather than at the injected context a transcript begins with.

Search supports case-insensitive words and quoted phrases; all terms must match.
Full search decodes JSON text, including Chinese escaped as `\u....`. It reads
only configured sources, reports partial scans, and never executes transcript
content. The UI renders titles and previews as plain, sanitized text.

## Sessions, not processes

A native agent is a file. `pi --session <path>`, `claude --resume <id>` and
`codex resume <id>` all work from the transcript alone, so the transcript is the
thing 4top tracks, and the process is the ephemeral part.

**Resume** starts a new native process from an exact ID or source path. It cannot
restore lost memory, network connections, shell children, or a destroyed machine.
Resume uses the CLI's **current native configuration**; 4top does not replay the
original launch flags. Review native permissions before sending another task.

4top keeps no record of processes either. In the tmux layout a pane is tagged with
the session it runs, and `●` means tmux has that pane now; nothing else is
remembered. Failed queries are reported as issues and never rendered as an empty
machine.

Two consequences worth knowing. A resumed agent is a **new** process; two agents in
one directory still have **no code/worktree isolation**. Outside the tmux layout,
`4top new` runs the agent in the foreground of this terminal and ends with it.

## Remote hosts over SSH

Point 4top at any machine you can already `ssh` into. There is no daemon to
install, no port to open, and no credential store: the remote side is the same
CLI, and the local 4top only runs it.

```toml
# ~/.config/4top/config.toml
[hosts.build-box]
ssh = "me@build-box"                 # any ssh destination, including a tailnet name
# command = "/opt/4top/bin/4top"     # if a non-login PATH does not include 4top
# refresh_seconds = 15.0               # slower than local: each tick is a round trip
# timeout_seconds = 10.0
```

```sh
4top                               # this machine and every configured host
4top --host build-box              # the panel, scoped to that host only
4top --host build-box list --json
4top --host me@10.0.0.4 doctor     # an unconfigured target works too
```

Each configured host gets its own section of the list, refreshed on its own
interval and incrementally, so an unchanged host costs a few hundred bytes. The
last rows seen are cached, so a host shows at once and then catches up; one that
cannot be reached keeps its rows and says so. Anything that starts a process runs
**on that host** through `ssh -t`, so the resumed agent lives where its history
lives; the remote CLI does the work and the local side only hands over the
terminal. Connection reuse (`ControlMaster`) keeps refreshes cheap, `BatchMode`
means a missing key fails fast instead of prompting, and a remote that speaks a
different row schema is refused instead of partially parsed.

Before it hands over the terminal, the panel asks the host that owns the session
whether the resume can work there (`4top check`). A host without that agent
installed, or a session whose directory is gone, is reported in the panel instead
of failing during the hand-over, where the message would be painted over. The ssh
connection also keeps a liveness probe, so a link that dies becomes an error rather
than a hang, and the session stays in its transcript to be resumed again.

## Command line

```sh
4top list --json                          # one JSON object per row
4top list --agent pi --project 4top
4top search 'retry "database timeout"'    # metadata match
4top search '中文' --full                  # decoded full-content search
4top preview h_<key>                      # one bounded read-only page
4top preview h_<key> --tail               # the latest messages instead
4top check h_<key> --json                 # would a resume work here, and why not
4top new codex -- --model MODEL           # native arguments after --
4top resume h_<key> --yes                 # restore this process as the agent
4top doctor --json
```

`doctor` also reports `revision`, and `4top --host NAME doctor` reports it for both
sides, so a remote running older code is visible instead of failing later. Bring a
remote forward with `scripts/remote_update.py NAME`; it uses the host's own egress
first and falls back to a tunnel from this machine.

`--config`, `--host` and `--no-color` work before or after the subcommand.
`check` exits 0 when the session can resume here and 3 when it cannot. Keys
may be shortened only when their prefixes are unambiguous (at least four
characters). Row numbers are never execution targets. `list --json` rows carry
`schema_version`, `key`, `agent`, `host`, `cwd`, `title`, `started`, `last`,
`source`, `status` and `can_resume`.

## Configuration and privacy

Optional configuration: `$XDG_CONFIG_HOME/4top/config.toml` (default
`~/.config/4top/config.toml`). No setup file is needed for standard stores.

```toml
[ui]
refresh_seconds = 1.0
history_refresh_seconds = 5.0
color = "auto"                         # or "none"; NO_COLOR is also supported
layout = "auto"                        # "tmux" (require it), "plain", or auto
rows_per_host = 0                      # sessions per machine page; 0 fits the screen
update_check = true                    # daily PyPI check; shows how to upgrade

[history]
metadata_max_bytes = 2097152
metadata_max_lines = 2000
preview_max_lines = 200

[agents.codex]
# root = "/absolute/path/to/codex-home"
# executable = "/absolute/path/to/a-real-wrapper"
# args = ["--dangerously-bypass-approvals-and-sandbox"]   # added to every launch

[agents.claude]
# args = ["--dangerously-skip-permissions"]
```

`args` are added to every start and resume of that agent, for example a permission
mode. They are yours to choose: 4top's default adds nothing, and arguments that
would change which session is resumed are refused. For a remote host, set them in
that host's own configuration, because the remote 4top builds the command.

Agent store roots respect `CODEX_HOME`, `CLAUDE_CONFIG_DIR`, and
`PI_CODING_AGENT_DIR`; an explicit configured root wins. Selected history and
launch profile must agree. Local state is private: `$XDG_STATE_HOME/4top` holds a
local identity and your last selection, and `$XDG_CACHE_HOME/4top` holds
rebuildable metadata. No environment values, prompt text, or transcripts are
retained. Network access is the ssh you configured, plus the daily update check.

[Privacy](docs/privacy.md) · [Troubleshooting](docs/troubleshooting.md) · [Design](docs/design.md)

## Develop and contribute

```sh
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m pytest
.venv/bin/python -m ruff check .
uv tool install --force --editable .   # optional: `4top` runs this checkout
```

Tests use private temporary HOME/state directories and synthetic agents; the ssh
tests use a fake `ssh` on `PATH`. No account credentials, network access or model
calls are required. Record your OS, Python and native CLI versions when reporting
compatibility. **Never post raw transcripts or tokens.**

For fresh installation, CI, reproducible demo export, native smoke testing, and
release gates, see [CONTRIBUTING](CONTRIBUTING.md) and the [acceptance guide](docs/validation/README.md).

4top builds on 4ier's `session-ls` parsers and uses Textual. It is not affiliated
with the vendors of the supported coding agents. **MIT licensed.**
