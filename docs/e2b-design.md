# E2B sandboxes as hosts (design, not implemented)

Status: proposal on `feat/e2b-sandbox`. Nothing here ships yet.

## The idea in one line

An [E2B](https://e2b.dev) sandbox is a disposable Linux machine that can pause with
its memory intact. 4top already knows how to use another machine: run the same CLI
over ssh. So a sandbox becomes **one more host**, reached over ssh, and E2B only adds
what ssh cannot do: create, pause, wake, fork and throw away the machine.

The transport and every contract in [remote-design.md](remote-design.md) stay as they
are: `list --json --sync`, `check`, `preview`, cached rows, opaque keys, `ssh -t` for
agents, the tmux layout. No daemon, no new protocol.

## What it gives

| Pain today | With a sandbox |
| --- | --- |
| yolo agents run on a real machine; two agents in one directory share it | each task gets its own machine; yolo is harmless there |
| closing the laptop ends local agents | the sandbox keeps running, and pauses itself when idle |
| a resumed session is a new process; shells, servers and memory are gone | pause keeps the **process** too; waking takes about a second |
| trying two approaches means doing it twice by hand | snapshot once, fork N sandboxes from it |
| the tablet reaches a dev server only over the tailnet | every port has a public URL, `https://PORT-ID.e2b.app` |

## Facts measured on 2026-09-28 (e2b CLI 2.18.0)

- `e2b sbx exec ID -- cmd` runs a command, keeps stdout and stderr apart and returns
  the exit code. About 3.4 s per call from here: too slow for a 15 s poll on its own.
- `exec` **wakes a paused sandbox**; a file written before `pause` was read back.
  `pause` took 3.3 s for a 512 MB sandbox.
- `exec` has no terminal, and `connect` opens a shell but takes no command, so neither
  can start an interactive agent.
- Paused sandboxes are kept until killed. Continuous runtime is capped (24 h Pro,
  1 h Hobby); a pause resets the clock. `--lifecycle.ontimeout pause
  --lifecycle.autoresume` makes idleness pause and traffic wake.
- A team's API key sees **every** sandbox of the team, including production ones that
  are not ours. 4top must only ever look at, pause or kill sandboxes it tagged.

## Transport: ssh through the sandbox's websocket port

E2B documents ssh access: the template runs `sshd` plus
`websocat -b --exit-on-eof ws-l:0.0.0.0:8081 tcp:127.0.0.1:22`, and the client
connects with

```sh
ssh -o 'ProxyCommand=websocat --binary -B 65536 - wss://8081-%h.e2b.app' user@SANDBOX_ID
```

This is chosen over the SDK's terminal API because it changes nothing downstream:
`ControlMaster` removes the 3.4 s per call after the first, `ssh -t` gives agents a
real terminal, and tests for ssh hosts cover sandboxes too. The cost is `websocat` on
each client (Homebrew and Termux both package it) and a template with `sshd`.

The only change to `hosts.ssh_argv` is one option for a sandbox host:
`-o ProxyCommand=websocat ... wss://8081-%h.e2b.app`, with the sandbox ID as the
ssh destination.

## Waking is the one new rule

Traffic wakes a paused sandbox, so polling it every 15 s would keep it awake, and
billed, forever. Before a refresh tick the panel makes one API call for all tagged
sandboxes (`e2b sbx list -s running,paused -m fourtop=1 -f json`, or the same REST
call without the node CLI) and polls only running ones. A paused sandbox shows its
cached rows with the state `paused`, like `cached` or `unreachable` today. Enter on
one of its sessions wakes it, because that is the moment the user asked for it.

## Configuration

```toml
[e2b]
api_key_env = "E2B_API_KEY"   # which variable holds the key; several teams, several keys
template = "4top"             # built from contrib/e2b
idle_minutes = 10             # timeout before an idle sandbox pauses

[hosts.scratch]
e2b = "SANDBOX_ID"            # an existing sandbox, pinned by ID
```

Pinned sandboxes are optional. Sandboxes 4top created are found through their
`fourtop=1` metadata and each gets a section named by its `fourtop_name` metadata, so
creating one never edits the configuration file.

## Template (`contrib/e2b/`)

Base image plus `openssh-server`, `websocat`, `tmux`, `git`, Node, Claude Code, Codex
and 4top itself, so the remote side of the contract is present. The user's public
key goes into `authorized_keys` at creation, not into the image.

Agent credentials are the user's own, passed at creation: `CLAUDE_CODE_OAUTH_TOKEN`
from the file the Mac already uses over ssh, and `~/.codex/auth.json` written into
the sandbox. `[agents.NAME] args` from the user's config apply there as everywhere.

## Phases

**1. A sandbox is a host.** `[hosts.NAME] e2b = ID`, the ProxyCommand, the paused
rule, `doctor` reporting `websocat`, the key and the sandbox state. Template in
`contrib/e2b`. Everything else is existing code. Acceptance: list, preview, resume
and new against a real sandbox from the Mac and the tablet; a paused sandbox stays
paused while the panel is open.

**2. Create and dispose from the panel.** A new `sandbox new` subcommand (repo URL and name as options)
creates from the template with the tags and auto-pause, clones the repo, and the
section appears. `n` on a sandbox section starts an agent there as today. Keys on a
sandbox section: pause and kill (kill is the one action that asks, because it is not
reversible).

**3. Beyond a machine.**
- *Move a session to the cloud*: copy the transcript and a `git bundle` of its
  repository to the same path in a new sandbox, then resume it there. The exact
  native resume already works from a transcript alone, which is the premise of 4top.
- *Fork*: `e2b sbx snapshot create` on a sandbox, then N sandboxes from the snapshot,
  each with its own agent and the same history: best-of-N without shared files.
- *Ports*: the details view lists listening ports as `https://PORT-ID.e2b.app`
  links, so the tablet opens what the agent is serving.

## Out of scope

Running agents through the E2B SDK instead of their own CLIs, a hosted 4top, billing
dashboards, and sandboxes 4top did not tag.
