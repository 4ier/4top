# Changelog

## 4top 0.2.0a3 — 2026-09-26

Get the ssh connection socket length right, measured rather than assumed.

- The socket directory now allows for what ssh actually creates there: a 40-character
  `%C` hash plus a 16-character random suffix, under a limit measured at 103 bytes on
  macOS and 106 on Android. The state directory is preferred, the temporary directory
  is the fallback, and if neither fits, ssh runs without connection reuse instead of
  failing every host.

## 4top 0.2.0a4 — 2026-09-26

- `4top check` now proves the agent can actually run here, not just that a path is
  configured. It runs the same `--help` probe a resume would, and reports why the CLI
  failed: the case that prompted this was a Mac whose `pi` and `codex` launchers need
  `node` on `PATH`, which a non-interactive ssh session does not have, so the panel's
  preflight passed and the resume then exited 5 with no explanation.

## Unreleased

- Document that an agent installed on a host can still be "not found" when that host's
  4top is invoked over ssh, because a non-interactive session gets a minimal `PATH`.
  The remedy is an absolute `[agents.NAME].executable` on that host, which is what the
  error message already suggests.


- Put the ssh connection socket somewhere short enough. Unix sockets have a hard
  path limit, and Termux on Android runs under
  `/data/data/com.termux/files/home`, so the connection path overflowed and every
  remote host failed with `unix_listener: path ... too long for Unix domain socket`.
  The socket now prefers the state directory, then the temporary directory, then
  `/tmp`.

## 4top 0.2.0a2 — 2026-09-26

Fix the remote-host path on Android/Termux, which made every host unusable there.

