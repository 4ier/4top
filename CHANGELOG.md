# Changelog

## Unreleased

### Breaking: 4top tracks sessions, not processes

A native agent is recoverable from its transcript alone, so the transcript is the
durable object and the process is the ephemeral one. 4top now keeps only
transcripts: it does not own, supervise, attach to, or report on a process, and it
no longer drives a multiplexer.

- Deleted: the tmux backend, the one-shot launch handoff and its transport, run
  records, reservations, locks and the operation log, the exit-code and Linux
  zombie evidence, `attach`, `open`, `link`, `terminate`, `dismiss`, `--socket`,
  `--client`, `--detach`, the `[runtime]` configuration section, and the `h`
  history toggle. There is no `LIVE`/`EXIT`/`MISSING`/`UNKNOWN`/`HIST` state
  because there is nothing live to observe.
- `new` runs the agent in the calling terminal: the CLI replaces itself with the
  agent (`execvpe`), and the TUI suspends, waits and returns to the panel.
  Persistence across a disconnect belongs to byobu/tmux, which 4top neither
  requires nor touches.
- `list --json` rows are `schema_version` 2 and carry `key`, `agent`, `host`,
  `cwd`, `title`, `started`, `last`, `source`, `status`, `problems` and
  `can_resume`. Anything reading `state`, `run_id` or `pid` must be updated.
- Local history is shown by default. The old view-schema `history` flag is
  ignored and the view file is written as schema 3.
- Nothing needs migrating: state is now a local identity plus the last selection.
  Old run records are ignored and can be deleted. A new local identity changes
  history keys, so anything that copied a key must copy it again.

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
- Correct the acceptance record: Pi 0.87.0 is installed on the acceptance Mac and
  its capability probe passes. No authenticated Pi smoke check was run, and the
  previous "not installed" statement was wrong.

## 4top 0.1.0a2 — 2026-09-24

- Preserve absent native store environment overrides. In particular, launching
  Claude no longer relocates its default configuration and triggers onboarding
  for an already-authenticated installation. Explicit profiles still take priority.
- Add 13 native-root regression cases covering all three runtime drivers.
- Explain that resume uses current native configuration, not replayed launch flags.
- Validate 141 automated tests on macOS 27.0 arm64, plus a separate authenticated
  Codex 0.155.1 attach/detach/resize/exact-resume smoke check.
- Require confirmed tmux exit code/signal before classifying a closed pane as EXIT;
  add eight regression cases for the Linux PTY-close/child-reap race and signal exits.
- Recover Linux zombie exit evidence by matching boot/PID/start time and current
  ownership before reading kernel wait status. Never send signals to the tmux
  server or guess a successful zero status when /proc may have masked it.
  Add 24 regression cases for PID reuse, ownership, malformed data and live states.
- Establish the independent `4ier/4top` repository and fresh-wheel installation.
- Keep Pi, other native versions, physical SSH loss and Linux native validation
  explicitly outside this release's compatibility evidence.

See [the Mac acceptance record](docs/validation/macos-0.1.0a2.md).

## 4top 0.1.0a1 / session-ls 0.2.0

- Initial keyboard-first TUI and scriptable CLI over a shared service layer.
- Native tmux handoff, verified attach, retained exits, bounded previews,
  safe destructive actions, explicit history linking and same-history reservations.
- Experimental Claude/Codex/Pi exact resume drivers; read-only Cursor history.
- Private atomic state, one-shot in-memory environment transfer, no extra daemon.
- Literal Unicode full search, cancellation, stable selection, no-color, isolated demo.
- Real tmux/PTY tests alongside core and headless UI tests.
- session-ls keeps its six-field JSON interface and independent stdlib-only package.
  Imports no longer change SIGPIPE; full search now follows documented literal
  semantics instead of accidentally interpreting a grep regular expression.
- Cached file identity includes parser version and inode metadata; cache writes use
  unique temporary names. Corrupt caches are rebuilt rather than silently trusted.

This alpha does not certify authenticated native CLI compatibility, all minimum
platform versions, multi-host aggregation, or external-user usability gates.
