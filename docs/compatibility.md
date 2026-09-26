# Compatibility and evidence

**Status: 0.2.0a1 alpha.** Implementation, automated tests and real native CLI
checks are different evidence. The [Mac acceptance record](validation/macos-0.1.0a2.md)
contains the actual results and their limits. It was written before the current
release line, so only its parsing, installation and native-resume findings apply.

| Source/driver | History | Launch | Exact native resume | Evidence |
| --- | --- | --- | --- | --- |
| Claude Code | JSONL adapter | Experimental; preallocated UUID when advertised | Experimental `--resume UUID` | No authenticated native smoke evidence for this build; synthetic fixtures and a local help/version probe only. |
| Codex | JSONL + archive adapter | Experimental | Experimental `resume UUID` | Native **0.155.1** passed an authenticated exact-resume smoke against an earlier build: a new process, the exact history ID, the recorded directory. |
| Pi | JSONL adapter | Experimental; UUID only when advertised | Experimental `--session PATH` | Native 0.87.0 installed on the acceptance Mac; its help/version probe passes, but no authenticated smoke evidence yet. Excerpts follow file order, not reconstructed active branches. |
| Cursor | Transcript JSONL | Unsupported | Unsupported | Read-only parser fixtures. Inferred cwd is display-only. |

A smoke check for one installed version is not a compatibility promise for all
releases. Raw histories, credentials and personal account details are not
published. Hosted CI never receives native agent credentials.

## Remote hosts

A remote host must run the same row schema. `list --json` rows declare
`schema_version` (currently **2**) and a mismatch is refused instead of partially
parsed, so upgrading one side first is safe and visible. Anything a remote row
claims about resumability is re-checked by the remote CLI when the action runs
there, so a stale view cannot cause a wrong resume.

4top has no remote daemon and no protocol of its own: the remote side is the same
CLI invoked over the ssh you configured. Nothing is certified about a remote
machine beyond what its own `4top doctor --json` reports.

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
- No multiplexer is required; 4top neither drives nor imports one.
- **Developer Mac:** macOS 27.0 arm64, Python 3.13.13, Textual 8.2.8. The current
  suite covers history parsing, config and host validation, process launch plans,
  the ssh transport (against a fake `ssh`) and the Textual UI.
- **Earlier 0.1.0a2 record:** 141 automated tests on the same Mac, plus one
  authenticated Codex exact-resume smoke check. The suite has changed shape since,
  so the count is not comparable.
- **Earlier 0.1.0a1 container run:** Linux x86_64, Python 3.13.5; 96 automated
  tests. Historical only.
- **Remote host (2026-09-26):** Debian 12 x86_64, Python 3.11.2, reached by ssh
  from macOS. View, preview, full search, terminal handover for `resume` and `new`,
  the confirmation gate and the row-schema refusal all verified; see
  [the remote verification record](validation/linux-remote-2026-09-26.md). No real
  agent CLI was installed there.
- **Hosted CI:** Ubuntu/macOS with Python 3.11/3.13 plus a Python 3.9 core job.
  See the repository's Actions runs for each exact commit; configuration alone
  does not prove a matrix passed.

Developer Linux native-agent testing with authenticated agents, physical SSH
disconnection, a full terminal-emulator matrix, disk-full/power-loss coverage and
independent user usability sign-off remain open. These limitations
are compatible with an explicit alpha, not a stable/beta certification.

Known limits: agent formats/flags can drift; archived records may be rejected by
an older native CLI; a CLI not advertising resume is shown as unsupported instead
of using `--last`/`--continue` as a guess. 4top cannot tell whether a session is
already running somewhere, so it cannot warn about a second live process.

## Reference documentation

These are design inputs, not substitutes for the versioned checks above:

- Claude: https://code.claude.com/docs/en/cli-reference
- Codex: https://developers.openai.com/codex/cli/reference/
- Pi: https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/cli.md
- OpenSSH: https://man.openbsd.org/ssh.1
- Textual: https://textual.textualize.io/guide/app/
