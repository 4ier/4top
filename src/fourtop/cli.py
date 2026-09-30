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
from .config import NOTIFY_URL_RE, Config
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
    ("attach", "Show a session's agent kept on this host, starting it if needed"),
    ("peek", "Print the screen of an agent kept on this host"),
    ("send", "Type a message into an agent kept on this host, then Enter"),
    ("approve", "Answer yes to the permission prompt on a kept agent's screen"),
    ("deny", "Answer no to the permission prompt on a kept agent's screen"),
    ("label", "Name a session (no name clears it)"),
    ("mute", "Hide a session, or a project, from Now"),
    ("unmute", "Show a muted session or project again"),
    ("projects", "Recent project directories on this host, for starting a task"),
    ("doctor", "Read dependency and source diagnostics"),
    ("notify", "Push notifications to a phone through ntfy (opt-in)"),
    ("cloud", "Tasks in cloud sandboxes (E2B): a repository, a ref and a prompt"),
)
CLOUD = (
    ("new", "Start a task: an agent on a repository's ref and a prompt, in a sandbox"),
    ("ls", "List cloud tasks: state, cost so far, lifetime left, today's spending"),
    ("open", "Attach to a task's agent, waking its sandbox within its lifetime"),
    ("pause", "Pause a task's sandbox; it costs nothing while paused"),
    ("done", "Push the task's work as branch 4top/NAME, keep a snapshot, end the sandbox"),
    ("rm", "Remove a task; refused while its work is not on its branch"),
)
# Commands whose last argument is free text, which may follow `--` so that text
# starting with a dash is not read as an option.
TEXT_AFTER_DASHES = ("send", "label")


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
        if command in ("list", "search", "doctor", "projects"):
            sub.add_argument("--json", action="store_true",
                             help="JSON output (list/search/projects: JSON Lines)")
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
            sub.add_argument("--resident", action="store_true",
                             help="Keep the agent in this host's own tmux, so a closed terminal "
                                  "or a dropped link does not end it")
            sub.add_argument("--prompt", help="The agent's first request")
        if command == "resume":
            sub.add_argument("key", help="Stable key / unique native ID prefix; never a row number")
            sub.add_argument("--cwd", help="Explicit override for a historical working directory")
            sub.add_argument("--yes", action="store_true", help="Explicitly confirm this exact operation")
        if command == "attach":
            sub.add_argument("key", help="Stable key / unique native ID prefix; never a row number")
            sub.add_argument("--cwd", help="Explicit override for a historical working directory")
        if command == "notify":
            action = sub.add_mutually_exclusive_group(required=True)
            action.add_argument("--install", action="store_true",
                                help="Wire this host's Claude Code, Codex and Pi to notify")
            action.add_argument("--uninstall", action="store_true",
                                help="Remove exactly what --install added")
            action.add_argument("--test", action="store_true", help="Send a test message")
            action.add_argument("--from-hook", choices=("claude", "codex", "pi"), metavar="AGENT",
                                help="What an agent's hook runs; always exits 0")
            sub.add_argument("--agent", action="append", choices=("claude", "codex", "pi"),
                             help="With --install/--uninstall: only this agent (repeatable)")
            sub.add_argument("--url", help="With --install: an ntfy topic URL instead of a new "
                                           "random topic on ntfy.sh")
        if command == "cloud":
            verbs = sub.add_subparsers(dest="verb", required=True)
            for verb, verb_text in CLOUD:
                cloud = verbs.add_parser(verb, help=verb_text)
                _globals(cloud, suppress=True)
                if verb == "new":
                    cloud.add_argument("agent", choices=("claude", "codex"))
                    cloud.add_argument("prompt")
                    cloud.add_argument("--repo", help="Repository URL or GitHub owner/repo "
                                                      "(default: this directory's origin)")
                    cloud.add_argument("--ref", help="Branch, tag or commit to start from "
                                                     "(default: this directory's branch, else the default)")
                    cloud.add_argument("--name", help="Task name (default: the repository's)")
                    cloud.add_argument("--open", action="store_true", help="Attach once it runs")
                if verb in ("open", "pause", "done", "rm"):
                    cloud.add_argument("name", help="A task, as `4top cloud ls` names it")
                if verb == "done":
                    cloud.add_argument("--pr", action="store_true", help="Also open a pull request")
                    cloud.add_argument("--no-snapshot", action="store_true",
                                       help="Keep no snapshot of the sandbox")
                if verb == "rm":
                    cloud.add_argument("--discard", action="store_true",
                                       help="Remove it even though its work is not on its branch")
                if verb in ("new", "ls", "done"):
                    cloud.add_argument("--json", action="store_true")
        if command in ("peek", "send", "approve", "deny", "label"):
            sub.add_argument("key", help="Stable key; never a row number")
        if command == "peek":
            sub.add_argument("--lines", type=int, default=40, help="How many lines, from the bottom")
            sub.add_argument("--json", action="store_true")
        if command == "send":
            sub.add_argument("text", nargs="?", help="What to type (or after --)")
            sub.add_argument("--no-enter", action="store_true", help="Type it without pressing Enter")
        if command == "label":
            sub.add_argument("name", nargs="?", default="", help="The name; none clears it")
        if command in ("mute", "unmute"):
            sub.add_argument("key", nargs="?", help="Stable key; never a row number")
            sub.add_argument("--project", help="A project directory instead: every session in it")
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


