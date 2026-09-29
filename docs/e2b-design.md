# E2B sandboxes: sessions that own a machine

Status: implemented on `feat/e2b-sandbox` (`fourtop.cloud`, `fourtop.e2b`); every
command below was run against real sandboxes on 2026-09-29.

## The idea

A native session is a transcript, and 4top's premise is that the transcript is the
durable part while the process is disposable. An [E2B](https://e2b.dev) sandbox
moves that line: the **whole machine** becomes durable too. It can be copied,
wound back, parked and carried, with its files, installed dependencies and running
processes. So in the cloud a session is *transcript + its own machine*, and 4top
gets verbs that no ssh host can offer.

All of them are one primitive, the **checkpoint** (an E2B snapshot of a running
sandbox, memory included):

| Verb | Is |
| --- | --- |
| instant project machine | a checkpoint of the project with dependencies installed; each new task starts from it |
| fork, race | N sandboxes from one checkpoint |
| rewind | one sandbox from an earlier checkpoint, taken after every agent turn |
| to the cloud, back home | a sandbox from the project checkpoint, plus this machine's work and transcript; and the reverse |

## Measured on 2026-09-28/29

| | |
| --- | --- |
| checkpoint of a running sandbox | 2.4 s |
| new sandbox from a checkpoint | 1.6 s |
| `fork` of a running sandbox into two | 1.3 s; running processes carried on in both, files then diverged, transcripts came along |
| wake a paused sandbox | about 1 s |
| ssh call over the websocket, connection reused | 0.4 s (first 5 s) |
| `cloud new`, project checkpoint ready | about 10 s to a running agent; building the checkpoint once, about 70 s for this repository |
| `cloud fork NAME -n 2` | 15 s; the running Claude carried on in both |
| `cloud race` with two agents | 36 s until both were working |
| `cloud up KEY` / `cloud home NAME` | 40 s / 14 s, transcript and files both ways |

- A sandbox's timeout counts from creation or resume; traffic does not extend it.
- A paused sandbox wakes on traffic, so it must never be polled.
- Forks inherit metadata and there is no call to change it, so forks are made as
  *checkpoint, then create with metadata*, which also names them.
- Inside, `/run/e2b/.E2B_SANDBOX_ID` names the sandbox, forks included.
- A snapshot freezes the machine for a moment and may reset its connections, so an
  attached client reattaches (ssh exits 255 only when the link is lost), and the
  checkpoint hook logs its turn before asking for the snapshot and never waits for
  the answer: a copy made from that instant must not wait on a dead connection.
- E2B refuses to delete a checkpoint while a sandbox made from it runs; the last
  copy's `rm` deletes it.
- Files can be written over HTTP (envd, port 49983) with the `envdAccessToken` of a
  `secure` sandbox, which is how the first ssh key gets in without the node CLI.
- An API key sees every sandbox of its project; 4top touches only those tagged
  `fourtop=1`.

## Model

- **A cloud session lives at the same path as at home.** The project is placed at its
  local absolute path, so Claude's per-directory transcript folder and every `cwd`
  in a transcript mean the same thing on both sides. Moving a session is then a file
  copy, not a rewrite.
- **Sandboxes are found, not configured.** Every sandbox tagged `fourtop=1` becomes a
  section named by its `fourtop_name`; one listing call per refresh gives every
  sandbox's state, so paused ones are shown from cache and never woken.
- **Transport is ssh** through the sandbox's websocket, as for `[hosts] e2b`.
- **Files travel by git's account**, as tar over ssh: tracked and untracked files and
  the git directory; what git ignores is never sent, overwritten or deleted. A
  worktree's repository goes to its own absolute path, so the worktree's pointer
  holds. rsync's `.gitignore` filter came first, and macOS's openrsync ignores it:
  `--delete` removed ignored files, a local `.venv` among them.
- **The agent lives in the sandbox's tmux** (session `agent`, no prefix, no status
  line); this machine only attaches. A fork or a rewind carries the running agent,
  and the agent keeps working with nobody attached.
