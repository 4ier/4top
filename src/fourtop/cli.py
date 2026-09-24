"""4top's explicit, scriptable interface. TUI and CLI use the same services."""
from __future__ import annotations

import argparse
import json
import os
import sys

from session_ls.api import clean_text
from session_ls.storage import StorageError

from . import __version__
from .config import Config
from .errors import Conflict, Dependency, FourtopError, Missing
from .services import DemoManager, Manager


def _globals(parser: argparse.ArgumentParser, suppress=False) -> None:
    default = argparse.SUPPRESS if suppress else None
    parser.add_argument("--config", default=default, help="Explicit local TOML configuration")
    parser.add_argument("--socket", default=default, help="Select one tmux socket (absolute path)")
    parser.add_argument("--no-color", action="store_true", default=argparse.SUPPRESS if suppress else False)


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="4top", description="Your coding agents, one terminal.")
    _globals(ap)
    ap.add_argument("--version", action="version", version=f"4top {__version__}")
    ap.add_argument("--demo", action="store_true", help="Isolated, read-only synthetic demo")
    commands = ap.add_subparsers(dest="command")
    for command, text in (("list", "Print one snapshot"), ("search", "Search approved local history"),
                          ("new", "Start an original agent in tmux"), ("open", "Attach or confirm a native resume"),
                          ("attach", "Only attach; never start a replacement"), ("resume", "Resume one exact history"),
                          ("link", "Explicitly associate a runtime and history"),
                          ("terminate", "Close one verified managed pane"),
                          ("dismiss", "Hide an exited runtime; never delete native history"),
                          ("doctor", "Read local dependency and compatibility diagnostics")):
        sub = commands.add_parser(command, help=text)
        _globals(sub, suppress=True)
        if command in ("list", "search", "doctor"):
            sub.add_argument("--json", action="store_true", help="JSON output (list/search: JSON Lines)")
        if command in ("list", "search"):
            sub.add_argument("--agent", choices=("claude", "codex", "pi", "cursor"))
            sub.add_argument("--project", help="Case-insensitive project path substring")
        if command == "search":
            sub.add_argument("query")
            sub.add_argument("--full", action="store_true", help="Explicit decoded literal full-content search")
        if command == "new":
            sub.add_argument("agent", choices=("codex", "claude", "pi"))
            sub.add_argument("--cwd", default=os.getcwd())
            sub.add_argument("--name", default="")
            sub.add_argument("--detach", action="store_true")
        if command in ("open", "attach", "resume", "terminate", "dismiss"):
            sub.add_argument("key", help="Stable key / unique UUID prefix; never a row number")
        if command in ("resume", "open"):
            sub.add_argument("--cwd", help="Explicit override for a historical working directory")
            sub.add_argument("--detach", action="store_true")
        if command in ("attach", "open", "new", "resume"):
            sub.add_argument("--client", help="Exact calling client TTY when inside a shared tmux session")
        if command == "link":
            sub.add_argument("run")
            sub.add_argument("history")
        if command in ("open", "resume", "link", "terminate", "dismiss"):
            sub.add_argument("--yes", action="store_true", help="Explicitly confirm this exact operation")
    return ap


def confirm(message: str, yes: bool) -> None:
    if yes:
        return
    if not sys.stdin.isatty():
        raise Conflict("Confirmation required; inspect the target and pass --yes for a noninteractive operation")
    print(clean_text(message, multiline=True), file=sys.stderr)
    print("Continue? [y/N] ", end="", file=sys.stderr, flush=True)
    if sys.stdin.readline().strip().casefold() not in ("y", "yes"):
        raise Conflict("Cancelled; no change made")


def _display(snapshot, as_json=False, agent=None, project=None) -> int:
    rows = [row for row in snapshot.rows if (not agent or row.agent == agent)
            and (not project or project.casefold() in row.cwd.casefold())]
    if as_json:
        for row in rows:
            print(json.dumps(row.json(), ensure_ascii=False))
    else:
        from rich.console import Console
        from rich.table import Table
        from rich.text import Text
        table = Table(box=None, expand=False, highlight=False, pad_edge=False)
        for name in ("STATE", "AGENT", "PID", "PROJECT", "TITLE", "KEY"):
            table.add_column(name, overflow="ellipsis", no_wrap=True)
        for row in rows:
            table.add_row(*[Text(clean_text(value)) for value in (
                row.state + ("*" if row.stale else ""), row.agent, str(row.pid or "—"),
                row.cwd, row.title, row.key)])
        Console(color_system=None, highlight=False).print(table)
        print(f"\n{len(rows)} work items; HIST means no verified live association, not proof of exit.")
    for issue in snapshot.issues:
        print("4top: " + clean_text(issue), file=sys.stderr)
    return 6 if snapshot.issues else 0


