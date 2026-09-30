# Cloud tasks on E2B

Status: implemented in `fourtop.cloud` and `fourtop.e2b`. It supersedes the design of
PR #15 ("sessions that own a machine"). The lifecycle below was run against real
E2B sandboxes on 2026-09-30, and every verb is tested against a fake E2B
(`tests/fake_e2b.py`).

## Home first, cloud as overflow

The home machines are where agents run: they hold the checkouts, the credentials and
the history. The cloud is for the times they cannot take the work, because they are
asleep, busy or unreachable. That is also when the person is likely away, holding a
tablet with no checkout on it. So a cloud task is defined by what a tablet can say:

**a repository URL, a ref and a prompt (and an agent)**, and its outcome is a
**pushed branch**, optionally a pull request.

| Step | What happens |
| --- | --- |
| `4top cloud new AGENT PROMPT --repo URL --ref REF` | a sandbox from the versioned `4top` template; the repository cloned *inside* it, branch `4top/NAME`; the agent started on the prompt in the sandbox's agent tmux |
| (nobody attached) | the agent works; the panel lists the sandbox as a section; Enter runs `4top attach KEY` there, like on any host |
| `4top cloud done NAME [--pr]` | commit, push `4top/NAME`, open the pull request; stop the agent and remove its credentials; snapshot, kept `keep_snapshot_days`; kill the sandbox |
| `4top cloud rm NAME` | a live task: refused while its work is not on its branch (`--discard` overrides); a finished one: its snapshot deleted |

A task's facts (repository, ref, branch, agent, prompt, directory, deadline) are the
sandbox's E2B metadata, written once at creation, so every device sees the same
task and 4top keeps no record of its own.

## What PR #15 did, and why it changed

- **Its own keeper for the agent.** A tmux session called `agent` per sandbox with
  `new-session -A`, and a loop that reattached whenever ssh exited 255. Opening
  session B in a sandbox showed agent A. The loop also defeated the panel's
  "Disconnected" handling, and it kept pinging a paused sandbox awake. Now a
  sandbox is a host like any other: the agent is resident in `tmux -L 4top-agents`
  (fourtop.resident), its session is named after the history key once the
  transcript exists, and opening a row runs `4top attach KEY`.
- **Could not start from a tablet.** It read the local git repository, local
  lockfiles and local credentials. Now the sandbox clones the repository itself.
- **No cost guardrails.** Keep-alive loops had no cap, checkpoints piled up, and a
  failure left orphaned sandboxes. Now there is a deadline, a daily budget,
  snapshot retention, and a kill on any failed start.
- **Secrets.** Tokens were baked into persistent checkpoints, the E2B project key
  sat inside every sandbox, and `~/.claude/settings.json` there was replaced. Now
  credentials are injected per start, none survives into a snapshot, and the E2B
  key stays on the device.

