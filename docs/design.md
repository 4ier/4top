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
`started`, `last`, `source`, `status`, `problems` and `can_resume`, plus what
session-ls reads from the transcript's end (`activity`, `last_request`, `branch`).
There is no launch record and no ownership marker: a native agent is resumable from
its transcript alone. `resident` is not a record either; it is the host's tmux
answering, at the moment of listing, whether an agent for the session runs there.

`fourtop.agents.Drivers` turns a capability probe into an exact argv. `plan_new`
preallocates a session identifier when the CLI advertises one, so a new session
can be found immediately afterwards. `plan_resume` reopens the approved source
immediately before planning, refuses an agent/root mismatch, and requires an exact
UUID for Claude and Codex rather than guessing "the latest".

No module supervises or re-parents a process, and none keeps a record of one.
The CLI becomes the native agent (`execvpe`). The panel either runs it in the
calling terminal and waits (plain layout), or, in the tmux layout
(`fourtop.workspace`), starts it in a window of 4top's own tmux server and swaps
its pane beside the list. A pane is tagged with its session and tmux is asked what
is open each time; a pane whose agent exited is closed on the next poll. The
earlier runtime failed by keeping its own records of panes, exit codes and
zombies, which drifted from reality. This layer keeps none. `q` detaches the tmux
client, so agents survive a closed laptop or a dropped phone link.

## Remote hosts

`fourtop.hosts` is the only network code. A host is one `ssh` destination plus an
optional command path. The local side runs the remote CLI's read-only commands
(`list --json --sync`, `search --json`, `preview`, `check`, `doctor --json`) with
`BatchMode=yes`, compression, a `ControlMaster` socket inside private state, and a
hard timeout; anything that starts a process is handed to `ssh -t` instead.
`fourtop.resident` is the host side of that: `attach` keeps the agent in a tmux
server of the host's own (`4top-agents`) and only attaches the ssh session to it,
so a dropped link detaches instead of killing the agent. Which sessions have an
agent there is asked of that server each time (`list-sessions`) and reported as
the row's `resident`; like the panel's layout, it records nothing.
`fourtop.sync` makes refresh incremental: only rows written since a cursor travel,
and a digest over every row proves the merged view equal to the host's, or one
full listing follows.

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
concurrency quotas, automatic model summaries or automatic permission bypasses. A
permission mode is only ever an `[agents.NAME] args` entry the user wrote.

## Refresh and UI

History scans and remote calls run off the UI event loop. Metadata and explicit
full-content search are separate. Cancellation and generation IDs prevent an old
search from replacing a newer query. Stable keys preserve the selection as new
rows arrive. Failed sources keep the previous rows visible and are reported as
issues rather than as an empty machine.

The panel lists every source at once, one section per machine, and each shows one
page of rows. Page sizes are shared out by need, so a machine with few sessions
leaves its space to the others. Only the visible page is rendered, and the list is
rebuilt only when what it shows changes, so a 2.7k-row store costs one page of
rows per tick, not the whole store.

ANSI/OSC/control data and Rich markup are not trusted. All record-derived output
is rendered literally. Preview is bounded and paged; it sends no keystrokes.
The Pi preview is file order, explicitly not a reconstruction of its active tree.

## Deliberate alpha limits

No liveness records (only tmux's own answer, in the layout and on each host), no
restart of an agent that exited, no process migration, no semantic
status inference, no automatic Codex-history binding, no auto-restart, no worktree
isolation and no Cursor runtime driver. Polling over ssh is the ceiling: push
updates would require a daemon and are out of scope.

No compatibility certification without real native-version smoke tests. State
schema 1 and row schema 2 are the current implementations; unknown future schemas
are refused, not guessed or destructively migrated.
