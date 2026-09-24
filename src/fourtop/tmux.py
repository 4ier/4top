"""Exact-target tmux operations; all native agent arguments bypass tmux parsing."""
from __future__ import annotations

import contextlib
import hashlib
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import replace
from pathlib import Path

from session_ls.api import is_uuid, utc_now

from .config import Config
from .errors import Conflict, Dependency, FourtopError, Unavailable
from .models import LaunchPlan, Pane, TmuxSnapshot
from .state import StateStore
from .transport import read_frame, send_frame, verify_peer

PANE_RE = re.compile(r"%\d+\Z")
TTY_RE = re.compile(r"/dev/[a-zA-Z0-9/_-]+\Z")
FORMAT = "\t".join("#{" + field + "}" for field in (
    "session_id", "window_id", "pane_id", "pane_pid", "pane_dead", "pane_dead_status",
    "session_attached", "@4top_run_id", "@4top_host_id", "@4top_agent", "@4top_ready", "pid", "pane_dead_signal"))


def process_identity(pid: int) -> str | None:
    if pid <= 0:
        return None
    proc = Path(f"/proc/{pid}/stat")
    try:
        if sys.platform.startswith("linux"):
            fields = proc.read_text().rpartition(")")[2].split()
            if fields[0] in ("Z", "X"):
                return None
            boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
            return f"linux:{boot}:{pid}:{fields[19]}"
        result = subprocess.run(["ps", "-p", str(pid), "-o", "state=", "-o", "lstart=", "-o", "uid="],
                                capture_output=True, text=True, timeout=2,
                                env={**os.environ, "LC_ALL": "C"})
        if result.returncode == 0 and result.stdout.strip():
            state, _, identity = result.stdout.strip().partition(" ")
            if "Z" in state or "X" in state:
                return None
            return f"ps:{pid}:{identity.strip()}"
    except (OSError, IndexError, subprocess.TimeoutExpired):
        pass
    return None



def linux_zombie_exit(pid: int, expected_identity: str | None,
                      proc_root: Path = Path("/proc")) -> tuple[int | None, str | None] | None:
    """Read a proven zombie's exit, without sending signals or reaping children.

    The caller must already verify tmux server/pane ownership. Linux /proc stat
    fields 3/22/52 carry state/start time/wait status. Field 52 can be zeroed by
    ptrace restrictions, so zero proves neither success nor failure here.
    """
    if type(pid) is not int or pid <= 0 or not expected_identity:
        return None
    try:
        with (proc_root / str(pid) / "stat").open() as stream:
            if os.fstat(stream.fileno()).st_uid != os.getuid():
                return None
            text = stream.read(16384)
        prefix, separator, suffix = text.rpartition(")")
        fields = suffix.split()
        if not separator or len(fields) < 50 or fields[0] != "Z":
            return None
        if int(prefix.partition("(")[0].strip()) != pid or not fields[19].isdigit():
            return None
        boot = (proc_root / "sys/kernel/random/boot_id").read_text().strip()
        if f"linux:{boot}:{pid}:{fields[19]}" != expected_identity:
            return None
        status = int(fields[49])
        if not 0 <= status <= 65535:
            return None
        if status == 0:
            return None, None  # Confirmed exit, but possibly permission-masked status.
        if os.WIFEXITED(status):
            return os.WEXITSTATUS(status), None
        if os.WIFSIGNALED(status):
            return None, str(os.WTERMSIG(status))
    except (OSError, ValueError, IndexError):
        pass
    return None


def default_socket(environment: dict[str, str]) -> str:
    inherited = environment.get("TMUX", "")
    if inherited:
        parts = inherited.rsplit(",", 2)
        if len(parts) == 3 and parts[1].isdigit() and Path(parts[0]).is_absolute():
            return str(Path(parts[0]).resolve())
    base = Path(environment.get("TMUX_TMPDIR", "/tmp"))
    return str((base / f"tmux-{os.getuid()}" / "default").resolve())


