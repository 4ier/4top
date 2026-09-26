"""4top's explicit, scriptable interface. TUI and CLI use the same services."""
from __future__ import annotations

import argparse
import json
import os
import sys
import unicodedata

from session_ls.api import clean_text
from session_ls.storage import StorageError

from . import __version__
from .config import Config
from .errors import Conflict, Dependency, FourtopError
from .models import LaunchPlan, age
from .services import DemoManager, Manager

COMMANDS = (
    ("list", "List native sessions (JSON Lines with --json)"),
    ("search", "Search approved local history"),
    ("preview", "Print one bounded read-only transcript page"),
    ("check", "Report whether one session can be resumed here"),
    ("new", "Start an original agent in this terminal"),
    ("resume", "Resume one exact session as a new process"),
    ("doctor", "Read dependency and source diagnostics"),
)


def _globals(parser: argparse.ArgumentParser, suppress=False) -> None:
    default = argparse.SUPPRESS if suppress else None
    parser.add_argument("--config", default=default, help="Explicit local TOML configuration")
    parser.add_argument("--host", default=default,
                        help="View one remote host: a [hosts.NAME] entry or an ssh destination")
    parser.add_argument("--no-color", action="store_true", default=argparse.SUPPRESS if suppress else False)


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="4top", description="Your coding agents, one terminal.")
    _globals(ap)
    ap.add_argument("--version", action="version", version=f"4top {__version__}")
    ap.add_argument("--demo", action="store_true", help="Isolated, read-only synthetic demo")
    commands = ap.add_subparsers(dest="command")
    for command, text in COMMANDS:
        sub = commands.add_parser(command, help=text)
        _globals(sub, suppress=True)
        if command in ("list", "search", "doctor"):
            sub.add_argument("--json", action="store_true", help="JSON output (list/search: JSON Lines)")
        if command in ("list", "search"):
            sub.add_argument("--agent", choices=("claude", "codex", "pi", "cursor"))
            sub.add_argument("--project", help="Case-insensitive project path substring")
        if command == "list":
            sub.add_argument("--query", default="", help="Same literal metadata match as the TUI search")
        if command == "search":
            sub.add_argument("query")
            sub.add_argument("--full", action="store_true", help="Explicit decoded literal full-content search")
        if command == "check":
            sub.add_argument("key", help="Stable key / unique native ID prefix; never a row number")
            sub.add_argument("--json", action="store_true")
        if command == "preview":
            sub.add_argument("key", help="Stable key; never a row number")
            sub.add_argument("--cursor", type=int, default=0, help="Byte offset returned by a previous page")
            sub.add_argument("--json", action="store_true")
        if command == "new":
            sub.add_argument("agent", choices=("codex", "claude", "pi"))
            sub.add_argument("--cwd", default=os.getcwd())
            sub.add_argument("--yes", action="store_true", help="Confirm a start on another host")
        if command == "resume":
            sub.add_argument("key", help="Stable key / unique native ID prefix; never a row number")
            sub.add_argument("--cwd", help="Explicit override for a historical working directory")
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


def _cell_width(value: str) -> int:
    """Display width: wide CJK characters count twice, combining marks not at all.

    This stays in the standard library on purpose. The non-interactive interface
    must not need a terminal UI stack to print a table.
    """
    width = 0
    for character in value:
        if unicodedata.combining(character):
            continue
        width += 2 if unicodedata.east_asian_width(character) in ("W", "F") else 1
    return width


def _clip(value: str, width: int) -> str:
    """Pad or truncate to a display width, counting wide characters as two."""
    value = clean_text(value)
    if _cell_width(value) > width:
        kept = ""
        for character in value:
            if _cell_width(kept + character) > width - 1:
                break
            kept += character
        value = kept + "…"
    return value + " " * max(0, width - _cell_width(value))


COLUMNS = (("AGENT", 7), ("UPDATED", 8), ("PROJECT", 26), ("TITLE", 34), ("KEY", 35))


