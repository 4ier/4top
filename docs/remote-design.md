# Remote hosts over SSH

The panel lists this machine and every configured host at once, one section
each. `--host` scopes the panel, or any command, to a single machine you can
already `ssh` into.

## Why ssh instead of an agent

| | ssh | custom daemon |
| --- | --- | --- |
| Authentication | existing sshd, keys, VPN identity | to be built, including revocation |
| Network surface | none added | a port to bind, protect and version |
| Remote install | the same `4top` CLI | a second long-running service |
| Project promise | unchanged | breaks "no extra daemon" |

The remote side is not a new interface. It is the existing read-only CLI, so
anything 4top does remotely is something a person could type.

## Contract with a remote host

- Commands used remotely are read-only and already JSON: `list --json`,
  `search --json`, `preview`, `doctor --json`. Rows keep `schema_version`.
- `ssh -o BatchMode=yes`: never prompt, fail fast, surface the failure as an issue.
  A failed query must never render as an empty machine.
- **Incremental refresh.** The panel asks `list --json --sync --since CURSOR` and
  receives only the rows written since the cursor, then one summary line with the
  count of all rows and a digest over every row's fingerprint. The client merges and
  recomputes the digest; a mismatch (a deleted transcript, a status change, a clock
  that moved back) triggers one full listing. An unchanged host of 2773 sessions
  answers in about 650 bytes instead of 1.7 MB. A host without `--sync` gets plain
  `list --json`, and is not asked again.
- **Cached rows.** The last proven rows per host are kept in the private cache, so
  the panel shows a host immediately and marks the view "cached, updating…" until
  the host answers.
- Connection reuse via `ControlMaster`/`ControlPersist`, with the control socket
  inside private state. Without it every refresh pays a full handshake.
- **Version handshake.** A remote `schema_version` that does not match the local
  one is an explicit issue, never a partial parse.
- **Explicit command path.** Non-interactive ssh shells do not source login
  profiles, so `4top` may not be on `PATH` even though it is on the host. The host
  entry carries an absolute command, with bare `4top` only as a fallback.
- **Keys are host-scoped and opaque.** A history key produced on a remote host is
  passed back verbatim for remote actions; the local process never recomputes it.
- Every remote argument is quoted for the remote shell, because ssh joins its
  arguments into one string before the remote shell parses them.
- A remote row is relabelled with the configured host name, so nothing from
  another machine is filed as local.

## Configuration

```toml
[hosts.build-box]
ssh = "me@build-box"
# command = "/absolute/path/to/4top"   # non-login PATH on the remote may differ
# refresh_seconds = 15.0               # slower than local: each tick is a round trip
# timeout_seconds = 10.0
```

`--host NAME` (or an ad-hoc `--host user@address`) is a global option with the
same placement rules as `--config`. `4top --host N doctor` reports the remote's own
diagnosis plus the transport result, without writing on either side.

## Every host in one panel

Each configured host is a section of the list with its own page, refresh interval
and state line: `cached` while the first answer is on its way, `unreachable` when
the host cannot be asked (its last rows stay), `partial` when it reported issues.
Rows keep the host they came from, and an action goes to that host. The saved
selection is local-only, because a history key from another machine means nothing
here.

## Deploying and updating the remote side

The remote needs the CLI, and both this machine and the remote usually have working
egress, so a normal install works. Two details bite in practice: a non-interactive
ssh shell does not read login profiles (so the proxy environment and `PATH` are not
there), and a host's own proxy is not always working.

- Prefer a **git checkout** where git exists: the revision is then known and an
  update is a `pull`. Where there is no pip, virtualenv or git, a self-contained
  Python tree plus a two-line wrapper works, and a `REVISION` file records what is
  deployed.
- The updater on the host (`4top-update`) tries candidate proxies in order and takes
  the first that answers, so a healthy host needs nothing from here.
- When the host has no working egress at all, `scripts/remote_update.py NAME` opens a
  reverse tunnel from this machine on a free port and hands it to the updater as
  `FOURTOP_PROXY`. Nothing on the host is reconfigured.
- `4top doctor` reports a `revision` on both sides; `4top --host NAME doctor` reports
  a mismatch as an issue, because two machines running different revisions is how a
  missing command turns into a confusing error.

## Preflight and flaky links

An action preflights on the host that owns the session (`4top check KEY`). The panel
runs it before taking over the terminal, because a refusal raised *during* the
hand-over prints under a screen that is repainted immediately: it reads as "nothing
happened". The preflight answers three things: is there an exact native identifier,
is the recorded directory still there, and is that agent installed on that host.

The interactive connection carries `ServerAliveInterval`/`ServerAliveCountMax` and
`TCPKeepAlive`, so a dead link becomes an error in about 45 seconds instead of a
hang, and it reuses the warm `ControlMaster` connection so the hand-over does not
pay a fresh handshake. If the link does drop, the remote process may be gone but the
transcript is not: the panel says so and the session can be resumed again. Keeping an
agent alive across a drop is the remote machine's own multiplexer, not 4top's.

## Actions

`resume` and `new` with `--host` hand the terminal to `ssh -t`, so the process is
created on the machine that owns the history and the remote CLI does the work. The
local side only quotes the arguments, hands over the terminal and reports what the
remote said. Persistence across a disconnect is the remote user's own multiplexer,
not 4top's business.

## Deploying the remote side

The remote needs the CLI. A package install is the normal path; where pip, a
virtual environment or PyPI access are unavailable, a self-contained Python tree
works just as well: unpack 4top, its parsers and the UI stack into one directory,
put a wrapper next to it, and point `command` at the wrapper.

```sh
# lib/ holds the packages, bin/4top is a two-line wrapper that sets PYTHONPATH
4top --host NAME doctor          # reports the remote's Python and sources
```

Keep it somewhere the host considers durable, and give `command` an absolute path,
because a non-interactive ssh shell does not read login profiles.

## Out of scope

A daemon or port binding, discovery, a web interface, credentials stored in
configuration, multi-user hosts, session migration between machines, and push
updates — polling over ssh is the ceiling.
