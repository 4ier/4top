# Implementation architecture

This is the implementation companion to the accepted 4top design. Alpha is not a
claim that every release gate of that design is complete.

## Boundaries

`session_ls.api` discovers approved local stores, reads bounded metadata, caches
it privately, searches literal decoded text, and returns read-only excerpts.
It has no import-time signal or filesystem operations. Legacy `session-ls`
retains six JSON fields; the typed Python API is versioned independently.

`fourtop.services.Manager` is the common CLI/TUI application service. Its unit is
a **session**, not a process: one row is a `HistoryRecord` plus the host it was
found on. `fourtop.models.Session` carries `key`, `agent`, `host`, `cwd`, `title`,
`started`, `last`, `source`, `status`, `problems` and `can_resume`, and nothing
else. There is no launch record, no ownership marker and no liveness field, because
there is nothing to observe: a native agent is resumable from its transcript alone.

`fourtop.agents.Drivers` turns a capability probe into an exact argv. `plan_new`
preallocates a session identifier when the CLI advertises one, so a new session
can be found immediately afterwards. `plan_resume` reopens the approved source
immediately before planning, refuses an agent/root mismatch, and requires an exact
UUID for Claude and Codex rather than guessing "the latest".

No module owns, supervises, kills or re-parents a process. 4top starts
the native CLI in the calling terminal and becomes it (`execvpe` from the CLI) or
waits for it (TUI, which suspends first). Persistence across a disconnect is the
user's multiplexer, not 4top's business.

## Remote hosts

`fourtop.hosts` is the only network code. A host is one `ssh` destination plus an
optional command path. The local side runs the remote CLI's read-only commands
(`list --json`, `search --json`, `preview`, `doctor --json`) with
`BatchMode=yes`, a `ControlMaster` socket inside private state, and a hard
timeout; anything that starts a process is handed to `ssh -t` instead.

Three rules keep a remote from being trusted blindly. Every remote argument is
quoted for the remote shell because ssh joins its arguments into one string. A
row whose `schema_version` does not match is refused, never partially parsed. And
a remote row is relabelled with the configured host name, while the history key
stays opaque and is passed back verbatim for any action, so a key is never
recomputed on the wrong machine.

## State and concurrency

`$XDG_STATE_HOME/4top`: private local identity and view preferences, plus the
`ControlMaster` socket directory. `$XDG_CACHE_HOME/4top`: rebuildable history
metadata. Directories are 0700; files are 0600. Writes use unique temporary files,
flush/fsync and atomic rename. Symlink writes and foreign ownership fail closed;
OS-owned `/tmp` and `/var` aliases are allowed on macOS.

The local identity scopes history keys. It is **not** a machine name: two machines
that have never seen each other cannot derive the same key for the same session,
which is why remote keys are treated as opaque.

There are no embeddings, database server, daemon, agent loop, task-duration caps,
concurrency quotas, automatic model summaries or automatic permission bypasses.

## Refresh and UI

History scans and remote calls run off the UI event loop. Metadata and explicit
full-content search are separate. Cancellation and generation IDs prevent an old
search from replacing a newer query. Stable keys preserve the selection as new
rows arrive. Failed sources keep the previous rows visible and are reported as
issues rather than as an empty machine.

Rendering a large store is the one measured performance limit: text sanitization
is expensive, so each row keeps its rendered cells and its derived labels (age
bucket, project name) until the row itself or the age bucket changes. A 2.7k-row
store renders in under two milliseconds per tick; the same loop cost about 50 ms
before the cache existed.

ANSI/OSC/control data and Rich markup are not trusted. All record-derived output
is rendered literally. Preview is bounded and paged; it sends no keystrokes.
The Pi preview is file order, explicitly not a reconstruction of its active tree.

## Deliberate alpha limits

No liveness tracking, no process migration, no semantic status inference, no automatic Codex-history binding, no auto-restart, no worktree
isolation and no Cursor runtime driver. Remote hosts are view-scoped rather than
merged, and polling over ssh is the ceiling: push updates would require a daemon
and are out of scope.

No compatibility certification without real native-version smoke tests. State
schema 1 and row schema 2 are the current implementations; unknown future schemas
are refused, not guessed or destructively migrated.
