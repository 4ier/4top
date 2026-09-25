# Remote-host verification — Debian 12 x86_64

Date: **2026-09-26**. Host: Debian GNU/Linux 12 (bookworm), kernel
6.5.0-0.deb12.4-amd64, Python 3.11.2, x86_64, reached from macOS over ssh.

No coding-agent CLI was installed there. The `pi` driver was pointed at the
synthetic fixture from `tests/fixtures`, so nothing touched credentials, a real
vendor store or a model. 4top itself was run from source on a host with only the
standard library available.

## Verified across a real ssh connection

- `--host NAME doctor --json`: the remote's own diagnosis (Python version, agent
  probe, history sources) plus the transport result.
- `--host NAME list --json`: one JSON row per session, relabelled with the
  configured host name and never filed as local.
- `--host NAME list`: the human table, rendered on a host without the UI stack.
- `--host NAME preview KEY`: a bounded read-only page fetched from the remote.
- `--host NAME search --json '中文'` and `search --json 'verify "continuity"'`: the
  query arrives as one argument, including CJK and quoted phrases.
- `--host NAME resume KEY --yes`: the terminal is handed to `ssh -t` and the remote
  CLI created the process there, with `--session <exact file>` and the recorded
  directory. The report from the synthetic agent on the remote confirmed both.
- `--host NAME new pi --yes`: created a new session on the remote, visible in the
  next `list`.
- `new` without `--yes` in a non-interactive shell: exit 4 with an explicit message.
- A remote speaking row schema 1: refused with exit 6 and no partial parse.

## Second host: Ubuntu 26.04

Same day, same Mac: Ubuntu 26.04.1 LTS x86_64, Python 3.14.4, reached over
Tailscale, with **507 sessions** in real agent stores. `doctor`, `list` (human and
JSON), `search` and `preview` were verified, and both sides reported no issues.

The remote panel was also started there in demo mode over `ssh -t` and rendered,
which exercises the UI stack on a host with no display, no package manager access
and no PyPI reachability. Both hosts were installed without pip or a virtual
environment: a self-contained Python tree plus a wrapper, with `command` pointing
at the wrapper.

## Three defects this run found

- The human table needed `rich`, which only arrives with the terminal UI
  dependency, so a host with the standard library alone could not print a table.
  Display width is now computed with `unicodedata`.
- A remote login banner on stderr was reported as a query issue, so a healthy host
  looked broken and the command exited 6. Only diagnostics prefixed by the remote
  CLI are issues now.
- A plain marker file inside a store's project directory was matched by the history
  pattern, opened as a directory and reported as unavailable, so a healthy store
  reported an issue and exited 6 on every scan. A matched name that is not a
  directory is now skipped.

## Not covered

A real authenticated native agent on the remote, a remote where both sides were
installed by pip rather than run from a self-contained tree, physical network loss,
host-key changes, and Windows or macOS as the remote side.
