# Privacy and local security

4top has no account, telemetry endpoint, automatic update check, model API call,
transcript upload or crash-report upload. Pure browsing/searching stays local.
Agent `--help`/`--version` probes are invoked for diagnostics and runtime planning;
the original CLI's own behavior and settings still apply.

Sources: only configured Claude/Codex/Pi/Cursor history patterns. Source reads are
read-only, bounded, owned-file checked, and beneath approved canonical roots.
Symlink traversal below those roots and FIFOs/nonregular files are rejected.
Authentication files are not included in discovery. Full-content search is explicit.

Caches contain titles and project paths and are therefore sensitive. Private state
contains a local identity and your last selection, and nothing else: no run IDs, no
process identity, no environment values, no native argument arrays, no prompt
arguments, no search bodies and no terminal scrollback.

A remote host is reached with the `ssh` you already configured; 4top adds no
credential store, opens no port and starts no service. The ssh control socket lives
inside private state (0700). Remote rows necessarily carry that machine's project
paths and history locations to the local view, and a remote action runs on the
remote machine with the remote CLI's permissions.

Original authentication remains the agent's responsibility. 4top is not a sandbox:
an agent can modify its project and native store and can perform paid network
requests using its normal permissions.

A same-UID malicious process can generally interfere with another local process;
4top does not claim an OS security boundary against that adversary. State storage
is designed for a local filesystem, not shared NFS/SMB locking.

Before sharing diagnostics or recordings, inspect them yourself. Automatic
redaction is not guaranteed. Never attach raw transcripts or credential files to
GitHub issues. The demo is explicitly synthetic and touches no real agent store.

## Uninstall and data removal

Uninstall `4top` using the package manager/environment that installed it. This
alone deletes no native transcripts and stops no agent: agents started by 4top are
ordinary processes in your terminal or in your own multiplexer.

Deleting only `$XDG_CACHE_HOME/4top` removes rebuildable metadata. Removing
`$XDG_STATE_HOME/4top` loses local identity, the last selection and the ssh control
socket; history keys change with the identity, so previously copied keys stop
resolving. Removing state never terminates anything. Native store retention and
your multiplexer's scrollback have their own policies.
