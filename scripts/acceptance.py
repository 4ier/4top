#!/usr/bin/env python3
"""Run credential-free acceptance tests and record explicit evidence boundaries."""
from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import platform
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("acceptance-output"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    binary = shutil.which("tmux")
    if not binary:
        raise SystemExit("tmux is required; acceptance must not silently skip runtime tests")
    version = subprocess.run([binary, "-V"], check=True, text=True, capture_output=True).stdout.strip()
    env = {**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "FOURTOP_TEST_REQUIRE_TMUX": "1"}
    started = time.monotonic()
    xml_file = args.output.resolve() / "tests.xml"
    with (args.output / "tests.log").open("w") as log:
        test = subprocess.run([sys.executable, "-m", "pytest", "-p", "pytest_asyncio.plugin",
                               "-v", "--junitxml=" + str(xml_file)], env=env, stdout=log,
                              stderr=subprocess.STDOUT, cwd=Path(__file__).resolve().parents[1])
    suites = ET.parse(xml_file).getroot().iter("testsuite") if xml_file.exists() else []
    counts = {name: 0 for name in ("tests", "failures", "errors", "skipped")}
    for suite in suites:
        for name in counts:
            counts[name] += int(suite.get(name, "0"))
    summary = {"python": platform.python_version(), "os": platform.system(),
               "machine": platform.machine(), "tmux": version,
               "textual": importlib.metadata.version("textual"), "counts": counts,
               "returncode": test.returncode, "seconds": round(time.monotonic() - started, 3),
               "real_tmux": True, "real_pty": True, "native_agents": "synthetic fixtures only",
               "environment_scope": "this execution host only; no other machine is certified",
               "acceptance_scope": "automated runtime/UI safety; not native CLI compatibility certification"}
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))
    raise SystemExit(test.returncode or bool(counts["skipped"]))


if __name__ == "__main__":
    main()
