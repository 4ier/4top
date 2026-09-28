"""Small value objects over native history. Nothing here tracks a live process."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from session_ls.api import HistoryRecord, utc_now

ROW_SCHEMA = 2
# A title is the first prompt, and a pasted prompt can be 100 KB. The panel shows one
# line of it, and a remote panel fetches every row on every refresh, so JSON rows
# carry a bounded title; metadata search still sees the whole one on its own host.
TITLE_LIMIT = 200


def age(value: str) -> str:
    """A short, monotonic-ish label for how long ago a session was written."""
    try:
        started = datetime.fromisoformat(value.replace("Z", "+00:00"))
        delta = max(0, int((datetime.now(timezone.utc) - started).total_seconds()))
    except (ValueError, TypeError):
        return "—"
    if delta < 60:
        return f"{delta}s"
    if delta < 3600:
        return f"{delta // 60}m"
    if delta < 86400:
        return f"{delta // 3600}h{delta // 60 % 60:02d}m"
    return f"{delta // 86400}d"


@dataclass(frozen=True)
class LaunchPlan:
    agent: str
    executable: str
    argv: tuple[str, ...]
    cwd: str
    environment: dict[str, str] = field(repr=False)
    root: str = ""
    native_id: str | None = None
    history_key: str | None = None


@dataclass(frozen=True)
class Session:
    """One native session. The transcript is the durable object; no process is implied.

    ``key`` is scoped to the host that produced it and is never recomputed by a
    client reading another machine's rows.
    """

    key: str
    agent: str
    cwd: str
    title: str
    started: str
    last: str
    host: str = "local"
    source: str = ""
    status: str = "available"
    problems: tuple[str, ...] = ()
    can_resume: bool = True
    issue: str | None = None
    record: HistoryRecord | None = field(default=None, repr=False, compare=False)
    # A session another agent session started for itself, such as Codex's approval
    # reviewer or a spawned worker. Hidden by default: nobody resumes those by hand.
    subagent: bool = False
    # From the transcript's end (session-ls): "working" mid-turn, "waiting" once the
    # turn is handed back; the person's latest request; the git branch.
    activity: str = ""
    last_request: str = ""
    branch: str = ""

    def json(self) -> dict[str, Any]:
        result = asdict(self)
        result.pop("record")
        result["problems"] = list(self.problems)
        for name in ("title", "last_request"):
            if len(result[name]) > TITLE_LIMIT:
                result[name] = result[name][:TITLE_LIMIT - 1] + "…"
        result["schema_version"] = ROW_SCHEMA
        return result


@dataclass
class Snapshot:
    rows: list[Session]
    issues: list[str] = field(default_factory=list)
    observed_at: str = field(default_factory=utc_now)
    scope: str = "local"
    cached: bool = False  # rows from the last visit, shown while the host is asked again
