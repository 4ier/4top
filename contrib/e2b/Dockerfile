# The 4top sandbox template: sshd reached through a websocket, the agents, and 4top
# itself, so a sandbox answers the same CLI as any other host.
FROM e2bdev/base:latest

USER root
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        openssh-server tmux git curl ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /run/sshd \
    && echo 'PasswordAuthentication no' > /etc/ssh/sshd_config.d/4top.conf \
    && echo 'KbdInteractiveAuthentication no' >> /etc/ssh/sshd_config.d/4top.conf \
    && echo 'PermitRootLogin no' >> /etc/ssh/sshd_config.d/4top.conf \
    && echo 'PermitUserEnvironment yes' >> /etc/ssh/sshd_config.d/4top.conf \
    && curl -fsSL -o /usr/local/bin/websocat \
        https://github.com/vi/websocat/releases/download/v1.14.1/websocat.x86_64-unknown-linux-musl \
    && chmod a+x /usr/local/bin/websocat \
    && npm install -g @anthropic-ai/claude-code @openai/codex \
    && pip install --no-cache-dir --pre 4top

COPY start.sh /usr/local/bin/4top-sandbox-start
USER user
