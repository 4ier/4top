# A phone or tablet as the panel

4top runs in [Termux](https://termux.dev) on Android. Browsing another machine is
plain ssh (see [remote hosts](remote-design.md)), so the phone needs only a route to
that machine. That route is the whole problem, and this page is one tested answer to
it. Nothing on it changes 4top, and none of it is needed on a LAN.

## Why the obvious setups fall short

- **LAN only.** Termux's sshd and a home LAN work, but not from outside the home.
- **The Tailscale app plus a proxy app.** Android runs one VPN (`VpnService`) at a
  time, so the Tailscale app and a proxy such as Clash exclude each other.
- **Tailscale behind a proxy.** When the proxy captures Tailscale's UDP, hole
  punching fails and traffic falls back to a DERP relay. From China the nearest
  ones measured 150–170 ms (San Francisco, Tokyo), which is the lag people notice.
- **A relay VPS.** An ssh jump host works with any proxy, but it adds a server and
  its round trip. It is still a sound fallback for networks that block UDP.

## The setup

Run `tailscaled` **inside Termux in userspace mode**. It claims no `VpnService`, so
the proxy app keeps the one Android allows, and it offers a SOCKS5 port that ssh
uses as its `ProxyCommand`. The files are in [`contrib/termux`](../contrib/termux).

1. **Build tailscaled for Android** on any machine with Go, then copy it into
   Termux as `$PREFIX/bin/tailscaled`:

   ```sh
   contrib/termux/build-tailscaled.sh 1.102.4 tailscaled
   ```

   The stock linux-arm64 binaries do not run in an Android app. Their CLI dies with
   `SIGSYS`, because seccomp blocks `faccessat2`. The daemon stops with
   `netlinkrib: permission denied`, because apps may not bind a netlink socket or
   dump links. The script builds for `GOOS=android` and adds an interface lister
   that uses what Android still allows: an unbound address dump, plus ioctls for
   names, flags and MTU. It also drops Taildrop, which on Android expects the
   Tailscale app to supply file access and panics without it.

2. **Run it as a service.** With `termux-services` installed, copy
   `tailscaled.run` to `$PREFIX/var/service/tailscaled/run`, and link
   `$PREFIX/share/termux-services/svlogger` as its `log/run`. Install the
   `tailscale` wrapper as `$PREFIX/bin/tailscale`. Then run
   `tailscale up --hostname=NAME` once and approve the login URL.

3. **Let tailscaled's traffic bypass the proxy.** In Clash Meta for Android, set
   Access control to "not allowed apps" and select Termux. If your proxy cannot
   bypass an app, route UDP from source port 41641 `DIRECT` instead.

4. **Route ssh through it.** Install `route` as `~/.ssh/route`. It uses tailscaled
   whenever that is running, which also finds the LAN path at home, and falls back
   to the LAN address when it is not. A machine that is not on the tailnet is
   reached through one that is:

   ```
   Host ubuntu
       User me
       HostKeyAlias ubuntu
       ProxyCommand ~/.ssh/route %p 192.168.0.104 100.77.239.100

   Host nas
       User root
       HostKeyAlias nas
       ProxyCommand ~/.ssh/route %p 192.168.0.103 via:ubuntu
   ```

   `HostKeyAlias` keeps one `known_hosts` entry per machine whichever path is
   taken. Point 4top at the aliases with `[hosts.ubuntu] ssh = "ubuntu"`.

5. **Keep Termux alive.** Android freezes background apps and limits their child
   processes. Exempt Termux from battery optimisation. From a computer with adb,
   `adb shell settings put global settings_enable_monitor_phantom_procs false`
   lifts the child-process limit. Without Termux:Boot, open Termux once after a
   reboot.

Check the path with `tailscale ping HOST`: `via [address]:port` is direct, and
`via DERP(...)` means the proxy or the network is still in the way.

## Measured

This was measured on 2026-09-28 on an iPlay80miniUltra tablet: Android 16,
Termux, 4top from main at `143a66e`, and tailscale 1.102.4 built as above. The
link was mobile data with Clash Meta 2.11.7 running and Termux bypassed. The home
machines were an Ubuntu host with 507 sessions, a Mac with 2773 sessions, and a NAS
that is not on the tailnet.

| | Result |
| --- | --- |
| Path to both tailnet hosts | direct over IPv6, `tailscale ping` 57–117 ms (44–54 ms with Clash off) |
| Same path at home | direct over LAN, 2–7 ms |
| `4top --host ubuntu list --json` | 507 rows, about 1.0 s |
| `4top --host mac list --json` | 2773 rows, 2.5–3.3 s |
| `4top --host nas list --json` via ubuntu | about 0.4 s |
| `doctor` on all three hosts | no issues |

The Mac's rows took 13–15 s, and sometimes hit the 15 s timeout, before 4top capped
the title in JSON rows and asked ssh to compress. Both the phone and the hosts need
a 4top that includes that change.

Not covered: a network that blocks UDP (use the relay fallback above), iOS, and
resuming a session over this path (only browsing and `doctor` were measured).