- `session-ls` moved to its own repository
  ([4ier/session-ls](https://github.com/4ier/session-ls)), which is now the home of
  the parser package and the owner of its releases. This repository consumes it as
  an ordinary PyPI dependency, so its CI no longer builds or publishes a second
  project, and its release gate asks PyPI whether the required version exists
  instead of assuming a local package.

## 4top 0.2.0a1 — 2026-09-26

Published to PyPI from tag `v0.2.0a1`, together with `session-ls 0.2.0` as its
dependency. Being a pre-release, `pip install 4top` needs `--pre`; `uv tool
install 4top` does not.

The first release of the session-first line: 4top tracks transcripts rather than
processes, and reaches other machines over ssh. The previous line owned a process
per launch and reported liveness; that layer and its vocabulary are gone.

### Breaking: 4top tracks sessions, not processes

A native agent is recoverable from its transcript alone, so the transcript is the
durable object and the process is the ephemeral one. 4top now keeps only
transcripts: it does not own, supervise or report on a process, and it drives no
terminal multiplexer of its own.

- Deleted: the process-ownership layer and everything that existed to support it
  — launch records, reservations, locks, the operation log, ownership markers,
  exit-code and zombie evidence, the liveness states, and the commands, options
  and `[runtime]` configuration section that reached into it.
- `new` runs the agent in the calling terminal: the CLI replaces itself with the
  agent (`execvpe`), and the TUI suspends, waits and returns to the panel. Keeping
  work alive across a disconnect belongs to whatever the user already runs.
- `list --json` rows are `schema_version` 2 and carry `key`, `agent`, `host`,
  `cwd`, `title`, `started`, `last`, `source`, `status`, `problems` and
  `can_resume`. Consumers of the previous row schema must be updated.
- Local history is shown by default. The old view-schema `history` flag is
  ignored and the view file is written as schema 3.
- Nothing needs migrating: state is now a local identity plus the last selection.
  Earlier files under `$XDG_STATE_HOME/4top` are ignored and can be deleted. A new
  local identity changes history keys, so anything that copied a key must copy it
  again.

### Remote hosts over SSH

- Add `[hosts.NAME]` with `ssh`, `command`, `refresh_seconds` and
  `timeout_seconds`, plus the global `--host` option. Views are isolated:
  `--host` replaces the local scope instead of merging machines into one table.
- The remote side is the same CLI, with no daemon and no new port. It runs with
  `BatchMode=yes` (a missing key fails fast), a `ControlMaster` socket inside
  private state, and a hard timeout; every remote argument is quoted for the
  remote shell.
- A row whose `schema_version` differs is refused instead of partially parsed. A
  remote row is relabelled with the configured host name, and its history key
  stays opaque so it is never recomputed on the wrong machine.
- `resume` and `new` with `--host` hand the terminal to `ssh -t`, so the process
  is created on the machine that owns the history.
- `H` switches the running panel between this machine and a configured host, so
  comparing two machines no longer means quitting and relaunching. Rows, selection
  and search state are dropped with the old scope, an in-flight refresh for the old
  scope is discarded, and the saved selection stays local.

### Releases

- Publish one project per repository. The first attempt to publish both from this
  workflow failed with `403 Invalid API Token: OIDC scoped token is not valid for
  project '4top'`: one job means one OIDC exchange, and the token is scoped to the
  project its publisher matched. Rather than disambiguate two publishers with two
  environments, this workflow now publishes only `4top` and consumes `session-ls`
  from PyPI, which is the layout the two packages actually have: separate projects,
  separate version lines, separate pipelines.
- Publish from CI on a `v<version>` tag: the suite runs, `scripts/check_release.py`
  gates the release, both wheels are built and installed into a fresh environment,
  `session-ls` is published before `4top`, and the GitHub release is opened. Uploads
  use PyPI trusted publishing, so no token is stored in the repository, and the
  manual trigger defaults to a dry run.
- The gate refuses a tag that does not match the version, artifacts that are not the
  versions being released, and a root requirement the library cannot satisfy. That
  last one is real: the root requires `session-ls>=0.2.0` while PyPI only ever held
  0.1.0, so publishing the root alone would have produced an uninstallable package.

### Interface

- The host picker (`H`) switches the panel between this machine and a configured
  host, so comparing two machines no longer means relaunching.
- The selection line is context and one action: `agent · directory` and what Enter
  does. The history key moved to the details screen, where it is needed, and the
  status line says nothing when there is nothing to report.
- A recorded directory that no longer exists now names itself, is marked in the
  details screen, and asks for a directory to resume in instead of failing with
  "not accessible".
- The mouse wheel moves the highlight with the view instead of scrolling the
  viewport away from it.
- Resume preflights first. `4top check KEY` reports whether a session can resume
  here, and the panel asks before it hands over the terminal, so a host without
  that agent installed is a visible message instead of a failure that flashes past
  under a repainted screen. A hand-over that still fails reports its exit code, and
  a dropped ssh connection says that the session is unchanged in its transcript.
- The ssh connection keeps a liveness probe and a warm control connection, so an
  unstable link fails instead of hanging.
- `doctor` reports the `revision` of the code it is running, and `--host NAME doctor`
  reports both, so two machines on different revisions is visible instead of turning
  into a confusing error later.
- `scripts/remote_update.py NAME` brings a remote's 4top forward. It uses the host's
  own egress first and, only if that fails, lends this machine's proxy through a
  reverse tunnel that lives exactly as long as the update. Both hosts were observed
  with a proxy that answers on one port and fails on another, or works and then stops,
  so the fallback is a real case and it says which one it used.
- `scripts/check_docs.py` fails when the documentation stops describing the code,
  and runs in a pre-commit hook, the test suite and CI.

### Fixes in this line

- A large history no longer costs a full re-render on every refresh. Rows whose
  inputs are unchanged keep their rendered cells and their derived labels, so a
  2.7k-row store renders in under two milliseconds per tick instead of about 50 ms.
- Recognize Claude 2.1.x metadata-only session files. Claude prepends records such
  as `last-prompt`, `mode`, `attachment` and `cost-state`, and a session that was
  opened, renamed and quit never writes a user or assistant message; those files
  were reported as `ValueError` instead of being listed. Identity now comes from
  the `sessionId` these records carry, and the native `ai-title`/`agent-name` is
  used as the title only when the session has no user text. `PARSER_VERSION` is 2
  in both parsers, so the first scan after upgrading re-reads every file once.
- Report why a history file was rejected, not only the exception class, and keep
  OSError text (which contains the private path) out of the message.
- Print the human table without the terminal UI stack. Display width is computed
  with `unicodedata`, so `4top list` works where only the standard library is
  installed. Found by running the CLI on a host that had neither the UI dependency
  nor a package manager to add it.
- Stop reporting a non-directory that a history pattern matched as an
  unavailable directory. A real store keeps a marker file inside its project
  directory, so every scan reported an issue and every list exited 6. A configured
  root that is not a directory is still reported once, and a genuinely unreadable
  directory is still reported.
- Stop reporting a remote login banner as a query issue. Only diagnostics prefixed
  by the remote CLI count, so a healthy host no longer looks broken and no longer
  exits 6. Found by pointing `--host` at a machine whose ssh shell prints a banner.
- Correct the acceptance record: Pi 0.87.0 is installed on the acceptance Mac and
  its capability probe passes. No authenticated Pi smoke check was run, and the
  previous "not installed" statement was wrong.

## 4top 0.1.0a2 — 2026-09-24

- Preserve absent native store environment overrides. In particular, launching
  Claude no longer relocates its default configuration and triggers onboarding
  for an already-authenticated installation. Explicit profiles still take priority.
- Add 13 native-root regression cases covering all three agent drivers.
- Explain that resume uses current native configuration, not replayed launch flags.
- Validate 141 automated tests on macOS 27.0 arm64, plus a separate authenticated
  Codex 0.155.1 exact-resume smoke check.
- Add 32 regression cases hardening the then-current process-ownership layer
  against exit races, PID reuse and malformed ownership data.
- Establish the independent `4ier/4top` repository and fresh-wheel installation.
- Keep Pi, other native versions, physical SSH loss and Linux native validation
  explicitly outside this release's compatibility evidence.

See [the Mac acceptance record](docs/validation/macos-0.1.0a2.md).

## 4top 0.1.0a1 / session-ls 0.2.0

- Initial keyboard-first TUI and scriptable CLI over a shared service layer.
- Exact-target process management, bounded previews, safe destructive actions,
  explicit history linking and same-history reservations. That layer is not part
  of the current line; see the unreleased section above.
- Experimental Claude/Codex/Pi exact resume drivers; read-only Cursor history.
- Private atomic state, no extra daemon, no account.
- Literal Unicode full search, cancellation, stable selection, no-color, isolated demo.
- Real process and terminal tests alongside core and headless UI tests.
- session-ls keeps its six-field JSON interface and independent stdlib-only package.
  Imports no longer change SIGPIPE; full search now follows documented literal
  semantics instead of accidentally interpreting a grep regular expression.
- Cached file identity includes parser version and inode metadata; cache writes use
  unique temporary names. Corrupt caches are rebuilt rather than silently trusted.

This alpha does not certify authenticated native CLI compatibility, all minimum
platform versions, cross-machine aggregation, or external-user usability gates.
