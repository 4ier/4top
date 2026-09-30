"""Remote hosts: the same read-only CLI, run over SSH.

No daemon, no listening port and no credential store. The remote side is the
existing CLI, so anything this module can do is something a person could type.
"""
from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path

from session_ls.api import clean_text
from session_ls.storage import StorageError, private_dir

from . import e2b
from .config import Config, Host
from .errors import Unavailable
from .models import ROW_SCHEMA, Session, Snapshot
from .sync import SYNC_VERSION, SyncState, digest, fingerprint

# BatchMode never prompts, and the ServerAlive pair turns a dead link into an error in
# about 45 seconds instead of a hang. Rows are repetitive JSON fetched on every refresh,
# often over a phone's metered link, and compress several times over.
SSH_OPTIONS = ("-o", "BatchMode=yes", "-o", "ServerAliveInterval=15",
               "-o", "ServerAliveCountMax=3", "-o", "TCPKeepAlive=yes",
               "-o", "Compression=yes")
# Reusing one warm connection saves a handshake per refresh, but the socket has to fit.
SSH_MULTIPLEX = ("-o", "ControlMaster=auto", "-o", "ControlPersist=60")


# Unix sockets have a hard path limit (104 bytes on macOS, 108 on Android, NUL
# included, measured), and ssh names the socket it binds <ControlPath>.<40-character
# %C hash>.<16-character random suffix>. The directory therefore has 45 characters to
# work with. Termux runs under /data/data/com.termux/files/home, where the state
# directory alone is 54, and every remote call failed with
# "unix_listener: path ... too long for Unix domain socket".
CONTROL_PATH_LIMIT = 103
CONTROL_SOCKET_LENGTH = 1 + 40 + 1 + 16  # .<40-char hash>.<16-char suffix>


def control_dir(config: Config) -> str | None:
    """A writable private directory short enough for a connection socket, or None.

    The state directory is preferred, then the temporary directory: on Android the
    temporary directory is the one that fits, and ``/tmp`` is not writable at all
    (and on macOS it is a symlink this project refuses to write through). ``None``
    means run ssh without connection reuse, which is slower but always works.
    """
    temporary = config.environment.get("TMPDIR") or "/tmp"
    budget = CONTROL_PATH_LIMIT - CONTROL_SOCKET_LENGTH
    for candidate in (config.state_dir / "ssh", Path(temporary) / "4top"):
        if len(str(candidate)) > budget:
            continue
        try:
            return str(private_dir(candidate))
        except (OSError, StorageError):
            continue
    return None


def ssh_argv(config: Config, host: Host, args: list[str], *, tty: bool = False) -> list[str]:
    """Build one local argv. ssh hands the last element to a remote shell, so every
    remote argument is quoted for that shell rather than trusted as a literal."""
    remote = " ".join(shlex.quote(value) for value in (host.command, *args))
    return [*ssh_command(config, host, tty=tty), host.ssh, remote]


def ssh_command(config: Config, host: Host, *, tty: bool = False) -> list[str]:
    """ssh and its options for this host, without the destination: also what rsync
    is given as its remote shell."""
    options = [*SSH_OPTIONS, "-o", f"ConnectTimeout={max(1, int(host.timeout_seconds))}"]
    if host.e2b:
        # Every sandbox is a new host name with the template's host key, and the
        # first question about it must not be a prompt that BatchMode refuses.
        options += ["-o", f"ProxyCommand={e2b.proxy(config)}", "-o", "StrictHostKeyChecking=accept-new"]
    directory = control_dir(config)
    if tty:
        # An agent gets a connection of its own. Multiplexed over the panel's
        # ControlMaster, it died with the panel: closing the panel's pane hangs up
        # its process group, the master with it, and every agent riding on it.
        options += ["-o", "ControlMaster=no", "-o", "ControlPath=none", "-t"]
    elif directory:
        options = [*options, *SSH_MULTIPLEX, "-o", f"ControlPath={directory}/%C"]
    return ["ssh", *options]


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


def parse_payloads(host: Host, stdout: str) -> tuple[list[dict], dict | None]:
    """Validated row objects, and the sync trailer if the host sent one."""
    payloads, summary = [], None
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
        if "sync" in payload:
            summary = payload["sync"]
            if (not isinstance(summary, dict) or summary.get("version") != SYNC_VERSION
                    or not isinstance(summary.get("count"), int)
                    or not isinstance(summary.get("digest"), str)
                    or not isinstance(summary.get("resident", []), list)):
                raise Unavailable(f"{host.name}: unreadable sync summary; nothing was trusted")
            continue
        row_from(host, payload, number)  # validate now; a bad row poisons the whole answer
        payloads.append(payload)
    return payloads, summary


