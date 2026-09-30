"""A fake E2B: the REST API 4top uses, and each sandbox's file API, in-process.

Sandboxes are records with a state; pausing and killing one writes the lifecycle
event E2B writes, with the stretch it ran, so costs are computed from the same
shapes as against the real API (see the event captured in test_e2b.py). Every
request is logged, so a test can say what was *not* asked (a paused sandbox woken,
a sandbox created over budget).
"""
from __future__ import annotations

import json
import threading
import time
import urllib.parse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

KEY = "e2b_fake_key"


def iso(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class FakeE2B:
    def __init__(self, homes: Path):
        self.homes = homes  # each sandbox's /home/user is homes/<sandbox ID>
        self.sandboxes: dict[str, dict] = {}
        self.snapshots: dict[str, str] = {}  # snapshot ID -> sandbox it was taken of
        self.events: list[dict] = []
        self.log: list[tuple[str, str, dict | None]] = []
        self.templates = [{"templateID": "t4top", "aliases": ["4top"], "cpuCount": 2, "memoryMB": 2048}]
        self.max_timeout = 3600  # E2B Hobby: "Timeout cannot be greater than 1 hours"
        self.lock = threading.Lock()
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _answer(self, code, body=None):
                data = b"" if body is None else json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _handle(self, method):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                url = urllib.parse.urlsplit(self.path)
                if url.path == "/files":
                    return self._answer(*fake.upload(self.headers, urllib.parse.parse_qs(url.query), raw))
                if self.headers.get("X-API-KEY") != KEY:
                    return self._answer(401, {"message": "bad key"})
                body = json.loads(raw) if raw and "json" in (self.headers.get("Content-Type") or "") else None
                with fake.lock:
                    fake.log.append((method, url.path, body))
                    code, answer = fake.route(method, url.path, urllib.parse.parse_qs(url.query), body)
                self._answer(code, answer)

            def do_GET(self):
                self._handle("GET")

            def do_POST(self):
                self._handle("POST")

            def do_DELETE(self):
                self._handle("DELETE")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()

    def asked(self, method: str, prefix: str) -> list:
        return [entry for entry in self.log if entry[0] == method and entry[1].startswith(prefix)]

    # ----- state changes, also usable directly by a test ------------------------------

    def event(self, sandbox: dict, kind: str, at: float):
        data = {"sandbox_metadata": dict(sandbox["metadata"])}
        if kind in ("paused", "killed"):
            data["execution"] = {"execution_time": int((at - sandbox["_since"]) * 1000),
                                 "started_at": iso(sandbox["_since"]).split(".")[0] + "Z",
                                 "vcpu_count": sandbox["cpuCount"], "memory_mb": sandbox["memoryMB"]}
        self.events.insert(0, {"type": f"sandbox.lifecycle.{kind}", "timestamp": iso(at),
                               "sandboxId": sandbox["sandboxID"], "eventData": data})

    def add(self, metadata: dict, state="running", started: float | None = None, **extra) -> dict:
        now = time.time()
        self.created = getattr(self, "created", 0) + 1
        sandbox_id = f"sbx{self.created:03d}"
        record = {"sandboxID": sandbox_id, "templateID": "t4top", "state": state,
                  "startedAt": iso(started or now), "endAt": iso(now + 600), "cpuCount": 2,
                  "memoryMB": 2048, "metadata": dict(metadata), "_since": started or now, **extra}
        self.sandboxes[sandbox_id] = record
        self.event(record, "created", started or now)
        (self.homes / sandbox_id / ".ssh").mkdir(parents=True, exist_ok=True)
        return record

    def pause(self, sandbox_id: str, at: float | None = None):
        record = self.sandboxes[sandbox_id]
        record["state"] = "paused"
        self.event(record, "paused", at or time.time())

    # ----- the API --------------------------------------------------------------------

    def public(self, record: dict) -> dict:
        return {key: value for key, value in record.items() if not key.startswith("_")}

    def route(self, method, path, query, body):
        parts = path.strip("/").split("/")
        now = time.time()
        if method == "POST" and path == "/sandboxes":
            if body["timeout"] > self.max_timeout:
                return 400, {"code": 400, "message": "Timeout cannot be greater than 1 hours"}
            record = self.add(body.get("metadata") or {}, _create=body)
            return 201, {"sandboxID": record["sandboxID"], "templateID": body["templateID"],
                         "envdAccessToken": "envd-token", "clientID": "x", "envdVersion": "1"}
        if method == "GET" and path == "/v2/sandboxes":
            wanted = dict(item.split("=", 1) for item in query.get("metadata", [""])[0].split("&") if "=" in item)
            states = query.get("state", ["running,paused"])[0].split(",")
            return 200, [self.public(r) for r in self.sandboxes.values()
                         if r["state"] in states and all(r["metadata"].get(k) == v for k, v in wanted.items())]
        if method == "GET" and path == "/templates":
            return 200, self.templates
        if method == "GET" and path == "/snapshots":
            return 200, [{"snapshotID": s, "names": [s.split(":")[0]]} for s in self.snapshots]
        if method == "DELETE" and parts[0] == "templates":
            name = urllib.parse.unquote(parts[1])
            gone = [s for s in self.snapshots if s.split(":")[0] == name]
            for s in gone:
                del self.snapshots[s]
            return (204, None) if gone else (404, {"message": "no such template"})
        if method == "GET" and parts[:2] == ["events", "sandboxes"]:
            events = [e for e in self.events if len(parts) == 2 or e["sandboxId"] == parts[2]]
            types = query.get("types")
            if types:
                events = [e for e in events if e["type"] in types]
            offset, limit = int(query.get("offset", ["0"])[0]), int(query.get("limit", ["10"])[0])
            return 200, events[offset:offset + limit]
        record = self.sandboxes.get(parts[-1] if parts[0] == "sandboxes" and len(parts) == 2 else
                                    parts[2] if parts[:2] == ["v2", "sandboxes"] else parts[1])
        if record is None:
            return 404, {"message": "sandbox not found"}
        if method == "GET" and parts[0] == "sandboxes":
            return 200, self.public(record)
        if method == "POST" and parts[-1] == "connect":
            if record["state"] == "paused":
                record.update(state="running", _since=now, startedAt=iso(now))
                self.event(record, "resumed", now)
            record["endAt"] = iso(max(now + body["timeout"], datetime.fromisoformat(
                record["endAt"].replace("Z", "+00:00")).timestamp()))
            return 200, self.public(record)
        if method == "POST" and parts[-1] == "pause":
            if record["state"] == "paused":
                return 409, {"message": "already paused"}
            self.pause(record["sandboxID"], now)
            return 204, None
        if method == "POST" and parts[-1] == "snapshots":
            snapshot = f"team/{body['name']}:default"
            self.snapshots[snapshot] = record["sandboxID"]
            self.event(record, "checkpointed", now)
            record["_since"] = now  # E2B counts the rest as a new stretch
            return 201, {"snapshotID": snapshot, "names": [snapshot]}
        if method == "DELETE" and parts[0] == "sandboxes":
            if record["state"] == "running":
                self.event(record, "killed", now)
            else:
                self.event({**record, "_since": now}, "killed", now)
            del self.sandboxes[record["sandboxID"]]
            return 204, None
        return 404, {"message": f"fake E2B has no {method} {path}"}

    def upload(self, headers, query, raw):
        if headers.get("X-Access-Token") != "envd-token":
            return 401, {"message": "bad token"}
        path = query["path"][0]
        boundary = headers["Content-Type"].split("boundary=")[1].encode()
        data = raw.split(b"\r\n\r\n", 1)[1].rsplit(b"\r\n--" + boundary, 1)[0]
        # One sandbox is written to at a time in these tests: the newest.
        home = self.homes / sorted(self.sandboxes)[-1]
        target = home / path.removeprefix("/home/user/")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return 200, [{"path": path}]
