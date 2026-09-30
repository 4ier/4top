"""What an agent kept on this host is waiting for, read from its screen.

A transcript cannot say that an agent is blocked on the person: a permission prompt
is drawn, not written, so such a session looked "working" and then "stopped". An
agent kept on its host (fourtop.resident) has a screen tmux can read, and each CLI
draws its prompts in a fixed shape. Each shape is one entry below, matched against
the bottom of the screen and tested against screens captured from the real CLIs
(tests/fixtures/screens). Nothing is inferred beyond those shapes, and no model is
asked.

An entry also says which keys answer it, so `approve` and `deny` press exactly what
the prompt on screen offers, and nothing when no such prompt is there.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

TAIL = 30  # lines from the bottom of the screen that are read
FOOTER = 3  # a prompt's footer is among its last non-blank lines


@dataclass(frozen=True)
class Prompt:
    name: str
    agent: str
    kind: str  # "permission": approve/deny answer it; "question": the person answers
    body: tuple[str, ...]  # patterns that match lines in this order
    footer: str  # the prompt's own last line, so text quoted in the conversation is not it
    approve: tuple[str, ...] = ()  # tmux key names
    deny: tuple[str, ...] = ()


PROMPTS = (
    # Patterns match line starts only: agents wrap their text to the window, and a
    # phone's window is narrow.
    # Claude Code 2.1: "Do you want to proceed?" / "Do you want to create w.txt?" and so
    # on, numbered options with "1. Yes" first. "1" chooses it; Esc cancels (and tells
    # Claude the person declined).
    Prompt("claude-permission", "claude", "permission",
           (r"^\s*Do you want to\b", r"^\s*(?:❯\s*)?1\.\s+Yes\b"),
           r"^\s*Esc to cancel\b", approve=("1",), deny=("Escape",)),
    # Starting in a directory Claude has not seen asks whether to trust it. The cursor
    # starts on "No, exit"; Down selects "Yes, I trust this folder". Claude wraps its
    # text to the window, and a phone's window is narrow, so only lines too short to
    # wrap are matched.
    Prompt("claude-trust", "claude", "permission",
           (r"^\s*❯\s*No, exit\s*$", r"^\s*Yes, I trust this folder\s*$"),
           r"^\s*Enter to confirm\b", approve=("Down", "Enter"), deny=("Escape",)),
    # AskUserQuestion: numbered choices the person picks from. Not a permission.
    Prompt("claude-question", "claude", "question", (r"^\s*(?:❯\s*)?1\.\s+\S",),
           r"^\s*Enter to select\b.*Esc to cancel"),
    # Codex 0.157/0.158: "Would you like to run the following command?" / "... make the
    # following edits?", with "1. Yes, proceed (y)". "y" is its shortcut; Esc declines.
    Prompt("codex-approval", "codex", "permission",
           (r"^\s*Would you like to\b", r"^\s*(?:›\s*)?1\.\s+Yes, proceed \(y\)"),
           r"^\s*Press enter to confirm\b", approve=("y",), deny=("Escape",)),
    # Codex asks to trust a new folder before it starts; Enter takes the highlighted
    # "Trust and continue".
    Prompt("codex-trust", "codex", "permission",
           (r"^\s*Trust this folder\?", r"^\s*›\s*1\.\s+Trust and continue"),
           r"^\s*enter continue · esc\b", approve=("Enter",), deny=("Escape",)),
    # Pi asks nothing: it has no permission prompts of its own, so no entry.
)


def _compiled():
    return [(prompt, [re.compile(p) for p in prompt.body], re.compile(prompt.footer))
            for prompt in PROMPTS]


_PATTERNS = _compiled()


def detect(screen: str) -> Prompt | None:
    """The prompt at the bottom of this screen, or None."""
    lines = screen.splitlines()
    while lines and not lines[-1].strip():
        lines.pop()
    lines = lines[-TAIL:]
    footer = [line for line in lines if line.strip()][-FOOTER:]
    for prompt, body, end in _PATTERNS:
        if not any(end.search(line) for line in footer):
            continue
        at = 0
        for pattern in body:
            at = next((i + 1 for i in range(at, len(lines)) if pattern.search(lines[i])), -1)
            if at < 0:
                break
        if at >= 0:
            return prompt
    return None


def attention(screen: str) -> str:
    """"permission", "question" or "" for a screen."""
    prompt = detect(screen)
    return prompt.kind if prompt else ""
