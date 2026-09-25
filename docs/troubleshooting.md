# Troubleshooting and command contract

Run `4top doctor --json` in the **same shell/profile** used for launching. It
reports installed help/version evidence, not authenticated compatibility. Review
output before sharing. A corrupt/unsafe state directory is a hard error for
runtime control; 4top will not reset identity behind your back.

| Situation | Action |
| --- | --- |
| tmux missing | Browsing/demo still work; install tmux through your package manager for runtime actions. |
| Agent missing | Install its original CLI, or set `[agents.NAME].executable` to an executable wrapper path. Shell aliases are not executable files. |
| Missing cwd | Select a valid existing directory, or explicitly pass `resume --cwd /new/path`. No directory or symlink is created automatically. |
| New Codex says unlinked | Search history and confirm a `4top link RUN HISTORY --yes`. Directory/time similarity is not proof. |
| History already active | Use attach/open. Do not request another resume. Check all registered sockets. |
| UNKNOWN / stale | Inspect the exact native tmux pane/server and process. A failed query is not evidence the agent exited. |
| tmux socket changed | Supply `--socket /absolute/path` or run in the intended tmux environment. No default traversal of arbitrary sockets. |
| History rows are not listed | The table always shows managed runs; `h` toggles local history in and out. The counts line reports the real total either way. |
| Shared calling session | Use `attach RUN --client /dev/pts/N` (Linux) or the exact macOS TTY from native `tmux list-clients`. |
| Different socket inside tmux | Return to an outer terminal before attaching; nesting is refused. |
| Immediate native failure | The exited pane is retained for preview. No automatic retry. |
| `exit-unattached` is on | Choose a server that retains detached sessions or explicitly change your own tmux configuration. 4top does not change this global option. |
| Changed native /new context | Bindings describe launch/user evidence, not continuously observed current context. |
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

`new` is an explicit launch. `resume`, `link`, `terminate`, and `dismiss` require
confirmation or `--yes`. `open` attaches directly when verified live, otherwise
requires a resumable exact history and confirmation. `attach` never falls back
to starting a process. Prefixes must be unique; row numbers are not accepted.

Full search is literal (AND words, quoted phrases), not regex. Metadata defaults
to 2 MiB / 2,000 lines per file and preview to 200 text entries / 256 KiB. Explicit
full scans are cancellable. Oversized-line and malformed-data limits are reported.
Control/startup timeouts do not set an agent lifetime limit.
