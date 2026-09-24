# 4top

**Your coding agents, one terminal.**

Find your work. Attach to the original process. Resume an exact native history.
A keyboard-first terminal dashboard built on **tmux + session-ls**, with no extra
4top daemon, account, model calls, or telemetry.

[中文](README.zh-CN.md) · [Compatibility](docs/compatibility.md) · [Acceptance](docs/validation/README.md)

![4top synthetic demo — no real user history](docs/demo/demo.svg)

> **0.1.0a2 — alpha.** Real tmux/PTY tests pass on the Mac acceptance host.
> Claude Code **2.1.280** and Codex **0.155.1** also passed separate authenticated
> attach/detach and exact-resume smoke checks there. Drivers remain experimental;
> Pi and other native versions are not certified. [Evidence](docs/validation/macos-0.1.0a2.md).
> This release is not on PyPI.

## Install from this checkout

macOS or Linux; **Python 3.11+ and tmux 3.3+**. Current validation records, rather
than this minimum target, determine which versions have actually been tested.
Windows users need a Linux environment such as WSL; native Windows is unsupported.

```sh
# Install tmux using your OS package manager, then:
git clone https://github.com/4ier/4top.git
cd 4top
python3 -m venv .venv
.venv/bin/python -m pip install ./packages/session-ls .
.venv/bin/4top --demo
```

Both local packages are intentionally supplied to pip: `session-ls 0.2.0` is not
assumed to exist on a package registry. The root wheel contains only `fourtop`;
the independent `session-ls` package retains its small, stdlib-only CLI.

Use `.venv/bin/4top` below, or activate the environment:

```sh
. .venv/bin/activate
4top                         # live work first; history is discovered locally
4top new codex               # launch in this directory, then attach
4top new claude --detach     # start without taking over this terminal
```

4top never installs or authenticates agents for you. Install the original Claude
Code, Codex, or Pi CLI separately and keep its existing authentication flow.
The panel also reads Cursor transcripts, but does not launch or resume Cursor.

## The daily loop

Open `4top`, select a row, press **Enter**. A verified live row attaches to its
original terminal. A history row asks for confirmation before creating a new
native process. Press your configured tmux prefix and then `d` to detach; from an
outer terminal, the same 4top view returns. **`q` closes only the panel.**

Inside an existing tmux server, 4top restores the terminal and switches the one
provable calling client. It does not nest tmux, pick an arbitrary client, or
kick other clients off. The panel exits before the switch; use your own tmux
previous-session binding to return to the caller. Shared caller sessions require
an explicit `--client` through the CLI. Cross-socket nested attach is refused.

| Key | Action |
| --- | --- |
| `↑` / `↓`, `Enter` | Select and open |
| `/`, `Enter`, `Esc` | Search metadata, return to table, clear/cancel |
| `Ctrl-F` | Explicit literal full-content search; `Esc` cancels |
| `h`, `Space`, `i` | Include history, read-only preview, details/actions |
| `n`, `r`, `?`, `q` | New agent, refresh, help, close panel |

Search supports case-insensitive words and quoted phrases; all terms must match.
Full search decodes JSON text, including Chinese escaped as `\u....`. It reads
only configured sources, reports partial scans, and never executes transcript
content. The UI renders titles and previews as plain, sanitized text.

## Attach and resume have different guarantees

**Attach** reconnects to a verified process. It does not restart the CLI or reload
its history. tmux, the host, and the process must still exist.

**Resume** starts a new native process with an exact ID or source path. It cannot
restore lost memory, network connections, shell children, or a destroyed machine.
Resume uses the CLI's **current native configuration**; 4top does not replay the
original launch flags. A one-off read-only flag on `new` is not a persistent
4top resume policy. Review native permissions before sending another task.
`HIST` means there is no verified live association; an externally launched agent
might still be running elsewhere.

```sh
4top list --json
4top search 'retry "database timeout"'
4top search '中文' --full
4top attach r_<run-uuid>
4top resume h_<history-key> --yes --detach
4top link r_<run-uuid> h_<history-key> --yes
4top doctor --json
```

IDs may be shortened only when their prefixes are unambiguous (at least four
characters). Row numbers are never execution targets. `resume` rejects duplicate
4top-managed launches across all recorded local sockets. It cannot police agents
started outside 4top, other users, other state directories, or remote machines.

## Honest state, no guessing

`LIVE` means the managed terminal's recorded process and ownership markers were
verified. It does not mean the agent is thinking. `EXIT` reports process exit,
not task success. `START`, `UNKNOWN`, `MISSING`, and stale observations are shown
explicitly; failed queries never become an empty, supposedly idle machine.

New Codex runs are initially **unlinked**. A matching directory or modification
time is only a clue, not identity. Explicitly link a history when needed. A launch
association remains a **launch** association: `/new`, `/resume`, or a native fork
can change current context without notifying 4top. The details screen says so.

Normal terminals cannot be moved into tmux retroactively. Start future work with
`4top new`; existing transcripts remain searchable. Multiple agents in one
working directory have **no code/worktree isolation**.

## Configuration and safety

Optional configuration: `$XDG_CONFIG_HOME/4top/config.toml` (default
`~/.config/4top/config.toml`). No setup file is needed for standard stores.

```toml
[ui]
refresh_seconds = 1.0
history_refresh_seconds = 5.0
color = "auto"                         # or "none"; NO_COLOR is also supported

[runtime]
startup_handshake_seconds = 10.0        # startup protocol only, not agent duration
control_timeout_seconds = 5.0
# socket = "/absolute/path/to/tmux.sock"

[agents.codex]
# root = "/absolute/path/to/codex-home"
# executable = "/absolute/path/to/a-real-wrapper"
```

`--config` and `--socket` also work before or after a subcommand. Agent store roots
respect `CODEX_HOME`, `CLAUDE_CONFIG_DIR`, and `PI_CODING_AGENT_DIR`; an explicit
configured root wins. Selected history and launch profile must agree.

Native arguments go after `--`: `4top new codex -- --model MODEL`. Conflicting
identity/directory/mode options are rejected. No permission-bypass, billing,
concurrency, or total runtime limits are inserted. A confirmed native launch can
modify files and use the agent's normal paid services.

Runtime metadata is private local JSON under `$XDG_STATE_HOME/4top`; rebuildable
metadata cache is under `$XDG_CACHE_HOME/4top`. Environment values and complete
native argv travel through a private one-shot Unix socket, **not a payload file**.
The helper `exec`s the original CLI. No 4top supervisor is left running.

Use `i` → **Terminate** only when you intend to close a managed pane; in-flight
writes can be interrupted. Prefer attaching and exiting natively for graceful
shutdown. **Dismiss** hides an exited run without removing native history.

[Privacy](docs/privacy.md) · [Troubleshooting](docs/troubleshooting.md) · [Design](docs/design.md)

## Develop and contribute

```sh
.venv/bin/python -m pip install -e ./packages/session-ls -e '.[dev]'
.venv/bin/python -m pytest
.venv/bin/python -m ruff check .
```

Tests use private temporary HOME/state/socket directories and synthetic agents.
No account credentials or model calls are required. Real PTY tests and Textual's
headless tests are separate layers. Record your OS, Python, tmux, and native CLI
versions when reporting compatibility. **Never post raw transcripts or tokens.**

For fresh installation, CI, reproducible demo export, native smoke testing, and
release gates, see [CONTRIBUTING](CONTRIBUTING.md) and the [acceptance guide](docs/validation/README.md).

4top builds on 4ier's `session-ls` parsers and uses tmux and Textual. It is not
affiliated with the vendors of the supported coding agents. **MIT licensed.**
