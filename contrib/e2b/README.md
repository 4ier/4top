# The 4top sandbox template

`Dockerfile` and `start.sh` build the E2B template the cloud commands start from:
sshd behind a websocket on port 8081, websocat, tmux, git, rsync, uv, Claude Code,
Codex and 4top. Build it once per E2B project, and again after changing it:

```sh
cd contrib/e2b
e2b template create 4top -d Dockerfile -c /usr/local/bin/4top-sandbox-start \
    --ready-cmd 'bash -c "</dev/tcp/127.0.0.1/8081"' --memory-mb 2048
```

Nothing personal is in the image. Keys, tokens and the project are written into
each sandbox by `4top cloud`; see [docs/e2b-design.md](../../docs/e2b-design.md).
