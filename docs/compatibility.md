# Compatibility and evidence

**Status: 0.1.0a2 alpha.** Implementation, automated tests and real native CLI
checks are different evidence. The [Mac acceptance record](validation/macos-0.1.0a2.md)
contains the actual results and their limits.

| Source/driver | History | Managed launch | Exact native resume | Evidence |
| --- | --- | --- | --- | --- |
| Claude Code | JSONL adapter | Experimental; preallocated UUID when advertised | Experimental `--resume UUID` | Native **2.1.280**, macOS 27.0 arm64: authenticated response, two fresh attach/detach cycles, resize, unchanged process identity, graceful exit, new-PID exact resume and duplicate protection passed. |
| Codex | JSONL + archive adapter | Experimental; new runs unlinked | Experimental `resume UUID` | Native **0.155.1**, same Mac: same smoke checks passed. Exact ID obtained from the native status screen before explicit linking; no cwd/time guess. |
| Pi | JSONL adapter | Experimental; UUID only when advertised | Experimental `--session PATH` | Synthetic fixtures + help-probe mechanism + tmux/PTY harness. Native CLI unavailable on this Mac; not certified. Excerpts follow file order, not reconstructed active branches. |
| Cursor | Transcript JSONL | Unsupported | Unsupported | Read-only parser fixtures. Inferred cwd is display-only. |

A smoke check for one installed version is not a compatibility promise for all
releases. The native tests used a disposable project and private tmux socket,
not a customer's code. Raw histories, credentials and personal account details
are not published. Hosted CI never receives native agent credentials.

## Native configuration semantics

4top preserves an **absent** native store override. For example, it must not set
`CLAUDE_CONFIG_DIR=~/.claude` merely to discover history: doing so changes Claude's
lookup of its separate configuration file on the validated installation.
An explicitly configured root still wins over an inherited environment root.

Resume starts the CLI using its **current native configuration** and exact
history ID/path. It does not replay the original launch flags or save the
caller's complete environment. A one-off sandbox/permission flag passed to
`4top new` is not a persistent 4top resume policy. Inspect native permissions
before submitting another task. 4top adds no permission-bypass flags itself.

## Platform evidence

- Root package target: Python >=3.11, macOS/Linux. Core session-ls: Python >=3.9.
- Runtime target: tmux >=3.3. Not every minimum version is certified.
- **Developer Mac:** macOS 27.0 arm64, Python 3.13.13, tmux 3.6b, Textual 8.2.8.
  **117 automated tests passed, zero failures/errors/skips**, plus the separate
  real native checks above and a clean virtual-environment wheel installation.
- **Earlier 0.1.0a1 container run:** Linux x86_64, Python 3.13.5, tmux 3.4,
  Textual 8.2.8; 96 automated tests. This is historical evidence, not a Linux
  native-agent check for 0.1.0a2.
- **Hosted CI:** Ubuntu/macOS with Python 3.11/3.13 plus a Python 3.9 core job.
  See the repository's Actions runs for each exact commit; configuration alone
  does not prove a matrix passed.

Developer Ubuntu native-agent testing, physical SSH disconnection, a full
terminal-emulator matrix, disk-full/power-loss coverage and independent user
usability sign-off remain open. These limitations are compatible with an
explicit alpha, not a stable/beta certification.

Known limits: agent formats/flags can drift; archived records may be rejected by
an older native CLI; `/new` can invalidate a launch title's relevance; external
processes can evade duplicate-resume protection. A CLI not advertising resume is
shown as unsupported instead of using `--last`/`--continue` as a guess.

## Reference documentation

These are design inputs, not substitutes for the versioned checks above:

- Claude: https://code.claude.com/docs/en/cli-reference
- Codex: https://developers.openai.com/codex/cli/reference/
- Pi: https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/cli.md
- tmux: https://man.openbsd.org/tmux.1
- Textual: https://textual.textualize.io/guide/app/
