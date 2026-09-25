# Remote hosts over SSH

4top is bound to one machine at a time. `--host` points the whole panel at
another machine you can already `ssh` into. Views are isolated rather than merged:
the default scope is this machine, and a remote scope replaces it.

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

## Switching in the panel

`H` lists this machine and every configured host, and selecting one replaces the
whole view: rows, selection and any running search belong to the previous scope
and are dropped. A refresh that was in flight for the old scope is discarded
rather than rendered into the new one. The scope is always named in the counts
line.

The saved selection is local-only, because a history key from another machine
means nothing here.

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