def _minutes(seconds) -> str:
    return "—" if seconds is None else f"{int(seconds) // 3600}h{int(seconds) // 60 % 60:02d}m"


def _cloud(config: Config, args) -> int:
    from . import cloud
    if args.verb == "new":
        repo, ref = args.repo, args.ref
        if not repo:
            repo, branch = cloud.origin(os.getcwd())
            ref = ref or branch
            cloud.say(f"working on {repo} at {ref or 'its default branch'} as pushed; "
                      "what is only on this machine does not travel")
        task = cloud.new(config, cloud.Request(args.agent, args.prompt, repo, ref or "", args.name or ""))
        if args.json:
            print(json.dumps(task, ensure_ascii=False))
        else:
            cloud.say(f"{task['name']} is working on {task['branch']}; it stops by {task['deadline']} "
                      f"(about ${task['usd_per_hour']:.2f}/h, estimate). `4top cloud open {task['name']}` "
                      f"watches it, `4top cloud done {task['name']}` brings the work home")
        return _exec(cloud.open_argv(config, task["name"])) if args.open else 0
    if args.verb == "ls":
        rows, listing = cloud.tasks(config)
        spending = cloud.budget(config, listing)
        if args.json:
            for row in rows:
                print(json.dumps(row, ensure_ascii=False))
            print(json.dumps({"budget": spending}, ensure_ascii=False))
            return 0
        for row in rows:
            if row["state"] == "done":
                print(f"{_clip(row['name'], 24)}  done     {row['branch']}  kept until {row['expires']}")
                continue
            cost = "?" if row["cost_usd"] is None else f"${row['cost_usd']:.3f}"
            print(f"{_clip(row['name'], 24)}  {_clip(row['state'], 7)}  {cost:>6}  "
                  f"{_minutes(row['lifetime_left'])} left  {row['branch'] or row['sandbox']}  "
                  f"{_clip(row['prompt'], 50)}")
        print(f"today ${spending['spent_today_usd']:.3f} of ${spending['daily_budget_usd']:.2f} "
              f"(estimate from E2B's prices of {spending['prices_read']})")
        return 0
    if args.verb == "open":
        return _exec(cloud.open_argv(config, args.name))
    if args.verb == "pause":
        cloud.pause(config, args.name)
        cloud.say(f"{args.name} is paused; `4top cloud open {args.name}` resumes it")
        return 0
    if args.verb == "done":
        result = cloud.done(config, args.name, pr=args.pr, snapshot=not args.no_snapshot)
        if args.json:
            print(json.dumps(result, ensure_ascii=False))
        elif result["branch"]:
            cloud.say(f"{args.name}: pushed {result['branch']} at {result['commit'][:12]}"
                      + (f"; {result['pull_request']}" if result["pull_request"] else ""))
        else:
            cloud.say(f"{args.name} made no changes; nothing was pushed")
        if not args.json and result["snapshot"]:
            cloud.say(f"snapshot {result['snapshot']} is kept for "
                      f"{config.cloud.keep_snapshot_days:g} days; the sandbox is gone")
        return 0
    if args.verb == "rm":
        cloud.say(f"removed {cloud.remove(config, args.name, args.discard)}")
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
                if manager.config.cloud.enabled:
                    from .cloud import sources
                    hosts += [Manager(manager.config, host) for host in sources(manager.config)]
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
                remote = (["new", args.agent, "--cwd", args.cwd] + (["--resident"] if args.resident else [])
                          + (["--prompt", args.prompt] if args.prompt is not None else [])
                          + (["--", *extra] if extra else []))
                return _exec(manager.remote_argv(remote))
            plan = manager.new(args.agent, args.cwd, extra, args.prompt)
            return _hand_over(manager, manager.keep(plan) if args.resident else plan)
        if command == "cloud":
            return _cloud(manager.config, args)
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
        if command == "attach":
            # What the panel runs to open a session on a host: no confirmation, like Enter.
            if manager.remote:
                return _exec(manager.remote_argv(["attach", args.key]
                                                 + (["--cwd", args.cwd] if args.cwd else [])))
            return _hand_over(manager, manager.attach(args.key, args.cwd))
        if command == "notify":
            from . import notify
            flag = next(name for name in ("install", "uninstall", "test") if getattr(args, name))
            if manager.remote:  # the hooks and the topic belong to that host
                return _exec(manager.remote_argv(
                    ["notify", "--" + flag] + (["--url", args.url] if args.url else [])
                    + [word for agent in args.agent or () for word in ("--agent", agent)]))
            if args.url and (flag != "install" or not NOTIFY_URL_RE.match(args.url)):
                raise FourtopError("--url takes an ntfy topic URL, with --install", 2)
            if flag == "test":
                print(notify.test(manager.config))
                return 0
            report = (notify.install(manager.config, args.config, args.url, agents=args.agent)
                      if flag == "install" else notify.uninstall(manager.config, args.agent))
            print("\n".join(report))
            if flag == "install":
                print("\n" + notify.subscribe_help(manager.config.notify_url))
            return 0
        if command == "peek":
            screen = manager.screen(args.key, args.lines)
            if args.json:
                print(json.dumps(screen, ensure_ascii=False))
            else:
                print("\n".join(clean_text(line) for line in screen["lines"]))
            return 0
        if command == "send":
            manager.send(args.key, args.text if args.text is not None else " ".join(extra),
                         enter=not args.no_enter)
            return 0
        if command in ("approve", "deny"):
            getattr(manager, command)(args.key)
            return 0
        if command == "label":
            manager.label(args.key, args.name or " ".join(extra))
            return 0
        if command in ("mute", "unmute"):
            if bool(args.key) == bool(args.project):
                raise FourtopError(f"{command} takes a session key or --project DIR", 2)
            if args.project:
                manager.mute_project(args.project, command == "mute")
            else:
                manager.mute(args.key, command == "mute")
            return 0
        if command == "projects":
            found = manager.projects()
            for item in found:
                if args.json:
                    print(json.dumps(item, ensure_ascii=False))
                else:
                    print(f"{_clip(str(item.get('path', '')), 60)}  {item.get('sessions', 0):>4}  "
                          f"{age(str(item.get('last', '')))}")
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
    # `--prompt TEXT` whose text starts with a dash would be read as an option; joined
    # into one word it is always the value.
    if "--prompt" in arguments[:-1]:
        index = arguments.index("--prompt")
        arguments[index:index + 2] = [f"--prompt={arguments[index + 1]}"]
    ap = parser()
    args = ap.parse_args(arguments)
    if args.command == "notify" and args.from_hook:
        # Inside an agent's turn: never an error, never a wait, nothing on stdout.
        from .notify import from_hook
        return from_hook(args.config, args.from_hook, extra)
    if extra and args.command not in ("new", *TEXT_AFTER_DASHES):
        ap.error("Only `new` accepts native agent arguments after --; send and label, text")
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
