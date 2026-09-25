# Implementation architecture (0.1.0a1)

This is the implementation companion to the accepted 4top v0.1 design. Alpha is
not a claim that every release gate of that design is complete.

## Boundaries

`session_ls.api` discovers approved local stores, reads bounded metadata, caches
it privately, searches literal decoded text, and returns read-only excerpts.
It has no import-time signal or filesystem operations. Legacy `session-ls`
retains six JSON fields; the typed Python API is versioned independently.

`fourtop.services.Manager` is the common CLI/TUI application service. Runtime
records and native history remain separate entities. A record can have an
immutable launch binding and an explicit user binding. Current native context is
not continuously observed. Every mutation rechecks identity and ownership.

`fourtop.tmux` is an argv-based, exact-target backend. One process belongs to one
managed pane. Stable IDs, a random run marker, a local host marker, server process
identity, socket device/inode, and recorded process identity form its evidence.
Socket **ctime cannot be an identity component**: tmux changes socket permissions
when clients attach or detach. PIDs and display names alone are insufficient.

On Linux, process start identity uses boot ID and `/proc/PID/stat` start ticks;
on macOS it uses PID, owner and `ps lstart`. Zombies are not live processes.
The macOS clock has coarser resolution, so pane markers and server identity remain
mandatory. No guarantee is made against a malicious process running as the same
OS user and deliberately forging all local metadata.

## Startup handoff

1. Validate explicit native argv, cwd, executable, root and capability.
2. Reserve a random run in an atomic private record.
3. Listen on a short, private Unix socket, with a nonce and same-UID peer check.
4. Create a detached tmux pane running the installed one-shot Python helper.
5. Verify its PID/pane; set ownership and `remain-on-exit` before execution.
6. Deliver argv, cwd and current environment through the socket, not through
   shell interpolation, tmux start-command text or a payload file.
7. Persist a committed reservation before granting permission to exec. A failed
   grant send is uncertain: it might have reached the peer.
8. The helper preserves the new pane's TMUX/TERM context and `exec`s the native CLI.
   Its close-on-exec connection provides handoff evidence. No supervisor remains.

UI shutdown cannot cancel a handed-off process. A failed parent never blindly
retries or kills an entire tmux server. After failures, source observations and
saved reservations are reconciled conservatively. A failed launch before any
execution grant may be shown as EXIT; uncertain ownership remains UNKNOWN.

## Attach, termination and clients

Outside tmux, Textual suspends before running an interactive tmux client; detach
returns to the same view. Inside the same server, the app restores the TTY before
switching the exact calling client. Ambiguous clients or cross-socket nesting are
refused. The inside panel exits rather than claiming it is a still-open dashboard.

Destructive control includes a final server-side marker/PID guard, so a pane
replaced after confirmation is not killed. No application path calls kill-server.
The test fixture may kill only its own isolated server for cleanup.

## State and concurrency

`$XDG_STATE_HOME/4top`: private identity, per-run JSON, view preferences, short
flock locks, bounded operations metadata. `$XDG_CACHE_HOME/4top`: rebuildable
history metadata. Directories are 0700; files are 0600. Writes use unique temporary
files, flush/fsync and atomic rename. Symlink writes and foreign ownership fail
closed; OS-owned `/tmp` and `/var` aliases are allowed on macOS.

Same-history startup uses a host/user/state-directory-scoped lock, then rechecks
all recorded sockets. An unavailable possibly conflicting source blocks resume.
External CLIs, other state directories and remote machines are outside this lock.

There are no embeddings, database server, daemon, agent loop, task-duration caps,
concurrency quotas, automatic model summaries or automatic permission bypasses.

## Refresh and UI

Runtime samples and history scans run off the UI event loop, with bounded worker
counts. Metadata and explicit full-content search are separate. Cancellation and
generation IDs prevent an old search from replacing a newer query. Stable keys
preserve the selection as new rows arrive. Stale sources remain visible.

ANSI/OSC/control data and Rich markup are not trusted. All record-derived output
is rendered literally. Preview is bounded and paged; it sends no keystrokes.
The Pi preview is file order, explicitly not a reconstruction of its active tree.

## Deliberate alpha limits

No multi-host aggregation, process migration, semantic status inference,
automatic Codex-history binding, auto-restart, worktree isolation or Cursor runtime
driver. Remote hosts are proposed separately in
[remote-design.md](remote-design.md), which also proposes deleting the runtime
layer described above. No compatibility certification without real native-version smoke tests.
State schema 1 is the first implementation; unknown future schemas are refused,
not guessed or destructively migrated. Unrecorded orphan markers are reported for
native tmux inspection rather than assigned fabricated directories or IDs.
