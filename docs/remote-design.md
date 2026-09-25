# Remote hosts (design note)

Status: **proposal, not implemented.** This note replaces the "No multi-host
aggregation" limit in [design.md](design.md) with a concrete scope. Nothing here
is a compatibility promise until it ships with tests.

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
[hosts.lazy4]
ssh = "root@lazy4ier.heiyu.space"
# command = "/absolute/path/to/4top"   # non-login PATH on the remote may differ
# refresh_seconds = 15.0               # slower than local: each tick is a round trip
# timeout_seconds = 10.0
# byobu = true                         # remote actions open a byobu window
```

`--host NAME` (or an ad-hoc `--host user@address`) is a global option with the
same placement rules as `--config` and `--socket`. `4top doctor --host N` reports
transport reachability, remote version, command resolution and remote state-dir
health without writing anything on either side.

## Phases

1. **Read-only remote view.** `list`/`search`/`doctor --host`, remote issues in the
   existing status line, remote scope in the TUI, slower cadence, manual `r`.
   Additive: no change to the local runtime layer.
2. **Delete the runtime layer.** Remove ownership and exit evidence (list below),
   and make `new` launch the agent in the current terminal instead of a
   handoff-created pane. byobu or tmux, if present, is the user's own choice and
   is not driven by 4top.
3. **Remote actions.** `resume`/`new --host` run on the remote through `ssh -t`,
   inside byobu when configured. The local TUI suspends and hands over the
   terminal, which is the pattern already used for interactive clients.

Phase 1 is first because it is additive and answers the real need immediately.
Phase 2 is second because it deletes rather than adds, and it is easier to do that
calmly once the transport is proven.

## Deletion candidates (phase 2, to be confirmed in the change)

- `state.py`: run records, reservations, locks, operation log.
- `tmux.py`: `observe`/`verify`/`attach`/`terminate`, pane markers, server
  identity, exit-code and zombie evidence.
- `_launch.py`: the one-shot handoff channel.
- `services.py`: run/history merge, live counts, socket-scoped filtering.
- `app.py`: runtime row actions, `Dismiss`/`Terminate`, state vocabulary.
- `config.py`: `runtime.socket`, `runtime.tmux`, `startup_handshake_seconds`,
  `control_timeout_seconds`.
- Tests: the real PTY/runtime suite, exit evidence, Linux zombie and handoff
  cases. This is the largest single deletion.

Kept: the whole `session-ls` layer, metadata cache, resume planning, preview,
search, privacy rules and `doctor`.

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
