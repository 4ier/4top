"""Synthetic interactive agent used only in isolated tests; never calls a model."""
import hashlib
import json
import os
import select
import signal
import subprocess
import sys
import uuid
from pathlib import Path

agent = Path(sys.argv[0]).name
args = sys.argv[1:]
if "--help" in args:
    print("fake interactive " + agent + " resume --resume --session --session-id --model --fail")
    raise SystemExit(0)
if "--version" in args:
    print("fake-" + agent + " 0.0-test")
    raise SystemExit(0)


def option(name):
    return args[args.index(name) + 1] if name in args else None


native = option("--session-id") or option("--resume")
if agent == "codex" and "resume" in args:
    native = args[args.index("resume") + 1]
if agent == "pi" and option("--session"):
    with open(option("--session"), encoding="utf-8") as handle:
        native = json.loads(handle.readline())["id"]
native = native or str(uuid.uuid4())
root_key = {"codex": "CODEX_HOME", "claude": "CLAUDE_CONFIG_DIR", "pi": "PI_CODING_AGENT_DIR"}[agent]
root_default = Path.home() / {"codex": ".codex", "claude": ".claude", "pi": ".pi/agent"}[agent]
root = Path(os.environ.get(root_key, str(root_default)))
now = "2026-09-24T00:00:00Z"
text = os.environ.get("FAKE_TITLE", "verify continuity 中文")
if agent == "codex":
    source = root / "sessions/2026/09/24" / ("rollout-2026-09-24-" + native + ".jsonl")
    records = [{"type": "session_meta", "payload": {"id": native, "cwd": os.getcwd(), "timestamp": now}},
               {"type": "response_item", "payload": {"role": "user", "content": [{"type": "input_text", "text": text}]}}]
elif agent == "claude":
    source = root / "projects/test" / (native + ".jsonl")
    records = [{"type": "user", "sessionId": native, "cwd": os.getcwd(), "timestamp": now,
                "message": {"role": "user", "content": text}}]
else:
    source = root / "sessions/test" / (native + ".jsonl")
    records = [{"type": "session", "id": native, "cwd": os.getcwd(), "timestamp": now},
               {"type": "message", "message": {"role": "user", "content": [{"type": "text", "text": text}]}}]
source.parent.mkdir(parents=True, exist_ok=True)
if not source.exists():
    source.write_text("".join(json.dumps(row) + "\n" for row in records), encoding="utf-8")
reports = Path(os.environ["FAKE_REPORTS"])
reports.mkdir(parents=True, exist_ok=True)
report_file = reports / (str(os.getpid()) + ".json")
identity = uuid.uuid4().hex
report = {"agent": agent, "pid": os.getpid(), "nonce": identity, "count": 0,
          "native_id": native, "cwd": os.getcwd(), "argv": args, "source": str(source),
          "term": os.environ.get("TERM"),
          "canary_hash": hashlib.sha256(os.environ.get("TEST_CANARY", "").encode()).hexdigest()}
child = None
if "--child" in args:
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    report["child_pid"] = child.pid


def save():
    temp = report_file.with_suffix(".tmp")
    temp.write_text(json.dumps(report), encoding="utf-8")
    temp.replace(report_file)


def stop(signum, frame):
    if child:
        child.terminate()
    raise SystemExit(0)


signal.signal(signal.SIGHUP, stop)
signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGWINCH, lambda s, f: print("RESIZED", flush=True))
save()
print(f"FAKE {agent} PID={os.getpid()} NONCE={identity}", flush=True)
if "--fail" in args:
    print("INTENTIONAL FAILURE 7", flush=True)
    raise SystemExit(7)
while True:
    report["count"] += 1
    save()
    print(f"COUNT={report['count']}", flush=True)
    readable, _, _ = select.select([sys.stdin], [], [], 0.2)
    if readable:
        line = sys.stdin.readline()
        if not line or line.strip() == "exit":
            stop(0, None)
        print("ECHO " + line.rstrip(), flush=True)