Forks, races, rewinds and carrying a local session up and home (PR #15's verbs)
are not part of this design. They depended on the machine-level checkpoints and the
in-sandbox E2B key that this design removes.

## Cost

E2B bills running seconds by size: **$0.000014 per vCPU-second and $0.0000045 per
GiB-second**, with storage free ([e2b.dev/pricing](https://e2b.dev/pricing), read
2026-09-30). The `4top` template is 2 vCPUs and 2 GiB, about **$0.133 an hour**.
Every cost 4top shows is labelled an estimate: E2B's invoice is the authority, and
plan fees and credits are not in it.

- **Lifetime cap.** A sandbox is created with `timeout = max_minutes` (default 60,
  the longest E2B's Hobby plan accepts; it answers "Timeout cannot be greater than 1
  hours" beyond that, which 4top reports in words) and `autoPause`. At the deadline it
  pauses, work kept. Waking never extends past the deadline (`e2b.wake`), except
  for a ten-minute grace so `done` or `rm` can bring the work home.
- **Never woken by traffic.** Sandboxes are created with `autoResume` off, and the
  panel asks E2B for a sandbox's state before polling it, so a refresh never
  resumes a paused one. Only an action taken by the person does (open, preview,
  search, `done`, `rm`).
- **Daily budget.** `new` sums today's spending and refuses a task whose worst case
  (its whole lifetime at the template's size) would pass `daily_budget_usd`,
  before creating anything.
- **Where spending comes from.** E2B's own lifecycle events for sandboxes tagged
  `fourtop=1`, so it covers every device and needs no local ledger. A pause or kill
  event carries the stretch that ended (`execution_time`, size, start). A
  checkpoint ends a stretch without recording it, so stretches are rebuilt from
  created/resumed/checkpointed/paused/killed timestamps. Measured: a task ran 90 s,
  was paused, resumed 25.6 s, snapshotted and killed (0.8 s). The kill event said
  848 ms; the rebuilt total was 117 s, $0.0043.
- **Snapshots** are named `fourtop-task-NAME-UNIXTIME` and deleted after
  `keep_snapshot_days` (default 7) by the next `new` or `done`.

## Credentials

Everything comes from the device that starts the task, travels on ssh's stdin as
`NAME=base64` lines (never in an argument list or a file), and goes to that task
only:

| | Where it lives in the sandbox |
| --- | --- |
| ssh public key | `~/.ssh/authorized_keys`, written through envd before ssh is up |
| GitHub token (`GH_TOKEN`, `GITHUB_TOKEN`, `gh auth token`) | git's memory, for the clone and the push only (`git -c http.…extraheader`); never on disk, never given to the agent |
| Claude: `CLAUDE_CODE_OAUTH_TOKEN` or `ANTHROPIC_API_KEY` | the agent's environment (the agent tmux server's), nowhere on disk |
| Codex: `OPENAI_API_KEY`, or `auth.json` | the environment, or `~/.codex/auth.json` until `done` |
| E2B key | never enters a sandbox |

`done` pushes, then kills the agent tmux server (every copy of the credentials in
memory goes with it) and deletes `auth.json`, and only then takes the snapshot.
Verified on a real sandbox: none of the Claude token, the GitHub token and the E2B
key appeared in any file under `$HOME` or `/tmp` of a running task.

Claude and Codex are told the task's directory is trusted and Claude's
bypass-mode warning is answered, by merging into the sandbox's `~/.claude.json`,
`~/.claude/settings.json` and `~/.codex/config.toml`, never replacing them. The
agent runs without per-command approval
(`--dangerously-skip-permissions`, `--dangerously-bypass-approvals-and-sandbox`):
the sandbox holds nothing but the task and a token scoped to its own sign-in.

## Measured on 2026-09-30

| | |
| --- | --- |
| `cloud new` to a working agent (clone of this repository, template of 2026-09-29 upgraded to 4top 0.2.0a8 during the start) | 17 s |
| the agent's tmux session renamed to its history key | seen 35 s after the start; its row listed `resident` |
| `cloud pause` / waking for an action | 1.8 s / 2.1 s |
| panel refreshes of a paused sandbox | did not wake it |
| `cloud done` (commit, push, snapshot, kill) | 11.5 s; branch on GitHub with the agent's file |
| in the panel (Now view) | the task listed as a source of its own, `○` resident, branch `4top/NAME`; the argv Enter runs attached to the same agent (one Claude process in the sandbox afterwards) |
| lifetime of 1 minute | paused itself at the deadline; `open` refused (exit 4); `rm` woke it with grace, found nothing unsaved, killed it |
| the whole verification | three tasks, about 5 sandbox-minutes: $0.007 by this estimate |

## The sandbox as a host

`[hosts.NAME] e2b = "SANDBOX_ID"` still pins any sandbox as a host. Transport is ssh
through the sandbox's websocket (`websocat` as ProxyCommand, `accept-new` for the
template's host key). The panel lists live tasks as sections of their own when the
configuration has a `[cloud]` section, from one listing call at start
(`cloud.sources`). Each section's `Snapshot.cloud` carries `state`, `cost_usd`,
`running_seconds`, `lifetime_left`, `deadline`, `usd_per_hour` and the task's
fields, for its header. `4top cloud ls --json` prints the same, one task per line,
then today's `budget`.

## Not done

- Tasks started after the panel opened appear on its next start; the panel does
  not ask E2B again while it runs.
- The header of a task's section does not show `Snapshot.cloud` yet: that is the
  panel's to render.
- Starting a task from the panel's dispatch dialog (`n`). The entry point is
  `cloud.new(config, cloud.Request(agent, prompt, repo, ref))`, offered as "cloud"
  among the machines, with the repository taken from the chosen project's origin.
- The template (`contrib/e2b`) was not rebuilt for this change; an older build is
  upgraded during the start (a few seconds more).
- A pi agent in the cloud. `4top --host H cloud …` runs the verbs on this device,
  not on H.
