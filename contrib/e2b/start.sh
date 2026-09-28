#!/bin/sh
# Start command of the 4top template: sshd, and a websocket on port 8081 that carries
# ssh. The client side is `websocat` as ssh's ProxyCommand.
sudo /usr/sbin/sshd
exec sudo websocat --binary ws-l:0.0.0.0:8081 tcp:127.0.0.1:22
