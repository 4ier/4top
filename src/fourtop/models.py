"""Small value objects; runtime state is always derived from fresh observations."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from session_ls.api import HistoryRecord, utc_now


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
    name: str = ""


@dataclass(frozen=True)
class Pane:
    session: str
    window: str
    pane: str
    pid: int
    dead: bool
    exit_code: int | None
    clients: int
    run_id: str
    host_id: str
    agent: str
    ready: bool


@dataclass(frozen=True)
class TmuxSnapshot:
    panes: tuple[Pane, ...]
    server: str
    observed_at: str
    available: bool = True
    issue: str | None = None


@dataclass(frozen=True)
class ViewRow:
    key: str
    state: str
    agent: str
    cwd: str
    title: str
    created_at: str
    last: str
    run_id: str | None = None
    history_key: str | None = None
    pid: int | None = None
    clients: int = 0
    binding: str = "none"
    observed_at: str = ""
    stale: bool = False
    can_attach: bool = False
    can_resume: bool = False
    source: str = ""
    issue: str | None = None
    history: HistoryRecord | None = field(default=None, repr=False, compare=False)

    def json(self) -> dict[str, Any]:
        result = asdict(self)
        result.pop("history")
        result["schema_version"] = 1
        result["capability"] = {"attach": self.can_attach, "resume": self.can_resume}
        return result


@dataclass
class Snapshot:
    rows: list[ViewRow]
    issues: list[str] = field(default_factory=list)
    observed_at: str = field(default_factory=utc_now)
