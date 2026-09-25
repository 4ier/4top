# Acceptance and release evidence

The current package is an **alpha**. A passing automated suite is not the full
product/native-agent sign-off from the design. This guide makes that distinction
explicit and reproducible.

For the completed 0.1.0a2 developer Mac run, see
[the Mac acceptance record](macos-0.1.0a2.md); it was written before the current
release line, so only its parsing, installation and native-resume findings apply.
[Current suite summary](macos-suite-dev.json) and
[fresh-install summary](macos-fresh-install-0.1.0a2.json) are the machine records
kept in this tree. Raw records for older runs are in git history.

## Run the isolated suite

Install both packages and the exact development dependencies from the repository,
then run:

```sh
python scripts/acceptance.py --output acceptance-output
python -m build --no-isolation packages/session-ls --outdir dist
python -m build --no-isolation --outdir dist
python scripts/verify_install.py dist
```

No multiplexer is required. The acceptance runner starts real fake agents as real
processes, drives the ssh transport with a fake `ssh` on `PATH`, and runs the
unit and Textual headless suites; it writes a log, JUnit XML, and a JSON summary
that reports failures, errors and skips. All agents in this suite are synthetic
fixtures; they do not connect to models, use credentials, read real HOME stores,
or reach a network.

`verify_install.py` uses a **new virtual environment**, not an editable checkout.
It installs both wheels, validates commands and isolated demo output, checks the
old CLI, and ensures the 4top wheel does not vendor the session-ls namespace.

For an offline test, pip's standard `PIP_NO_INDEX` and `PIP_FIND_LINKS` environment
variables can point to an independently downloaded wheel cache. No dependency
binaries are included in the source repository.

## Acceptance cases as currently implemented

These replace the previous AT-numbered set: the runtime cases it described no
longer exist.

| Case | Automated evidence | Remaining scope |
| --- | --- | --- |
| Exact resume plan | Claude/Codex/Pi argv, root, cwd and UUID checks; a guessed "latest" is refused | Real native CLI |
| Launch in this terminal | Real fake agent started as a real process; argv, cwd, environment and written session file asserted | Wrapper scripts are the user's responsibility |
| Hand over the terminal | `execvpe` replacement asserted from a child process; suspend failure reported instead of crashing | Real terminal emulators |
| Metadata-only sessions | Claude 2.1.x prelude, `ai-title` fallback, user text precedence | Format drift needs new fixtures |
| Partial or malformed history | Blank/bad/half lines, budgets, removed sources, cache invalidation, parser version | Disk-full/power-loss matrix not complete |
| Unicode | CJK metadata search, full search, table alignment and preview | User needs a suitable terminal font |
| Unusual paths and arguments | Literal semicolons, quotes, dollar expressions; nothing executed | Arbitrary native CLI behavior |
| Terminal injection | ANSI/OSC/bidi/control sanitization; literal Rich text | Native CLI's own output remains native |
| Local state and cache faults | Concurrent writers, corrupt cache, symlinks, permissions, schema refusal | Not a security boundary against same-UID tampering |
| Absent prerequisites | Missing directory/executable/history; no silent substitution | Native auth setup stays with the user |
| Large store cost | Render cost measured against a named dataset on the developer host | Idle CPU and memory targets remain targets |
| Remote schema handshake | Mismatched `schema_version` refused, never partially parsed | Two-machine run still pending |
| Remote argument quoting | Every remote argument quoted for the remote shell | Exotic ssh configurations |
| Remote failure visibility | ssh failure, timeout and missing binary become explicit issues | Network partitions and host-key changes not covered |
| Remote view isolation | Remote rows are relabelled; scope replaces instead of merging | — |
| Private transport state | Control socket under private state, mode 0700 | Unusual `ssh` wrapper behavior |
| Selection stability | Selection survives refresh, reorder and insertion | — |
| Demo isolation | In-memory manager, no Config construction or real mutation | No native compatibility claim from the demo |
| Privacy by default | Synthetic roots, no telemetry, no auth-file scanning, no network except configured ssh | Not a network-level audit of every dependency |

Do not convert a synthetic driver test into a native vendor compatibility badge.
Performance claims need a named dataset, machine, versions and measurement.

## Native smoke test — local operator acceptance

Run this separately on macOS and Linux using the user's existing authenticated
CLI, an explicitly chosen throwaway project, and a small approved task. Record
versions before testing. Never put credentials or raw transcripts in CI or Issues.

1. Install the two released wheels in an isolated environment; run `4top doctor`.
2. Start each available native agent with `4top new <agent>`. Confirm the normal
   permission prompts and native full-screen interface are preserved, and that the
   agent lives in the terminal you launched it from.
3. Exit the agent natively; find the exact session in 4top. Confirm **resume**
   creates a new process for that ID/root/cwd and that the earlier response is
   visible in the resumed native UI.
4. Configure one remote host, then run `4top --host NAME doctor`, `list --json` and
   one `resume`. Confirm the new process exists on the remote machine and that the
   local view stays scoped to that host.
5. Check Unicode titles, resize and your terminal theme. Confirm an unreachable
   host fails with an explicit message instead of an empty list.

Use explicit consent for any task that can alter files or use model billing.
A `--help` probe alone is insufficient evidence that this checklist passed. Save
only sanitized identifiers, versions and pass/fail observations.

## Publication gates

A stable/beta release still requires native-agent smoke evidence, a real
two-machine remote run, physical terminal/SSH validation, further storage fault
tests, and usability feedback. This alpha may be shared for reproducible testing
with these limitations visible. The standalone repository is `4ier/4top`; a
published source branch or passing CI is not a stable release or a PyPI
publication. Developer Mac deployment and native smoke results are recorded
separately.
