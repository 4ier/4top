# Remote hosts (design note)

Status: **implemented.** The runtime layer has been deleted and the read-only
remote view plus remote actions are in the current development line. This note
replaces the "No multi-host aggregation" limit in [design.md](design.md); the
implementation notes below record what actually shipped and where it differs
from the original plan.

## Decision

Two decisions from the 2026-09-26 review, taken together:

1. **4top stops owning processes.** The runtime layer (run records, pane
   ownership markers, the `LIVE`/`EXIT`/`MISSING`/`UNKNOWN`/`HIST` vocabulary and
   the exit-evidence forensics) is to be deleted rather than extended. The reason
   is that native agents are file-based and recoverable: the transcript is the
   durable object and the process is the ephemeral one. 4top had that inverted.
2. **Remote hosts are reached over SSH, read-only first.** No daemon, no listening
   port, no new credential store.

The user runs 4top inside **byobu** on the remote host. byobu is therefore the
persistence and attach layer, and 4top does not need to implement either. What
remains for 4top on a remote host is exactly what it is best at: discovering
sessions, searching them, and planning an exact native resume.

Scope is **isolated, not merged**: the default view is the local machine, and
`--host` switches the view to one remote host. There is no combined table.

## Why SSH instead of an agent

| | SSH | Custom daemon |
| --- | --- | --- |
| Authentication | existing sshd, keys, Tailscale identity | to be built, including revocation |
| Network surface | none added | a port to bind, protect and version |
| Remote install | the same `4top` CLI | a second long-running service |
| Project promise | unchanged | breaks "no extra 4top daemon" |

The remote side is not a new interface. It is the existing read-only CLI.

## Contract with a remote host

- Commands used remotely are read-only and already JSON: `list --json`,
  `search --json`, `doctor --json`. Rows keep `schema_version`.
- `ssh -o BatchMode=yes`: never prompt, fail fast, surface the failure as an issue.
  A failed query must never render as an empty machine.
- Connection reuse via `ControlMaster`/`ControlPersist`. Without it every refresh
  pays a full handshake, which is the difference between usable and not.
- **Version handshake.** A remote `schema_version` that does not match the local
  one is an explicit issue, never a partial parse.
- **Explicit command path.** Non-interactive SSH shells do not source login
  profiles, so `4top` may not be on `PATH` even though it is on the host. The host
  entry carries an absolute command, with bare `4top` only as a fallback.
- **Keys are host-scoped and opaque.** A history key produced on a remote host is
  passed back verbatim for remote actions; the local process never recomputes it.
- Host identity becomes a stable, user-chosen name instead of today's random
  per-state-directory UUID, so rows from two machines cannot collide.

## Configuration

```toml
[hosts.build-box]
ssh = "me@build-box"
# command = "/absolute/path/to/4top"   # non-login PATH on the remote may differ
# refresh_seconds = 15.0               # slower than local: each tick is a round trip
# timeout_seconds = 10.0
```

`--host NAME` (or an ad-hoc `--host user@address`) is a global option with the
same placement rules as `--config`. It is absent from the plan, not built: 4top
does not open windows in anyone's multiplexer. `4top --host N doctor` reports the
remote's own diagnosis plus the transport result, without writing on either side.

## What shipped

The runtime layer was deleted first, because the remote view would otherwise have
been built twice on a row model that was about to change.

1. **Runtime layer deleted.** No run records, pane markers, liveness vocabulary,
   exit evidence, handoff channel or multiplexer driver. `new` runs the agent in
   the calling terminal; the CLI becomes it with `execvpe`, the TUI suspends and
   waits. Row schema is 2 and the state directory holds only identity and view
   preference.
2. **Read-only remote view.** `[hosts.NAME]` plus `--host`, implemented in
   `fourtop.hosts` over `ssh` with `BatchMode`, `ControlMaster`, a timeout and a
   row-schema handshake. `doctor --host` reports the remote's own diagnosis.
3. **Remote actions.** `resume` and `new` with `--host` hand the terminal to
   `ssh -t`, so the process is created on the host that owns the history. The
   remote CLI does the work; the local side only quotes arguments, hands over the
   terminal and reports what the remote said.

Not done, and deliberately: attaching to a process on another machine. There is
no process to attach to, on any machine.

## Deleted

`tmux.py`, `_launch.py`, `transport.py`, the `[runtime]` configuration section,
run records, reservations, the operation log, the `LIVE`/`EXIT`/`MISSING`/
`UNKNOWN`/`HIST` vocabulary, the exit-code and Linux zombie evidence, and the
integrations that tested them. `pyproject.toml` no longer mentions tmux.

Kept: the whole `session-ls` layer, metadata cache, resume planning, preview,
search, privacy rules and `doctor`. The launch tests that replaced the pane
tests run the real fake agent as a real process and assert its argv, cwd,
environment and the session file it wrote.

## What this gives up

- No attach to a process 4top started; attach is byobu's job.
- No retained exit code or exited-pane preview for 4top-launched runs.
- No `--detach` of its own; the multiplexer owns that.
- No claim about which pane belongs to 4top, because that claim no longer exists.

## Open risks

- Refresh cost and battery on the client: one SSH round trip per tick per host.
- Version skew between hosts, including a remote that is older than the local CLI.
- First-connection host keys and agent forwarding are the user's SSH setup, not
  something 4top manages.
- Remote actions create processes on another machine and keep the same
  confirm-by-default rule.
- Polling is the ceiling. Push updates would require a daemon and are explicitly
  out of scope.

## Out of scope

Daemon or port binding, discovery, a web interface, credentials stored in
configuration, multi-user hosts, session migration between machines.
