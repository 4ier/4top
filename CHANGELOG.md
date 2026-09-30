# Changelog

## 4top 0.2.0a8 — 2026-09-30

- A remote session whose ssh was killed (by Android, by Termux) says "Disconnected"
  on a host that keeps agents, like a dropped link: the agent keeps running there.
  It said "Ended". Found by killing the ssh on the tablet: the agent survived.

- **Remote agents stay on their host.** Opening a session on a host now runs
  `4top attach` there: the agent lives in a tmux server of that host's own
  (`4top-agents`, apart from yours), and the ssh connection only attaches to it. A
  dropped link, a killed Termux or a closed panel detaches; the agent keeps
  working, and opening the session again, from this device or another, attaches to
  the same process. Before, the agent was sshd's child and died with the link,
  confirmed on the tablet. `4top new AGENT --resident` does the same for a new agent.
- Rows carry `resident`: tmux on the host says an agent for the session runs there
  now. The panel marks such a row `○` when it is not showing it, and never calls it
  stopped. Nothing is recorded, and starting or exiting an agent costs no full
  listing: the sync trailer carries the list of resident sessions.
- A host running an older 4top is resumed as before, a host without tmux runs the
  agent in the ssh session as before, and a local session already kept on this
  machine is attached instead of started twice.

- The list is fitted to the window after a rotation: judged by its own, scaled-down
  width it looked like a narrow screen and stayed 25 columns wide on the tablet.

## 4top 0.2.0a7 — 2026-09-29

- A preview opened in the layout now really takes the whole window: fitting the
  layout after the zoom resized a pane, which in tmux unzooms, so the zoom undid
  itself. Found on the tablet.

- `contrib/termux/route` no longer closes quiet sessions after five seconds: netcat's
  `-w` is an idle timeout as well, and the previous release used it on the
  connection itself. Reachability is now probed with it and the connection is made
  without it. Found on the tablet: a remote pi exited 255 eight seconds after opening.
- From the tablet walk-through: a refresh no longer moves rows during input (it waits
  three seconds after the last key or tap, and what Enter acts on stays what is on
  screen); preview, details and help take the whole window in the layout; help
  fits a 40-column list; an "Ended" note clears when the session is opened again;
  parser notes no longer appear inside a preview.
- Rows say what each session is doing: `⟳ working`, `▶ your turn`, or `✗ stopped`
  (mid-turn but silent for ten minutes), for sessions active in the last day. A
  third line shows the latest request when it differs from the first, and the git
  branch joins the project. Read from the transcript's end by session-ls; with an
  older session-ls the rows look as before. No model is called.
- One tap on a touch screen still opened a session: Textual runs OptionList's own
  click handler after an override unless the default is prevented. It is now, and a
  touch that Termux reports twice within 120 ms counts once. Found on the tablet;
  the test now clicks through Textual instead of calling the handler.

- `contrib/termux/route` falls back to the LAN address when the tailnet path fails
  before connecting: with the jump host asleep, a NAS behind it showed as
  unreachable from a tablet on the same Wi-Fi.
- Placeholders no longer pile up in background windows when an agent on the stage
  is killed from outside or an older panel left one behind: the waiting one is
  brought back, and extras are closed when the panel starts.

Found by driving the panel on an Android tablet (Termux, 113×54, over the tailnet).

- Idle CPU fell from 8% to about 3% of one core (the bare framework is about 1.5%):
  the list is compared on plain values and rebuilt only when it changed, a host
  between its refreshes is not asked or repainted, local rows are rebuilt only
  after a new scan, and tmux is polled once every two seconds with one call. While
  the agent has the keyboard, remote hosts refresh every minute and catch up on
  return. A host that cannot be reached is retried on its interval, not every tick.
- A tap selects a row and a tap on the selected row opens it. Opening on the first
  touch started sessions nobody meant to open.
- When the agent beside the list has the keyboard, the list dims its selection and
  says so, because typing meant for the list went to the agent.
- A session written in the last minute says "active now": it is probably open
  elsewhere, where a second copy may be read-only. Ages under a minute read "now",
  and ages now advance instead of freezing at the moment the list was built.
- Rows no longer wrap for a few seconds after the layout splits; machine headers are
  no longer dimmed; the list takes about two fifths of a wide screen.
- An agent on another host no longer dies with the panel: it used the panel's ssh
  master, which closing the panel's pane hung up. Each agent has its own connection.
- Running `4top` again brings the list back into a layout whose list had exited
  (an upgrade, a crash), without touching the agents that kept running.

## 4top 0.2.0a6 — 2026-09-28

