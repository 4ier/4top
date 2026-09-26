# Changelog

## Unreleased

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
