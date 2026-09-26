# Troubleshooting and command contract

Run `4top doctor --json` in the **same shell/profile** used for launching. It
reports installed help/version evidence, not authenticated compatibility, and the
configured remote hosts. Review output before sharing. A corrupt or unsafe state
directory is a hard error; 4top will not reset identity behind your back.

| Situation | Action |
| --- | --- |
| Agent missing | Install its original CLI, or set `[agents.NAME].executable` to an executable wrapper path. Shell aliases are not executable files. |
| Missing cwd | Select a session whose directory is known, or pass `resume --cwd /new/path`. No directory or symlink is created automatically. |
| Session cannot resume | Claude and Codex need an exact UUID, and every agent needs a native (not inferred) directory. Cursor transcripts are read-only. |
| Cannot tell what is running | 4top never claims liveness. Use your own multiplexer, or the agent's native UI. |
| `4top new` ended with the terminal | The agent runs in the foreground. Start 4top inside your own terminal multiplexer to keep it alive. |
| `list` looks stale | `r` refreshes. Metadata rescans on `history_refresh_seconds`; remote hosts refresh on their own interval (default 15s). |
| Remote runs older code | `4top --host NAME doctor` prints both revisions and reports a mismatch. Update with `scripts/remote_update.py NAME`. |
| Remote has no working egress | Its own proxy may be down. `scripts/remote_update.py NAME` tunnels this machine's working egress to it for the update. |
| Remote agent missing | The host that owns the session must have that agent installed. `4top --host NAME check KEY` says so before the panel hands the terminal over. |
| Remote agent "not found" although it is installed | A non-interactive ssh session gets a minimal `PATH` (on macOS `~/.local/bin:/usr/bin:/bin:...`), so the host's own agents need absolute paths in its configuration too: `[agents.pi] executable = "..."`. `4top --host NAME doctor` shows which of them the remote can resolve. |
| Link dropped mid-resume | The session is unchanged in its transcript on that host; resume it again when the link is back. Keep agents alive across a drop with your own terminal multiplexer there. |
| Remote host unreachable | `4top --host NAME doctor` shows the ssh exit and stderr. A missing key fails fast because `BatchMode` is always on. |
| Remote `4top` not found | Non-login ssh shells may not have it on `PATH`; set `command` to an absolute path in `[hosts.NAME]`. |
| Row schema mismatch | Update both machines to the same 4top version. Mismatched rows are refused, never partially parsed. |
| Remote view shows nothing | The remote lists its own sessions; 4top does not merge machines into one table. |
| Read-only/unsafe state path | Check ownership, mode 0700 and filesystem; do not make directories world-writable. Symlink state writes are refused. |
| Cache damaged/full | Browsing can proceed without a trusted cache and reports degradation. Never edit a native transcript to repair 4top. |
| Bad configuration | Correct unknown/invalid keys; explicit files never silently fall back to a different profile. |

`4top list --json` and `search --json` emit one schema-versioned JSON object per
row; diagnostics go to stderr. A partial/unavailable source returns exit 6 with
available rows, not an apparently complete result. Human table output is for
viewing; use JSON for stable identifiers and scripting.

Exit codes: **0** success; **1** generic application error; **2** arguments/config;
**3** missing target; **4** conflict/cancel/confirmation; **5** missing dependency or
interactive terminal; **6** unavailable/unsafe/partial; **130** interrupted UI.

`new` is an explicit launch. `resume` requires confirmation or `--yes`, and on a
remote host it also requires `--yes` because that process starts on another
machine. 4top has no managed runtime, so it has no command that reaches into one.
Prefixes must be unique; row numbers are never
accepted.

Full search is literal (AND words, quoted phrases), not regex. Metadata defaults
to 2 MiB / 2,000 lines per file and preview to 200 text entries / 256 KiB. Explicit
full scans are cancellable; a remote full scan runs on that host and reports its
own issues. There is no agent lifetime limit.
