# Acceptance and release evidence

The current package is an **alpha**. A passing automated suite is not the full
product/native-agent sign-off from the design. This guide makes that distinction
explicit and reproducible.

For the completed 0.1.0a2 developer Mac run, including native Codex
checks, see [the Mac acceptance record](macos-0.1.0a2.md). The earlier
`local-summary.json` records only the original 0.1.0a1 container run.

## Run the isolated suite

Install both packages and the exact development dependencies from the repository,
with tmux available in PATH, then run:

```sh
python scripts/acceptance.py --output acceptance-output
python -m build --no-isolation packages/session-ls --outdir dist
python -m build --no-isolation --outdir dist
python scripts/verify_install.py dist
```

The acceptance runner requires tmux (no silent runtime skips), runs real tmux and
PTY tests plus unit/Textual headless tests, and writes a log, JUnit XML, and a JSON
summary. All agents in this suite are synthetic fixtures; they do not connect to
models, use credentials, read real HOME stores, or touch existing user sockets.
Cleanup is limited to the test's own temporary socket paths.

`verify_install.py` uses a **new virtual environment**, not an editable checkout.
It installs both wheels, validates commands and isolated demo output, checks the
old CLI, and ensures the 4top wheel does not vendor the session-ls namespace.

For an offline test, pip's standard `PIP_NO_INDEX` and `PIP_FIND_LINKS` environment
variables can point to an independently downloaded wheel cache. No dependency
binaries are included in the source repository.

## Traceability to design acceptance cases

| Design case | Automated evidence | Remaining scope |
| --- | --- | --- |
| AT-01 reattach same process | Runtime nonce/PID/counter; real PTY attach/detach and resize | Real native CLI |
| AT-02 close panel process | SIGKILL caller after handoff; agent and child remain | User OS session policies |
| AT-03 handoff failures | Inject before pane, plan transfer, execution grant, after grant | More storage fault points |
| AT-04 concurrent resume | Separate concurrent CLI processes, exactly one launch | External launchers are outside guarantee |
| AT-05 parallel Codex | Two runs in same directory remain unlinked and distinct | Native telemetry-based auto-link is absent |
| AT-06 immediate exit | Fake CLI exits 7, retained pane and exit evidence | Native startup/auth errors |
| AT-07 server identity reuse | New server and reused pane IDs are refused | Other OS/process policies |
| AT-08 same-server switch | Real PTY, correct calling client, no nesting | Real user terminal applications |
| AT-09 multiple clients | Second client is not detached; shared caller needs explicit target | Shared tmux view semantics remain |
| AT-10 return to panel | Actual suspended TUI, detach, selection/PID restored | Native rendering differences |
| AT-11 cross-socket conflicts | All recorded sockets checked; nested cross-socket refused | No multi-host aggregation |
| AT-12 resize/narrow screen | Real PTY SIGWINCH and headless 46/80/100-column layouts | More terminal emulators |
| AT-13 Unicode | Chinese metadata search, full search, table and preview | User must have a suitable terminal font |
| AT-14 unusual paths/arguments | Literal semicolons, quotes, dollar expressions; no execution | Arbitrary wrapper behavior is user's responsibility |
| AT-15 terminal injection | ANSI/OSC/bidi/control sanitization; literal Rich text | Native CLI's own terminal output remains native |
| AT-16 partial/malformed history | Blank/bad/half lines, limits, removed sources, cache invalidation | Format drift needs new fixtures |
| AT-17 local state/cache faults | Concurrent writers, corrupt cache, symlinks, permissions, locks | Disk-full/power-loss matrix not complete |
| AT-18 absent prerequisites | Missing directory/executable/history, no silent substitution | Native auth setup stays with user |
| AT-19 q/Ctrl-C/disconnection | Panel q, caller kill and PTY disconnect | Physical SSH loss / logind behavior not tested here |
| AT-20 native context changes | Launch association never labeled as current context | No native /new hook integration |
| AT-21 manual tmux changes | Rename/split/move accepted; respawn or changed marker refused | Complex user hook configurations |
| AT-22 demo isolation | In-memory manager, no Config construction or real mutation | No native compatibility claim from demo |
| AT-23 privacy by default | Synthetic roots, no network features, no auth-file scanning | Not a network-level audit of all dependencies |
| AT-24 fresh environment | Old server env overridden via memory; canary absent from state | Real proxy/tool installations |
| AT-25 final target guard | Replace target after validation; terminate refuses | Current-user deliberate process tampering is not a security boundary |
| AT-26 removal/cleanup | Dismiss preserves original history; uninstall instructions | Package-manager uninstall plus live native processes needs manual check |

The Unix transport, tmux, process identity and Textual tests are separate layers.
Do not convert a synthetic driver test into a native vendor compatibility badge.
Performance targets (idle CPU, p95 first-screen, 10k-row memory) remain targets
unless accompanied by a specific benchmark dataset and environment.

## Native smoke test — local operator acceptance

Run this separately on macOS and Linux using the user's existing authenticated
CLI, an explicitly chosen throwaway project, and a small approved task. Record
versions before testing. Never put credentials or raw transcripts in CI or Issues.

1. Install the two released wheels in an isolated environment; run `4top doctor`.
2. Start each available native agent using `4top new <agent>`. Confirm the normal
   permission prompts and native full-screen interface are preserved.
3. Record the run key and pane PID, detach, close 4top, reopen it and attach.
   Verify PID/run identity and continued interaction. Repeat through actual SSH.
4. Exit natively; find the exact history in 4top. Confirm **resume** creates a new
   process for that ID/root/cwd. A second 4top resume must refuse the live duplicate.
5. Start two Codex instances in one project: they must remain distinct/unlinked.
6. Verify `/new` inside the original CLI does not make 4top claim its launch record
   is a verified current context. Check Unicode, resize and original terminal theme.

Use explicit consent for any task that can alter files or use model billing.
A run entry or `--help` probe alone is insufficient evidence that this checklist
passed. Save only sanitized identifiers, versions and pass/fail observations.

## Publication gates

A stable/beta release still requires native-agent smoke evidence, two independent
user environments, physical terminal/SSH validation, further storage fault tests,
and usability feedback. This alpha may be shared for reproducible testing with
these limitations visible. The standalone repository is `4ier/4top`; a published
source branch or passing CI is not a stable release or a PyPI publication.
Developer Mac deployment and native smoke results are recorded separately.
