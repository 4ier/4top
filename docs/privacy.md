# Privacy and local security

4top has no account, telemetry endpoint, automatic update check, model API call,
transcript upload or crash-report upload. Pure browsing/searching stays local.
Agent `--help`/`--version` probes are invoked for diagnostics and runtime planning;
the original CLI's own behavior and settings still apply.

Sources: only configured Claude/Codex/Pi/Cursor history patterns. Source reads are
read-only, bounded, owned-file checked, and beneath approved canonical roots.
Symlink traversal below those roots and FIFOs/nonregular files are rejected.
Authentication files are not included in discovery. Full-content search is explicit.

Caches contain titles and project paths and are therefore sensitive. Private
metadata contains run IDs, native identifiers/locations, cwd, executable paths,
timestamps, process identity, and user-confirmed associations. It does **not**
retain complete environment values, native argument arrays, prompt arguments,
search bodies, or terminal scrollback. Operation logs are bounded to 200 entries.

The startup helper receives environment/argv through an authenticated same-user
local socket and then execs the CLI. Original authentication remains the agent's
responsibility. 4top is not a sandbox: an agent can modify its project and native
store and can perform paid network requests using its normal permissions.

A same-UID malicious process can generally interfere with another local process;
4top does not claim an OS security boundary against that adversary. Runtime/state
storage is designed for a local filesystem, not shared NFS/SMB locking.

Before sharing diagnostics or recordings, inspect them yourself. Automatic
redaction is not guaranteed. Never attach raw transcripts or credential files to
GitHub issues. The demo is explicitly synthetic and touches no real agent store.

## Uninstall and data removal

Uninstall `4top` using the package manager/environment that installed it. This
alone issues no tmux commands and deletes no native transcripts. Already exec'd
agents are independent of the 4top UI/helper; don't delete an environment that
also contains dependencies required by your original agents.

Deleting only `$XDG_CACHE_HOME/4top` removes rebuildable metadata. Removing
`$XDG_STATE_HOME/4top` loses runtime associations, local identity, and view state;
it does not kill tmux, but existing work may need native tmux inspection. Never
remove state as a substitute for terminating a running agent. Native stores and
tmux's own scrollback have separate retention policies.
