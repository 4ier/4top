"""Incremental remote refresh.

A remote panel refreshes every few seconds, and sending every row each time costs
a phone's metered link megabytes an hour for rows that have not changed. Instead
the client sends a cursor, and the host sends only the rows written since then,
plus a trailer that summarises *all* of its rows: their count and a digest over
each row's fingerprint.

The client merges the changed rows into what it already has and recomputes the
digest. A match proves the merged view equals the host's current rows. A mismatch
means something changed without a newer `last`, such as a deleted transcript, a
directory that went missing, or a clock that moved back. The client then asks once
for everything. Correctness never depends on the cursor; the cursor only makes the
common case small.

The last synced rows are kept on disk, so a panel opens a host with its rows on
screen at once and then catches up, instead of waiting for the first round trip.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from session_ls.storage import atomic_json, private_dir, read_json

SYNC_VERSION = 1
CACHE_SCHEMA = 1


def canonical(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def fingerprint(payload: dict[str, Any]) -> str:
    return hashlib.sha256(canonical(payload).encode()).hexdigest()[:20]


def digest(prints: dict[str, str]) -> str:
    h = hashlib.sha256()
    for key in sorted(prints):
        h.update(f"{key}\t{prints[key]}\n".encode())
    return h.hexdigest()


def trailer(payloads: list[dict[str, Any]]) -> dict[str, Any]:
    """What the host appends after the changed rows: a summary of all of them."""
    return {"version": SYNC_VERSION, "count": len(payloads),
            "digest": digest({str(p["key"]): fingerprint(p) for p in payloads}),
            "cursor": max((str(p.get("last") or "") for p in payloads), default="")}


def changed_since(payloads: list[dict[str, Any]], since: str | None) -> list[dict[str, Any]]:
    # `last` is an ISO timestamp in one fixed format, so text order is time order.
    # Equal timestamps are sent again: a row written in the same instant as the
    # cursor must not be missed, and a duplicate merges harmlessly.
    if not since:
        return payloads
    return [p for p in payloads if str(p.get("last") or "") >= since]


class SyncState:
    """One host's rows as last proven equal to the host's own, and where to resume."""

    def __init__(self, cache: Path | None = None, identity: str = ""):
        self.cache = cache
        self.identity = identity  # the ssh destination and command the rows came from
        self.payloads: dict[str, dict[str, Any]] = {}
        self.cursor = ""
        self.supported: bool | None = None  # None until the host has answered once

    def reset(self) -> None:
        self.payloads, self.cursor = {}, ""

    def accept(self, payloads: dict[str, dict[str, Any]], cursor: str) -> None:
        self.payloads, self.cursor = payloads, cursor
        self.save()

    def load(self) -> bool:
        if self.cache is None:
            return False
        try:
            value = read_json(self.cache)
        except (OSError, ValueError):
            return False
        if (not isinstance(value, dict) or value.get("schema_version") != CACHE_SCHEMA
                or value.get("identity") != self.identity or not isinstance(value.get("rows"), list)):
            return False
        rows = [row for row in value["rows"] if isinstance(row, dict) and "key" in row]
        self.payloads = {str(row["key"]): row for row in rows}
        self.cursor = str(value.get("cursor") or "")
        return True

    def save(self) -> None:
        if self.cache is None:
            return
        try:
            private_dir(self.cache.parent)
            atomic_json(self.cache, {"schema_version": CACHE_SCHEMA, "identity": self.identity,
                                     "cursor": self.cursor, "rows": list(self.payloads.values())})
        except OSError:
            pass  # A cache that cannot be written only costs the next start a round trip.
