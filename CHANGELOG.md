# Changelog

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