- **The sandbox keeps itself up** while an agent writes a transcript, and pauses
  ten minutes after the last write: a timeout counted from resume would otherwise
  pause an agent mid-turn.
- **The user's credentials go in once**, into the project checkpoint: ssh key,
  `CLAUDE_CODE_OAUTH_TOKEN`, Codex's `auth.json`, the `[agents]` entries.

## Commands

```sh
4top cloud new claude              # this project, on a fresh machine, in seconds
4top cloud fork NAME -n 3          # three copies of that machine and session, as they are now
4top cloud race "fix the flaky test" --agents claude,codex
4top cloud take NAME               # that sandbox's work as local branch 4top/NAME
4top cloud rewind NAME             # its checkpoints; with a turn, a new sandbox from it
4top cloud up KEY                  # carry a local session to the cloud and resume it there
4top cloud home NAME               # bring it back: work, transcript, resume here
4top cloud ls
4top cloud rm NAME
```

### 1. Instant project machine (`new`)

The project checkpoint is named after the project and a hash of its dependency
files (`uv.lock`, `package-lock.json`, `pnpm-lock.yaml`, `requirements.txt`, …), so it
is rebuilt only when dependencies change. Building it: sandbox from the `4top`
template, credentials in, the working tree synced to the same path, the detected
install run, checkpoint, builder killed. `new` then creates from the checkpoint,
syncs the current working tree (by git's account, so the installed dependencies
stay) and starts the agent.
Each task is a clean machine: yolo is harmless there, and parallel agents never
share a directory.

### 2. Fork and race

`fork` checkpoints a sandbox and creates N from it, named `NAME-1` … `NAME-N`. The
agent that was running continues in each, from the same instant. Verified: two
forks of a session diverged (`three` in one, `THREE` in the other), and `take` of
the second arrived as a local branch.

`race` syncs the project once, checkpoints, and starts one sandbox per agent, each
agent started on the prompt in the sandbox's tmux, interactive, so the winner can
simply be attached and continued. Verified with Claude and Codex. The panel shows them side by side; `take`
brings the chosen one's work home as branch `4top/NAME` (commit in the sandbox, git
bundle over ssh, fetch here), and `rm` removes the others.

### 3. Rewind

After every agent turn the sandbox checkpoints itself (a Claude `Stop` hook and a
Codex `notify` hook call the snapshot API with the sandbox's own ID). `rewind NAME`
lists them; `rewind NAME TURN` creates a sandbox from that checkpoint, where files,
dependencies, services and the transcript are as they were right after that turn.
Claude's own `/rewind` restores files; this restores the machine. Verified: after
two turns, `rewind demo 1` gave a machine whose file held only the first turn's line
and whose Claude answered that it had received one message.

### 4. To the cloud and back home

`up KEY` builds or reuses the project checkpoint, syncs the working tree, copies
the session's transcript to the same place in the sandbox and resumes it there.
The laptop can close. `home NAME` does the reverse: if the local tree is unchanged
since `up`, it is synced back; otherwise the work arrives as branch `4top/NAME`. The
transcript is copied back (a newer copy here wins) and the session resumes here.
Verified: a local Claude session went up, answered in the sandbox from its local
memory and wrote a file there; `home` brought the file and the transcript back, and
the local resume knew it had been running on Linux.

## Trade-offs

- Rewind puts the project's E2B key inside the sandbox, where a yolo agent could use
  it on the project's other sandboxes. A project used only by 4top avoids that.
- Checkpoints are kept by E2B until deleted; `rm` deletes a sandbox's checkpoints
  with it.
- The template carries sshd, websocat, rsync, tmux, git, uv, Claude Code, Codex and
  4top; `contrib/e2b/README.md` has the build command.

## Out of scope

Running agents through the E2B SDK instead of their own CLIs, a hosted 4top,
billing, and sandboxes 4top did not tag.