def _display(snapshot, as_json=False, agent=None, project=None) -> int:
    rows = [row for row in snapshot.rows if (not agent or row.agent == agent)
            and (not project or project.casefold() in row.cwd.casefold())]
    if as_json:
        for row in rows:
            print(json.dumps(row.json(), ensure_ascii=False))
    else:
        print("  ".join(_clip(name, width) for name, width in COLUMNS))
        for row in rows:
            print("  ".join(_clip(value, width) for value, (_, width) in zip(
                (row.agent, age(row.last), row.cwd, row.title or "(untitled)", row.key),
                COLUMNS, strict=True)))
        print(f"\n{len(rows)} sessions · {snapshot.scope}")
    for issue in snapshot.issues:
        print("4top: " + clean_text(issue), file=sys.stderr)
    return 6 if snapshot.issues else 0


def _hand_over(manager, plan: LaunchPlan) -> int:
    manager.hand_over(plan)
    raise AssertionError("exec failed to replace this process")


def _exec(argv: list[str]) -> int:
    os.execvp(argv[0], argv)
    raise AssertionError("exec failed to replace this process")


def execute(args, extra: tuple[str, ...] = ()) -> int:
    if args.demo:
        if args.command not in (None, "list", "search", "preview"):
            raise Conflict("Demo is read-only; runtime and diagnostic operations are disabled")
        manager = DemoManager()  # Deliberately chosen BEFORE reading configuration or state.
    else:
        config = Config.load(args.config)
        if args.no_color or "NO_COLOR" in config.environment:
            config.color = "none"
        manager = Manager(config, config.resolve_host(args.host))
    try:
        command = args.command
        if command is None:
            if not sys.stdin.isatty() or not sys.stdout.isatty():
                raise Dependency("A terminal is required. Use `4top list --json` for scripts.")
            from .app import FourtopApp
            FourtopApp(manager, no_color=args.no_color or "NO_COLOR" in os.environ).run()
            return 0
        if command in ("list", "search"):
            snapshot = (manager.search(args.query, args.full) if command == "search"
                        else manager.snapshot())
            return _display(snapshot, args.json, args.agent, args.project)
        if command == "preview":
            row = manager.resolve_row(args.key)
            title, body, cursor = manager.preview(row, max(0, args.cursor))
            if args.json:
                print(json.dumps({"key": row.key, "host": row.host, "label": title,
                                  "body": body, "next_cursor": cursor}, ensure_ascii=False))
            else:
                print(clean_text(title, multiline=True))
                print(clean_text(body, multiline=True))
                if cursor is not None:
                    print(f"\n--next-cursor {cursor}", file=sys.stderr)
            return 0
        if command == "check":
            report = manager.check(args.key)
            if args.json:
                print(json.dumps(report, ensure_ascii=False))
            else:
                state = "resumable" if report["resumable"] else "not resumable"
                print(f"{report['agent']} · {report['key']} · {state}")
                if not report["resumable"]:
                    print(clean_text(report["reason"] or ""), file=sys.stderr)
            return 0 if report["resumable"] else 3
        if command == "doctor":
            from .doctor import diagnose
            report = diagnose(manager)
            print(json.dumps(report, ensure_ascii=False, indent=None if args.json else 2))
            return 6 if report["issues"] else 0
        if command == "new":
            if manager.remote:
                confirm(f"Start {args.agent} on {manager.scope} through ssh?\n"
                        f"Directory: {args.cwd}\nThe remote CLI runs with its own configuration.",
                        args.yes)
                remote = ["new", args.agent, "--cwd", args.cwd] + (["--", *extra] if extra else [])
                return _exec(manager.remote_argv(remote))
            return _hand_over(manager, manager.new(args.agent, args.cwd, extra))
        if command == "resume":
            local = not manager.remote
            target = manager.resolve_history(args.key) if local else None
            if local:
                message = (f"Resume creates a NEW {target.agent} process.\nDirectory: {args.cwd or target.cwd}\n"
                           f"Session: {target.key}\nNative ID: {target.native_id or 'exact source path'}\n"
                           "Current native configuration applies; original launch flags are not replayed.")
            else:
                message = (f"Resume runs on {manager.scope} through ssh.\nSession: {args.key}\n"
                           "The remote CLI creates the new process with its own configuration.")
            confirm(message, args.yes)
            if not local:
                command_line = ["resume", args.key] + (["--cwd", args.cwd] if args.cwd else []) + ["--yes"]
                return _exec(manager.remote_argv(command_line))
            return _hand_over(manager, manager.resume(args.key, args.cwd))
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
        print("4top: interface interrupted; agents started elsewhere are not terminated", file=sys.stderr)
        return 130
