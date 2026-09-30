# The 4top sandbox template

`Dockerfile` and `start.sh` build the E2B template that cloud tasks start from:
sshd behind a websocket on port 8081, websocat, tmux, git, Claude Code, Codex and
4top. Build it once per E2B project, and again after changing it (and bump
`/etc/4top-template`):

```sh
cd contrib/e2b
e2b template create 4top -d Dockerfile -c /usr/local/bin/4top-sandbox-start \
    --ready-cmd 'bash -c "</dev/tcp/127.0.0.1/8081"' --cpu-count 2 --memory-mb 2048
```

A task costs its size times its running time: 2 vCPUs and 2 GiB is about $0.13 an
hour at E2B's prices of 2026-09-30. `[cloud] template` names another build, for
example a larger one or `4top:v3`. An older build still works: a task installs a
recent enough 4top into it when it starts.

Nothing personal is in the image. Keys and tokens go to each task when it starts;
see [docs/e2b-design.md](../../docs/e2b-design.md).
