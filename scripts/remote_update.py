#!/usr/bin/env python3
"""Bring a remote host's 4top up to a revision.

A host's only egress is often its own proxy, and that proxy is not always working.
This opens a reverse tunnel from this machine (whose egress does work) on a free
port, hands that port to the host's updater as `FOURTOP_PROXY`, runs the update, and
reports the revision on both sides. Without the tunnel the updater still tries the
host's own proxy first, so a healthy host needs nothing from here.

    scripts/remote_update.py ubuntu
    scripts/remote_update.py me@10.0.0.4 --rev main
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fourtop.config import Config  # noqa: E402
from fourtop.doctor import revision  # noqa: E402

CANDIDATE_PORTS = (7899, 7901, 7902, 7903, 7904, 7905)


def ssh(*args: str, capture=False, timeout=60):
    command = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", *args]
    return subprocess.run(command, capture_output=capture, text=True, timeout=timeout)


def mac_proxy_port() -> str | None:
    """This machine's own HTTP proxy, which is what makes the tunnel useful."""
    for name in ("https_proxy", "http_proxy", "all_proxy"):
        value = os.environ.get(name, "")
        if "://" in value and "127.0.0.1:" in value:
            return value.split(":", 2)[-1].rstrip("/")
    return "7897"  # the common local client port; harmless if nothing is there


def free_port(target: str) -> str | None:
    for port in CANDIDATE_PORTS:
        # `|| true` because grep exits 1 when the count is zero, which is the answer
        # we want here.
        probe = ssh(target, f"ss -ltn 2>/dev/null | grep -c ':{port} ' || true", capture=True)
        if probe.returncode == 0 and probe.stdout.strip() in ("0", ""):
            return str(port)
    return None


def open_tunnel(target: str, local_port: str, remote_port: str):
    """Forward the remote port to this machine's proxy and hold the process, so the
    tunnel lives exactly as long as the update and nothing is left behind."""
    process = subprocess.Popen(
        ["ssh", "-o", "BatchMode=yes", "-o", "ExitOnForwardFailure=yes", "-o", "ConnectTimeout=10",
         "-N", "-R", f"127.0.0.1:{remote_port}:127.0.0.1:{local_port}", target],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(20):
        time.sleep(0.25)
        if process.poll() is not None:
            return None
        check = ssh(target, f"curl -sL -x http://127.0.0.1:{remote_port} --max-time 8 "
                            f"-o /dev/null -w '%{{http_code}}' https://github.com/4ier/4top",
                    capture=True)
        if check.stdout.strip().startswith(("2", "3")):
            return process
    process.terminate()
    return None


def remote_doctor(target: str, command: str) -> dict:
    result = ssh(target, f"{command} doctor --json", capture=True)
    if result.returncode not in (0, 6):
        return {}
    try:
        return json.loads(result.stdout)
    except ValueError:
        return {}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("target", help="A [hosts.NAME] entry or an ssh destination")
    parser.add_argument("--rev", default="main", help="Branch, tag or commit to install")
    parser.add_argument("--no-tunnel", action="store_true", help="Use only the host's own egress")
    args = parser.parse_args()

    config = Config.load()
    host = config.resolve_host(args.target)
    target, command = host.ssh, host.command
    before = remote_doctor(target, command).get("revision")

    updater = str(Path(command).parent / "4top-update")
    result = ssh(target, f"{updater} {args.rev}", capture=True, timeout=600)
    tunnel = None
    if result.returncode != 0 and not args.no_tunnel:
        # The host's own egress failed. Lend it this machine's, on a free port, for
        # exactly as long as the update takes.
        port, proxy_port = free_port(target), mac_proxy_port()
        if port and proxy_port:
            tunnel = open_tunnel(target, proxy_port, port)
        if tunnel is None:
            sys.stdout.write(result.stdout)
            sys.stderr.write(result.stderr)
            print(f"\n{target} could not update: neither its own egress nor a tunnel from this "
                  f"machine worked. Check this machine's proxy, or pass FOURTOP_PROXY explicitly.",
                  file=sys.stderr)
            return result.returncode
        # Say why the host could not do it itself; "fell back to a tunnel" on its own
        # hides an unreliable proxy that is worth knowing about.
        reason = next((line.strip() for line in reversed(
            (result.stderr + result.stdout).splitlines()) if line.strip()), "unknown reason")
        print(f"{target}: its own egress failed ({reason}); lending this machine's proxy "
              f"(127.0.0.1:{proxy_port}) through a tunnel on port {port}", file=sys.stderr)
        try:
            result = ssh(target, f"FOURTOP_PROXY=http://127.0.0.1:{port} {updater} {args.rev}",
                         capture=True, timeout=600)
        finally:
            tunnel.terminate()
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    if result.returncode != 0:
        print(f"\n{target} could not update.", file=sys.stderr)
        return result.returncode

    after = remote_doctor(target, command).get("revision")
    print(f"{target}: {before or 'unknown'} -> {after or 'unknown'} (this build is {revision() or 'unknown'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