def row_from(host: Host, payload: dict, number: int = 0) -> Session:
    try:
        return Session(
            key=str(payload["key"]), agent=str(payload["agent"]), cwd=str(payload["cwd"]),
            title=str(payload["title"]), started=str(payload["started"]), last=str(payload["last"]),
            host=host.name, source=str(payload.get("source", "")),
            status=str(payload.get("status", "available")),
            problems=tuple(str(value) for value in payload.get("problems", ())),
            can_resume=bool(payload.get("can_resume", False)),
            issue=payload.get("issue") if isinstance(payload.get("issue"), str) else None,
            subagent=payload.get("subagent") is True,
            activity=str(payload.get("activity") or ""),
            last_request=str(payload.get("last_request") or ""),
            branch=str(payload.get("branch") or ""),
            resident=payload.get("resident") is True,
            attention=str(payload.get("attention") or ""),
            label=str(payload.get("label") or ""),
            muted=payload.get("muted") is True,
            repo=str(payload.get("repo") or ""),
            changes=payload.get("changes") if isinstance(payload.get("changes"), dict) else {},
            scripted=payload.get("scripted") is True)
    except (KeyError, TypeError, ValueError):
        raise Unavailable(f"{host.name}: row {number} is missing required fields") from None


def parse_rows(host: Host, stdout: str) -> list[Session]:
    payloads, _ = parse_payloads(host, stdout)
    return [row_from(host, payload) for payload in payloads]


def rows_of(host: Host, payloads) -> list[Session]:
    rows = [row_from(host, payload) for payload in payloads]
    rows.sort(key=lambda row: row.last, reverse=True)
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


def remote_snapshot(config: Config, host: Host, sync: SyncState | None = None) -> Snapshot:
    """The host's rows. With a sync state, only what changed since the last call
    travels, and the merged result is proven equal to the host's by digest."""
    if sync is None:
        rows, issues = _collect(config, host, ["list", "--json"])
        return Snapshot(rows, issues, scope=host.name)
    if sync.supported is False:
        code, out, err = run_remote(config, host, ["list", "--json"])
        if code not in (0, 6):
            raise ssh_failure(host, code, err)
        return _plain(host, sync, parse_payloads(host, out)[0], err, code)
    for attempt in ("incremental", "full"):
        if attempt == "full":
            sync.reset()
        args = ["list", "--json", "--sync"] + (["--since", sync.cursor] if sync.cursor else [])
        code, out, err = run_remote(config, host, args)
        if code == 2 and "--sync" in err:
            # A host older than incremental refresh; argparse names the flag it refused.
            sync.supported = False
            return remote_snapshot(config, host, sync)
        if code not in (0, 6):
            raise ssh_failure(host, code, err)
        payloads, summary = parse_payloads(host, out)
        if summary is None:
            # A host that answers without a summary ignored the flags and so sent
            # every row: use them as a full listing, and stop asking.
            sync.supported = False
            return _plain(host, sync, payloads, err, code)
        sync.supported = True
        merged = dict(sync.payloads) if sync.cursor else {}
        merged.update((str(payload["key"]), payload) for payload in payloads)
        if "resident" in summary:
            # An agent that started or exited changed a row the cursor cannot see.
            running = {str(key) for key in summary["resident"]}
            merged.update([(key, {**payload, "resident": key in running})
                           for key, payload in merged.items()
                           if payload.get("resident", False) != (key in running)])
        sync.attach = "resident" in summary
        prints = {key: fingerprint(payload) for key, payload in merged.items()}
        if len(merged) == summary["count"] and digest(prints) == summary["digest"]:
            sync.accept(merged, str(summary.get("cursor") or ""))
            return Snapshot(rows_of(host, merged.values()), _issues(host, err, code), scope=host.name)
        if not sync.cursor:
            break  # Even a full answer disagrees with its own summary.
    raise Unavailable(f"{host.name}: rows do not match the host's own summary; nothing was trusted")


def _plain(host: Host, sync: SyncState, payloads, err: str, code: int) -> Snapshot:
    """A full listing from a host without incremental refresh; still worth caching."""
    sync.accept({str(payload["key"]): payload for payload in payloads}, "")
    return Snapshot(rows_of(host, payloads), _issues(host, err, code), scope=host.name)


def remote_search(config: Config, host: Host, query: str, full: bool = False) -> Snapshot:
    args = ["search", "--json"] + (["--full"] if full else []) + [query]
    rows, issues = _collect(config, host, args)
    return Snapshot(rows, issues, scope=host.name)


def remote_preview(config: Config, host: Host, key: str, cursor: int = 0) -> str:
    code, out, err = run_remote(config, host, ["preview", key, "--cursor", str(cursor)])
    if code not in (0, 6):
        raise ssh_failure(host, code, err)
    return out


def remote_preview_tail(config: Config, host: Host, row: Session, before: int | None = None):
    """The latest messages on the host that owns the session. A host older than
    `preview --tail` gets the plain first page instead."""
    args = ["preview", row.key, "--tail", "--json"] + (["--before", str(before)] if before else [])
    code, out, err = run_remote(config, host, args)
    if code == 2 and "--tail" in err:
        text = remote_preview(config, host, row.key)
        return f"{row.agent} · {host.name} (read-only)", clean_text(text, multiline=True)[:2**18], None
    if code not in (0, 6):
        raise ssh_failure(host, code, err)
    try:
        page = json.loads(out)
        earlier = page.get("earlier_cursor")
        return (clean_text(str(page["label"])), clean_text(str(page["body"]), multiline=True)[:2**18],
                earlier if isinstance(earlier, int) else None)
    except (ValueError, KeyError, TypeError, AttributeError):
        raise Unavailable(f"{host.name}: preview did not return JSON") from None


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
