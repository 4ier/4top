# 0.1.0a2 — developer Mac acceptance

Date: **2026-09-24**. Result: the Mac automated suite, clean-wheel installation
and the scoped native Claude/Codex smoke checks passed. This is an **alpha**
acceptance record, not a stable release or a claim that every design gate passed.

## Environment

macOS 27.0 arm64; Python 3.13.13; tmux 3.6b; Textual 8.2.8.
Native installations: Claude Code **2.1.280**, Codex **0.155.1**.
The existing authenticated CLI installations were used; they were not upgraded.
Pi was not installed on this host.

## Automated and installation evidence

The unchanged 0.1.0a1 source first passed all 96 tests on this Mac. Native
verification then exposed a configuration-root bug. After the fix and 13 new
regression cases, the final 0.1.0a2 suite passed **109 tests**, with **zero
failures, errors or skips**. Ruff passed as well.

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

Both Claude and Codex passed these operator-driven checks through 4top's runtime
services and actual 4top CLI attach commands:

| Check | Claude 2.1.280 | Codex 0.155.1 |
| --- | --- | --- |
| Native UI, normal workspace trust prompt, one minimal model response | Pass | Pass |
| Two newly started 4top CLI attach/detach cycles | Pass | Pass |
| Same process identity throughout those cycles; terminal resize | Pass | Pass |
| Exact native history identified without a cwd/time guess | Pass | Pass |
| Live duplicate resume refused | Pass | Pass |
| Native Ctrl-C exit retained as EXIT | Pass | Pass |
| Resume creates a new PID with the exact history ID, root and cwd | Pass | Pass |
| Earlier test response visible in the resumed native UI | Pass | Pass |
| A second live resume is refused | Pass | Pass |

The test used a newly created empty project, isolated 4top state/cache/config
and a private tmux socket. Claude's first launch disabled tools/MCP/hooks; Codex's
first launch used read-only sandbox mode. Both received a request only to reply
with a fixed test token. The Codex session ID was read from its own native status
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
