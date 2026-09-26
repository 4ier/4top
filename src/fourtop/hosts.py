"""Remote hosts: the same read-only CLI, run over SSH.

No daemon, no listening port and no credential store. The remote side is the
existing CLI, so anything this module can do is something a person could type.
"""
from __future__ import annotations

import json
import shlex
import subprocess

from session_ls.api import clean_text
from session_ls.storage import private_dir

from .config import Config, Host
from .errors import Unavailable
from .models import ROW_SCHEMA, Session, Snapshot

# BatchMode never prompts; ControlMaster reuses one warm connection (a handshake per
# refresh is wasteful on a good link and painful on a bad one); the ServerAlive pair
# turns a dead link into an error in about 45 seconds instead of a hang.
SSH_OPTIONS = ("-o", "BatchMode=yes", "-o", "ControlMaster=auto", "-o", "ControlPersist=60",
               "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3",
               "-o", "TCPKeepAlive=yes")


def ssh_argv(config: Config, host: Host, args: list[str], *, tty: bool = False) -> list[str]:
    """Build one local argv. ssh hands the last element to a remote shell, so every
    remote argument is quoted for that shell rather than trusted as a literal."""
    options = [*SSH_OPTIONS, "-o", f"ConnectTimeout={max(1, int(host.timeout_seconds))}",
               "-o", f"ControlPath={private_dir(config.state_dir / 'ssh')}/%C"]
    if tty:
        options.append("-t")
    remote = " ".join(shlex.quote(value) for value in (host.command, *args))
    return ["ssh", *options, host.ssh, remote]


def run_remote(config: Config, host: Host, args: list[str]) -> tuple[int, str, str]:
    argv = ssh_argv(config, host, args)
    try:
        result = subprocess.run(argv, capture_output=True, text=True, errors="replace",
                                timeout=host.timeout_seconds, env=config.environment,
                                stdin=subprocess.DEVNULL)
    except FileNotFoundError:
        raise Unavailable("ssh is not installed on this machine") from None
    except subprocess.TimeoutExpired:
        raise Unavailable(f"{host.name}: no answer within {host.timeout_seconds:g}s") from None
    except OSError as exc:
        raise Unavailable(f"{host.name}: ssh failed ({type(exc).__name__})") from None
    return result.returncode, result.stdout, result.stderr


def ssh_failure(host: Host, code: int, stderr: str) -> Unavailable:
    detail = " ".join(clean_text(stderr).split())[:200]
    return Unavailable(f"{host.name}: ssh exit {code}" + (f" · {detail}" if detail else ""))


def parse_rows(host: Host, stdout: str) -> list[Session]:
    rows = []
    for number, line in enumerate(stdout.splitlines(), 1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except ValueError:
            raise Unavailable(f"{host.name}: row {number} is not JSON; nothing was trusted") from None
        if not isinstance(payload, dict):
            raise Unavailable(f"{host.name}: row {number} is not an object; nothing was trusted")
        version = payload.get("schema_version")
        if version != ROW_SCHEMA:
            raise Unavailable(f"{host.name} speaks row schema {version!r}; this build speaks "
                              f"{ROW_SCHEMA}. Update both sides before trusting this view.")
        try:
            rows.append(Session(
                key=str(payload["key"]), agent=str(payload["agent"]), cwd=str(payload["cwd"]),
                title=str(payload["title"]), started=str(payload["started"]), last=str(payload["last"]),
                host=host.name, source=str(payload.get("source", "")),
                status=str(payload.get("status", "available")),
                problems=tuple(str(value) for value in payload.get("problems", ())),
                can_resume=bool(payload.get("can_resume", False)),
                issue=payload.get("issue") if isinstance(payload.get("issue"), str) else None))
        except (KeyError, TypeError, ValueError):
            raise Unavailable(f"{host.name}: row {number} is missing required fields") from None
    return rows


DIAGNOSTIC_PREFIX = "4top: "


def _issues(host: Host, stderr: str, code: int) -> list[str]:
    """Only the remote CLI's own diagnostics are issues.

    A login banner, a shell warning or an ssh notice on stderr describes the host,
    not the query, so treating every line as a problem would report a healthy
    machine as failing.
    """
    issues = []
    for line in stderr.splitlines():
        text = clean_text(line).strip()
        if text.startswith(DIAGNOSTIC_PREFIX):
            issues.append(f"{host.name}: {text[len(DIAGNOSTIC_PREFIX):]}")
    if code == 6 and not issues:
        issues.append(f"{host.name}: the remote reported a partial result")
    return issues


def _collect(config: Config, host: Host, args: list[str]) -> tuple[list[Session], list[str]]:
    code, out, err = run_remote(config, host, args)
    if code not in (0, 6):
        raise ssh_failure(host, code, err)
    return parse_rows(host, out), _issues(host, err, code)


def remote_snapshot(config: Config, host: Host) -> Snapshot:
    rows, issues = _collect(config, host, ["list", "--json"])
    return Snapshot(rows, issues, scope=host.name)


def remote_search(config: Config, host: Host, query: str, full: bool = False) -> Snapshot:
    args = ["search", "--json"] + (["--full"] if full else []) + [query]
    rows, issues = _collect(config, host, args)
    return Snapshot(rows, issues, scope=host.name)


def remote_preview(config: Config, host: Host, key: str, cursor: int = 0) -> str:
    code, out, err = run_remote(config, host, ["preview", key, "--cursor", str(cursor)])
    if code not in (0, 6):
        raise ssh_failure(host, code, err)
    return out


def remote_check(config: Config, host: Host, key: str) -> dict:
    """Ask the host that owns the session whether a resume is possible there."""
    code, out, err = run_remote(config, host, ["check", key, "--json"])
    if code == 2:
        # argparse rejects an unknown subcommand with 2. Preflighting is an
        # improvement, not a requirement, so a remote older than `check` is stated
        # rather than blocking the action with a wall of usage text.
        return {"key": key, "agent": None, "host": host.name, "native_id": None, "cwd": None,
                "cwd_quality": None, "executable": None, "cwd_missing": False,
                "resumable": None, "reason": f"{host.name} runs a 4top without `check`",
                "status": None, "problems": []}
    if code not in (0, 3):
        raise ssh_failure(host, code, err)
    try:
        report = json.loads(out)
    except ValueError:
        raise Unavailable(f"{host.name}: check did not return JSON") from None
    if not isinstance(report, dict):
        raise Unavailable(f"{host.name}: check returned {type(report).__name__}")
    report["host"] = host.name
    return report


def remote_doctor(config: Config, host: Host) -> dict:
    code, out, err = run_remote(config, host, ["doctor", "--json"])
    if code not in (0, 6):
        raise ssh_failure(host, code, err)
    try:
        report = json.loads(out)
    except ValueError:
        raise Unavailable(f"{host.name}: doctor did not return JSON") from None
    if not isinstance(report, dict):
        raise Unavailable(f"{host.name}: doctor returned {type(report).__name__}")
    report["host"] = host.name
    report["issues"] = list(report.get("issues", [])) + _issues(host, err, code)
    return report