def execute(args, extra: tuple[str, ...] = ()) -> int:
    if args.demo:
        if args.command not in (None, "list", "search"):
            raise Conflict("Demo is read-only; runtime and diagnostic operations are disabled")
        manager = DemoManager()  # Deliberately chosen BEFORE reading configuration or state.
    else:
        config = Config.load(args.config, args.socket)
        if args.no_color or "NO_COLOR" in config.environment:
            config.color = "none"
        manager = Manager(config)
    try:
        command = args.command
        if command is None:
            if not sys.stdin.isatty() or not sys.stdout.isatty():
                raise Dependency("A terminal is required. Use `4top list --json` for scripts.")
            from .app import FourtopApp
            app = FourtopApp(manager, no_color=args.no_color or "NO_COLOR" in os.environ)
            result = app.run()
            if result and result[0] == "attach":
                return manager.attach(result[1])  # After Textual has restored the original TTY.
            return 0
        if command in ("list", "search"):
            snapshot = manager.search(args.query, args.full) if command == "search" else manager.snapshot()
            return _display(snapshot, args.json, args.agent, args.project)
        if command == "doctor":
            from .doctor import diagnose
            report = diagnose(manager)
            print(json.dumps(report, ensure_ascii=False, indent=None if args.json else 2))
            return 6 if report["issues"] else 0
        if command == "attach":
            return manager.attach(args.key, args.client)
        if command == "new":
            run = manager.new(args.agent, args.cwd, args.name, extra)
            print("r_" + run["run_id"], flush=True)
            return 0 if args.detach else manager.attach(run["run_id"], args.client)
        if command in ("open", "resume"):
            key = args.key
            if command == "open":
                row = manager.resolve_row(key)
                if row.can_attach and row.run_id:
                    return manager.attach(row.run_id, args.client)
                if row.stale or row.state in ("UNKNOWN", "START"):
                    raise Conflict("Target cannot be safely opened until its runtime state is resolved")
                if not row.history_key:
                    raise Missing("No exact history association; search history and select a record")
                key = row.history_key
            history = manager.resolve_history(key)
            confirm(f"Resume creates a NEW {history.agent} process.\nHistory: {history.key}\n"
                    f"Native ID: {history.native_id or 'exact source path'}\nDirectory: {args.cwd or history.cwd}\n"
                    "Current native configuration applies; original launch flags are not replayed.", args.yes)
            run = manager.resume(history.key, args.cwd)
            print("r_" + run["run_id"], flush=True)
            return 0 if args.detach else manager.attach(run["run_id"], args.client)
        if command == "link":
            run = manager.resolve_run(args.run)
            history = manager.resolve_history(args.history)
            confirm(f"Associate {run['agent']} run {run['run_id']} with history {history.key}?\n"
                    "This is a user-confirmed association; it does not change the agent's current context.", args.yes)
            manager.link(run["run_id"], history.key)
            print("Linked " + run["run_id"])
            return 0
        if command in ("terminate", "dismiss"):
            run = manager.resolve_run(args.key)
            operation = "Close this managed pane (may interrupt writes)" if command == "terminate" else "Hide this exited runtime"
            confirm(f"{operation}?\n{run['agent']} · {run['cwd']}\nRun: {run['run_id']}", args.yes)
            getattr(manager, command)(run["run_id"])
            print(command.capitalize() + " requested: " + run["run_id"])
            return 0
        raise FourtopError("Unknown operation", 2)
    finally:
        manager.close()


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    extra = ()
    if "--" in arguments:
        index = arguments.index("--")
        extra, arguments = tuple(arguments[index + 1:]), arguments[:index]
    ap = parser()
    args = ap.parse_args(arguments)
    if extra and args.command != "new":
        ap.error("Only `new` accepts native agent arguments after --")
    try:
        result = execute(args, extra)
        sys.stdout.flush()
        return result
    except BrokenPipeError:
        with open(os.devnull, "w") as sink:
            os.dup2(sink.fileno(), sys.stdout.fileno())
        return 0
    except FourtopError as exc:
        print("4top: " + clean_text(str(exc)), file=sys.stderr)
        return exc.code
    except (StorageError, OSError) as exc:
        # Paths and environment values are intentionally not included in this fallback.
        print(f"4top: local I/O failed ({type(exc).__name__}); no automatic retry performed", file=sys.stderr)
        return 6
    except (ValueError, TypeError) as exc:
        print(f"4top: invalid data ({type(exc).__name__}); use doctor to inspect local sources", file=sys.stderr)
        return 6
    except KeyboardInterrupt:
        print("4top: interface interrupted; handed-off agents are not terminated", file=sys.stderr)
        return 130
