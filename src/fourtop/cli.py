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
from .models import ROW_SCHEMA, LaunchPlan, age
from .services import DemoManager, Manager
from .sync import changed_since, trailer

COMMANDS = (
    ("list", "List native sessions (JSON Lines with --json)"),
    ("search", "Search approved local history"),
    ("preview", "Print one bounded read-only transcript page"),
    ("check", "Report whether one session can be resumed here"),
    ("new", "Start an original agent in this terminal"),
    ("resume", "Resume one exact session as a new process"),
    ("doctor", "Read dependency and source diagnostics"),
    ("cloud", "Sessions on machines of their own (E2B sandboxes)"),
)
CLOUD = (
    ("new", "Start an agent on a fresh machine with this project's current work"),
    ("fork", "Copy a sandbox, running agent included, as it is this instant"),
    ("race", "Give one prompt to several agents, each on its own machine"),
    ("take", "Bring a sandbox's work here as branch 4top/NAME"),
    ("rewind", "List a sandbox's checkpointed turns, or start a sandbox from one"),
    ("up", "Carry a local session to the cloud and resume it there"),
    ("home", "Bring a cloud session back here and resume it"),
    ("ls", "List cloud sandboxes"),
    ("rm", "Remove cloud sandboxes and their checkpoints"),
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
            sub.add_argument("--sync", action="store_true",
                             help="Append a summary of all rows (for a remote panel; with --json)")
            sub.add_argument("--since", default="",
                             help="With --sync: only rows written at or after this cursor")
        if command == "search":
            sub.add_argument("query")
            sub.add_argument("--full", action="store_true", help="Explicit decoded literal full-content search")
        if command == "check":
            sub.add_argument("key", help="Stable key / unique native ID prefix; never a row number")
            sub.add_argument("--json", action="store_true")
        if command == "preview":
            sub.add_argument("key", help="Stable key; never a row number")
            sub.add_argument("--cursor", type=int, default=0, help="Byte offset returned by a previous page")
            sub.add_argument("--tail", action="store_true",
                             help="The latest messages instead of the first page")
            sub.add_argument("--before", type=int, default=None,
                             help="With --tail: the earlier_cursor returned by a later page")
            sub.add_argument("--json", action="store_true")
        if command == "new":
            sub.add_argument("agent", choices=("codex", "claude", "pi"))
            sub.add_argument("--cwd", default=os.getcwd())
            sub.add_argument("--yes", action="store_true", help="Confirm a start on another host")
        if command == "resume":
            sub.add_argument("key", help="Stable key / unique native ID prefix; never a row number")
            sub.add_argument("--cwd", help="Explicit override for a historical working directory")
            sub.add_argument("--yes", action="store_true", help="Explicitly confirm this exact operation")
        if command == "cloud":
            verbs = sub.add_subparsers(dest="verb", required=True)
            for verb, verb_text in CLOUD:
                cloud = verbs.add_parser(verb, help=verb_text)
                _globals(cloud, suppress=True)
                if verb in ("new", "up"):
                    cloud.add_argument("--name", help="Section name (default: the project's)")
                if verb == "new":
                    cloud.add_argument("agent", choices=("codex", "claude", "pi"))
                if verb in ("new", "race", "take"):
                    cloud.add_argument("--cwd", default=os.getcwd())
                if verb in ("fork", "take", "rewind", "home"):
                    cloud.add_argument("name", help="A cloud sandbox, as `4top cloud ls` names it")
                if verb == "fork":
                    cloud.add_argument("-n", "--count", type=int, default=2)
                if verb == "race":
                    cloud.add_argument("prompt")
                    cloud.add_argument("--agents", default="claude,codex",
                                       help="Comma-separated; an agent may repeat")
                if verb == "rewind":
                    cloud.add_argument("turn", nargs="?", type=int,
                                       help="Start a sandbox from the checkpoint after this turn")
                if verb == "up":
                    cloud.add_argument("key", help="A local session: stable key or native ID prefix")
                if verb == "ls":
                    cloud.add_argument("--json", action="store_true")
                if verb == "rm":
                    cloud.add_argument("names", nargs="+")
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


def _display_sync(snapshot, since="", agent=None, project=None) -> int:
    """Rows written since the cursor, then one line summarising every row."""
    payloads = [row.json() for row in snapshot.rows if (not agent or row.agent == agent)
                and (not project or project.casefold() in row.cwd.casefold())]
    for payload in changed_since(payloads, since):
        print(json.dumps(payload, ensure_ascii=False))
    print(json.dumps({"schema_version": ROW_SCHEMA, "sync": trailer(payloads)}, ensure_ascii=False))
    for issue in snapshot.issues:
        print("4top: " + clean_text(issue), file=sys.stderr)
    return 6 if snapshot.issues else 0


def _hand_over(manager, plan: LaunchPlan) -> int:
    manager.hand_over(plan)
    raise AssertionError("exec failed to replace this process")


def _exec(argv: list[str]) -> int:
    os.execvp(argv[0], argv)
    raise AssertionError("exec failed to replace this process")


def _cloud(config: Config, args, extra: tuple[str, ...]) -> int:
    from . import cloud
    if args.verb == "new":
        host, where = cloud.new(config, args.cwd, args.name)
        cloud.say(f"{host.name} is ready; `4top` lists it, `4top cloud rm {host.name}` removes it")
        cwd = os.path.abspath(args.cwd)
        remote = ["new", args.agent, "--cwd", cwd, "--yes"] + (["--", *extra] if extra else [])
        return _exec(Manager(config, host).remote_argv(remote))
    if args.verb == "fork":
        for host in cloud.fork(config, args.name, max(1, args.count)):
            cloud.say(f"{host.name} is a copy of {args.name}; Enter on it in `4top` attaches")
        return 0
    if args.verb == "race":
        agents = [agent.strip() for agent in args.agents.split(",") if agent.strip()]
        if not agents or set(agents) - {"claude", "codex", "pi"}:
            raise FourtopError("--agents takes claude, codex or pi, comma-separated", 2)
        for host in cloud.race(config, args.cwd, args.prompt, agents):
            cloud.say(f"{host.name} is on it")
        cloud.say("watch them in `4top`; `4top cloud take NAME` brings the winner's work here")
        return 0
    if args.verb == "take":
        branch = cloud.take(config, args.name, args.cwd)
        cloud.say(f"the work of {args.name} is branch {branch}")
        return 0
    if args.verb == "rewind":
        if args.turn is None:
            _, _, turns = cloud.checkpoint_log(config, args.name)
            for number, (at, _, prompt) in enumerate(turns, 1):
                print(f"{number:>3}  {at}  {_clip(prompt, 80)}")
            if not turns:
                cloud.say(f"{args.name} has no checkpointed turns yet")
            return 0
        host = cloud.rewind(config, args.name, args.turn)
        cloud.say(f"{host.name} is {args.name} right after turn {args.turn}")
        # The checkpoint holds the machine's memory: the agent is already running.
        return _exec(cloud.agent_argv(config, host, None))
    if args.verb == "up":
        record = Manager(config).resolve_history(args.key)
        host, native = cloud.up(config, record, args.name)
        cloud.say(f"{record.agent} session {native[:8]} now runs on {host.name}; "
                  f"`4top cloud home {host.name}` brings it back")
        return _exec(Manager(config, host).remote_argv(["resume", native, "--yes"]))
    if args.verb == "home":
        path, branch = cloud.home(config, args.name)
        if branch:
            cloud.say(f"{path} changed since it left, so its work is branch {branch}")
        else:
            cloud.say(f"{path} now holds the work of {args.name}")
        cloud.say(f"its transcripts are here; `4top` resumes it, `4top cloud rm {args.name}` "
                  f"removes the sandbox")
        return 0
    if args.verb == "ls":
        found = cloud.discover(config)
        for host, item in found:
            metadata = item.get("metadata") or {}
            if args.json:
                print(json.dumps({"name": host.name, "sandbox": host.e2b, "state": item.get("state"),
                                  "path": metadata.get("fourtop_path"),
                                  "started": item.get("startedAt")}, ensure_ascii=False))
            else:
                print(f"{_clip(host.name, 24)}  {_clip(str(item.get('state')), 8)}  {host.e2b}  "
                      f"{metadata.get('fourtop_path', '')}")
        return 0
    if args.verb == "rm":
        for name in args.names:
            cloud.remove(config, name)
            cloud.say(f"removed {name}")
        return 0
    raise FourtopError("Unknown cloud operation", 2)


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
            from .workspace import Workspace, enter
            if not manager.demo:
                enter(manager.config, sys.argv[1:])  # becomes the tmux client unless plain
            hosts = []
            if not manager.demo and not manager.remote:
                hosts = [Manager(manager.config, host) for host in manager.config.hosts.values()]
            workspace = None if manager.demo else Workspace.current(manager.config.environment)
            FourtopApp(manager, no_color=args.no_color or "NO_COLOR" in os.environ, hosts=hosts,
                       workspace=workspace).run()
            return 0
        if command in ("list", "search"):
            snapshot = (manager.search(args.query, args.full) if command == "search"
                        else manager.snapshot())
            if command == "list" and args.sync:
                return _display_sync(snapshot, args.since, args.agent, args.project)
            return _display(snapshot, args.json, args.agent, args.project)
        if command == "preview":
            row = manager.resolve_row(args.key)
            if args.tail:
                title, body, earlier = manager.preview_tail(row, args.before)
                if args.json:
                    print(json.dumps({"key": row.key, "host": row.host, "label": title,
                                      "body": body, "earlier_cursor": earlier}, ensure_ascii=False))
                else:
                    print(clean_text(title, multiline=True))
                    print(clean_text(body, multiline=True))
                return 0
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
        if command == "cloud":
            return _cloud(manager.config, args, extra)
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
