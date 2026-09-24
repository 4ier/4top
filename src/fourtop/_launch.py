"""Private one-shot launcher, executed as an installed module with Python -I.

It finishes by exec'ing the original CLI: no 4top supervisor or agent loop
remains. All error messages intentionally omit environment and argv values.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys

from .transport import read_frame, send_frame, verify_peer


def main() -> int:
    if len(sys.argv) != 4:
        return 64
    channel, nonce, timeout = sys.argv[1:]
    inherited = {key: os.environ[key] for key in
                 ("TMUX", "TMUX_PANE", "TERM", "TERM_PROGRAM", "TERM_PROGRAM_VERSION")
                 if key in os.environ}
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.settimeout(float(timeout))
    try:
        connection.connect(channel)
        verify_peer(connection)
        send_frame(connection, {"nonce": nonce, "pid": os.getpid(),
                                "pane": inherited.get("TMUX_PANE", "")})
        plan = read_frame(connection)
        if plan is None or plan.get("nonce") != nonce:
            raise RuntimeError("Startup was cancelled before handoff")
        environment = plan["environment"]
        argv = plan["argv"]
        if (not isinstance(environment, dict) or not all(isinstance(k, str) and isinstance(v, str)
                for k, v in environment.items()) or not isinstance(argv, list)
                or not argv or not all(isinstance(x, str) for x in argv)):
            raise ValueError("Invalid startup plan")
        environment.update(inherited)
        # The new pane, rather than the caller's previous terminal, owns these values.
        environment.pop("LINES", None)
        environment.pop("COLUMNS", None)
        os.chdir(plan["cwd"])
        send_frame(connection, {"ready": True})
        decision = read_frame(connection)
        if decision is None or decision.get("go") != nonce:
            raise RuntimeError("Startup was cancelled before execution")
        # Setting this pane-local marker is the last step before exec.
        args = [plan["tmux_executable"], "-S", plan["tmux_socket"], "set-option",
                "-p", "-t", inherited["TMUX_PANE"], "@4top_ready", "1"]
        args = [value[:-1] + r"\;" if value.endswith(";") else value for value in args]
        subprocess.run(args, check=True, capture_output=True, env=environment, timeout=plan["control_seconds"])
        connection.set_inheritable(False)
        os.execvpe(argv[0], argv, environment)
    except BaseException as exc:
        try:
            send_frame(connection, {"error": type(exc).__name__, "errno": getattr(exc, "errno", None)})
        except (OSError, ValueError):
            pass
        print(f"4top startup failed ({type(exc).__name__}). Inspect `4top list` / `4top doctor`.",
              file=sys.stderr, flush=True)
        return 127
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
