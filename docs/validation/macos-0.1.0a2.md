# 0.1.0a2 — developer Mac acceptance

Date: **2026-09-24**. Result: the Mac automated suite, clean-wheel installation
and the scoped native Codex smoke check passed. This is an **alpha**
acceptance record, not a stable release or a claim that every design gate passed.

## Environment

macOS 27.0 arm64; Python 3.13.13; tmux 3.6b; Textual 8.2.8.
Native installation: Codex **0.155.1**. Pi **0.87.0** is also installed on this
host (via `~/.local/share/pi-node`), but no authenticated native Pi smoke check
was performed for this acceptance; only its help/version capability probe.
The existing authenticated CLI installations were used; they were not upgraded.

## Automated and installation evidence

The unchanged 0.1.0a1 source first passed all 96 tests on this Mac. Native
verification then exposed a configuration-root bug. After the fix and 13 new
root regression cases, 109 tests passed. The first hosted Ubuntu run then exposed
a PTY-close/child-reap race. The observer now requires an exit code or signal
before reporting EXIT, with eight further regression cases. Additional Linux diagnostics identified
unreaped zombies; a read-only kernel-evidence fallback gained 24 further cases
covering exact identity, ownership, masked status and refusal of live processes.
The final developer
Mac suite passed **141 tests**, with **zero failures, errors or skips**. Ruff passed.

Reproduce with both packages installed and tmux available:

```sh
python scripts/acceptance.py --output acceptance-output
python -m ruff check .
python -m build --no-isolation packages/session-ls --outdir dist
python -m build --no-isolation --outdir dist
python scripts/verify_install.py dist
```

The automated suite uses synthetic agents: unit tests, legacy parser/CLI tests,
Textual headless interaction, actual tmux processes and actual PTYs are separate
layers. It does not make model requests or use native credentials.

A new virtual environment successfully installed both wheels. Checks passed for
`4top --version`, six isolated demo rows, the legacy CLI under an empty HOME,
and the root wheel not owning the independent `session_ls` namespace.

Machine-readable records:
[automated suite](macos-suite-0.1.0a2.json),
[fresh install](macos-fresh-install-0.1.0a2.json),
[native smoke](macos-native-0.1.0a2.json).

## Separate authenticated native checks

Codex passed these operator-driven checks through 4top's runtime
services and actual 4top CLI attach commands:

| Check | Codex 0.155.1 |
| --- | --- |
| Native UI, normal workspace trust prompt, one minimal model response | Pass |
| Two newly started 4top CLI attach/detach cycles | Pass |
| Same process identity throughout those cycles; terminal resize | Pass |
| Exact native history identified without a cwd/time guess | Pass |
| Live duplicate resume refused | Pass |
| Native Ctrl-C exit retained as EXIT | Pass |
| Resume creates a new PID with the exact history ID, root and cwd | Pass |
| Earlier test response visible in the resumed native UI | Pass |
| A second live resume is refused | Pass |

The test used a newly created empty project, isolated 4top state/cache/config
and a private tmux socket. Codex's first launch used read-only sandbox mode.
It received a request only to reply with a fixed test token. The Codex session ID was read from its own native status
screen before explicit linking. No unrelated history was selected for execution.

Only test-owned processes and the private test tmux server were cleaned up.
No raw native transcript, credential, account identity, private project path or
native session ID is included in these public evidence files.

## Bug found by native testing

The old launch planner exported `CLAUDE_CONFIG_DIR` even when the user had not
set an override. On the tested Claude version this moved configuration lookup
from its normal location and caused a misleading first-run setup screen.

0.1.0a2 preserves an absent override and still honors explicit profiles and
inherited overrides. The fix required no copying or changing authentication
files. Thirteen regression cases cover default, inherited, configured and
explicit-default roots, plus path expansion and caller-environment immutability.

Native resume also demonstrated why initial launch flags cannot be described as
persistent policy: the native CLI uses its current configuration on resume.
Both CLI and TUI confirmations now say that original launch flags are not
replayed. Inspect native permissions before sending another task.

## Limits still open

Native Pi and Linux-native validation, physical SSH/network loss, all terminal
emulators, the complete storage-fault matrix and independent user acceptance
remain unverified here. The two fresh native attach cycles used CLI entry points;
the full suspended-TUI return flow is covered separately by synthetic PTY tests.
A resumed UI showing saved context is not an additional model round-trip test.

GitHub Actions results are commit-specific and separate from this developer Mac
record. No PyPI upload, stable/beta certification or minimum-version promise
is implied by this report.