- The panel says when a newer 4top is on PyPI, with the upgrade command for how this
  copy was installed (uv tool, pipx, pip, or a checkout). It asks at most once a day
  and never reports a failed check; `[ui] update_check = false` turns it off.
- The tmux layout no longer requires `infocmp`, which Termux does not ship: 0.2.0a5
  crashed there when the panel started. The terminfo directories are read instead.
- On a narrow screen the layout starts with the list alone instead of two cramped
  halves, and a machine with no sessions takes only its header line.

## 4top 0.2.0a5 — 2026-09-28

- The preview opens at the latest messages and pages back with Earlier (`e`); a
  transcript's opening is mostly injected context. `4top preview KEY --tail` is the
  command-line form, and a remote host without it falls back to the first page.
- Sessions an agent started for itself are hidden until `a`. Rows carry
  `subagent`, filled by session-ls once it reports it.
- The panel lists this machine and every configured host at once, one section each,
  with its own page (`[` / `]`), refresh interval and state (`cached`,
  `unreachable`, `partial`). `H` and host switching are gone; `--host` still scopes
  the panel to one machine.
- With tmux installed, 4top runs in a tmux server of its own and opens agents beside
  the list. Opened sessions keep running in the background, `●` marks them, `q`
  detaches and `Q` closes everything. It records nothing about them: panes are
  tagged with their session and tmux is asked what is open. `[ui] layout = "plain"`
  keeps the previous behaviour.
- Enter opens a session without a confirmation dialog; the owning machine's check
  runs first and a refusal is shown in the list.
- Rows have two lines: the title, then agent, project and age. `p` shows one project
  and `f` folds a machine.
- `[agents.NAME] args` adds arguments to every start and resume of an agent, such as
  a permission mode. They are validated like command-line extras.
- A remote panel refreshes incrementally: only rows written since the last refresh
  travel, with a digest that proves the merged view equals the host's own rows and
  falls back to one full listing when it does not. An unchanged host of 2773
  sessions answers in about 650 bytes instead of 1.7 MB.
- The last rows of each host are cached, so switching to a host shows it at once and
  then catches up.
- Switching the panel from one remote host to another did nothing; it compared
  "remote" with "remote" and took them for the same view.
- A remote panel no longer downloads every session's full first prompt on every
  refresh. The title in a JSON row is capped at 200 characters; one Mac's
  `list --json` was 15.8 MB because of a single 100 KB pasted prompt, and a tablet
  on a mobile link timed out fetching it. Metadata search on the owning host still
  sees the whole title.
- Remote queries ask ssh for compression. With the cap, that host's rows went from
  15.8 MB to about 0.3 MB on the wire.
- The checkout install no longer names `./packages/session-ls`, which left the
  repository with 0.2.0a2, and the docs gate now checks `./` paths in shell blocks.

## 4top 0.2.0a4 — 2026-09-26

- `4top check` now proves the agent can actually run here, not just that a path is
  configured. It runs the same `--help` probe a resume would, and reports why the CLI
  failed: the case that prompted this was a Mac whose `pi` and `codex` launchers need
  `node` on `PATH`, which a non-interactive ssh session does not have, so the panel's
  preflight passed and the resume then exited 5 with no explanation.
- Document that an agent installed on a host can still be "not found" when that host's
  4top is invoked over ssh, because a non-interactive session gets a minimal `PATH`.
  The remedy is an absolute `[agents.NAME].executable` on that host, which is what the
  error message already suggests.

## 4top 0.2.0a3 — 2026-09-26

Get the ssh connection socket length right, measured rather than assumed.

- The socket directory now allows for what ssh actually creates there: a 40-character
  `%C` hash plus a 16-character random suffix, under a limit measured at 103 bytes on
  macOS and 106 on Android. The state directory is preferred, the temporary directory
  is the fallback, and if neither fits, ssh runs without connection reuse instead of
  failing every host.

## 4top 0.2.0a2 — 2026-09-26

Fix the remote-host path on Android/Termux, which made every host unusable there.

- Put the ssh connection socket somewhere short enough. Unix sockets have a hard
  path limit, and Termux on Android runs under
  `/data/data/com.termux/files/home`, so the connection path overflowed and every
  remote host failed with `unix_listener: path ... too long for Unix domain socket`.
  The socket now prefers the state directory, then the temporary directory, then
  `/tmp`.
