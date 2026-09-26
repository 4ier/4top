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
        listening = ssh(target, f"ss -ltn | grep -c ':{port} '", capture=True)
        if listening.returncode == 0 and listening.stdout.strip() == "0":
            return str(port)
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

    port = None
    if not args.no_tunnel and (port := free_port(target)) and (proxy_port := mac_proxy_port()):
        opened = ssh("-f", "-N", "-o", "ExitOnForwardFailure=yes",
                     "-R", f"127.0.0.1:{port}:127.0.0.1:{proxy_port}", target)
        if opened.returncode != 0:
            print(f"warning: could not open a tunnel on {target}:{port}; using the host's own egress",
                  file=sys.stderr)
            port = None

    updater = str(Path(command).parent / "4top-update")
    prefix = f"FOURTOP_PROXY=http://127.0.0.1:{port} " if port else ""
    result = ssh(target, f"{prefix}{updater} {args.rev}", capture=True, timeout=600)
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    if result.returncode != 0:
        print(f"\n{target} could not update. Check its own egress, then retry without a tunnel "
              f"or pass FOURTOP_PROXY explicitly.", file=sys.stderr)
        return result.returncode

    after = remote_doctor(target, command).get("revision")
    print(f"{target}: {before or 'unknown'} -> {after or 'unknown'} (this build is {revision() or 'unknown'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
