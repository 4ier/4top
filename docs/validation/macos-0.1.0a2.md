# 0.1.0a2 — developer Mac run

Date: **2026-09-24**. This is an **alpha** record, not a stable release or a claim
that every design gate passed. It was written before the current release line;
only the parsing, installation and native-resume findings below still describe
the shipped code.

## Environment

macOS 27.0 arm64; Python 3.13.13; Textual 8.2.8.
Native installations: Codex **0.155.1**, Pi **0.87.0** (probe only),
Claude Code **2.1.280**. The existing authenticated CLI installations were used;
they were not upgraded.

## Suite and installation

The automated suite passed **141 tests**, with zero failures, errors or skips, and
Ruff passed. A new virtual environment installed both wheels, and checks passed for
`4top --version`, the six isolated demo rows, the legacy CLI under an empty HOME,
and the root wheel not owning the independent `session_ls` namespace.

The suite used synthetic agents and made no model requests. Machine-readable wheel
evidence for that run is kept here; the raw test summaries are in git history.

## Native checks

Codex 0.155.1 passed operator-driven checks through the CLI: an exact native
history identified without a cwd or time guess, a new PID created from that exact
history ID, root and cwd, and the earlier response visible in the resumed native
UI. No unrelated history was selected for execution.

No authenticated smoke check was run for Claude Code or Pi in this record. Claude's
use was limited to a launch that exposed the configuration bug below.

## Bug found by native testing

The launch planner exported `CLAUDE_CONFIG_DIR` even when the user had not set an
override. On the tested Claude version that moved configuration lookup from its
normal location and caused a misleading first-run setup screen.

The fix preserves an absent override and still honors explicit profiles and
inherited overrides; it required no copying or changing authentication files.
Thirteen regression cases cover default, inherited, configured and
explicit-default roots, plus path expansion and caller-environment immutability.

Native resume also showed why initial launch flags cannot be described as
persistent policy: the CLI uses its current configuration on resume. Confirmations
now say that original launch flags are not replayed.

## Limits still open

Native Pi and Claude smoke checks, Linux-native validation, physical SSH loss, all
terminal emulators, the complete storage-fault matrix and independent user
acceptance remain unverified. GitHub Actions results are commit-specific and
separate from this developer Mac record. No PyPI upload, stable/beta certification
or minimum-version promise is implied by this report.