- `session-ls` moved to its own repository
  ([4ier/session-ls](https://github.com/4ier/session-ls)), which is now the home of
  the parser package and the owner of its releases. This repository consumes it as
  an ordinary PyPI dependency, so its CI no longer builds or publishes a second
  project, and its release gate asks PyPI whether the required version exists
  instead of assuming a local package.

## 4top 0.2.0a1 — 2026-09-26

Published to PyPI from tag `v0.2.0a1`, together with `session-ls 0.2.0` as its
dependency. Being a pre-release, `pip install 4top` needs `--pre`; `uv tool
install 4top` does not.

The first release of the session-first line: 4top tracks transcripts rather than
processes, and reaches other machines over ssh. The previous line owned a process
per launch and reported liveness; that layer and its vocabulary are gone.

### Breaking: 4top tracks sessions, not processes

A native agent is recoverable from its transcript alone, so the transcript is the
durable object and the process is the ephemeral one. 4top now keeps only
transcripts: it does not own, supervise or report on a process, and it drives no
terminal multiplexer of its own.

- Deleted: the process-ownership layer and everything that existed to support it
  — launch records, reservations, locks, the operation log, ownership markers,
  exit-code and zombie evidence, the liveness states, and the commands, options
  and `[runtime]` configuration section that reached into it.
- `new` runs the agent in the calling terminal: the CLI replaces itself with the
  agent (`execvpe`), and the TUI suspends, waits and returns to the panel. Keeping
  work alive across a disconnect belongs to whatever the user already runs.
- `list --json` rows are `schema_version` 2 and carry `key`, `agent`, `host`,
  `cwd`, `title`, `started`, `last`, `source`, `status`, `problems` and
  `can_resume`. Consumers of the previous row schema must be updated.
- Local history is shown by default. The old view-schema `history` flag is
  ignored and the view file is written as schema 3.
- Nothing needs migrating: state is now a local identity plus the last selection.
  Earlier files under `$XDG_STATE_HOME/4top` are ignored and can be deleted. A new
  local identity changes history keys, so anything that copied a key must copy it
  again.

### Remote hosts over SSH

- Add `[hosts.NAME]` with `ssh`, `command`, `refresh_seconds` and
  `timeout_seconds`, plus the global `--host` option. Views are isolated:
  `--host` replaces the local scope instead of merging machines into one table.
- The remote side is the same CLI, with no daemon and no new port. It runs with
  `BatchMode=yes` (a missing key fails fast), a `ControlMaster` socket inside
  private state, and a hard timeout; every remote argument is quoted for the
  remote shell.
- A row whose `schema_version` differs is refused instead of partially parsed. A
  remote row is relabelled with the configured host name, and its history key
  stays opaque so it is never recomputed on the wrong machine.
- `resume` and `new` with `--host` hand the terminal to `ssh -t`, so the process
  is created on the machine that owns the history.
- `H` switches the running panel between this machine and a configured host, so
  comparing two machines no longer means quitting and relaunching. Rows, selection
  and search state are dropped with the old scope, an in-flight refresh for the old
  scope is discarded, and the saved selection stays local.

### Releases

- Publish one project per repository. The first attempt to publish both from this
  workflow failed with `403 Invalid API Token: OIDC scoped token is not valid for
  project '4top'`: one job means one OIDC exchange, and the token is scoped to the
  project its publisher matched. Rather than disambiguate two publishers with two
  environments, this workflow now publishes only `4top` and consumes `session-ls`
  from PyPI, which is the layout the two packages actually have: separate projects,
  separate version lines, separate pipelines.
- Publish from CI on a `v<version>` tag: the suite runs, `scripts/check_release.py`
  gates the release, both wheels are built and installed into a fresh environment,
  `session-ls` is published before `4top`, and the GitHub release is opened. Uploads
  use PyPI trusted publishing, so no token is stored in the repository, and the
  manual trigger defaults to a dry run.
- The gate refuses a tag that does not match the version, artifacts that are not the
  versions being released, and a root requirement the library cannot satisfy. That
  last one is real: the root requires `session-ls>=0.2.0` while PyPI only ever held
  0.1.0, so publishing the root alone would have produced an uninstallable package.

### Interface

- The host picker (`H`) switches the panel between this machine and a configured
  host, so comparing two machines no longer means relaunching.
- The selection line is context and one action: `agent · directory` and what Enter
  does. The history key moved to the details screen, where it is needed, and the
  status line says nothing when there is nothing to report.
- A recorded directory that no longer exists now names itself, is marked in the
  details screen, and asks for a directory to resume in instead of failing with
  "not accessible".
- The mouse wheel moves the highlight with the view instead of scrolling the
  viewport away from it.
- Resume preflights first. `4top check KEY` reports whether a session can resume
  here, and the panel asks before it hands over the terminal, so a host without
  that agent installed is a visible message instead of a failure that flashes past
  under a repainted screen. A hand-over that still fails reports its exit code, and
  a dropped ssh connection says that the session is unchanged in its transcript.
- The ssh connection keeps a liveness probe and a warm control connection, so an
  unstable link fails instead of hanging.
- `doctor` reports the `revision` of the code it is running, and `--host NAME doctor`
  reports both, so two machines on different revisions is visible instead of turning
  into a confusing error later.
- `scripts/remote_update.py NAME` brings a remote's 4top forward. It uses the host's
  own egress first and, only if that fails, lends this machine's proxy through a
  reverse tunnel that lives exactly as long as the update. Both hosts were observed
  with a proxy that answers on one port and fails on another, or works and then stops,
  so the fallback is a real case and it says which one it used.
- `scripts/check_docs.py` fails when the documentation stops describing the code,
  and runs in a pre-commit hook, the test suite and CI.

### Fixes in this line

- A large history no longer costs a full re-render on every refresh. Rows whose
  inputs are unchanged keep their rendered cells and their derived labels, so a
  2.7k-row store renders in under two milliseconds per tick instead of about 50 ms.
- Recognize Claude 2.1.x metadata-only session files. Claude prepends records such
  as `last-prompt`, `mode`, `attachment` and `cost-state`, and a session that was
  opened, renamed and quit never writes a user or assistant message; those files
  were reported as `ValueError` instead of being listed. Identity now comes from
  the `sessionId` these records carry, and the native `ai-title`/`agent-name` is
  used as the title only when the session has no user text. `PARSER_VERSION` is 2
  in both parsers, so the first scan after upgrading re-reads every file once.
- Report why a history file was rejected, not only the exception class, and keep
  OSError text (which contains the private path) out of the message.
- Print the human table without the terminal UI stack. Display width is computed
  with `unicodedata`, so `4top list` works where only the standard library is
  installed. Found by running the CLI on a host that had neither the UI dependency
  nor a package manager to add it.
- Stop reporting a non-directory that a history pattern matched as an
  unavailable directory. A real store keeps a marker file inside its project
  directory, so every scan reported an issue and every list exited 6. A configured
  root that is not a directory is still reported once, and a genuinely unreadable
  directory is still reported.
- Stop reporting a remote login banner as a query issue. Only diagnostics prefixed
  by the remote CLI count, so a healthy host no longer looks broken and no longer
  exits 6. Found by pointing `--host` at a machine whose ssh shell prints a banner.
- Correct the acceptance record: Pi 0.87.0 is installed on the acceptance Mac and
  its capability probe passes. No authenticated Pi smoke check was run, and the
  previous "not installed" statement was wrong.

## 4top 0.1.0a2 — 2026-09-24

- Preserve absent native store environment overrides. In particular, launching
  Claude no longer relocates its default configuration and triggers onboarding
  for an already-authenticated installation. Explicit profiles still take priority.
- Add 13 native-root regression cases covering all three agent drivers.
- Explain that resume uses current native configuration, not replayed launch flags.
- Validate 141 automated tests on macOS 27.0 arm64, plus a separate authenticated
  Codex 0.155.1 exact-resume smoke check.
- Add 32 regression cases hardening the then-current process-ownership layer
  against exit races, PID reuse and malformed ownership data.
- Establish the independent `4ier/4top` repository and fresh-wheel installation.
- Keep Pi, other native versions, physical SSH loss and Linux native validation
  explicitly outside this release's compatibility evidence.

See [the Mac acceptance record](docs/validation/macos-0.1.0a2.md).

## 4top 0.1.0a1 / session-ls 0.2.0

- Initial keyboard-first TUI and scriptable CLI over a shared service layer.
- Exact-target process management, bounded previews, safe destructive actions,
  explicit history linking and same-history reservations. That layer is not part
  of the current line; see the unreleased section above.
- Experimental Claude/Codex/Pi exact resume drivers; read-only Cursor history.
- Private atomic state, no extra daemon, no account.
- Literal Unicode full search, cancellation, stable selection, no-color, isolated demo.
- Real process and terminal tests alongside core and headless UI tests.
- session-ls keeps its six-field JSON interface and independent stdlib-only package.
  Imports no longer change SIGPIPE; full search now follows documented literal
  semantics instead of accidentally interpreting a grep regular expression.
- Cached file identity includes parser version and inode metadata; cache writes use
  unique temporary names. Corrupt caches are rebuilt rather than silently trusted.

This alpha does not certify authenticated native CLI compatibility, all minimum
platform versions, cross-machine aggregation, or external-user usability gates.