def safe_tmux_arg(value: str) -> str:
    # tmux reparses a trailing semicolon even when subprocess uses an argv array.
    return value[:-1] + r"\;" if value.endswith(";") else value


class Tmux:
    def __init__(self, config: Config, socket_path: str | None = None):
        self.config = config
        self.socket = str(Path(socket_path or config.socket or default_socket(config.environment)).resolve())
        self.executable = shutil.which("tmux", path=config.environment.get("PATH", os.defpath))
        if self.executable:
            self.executable = os.path.abspath(self.executable)
        self.environment = {**config.environment, "LC_ALL": "C"}

    def _argv(self, args: list[str], no_create: bool = True) -> list[str]:
        if not self.executable:
            raise Dependency("tmux is not installed. History browsing is still available.")
        prefix = [self.executable, "-u"] + (["-N"] if no_create else []) + ["-S", self.socket]
        return [safe_tmux_arg(value) for value in prefix + args]

    def command(self, args: list[str], check=True, no_create=True) -> subprocess.CompletedProcess:
        try:
            result = subprocess.run(self._argv(args, no_create), capture_output=True, text=True,
                                    errors="replace", env=self.environment,
                                    timeout=self.config.control_seconds, cwd="/")
        except subprocess.TimeoutExpired:
            raise Unavailable("tmux control query timed out; runtime state is uncertain") from None
        except OSError as exc:
            raise Unavailable(f"tmux control failed ({type(exc).__name__})") from None
        if check and result.returncode:
            raise Unavailable(f"tmux {args[0]} failed; use doctor to inspect the selected socket")
        return result

    def server_identity(self, pid: int) -> str:
        st = os.stat(self.socket)
        if not stat.S_ISSOCK(st.st_mode) or st.st_uid != os.getuid():
            raise Unavailable("Selected tmux socket is not owned by the current user")
        process = process_identity(pid)
        if process is None:
            raise Unavailable("tmux server process identity is unavailable")
        # tmux chmods its socket when clients attach/detach. ctime is NOT identity.
        return hashlib.sha256(f"{process}:{st.st_dev}:{st.st_ino}".encode()).hexdigest()

    def snapshot(self) -> TmuxSnapshot:
        observed = utc_now()
        if not self.executable:
            return TmuxSnapshot((), "", observed, False, "tmux is not installed")
        try:
            response = self.command(["list-panes", "-a", "-F", FORMAT], check=False)
            if response.returncode:
                message = response.stderr.lower()
                if any(value in message for value in ("no server running", "no such file or directory",
                                                       "connection refused")):
                    return TmuxSnapshot((), "", observed)
                return TmuxSnapshot((), "", observed, False, "tmux source is unavailable")
            panes = []
            server_pid = None
            for line in response.stdout.splitlines():
                fields = line.split("\t")
                if len(fields) != 13:
                    raise ValueError("Malformed tmux observation")
                (session, window, pane, pid, dead, status_code, clients, run_id,
                 host, agent, ready, server, exit_signal) = fields
                if not re.fullmatch(r"\$\d+", session) or not re.fullmatch(r"@\d+", window) or not PANE_RE.fullmatch(pane):
                    raise ValueError("Invalid tmux target")
                server_pid = int(server)
                panes.append(Pane(session, window, pane, int(pid), dead == "1",
                                  int(status_code) if status_code.isdigit() else None,
                                  int(clients), run_id, host, agent, ready == "1", exit_signal or None))
            identity = self.server_identity(server_pid) if server_pid else ""
            return TmuxSnapshot(tuple(panes), identity, observed)
        except (FourtopError, OSError, ValueError) as exc:
            return TmuxSnapshot((), "", observed, False, f"tmux observation failed ({type(exc).__name__})")

    def observe(self, record: dict, snapshot: TmuxSnapshot | None = None) -> tuple[str, Pane | None, str | None]:
        snapshot = snapshot or self.snapshot()
        if not snapshot.available:
            return "UNKNOWN", None, snapshot.issue
        target = record.get("tmux")
        if not target:
            if record.get("launch_phase") == "failed":
                return "EXIT", None, "Launch failed before granting permission to execute"
            return "START", None, "Startup has not recorded a terminal; inspect before retrying"
        if target.get("server") != snapshot.server:
            if process_identity(record.get("pid") or 0) == record.get("process_identity") and record.get("pid"):
                return "UNKNOWN", None, "Recorded process still exists, but its terminal cannot be verified"
            return "MISSING", None, "Original tmux server is no longer present"
        matches = [p for p in snapshot.panes if p.pane == target.get("pane")
                   and p.run_id == record["run_id"] and p.host_id == record["host_id"]]
        if len(matches) != 1:
            if any(p.pane == target.get("pane") for p in snapshot.panes):
                return "UNKNOWN", None, "Pane ownership marker changed; refusing to guess its identity"
            if record.get("pid") and process_identity(record["pid"]) == record.get("process_identity"):
                return "UNKNOWN", None, "Recorded process still exists without its original terminal"
            return "MISSING", None, "Original pane is no longer present"
        pane = matches[0]
        if pane.pid != record.get("pid"):
            return "UNKNOWN", pane, "Pane was respawned; it no longer contains the recorded process"
        if pane.dead:
            # tmux can close the PTY before reaping its child (notably 3.4/Linux).
            # pane_dead alone is not evidence that the native process exited.
            if pane.exit_code is None and not pane.exit_signal:
                # Some Linux tmux builds leave a zombie without collecting SIGCHLD.
                # Check boot/PID/start time again; never substitute a cwd/time guess.
                evidence = (linux_zombie_exit(pane.pid, record.get("process_identity"))
                            if sys.platform.startswith("linux") else None)
                if evidence is not None:
                    pane = replace(pane, exit_code=evidence[0], exit_signal=evidence[1])
                    return "EXIT", pane, "Exit confirmed by Linux kernel; tmux has not reaped the child"
                return "UNKNOWN", pane, "Terminal closed; tmux has not yet reported the process exit"
            return "EXIT", pane, None
        identity = process_identity(pane.pid)
        if identity is None or identity != record.get("process_identity"):
            return "UNKNOWN", pane, "Process identity cannot be verified"
        if not pane.ready:
            return "START", pane, "Launcher has not completed execution handoff"
        return "LIVE", pane, None

    def verify(self, record: dict, allow_dead=False) -> Pane:
        state, pane, issue = self.observe(record)
        if pane is None or state not in (("LIVE", "EXIT") if allow_dead else ("LIVE",)):
            raise Unavailable(issue or f"Runtime is {state}; refusing to operate on an uncertain target")
        return pane

    def start(self, plan: LaunchPlan, store: StateStore) -> dict:
        self._argv([])  # Fail before writing a reservation when tmux is absent.
        existing = self.snapshot()
        if not existing.available:
            raise Unavailable(existing.issue or "Cannot verify tmux availability")
        if existing.server:
            option = self.command(["show-options", "-sv", "exit-unattached"], check=False)
            if option.returncode == 0 and option.stdout.strip() == "on":
                raise Conflict("tmux exit-unattached is on; this server cannot retain detached work")
        run_id = str(uuid.uuid4())
        record = {"schema_version": 1, "run_id": run_id, "host_id": store.host_id,
                  "agent": plan.agent, "name": plan.name, "cwd": plan.cwd, "root": plan.root,
                  "created_at": utc_now(), "launch_phase": "prepared", "tmux": None,
                  "pid": None, "process_identity": None, "executable": plan.executable,
                  "launch_history_key": plan.history_key, "current_history_key": None,
                  "native_id": plan.native_id, "binding": "explicit-launch" if plan.history_key else "none",
                  "dismissed": False, "ownership": "managed"}
        store.save(record)
        nonce = uuid.uuid4().hex
        deadline = time.monotonic() + self.config.startup_seconds
        connection = None
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        # Keep AF_UNIX paths short on macOS too. This directory never contains payload files.
        directory = tempfile.mkdtemp(prefix=f"4top-{os.getuid()}-", dir="/tmp")
        channel = str(Path(directory) / "start.sock")
        os.chmod(directory, 0o700)
        committed = False
        try:
            server.bind(channel)
            os.chmod(channel, 0o600)
            server.listen(1)
            server.settimeout(max(0.05, deadline - time.monotonic()))
            self.command(["new-session", "-d", "-s", "4t-" + run_id[:12], "-c", "/",
                          sys.executable, "-I", "-m", "fourtop._launch", channel, nonce, str(self.config.startup_seconds)], no_create=False)
            connection, _ = server.accept()
            connection.settimeout(max(0.05, deadline - time.monotonic()))
            verify_peer(connection)
            hello = read_frame(connection)
            if not hello or hello.get("nonce") != nonce:
                raise Unavailable("Startup nonce mismatch")
            observed = self.snapshot()
            candidates = [p for p in observed.panes if p.pane == hello.get("pane")
                          and p.pid == hello.get("pid")]
            if not observed.available or len(candidates) != 1:
                raise Unavailable("Cannot prove launcher/pane identity")
            pane = candidates[0]
            for option, value in (("@4top_run_id", run_id), ("@4top_host_id", store.host_id),
                                  ("@4top_agent", plan.agent), ("@4top_ready", "0"), ("remain-on-exit", "on")):
                self.command(["set-option", "-p", "-t", pane.pane, option, value])
            self.command(["set-option", "-t", pane.session, "destroy-unattached", "off"])
            process = process_identity(pane.pid)
            if not process:
                raise Unavailable("Cannot record launcher process identity")
            record.update(tmux={"socket": self.socket, "server": observed.server,
                                "session": pane.session, "pane": pane.pane}, pid=pane.pid,
                          process_identity=process, launch_phase="prepared-pane")
            store.save(record)
            send_frame(connection, {"nonce": nonce, "argv": list(plan.argv), "cwd": plan.cwd,
                                    "environment": plan.environment, "tmux_executable": self.executable,
                                    "tmux_socket": self.socket, "control_seconds": self.config.control_seconds})
            connection.settimeout(max(0.05, deadline - time.monotonic()))
            response = read_frame(connection)
            if not response or not response.get("ready"):
                raise Unavailable("Launcher could not prepare the execution environment")
            # Persist the reservation before granting permission to exec.
            record["launch_phase"] = "committed"
            store.save(record)
            committed = True  # A failed send may still have delivered execution permission.
            send_frame(connection, {"go": nonce})
            connection.settimeout(max(0.05, deadline - time.monotonic()))
            response = read_frame(connection)  # EOF follows a successful close-on-exec.
            if response is not None:
                record["launch_phase"] = "failed"
                store.save(record)
                raise Dependency(f"Native executable failed during handoff; inspect run {run_id}")
            record = store.update(run_id, launch_phase="handed-off")
            store.event("new", run_id, "handed-off")
            return record
        except FourtopError:
            if not committed:
                with contextlib.suppress(OSError, FourtopError):
                    store.update(run_id, launch_phase="failed")
            store.event("new", run_id, "uncertain" if committed else "failed", 6)
            raise
        except (OSError, EOFError, ValueError) as exc:
            if not committed:
                record["launch_phase"] = "failed"
                with contextlib.suppress(OSError, FourtopError):
                    store.save(record)
            store.event("new", run_id, "uncertain" if committed else "failed", 6)
            raise Unavailable(f"Startup {type(exc).__name__}; inspect run {run_id} before retrying. "
                              "No confirmed running agent was terminated.") from None
        finally:
            if connection is not None:
                connection.close()
            server.close()
            with contextlib.suppress(FileNotFoundError):
                os.unlink(channel)
            with contextlib.suppress(OSError):
                os.rmdir(directory)

    def is_inside(self) -> bool:
        return bool(self.config.environment.get("TMUX"))

    def calling_client(self, explicit: str | None = None) -> str:
        if default_socket(self.config.environment) != self.socket:
            raise Conflict("Cross-socket attach from inside tmux is disabled; return to the outer terminal")
        caller = self.config.environment.get("TMUX_PANE", "")
        if not PANE_RE.fullmatch(caller):
            raise Conflict("Cannot identify the calling tmux pane")
        snapshot = self.snapshot()
        session = next((p.session for p in snapshot.panes if p.pane == caller), None)
        if not session:
            raise Conflict("Calling pane no longer exists")
        response = self.command(["list-clients", "-F", "#{client_tty}\t#{session_id}"])
        clients = []
        for line in response.stdout.splitlines():
            fields = line.split("\t")
            if len(fields) == 2 and fields[1] == session and TTY_RE.fullmatch(fields[0]):
                clients.append(fields[0])
        if explicit:
            if explicit not in clients:
                raise Conflict("Requested client is not attached to the calling session")
            return explicit
        if len(clients) != 1:
            raise Conflict("Calling session has zero or multiple clients; select one explicitly with --client")
        return clients[0]

    def attach(self, record: dict, client: str | None = None) -> int:
        pane = self.verify(record)
        target = f"{pane.session}:{pane.window}.{pane.pane}"
        if self.is_inside():
            client = self.calling_client(client)
            # Validate once more after discovering the exact calling client.
            pane = self.verify(record)
            target = f"{pane.session}:{pane.window}.{pane.pane}"
            response = self.command(["if-shell", "-F", "-t", pane.pane, self._guard(record),
                                     f"switch-client -c {client} -t {target}",
                                     "display-message -p 4top-target-changed"])
            if "4top-target-changed" in response.stdout:
                raise Unavailable("Target changed immediately before attach")
            return 0
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            raise Dependency("Attach requires an interactive terminal; use list --json for scripts")
        # Pane/session IDs cannot be reused within a server. Never detach other clients.
        pane = self.verify(record)
        target = f"{pane.session}:{pane.window}.{pane.pane}"
        try:
            result = subprocess.run(self._argv([
                "if-shell", "-F", "-t", pane.pane, self._guard(record),
                f"attach-session -t {target}", "display-message -p 4top-target-changed"
            ]), env=self.environment)
        except OSError:
            raise Unavailable("Unable to attach the terminal") from None
        return result.returncode

    @staticmethod
    def _guard(record: dict) -> str:
        # All values are generated identifiers or validated integers, never user text.
        run_id, pid = record["run_id"], record["pid"]
        if not is_uuid(run_id) or type(pid) is not int:
            raise Unavailable("Invalid exact-target guard")
        return "#{&&:#{==:#{@4top_run_id}," + run_id + "},#{==:#{pane_pid}," + str(pid) + "}}"

    def terminate(self, record: dict) -> None:
        if record.get("ownership") != "managed":
            raise Conflict("Only managed runtimes can be terminated")
        pane = self.verify(record, allow_dead=True)
        response = self.command(["if-shell", "-F", "-t", pane.pane, self._guard(record),
                                 f"kill-pane -t {pane.pane}", "display-message -p 4top-target-changed"])
        if "4top-target-changed" in response.stdout:
            raise Unavailable("Target changed immediately before termination")
        deadline = time.monotonic() + self.config.control_seconds
        while process_identity(record["pid"]) == record["process_identity"]:
            if time.monotonic() >= deadline:
                raise Unavailable("Pane was closed but its recorded process is still present; inspect before resuming")
            time.sleep(0.025)

    def preview(self, record: dict, lines: int = 200) -> str:
        pane = self.verify(record, allow_dead=True)
        return self.command(["capture-pane", "-p", "-t", pane.pane, "-S", str(-lines)]).stdout
