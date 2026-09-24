#!/usr/bin/env python3
"""Render the actual Textual app with synthetic data into a self-contained SVG."""
from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

from fourtop.app import FourtopApp
from fourtop.services import DemoManager


async def export(destination):
    app = FourtopApp(DemoManager())
    async with app.run_test(size=(110, 28)) as pilot:
        await pilot.pause(.5)
        content = app.export_screenshot(title="4top · DEMO · synthetic data")
        # Rich includes optional CDN font faces; the published demo stays offline.
        content = re.sub(r"@font-face\s*\{.*?\}", "", content, flags=re.S)
        content = content.replace("font-family: Fira Code, monospace;",
                                  'font-family: "Noto Sans Mono CJK SC", "DejaVu Sans Mono", monospace;')
        content = "\n".join(line.rstrip() for line in content.splitlines()) + "\n"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")
    print(destination)


if __name__ == "__main__":
    asyncio.run(export(Path(sys.argv[1] if len(sys.argv) > 1 else "docs/demo/demo.svg")))
