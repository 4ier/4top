"""The tmux layout: the session list on the left, agents on the right.

4top runs its own tmux server (socket name ``4top``), so nothing here touches a
tmux the user already runs. The first window holds two panes: the panel and the
stage. Every opened session runs in a window of its own in the background, and
showing one swaps its pane onto the stage, so switching sessions never stops one.

Which sessions are open is asked of tmux each time and never recorded. A pane is
tagged with the session it runs, and that tag is the only state. This is what the
earlier runtime got wrong: it kept its own records of panes, exit codes and
zombies, and they drifted from what was actually running.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .errors import Dependency, Unavailable

SOCKET = "4top"  # FOURTOP_TMUX_SOCKET overrides it, so tests never meet a real layout
SESSION = "4top"
MARKER = "FOURTOP_WORKSPACE"
KEY = "@fourtop-key"
PANEL = "@fourtop-panel"
STAGE = "@fourtop-stage"
# Below this width two panes are too cramped: the focused one is shown alone.
NARROW = 100
PLACEHOLDER = ("printf '\\n\\n    Enter on a session opens it here.\\n\\n"
               "    Alt-Left  the list      Alt-Right  the agent\\n\\n"
               "    Sessions you open keep running when you switch.\\n'; exec tail -f /dev/null")

CONF = """\
# Written by 4top for its own tmux server; your own tmux configuration is untouched.
set -g status off
set -g mouse on
set -g escape-time 10
set -g focus-events on
set -g history-limit 50000
set -g default-terminal "{terminal}"
set -as terminal-features ",*:RGB"
# Modified keys such as Shift-Enter reach the agent, including a host's own tmux
# nested in a pane. -q: an older tmux without these options still reads the rest.
set -gq extended-keys on
set -gq extended-keys-format csi-u
set -g pane-border-lines single
set -g pane-border-style "fg=colour238"
set -g pane-active-border-style "fg=colour39"
set -g set-titles on
set -g set-titles-string "4top"
# Alt-Left / Alt-Right move between the list and the agent. On a narrow screen the
# focused pane fills the window.
bind -n M-Left  if -F "#{{window_zoomed_flag}}" "resize-pane -Z" \\; select-pane -t :.0 \\; if -F "#{{e|<:#{{window_width}},{narrow}}}" "resize-pane -Z"
bind -n M-Right if -F "#{{window_zoomed_flag}}" "resize-pane -Z" \\; select-pane -t :.1 \\; if -F "#{{e|<:#{{window_width}},{narrow}}}" "resize-pane -Z"
"""


def socket(env: dict[str, str]) -> str:
    return env.get("FOURTOP_TMUX_SOCKET") or SOCKET


def tmux_binary(env: dict[str, str]) -> str | None:
    return shutil.which("tmux", path=env.get("PATH", os.defpath))


def _has_terminfo(name: str) -> bool:
    try:
        return subprocess.run(["infocmp", name], capture_output=True).returncode == 0
    except OSError:
        pass
    # No infocmp (Termux ships the entries without it): look where ncurses looks.
    # Directories are named by first letter, or by its hex code on macOS.
    roots = [os.environ.get("TERMINFO"), str(Path.home() / ".terminfo"),
             os.path.join(os.environ.get("PREFIX", "/usr"), "share/terminfo"),
             "/usr/share/terminfo", "/usr/lib/terminfo", "/lib/terminfo", "/etc/terminfo"]
    return any(root and (Path(root, name[0], name).is_file()
                         or Path(root, f"{ord(name[0]):x}", name).is_file()) for root in roots)


def terminal() -> str:
    for name in ("tmux-256color", "screen-256color"):
        if _has_terminfo(name):
            return name
    return "screen"


def enter(config, args: list[str]) -> None:
    """Run the panel inside 4top's tmux server, attaching to it if it already runs.

    Returns only when the layout does not apply: plain layout, already inside, or no
    tmux under ``layout = "auto"``. Otherwise this process becomes the tmux client.
    """
    env = dict(config.environment)
    if config.layout == "plain" or env.get(MARKER):
        return
    tmux = tmux_binary(env)
    if tmux is None:
        if config.layout == "tmux":
            raise Dependency("ui.layout is tmux, but tmux is not installed")
        return
    conf = Path(config.state_dir) / "tmux.conf"
    conf.write_text(CONF.format(terminal=terminal(), narrow=NARROW), encoding="utf-8")
    env.pop("TMUX", None)  # started from inside another tmux: nest, on a separate server
    panel = ["env", f"{MARKER}=1", sys.executable, "-m", "fourtop", *args]
    revive(tmux, socket(env), panel, env)
    os.execvpe(tmux, [tmux, "-L", socket(env), "-f", str(conf), "new-session", "-A", "-s", SESSION,
                      "-n", "4top", "--", *panel], env)


def revive(tmux: str, name: str, panel: list[str], env: dict[str, str]) -> None:
    """Bring the list back into a layout that outlived it.

    Agents keep running after the panel exits (an upgrade, a crash), and attaching
    again would show them without the list. The panel's pane is kept when it exits,
    so it is started again in place; a layout whose panel pane is gone entirely
    gets a new window with a panel of its own.
    """
    def run(*args):
        return subprocess.run([tmux, "-L", name, *args], capture_output=True, text=True, env=env)
    listed = run("list-panes", "-s", "-t", SESSION, "-F", f"#{{pane_id}}\t#{{{PANEL}}}\t#{{pane_dead}}")
    if listed.returncode != 0:
        return  # no layout yet: new-session starts one
    panes = [line.split("\t") for line in listed.stdout.splitlines()]
    mine = [pane for pane in panes if len(pane) == 3 and pane[1] == "1"]
    if any(dead == "0" for _, _, dead in mine):
        return
    if mine:
        run("respawn-pane", "-k", "-t", mine[0][0], "--", *panel)
        run("select-window", "-t", mine[0][0])
    else:
        run("new-window", "-t", f"{SESSION}:", "-n", "4top", "--", *panel)


@dataclass(frozen=True)
class Pane:
    id: str
    key: str
    dead: bool
    status: int | None


class Workspace:
    """The running layout, seen from the panel pane inside it."""

    def __init__(self, tmux: str, panel: str, env: dict[str, str]):
        self.tmux, self.panel, self.env = tmux, panel, env

    @classmethod
    def current(cls, env: dict[str, str]) -> Workspace | None:
        tmux = tmux_binary(env)
        if tmux and env.get(MARKER) and env.get("TMUX") and env.get("TMUX_PANE"):
            return cls(tmux, env["TMUX_PANE"], env)
        return None

    def run(self, *args: str) -> str:
        try:
            result = subprocess.run([self.tmux, "-L", socket(self.env), *args], capture_output=True,
                                    text=True, env=self.env, timeout=10)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise Unavailable(f"tmux: {type(exc).__name__}") from None
        if result.returncode != 0:
            raise Unavailable("tmux: " + (result.stderr.strip() or f"exit {result.returncode}"))
        return result.stdout

    def _window_panes(self) -> list[str]:
        return self.run("list-panes", "-t", self.panel, "-F", "#{pane_id}").split()

    def stage(self) -> str:
        others = [pane for pane in self._window_panes() if pane != self.panel]
        if others:
            return others[0]
        # The stage went missing (its agent was killed from outside, or an older
        # panel left it behind): bring the waiting placeholder back rather than
        # starting another one, so they never pile up in background windows.
        spare = self._placeholder()
        if spare:
            self.run("join-pane", "-h", "-d", "-s", spare, "-t", self.panel)
            return spare
        pane = self.run("split-window", "-h", "-d", "-t", self.panel, "-P", "-F", "#{pane_id}",
                        PLACEHOLDER).strip()
        self.run("set-option", "-p", "-t", pane, STAGE, "1")
        return pane

    def tidy(self) -> None:
        """Keep one placeholder: the one on the stage, or the one waiting for it."""
        stage = self.stage()
        out = self.run("list-panes", "-a", "-F", f"#{{pane_id}}\t#{{{STAGE}}}")
        spares = [pane for pane, tag in (line.split("\t") for line in out.splitlines())
                  if tag == "1" and pane != stage]
        on_stage = self.run("display-message", "-p", "-t", stage, f"#{{{STAGE}}}").strip() == "1"
        for pane in spares[0 if on_stage else 1:]:
            self.run("kill-pane", "-t", pane)

    def ensure_layout(self, width: int) -> None:
        # Mark the list's pane and keep it when it exits, so `4top` can revive it.
        self.run("set-option", "-p", "-t", self.panel, PANEL, "1")
        self.run("set-option", "-p", "-t", self.panel, "remain-on-exit", "on")
        self.tidy()
        self.fit(width)
        if width < NARROW:
            self.focus_panel()  # a narrow screen starts with the list alone

    def fit(self, width: int) -> None:
        """Give the list a readable width and the agent the rest.

        Not while a pane is zoomed: resizing a pane unzooms its window in tmux, and
        zooming the list (to read a preview) is itself the resize that calls this.
        """
        zoomed, window = self.run("display-message", "-p", "-t", self.panel,
                                  "#{window_zoomed_flag} #{window_width}").split()
        if zoomed == "1":
            return
        # The window's width, not the list's: after the screen rotates, tmux scales
        # the list down with it (182 -> 113 columns left it 25 wide), and judged by
        # its own width the list looked like a narrow screen and was never widened.
        width = int(window)
        if width >= NARROW:
            self.run("resize-pane", "-t", self.panel, "-x", str(max(40, min(60, width * 2 // 5))))

    def panes(self) -> dict[str, Pane]:
        out = self.run("list-panes", "-a", "-F",
                       f"#{{pane_id}}\t#{{{KEY}}}\t#{{pane_dead}}\t#{{pane_dead_status}}")
        found = {}
        for line in out.splitlines():
            pane, key, dead, status = (line.split("\t") + ["", "", ""])[:4]
            if key:
                found[key] = Pane(pane, key, dead == "1", int(status) if status.isdigit() else None)
        return found

    def _placeholder(self) -> str | None:
        out = self.run("list-panes", "-a", "-F", f"#{{pane_id}}\t#{{{STAGE}}}")
        return next((pane for pane, tag in (line.split("\t") for line in out.splitlines())
                     if tag == "1"), None)

    def _zoom(self, pane: str) -> None:
        window = self.run("display-message", "-p", "-t", self.panel,
                          "#{window_zoomed_flag} #{window_width}").split()
        if window and window[0] == "1":
            self.run("resize-pane", "-Z", "-t", self.panel)
        self.run("select-pane", "-t", pane)
        if len(window) > 1 and window[1].isdigit() and int(window[1]) < NARROW:
            self.run("resize-pane", "-Z", "-t", pane)

    def show(self, pane: str) -> None:
        stage = self.stage()
        if pane != stage:
            self.run("swap-pane", "-d", "-s", pane, "-t", stage)
        self._zoom(pane)

    def zoom_panel(self, on: bool) -> None:
        """Give the list the whole window (a preview to read) or take it back."""
        zoomed, width = self.run("display-message", "-p", "-t", self.panel,
                                 "#{window_zoomed_flag} #{window_width}").split()
        if on and zoomed == "0":
            self.run("resize-pane", "-Z", "-t", self.panel)
        elif not on and zoomed == "1" and int(width) >= NARROW:
            self.run("resize-pane", "-Z", "-t", self.panel)  # a narrow screen stays zoomed

    def focus_panel(self) -> None:
        self._zoom(self.panel)

    def open(self, key: str, argv: list[str], cwd: str, env: dict[str, str], name: str) -> str:
        """Show the session's pane, starting it in a background window if needed."""
        existing = self.panes().get(key)
        if existing and not existing.dead:
            self.show(existing.id)
            return existing.id
        if existing:
            self.run("kill-pane", "-t", existing.id)
        changed = [f"{k}={v}" for k, v in env.items()
                   if os.environ.get(k) != v and k not in (MARKER, "TMUX", "TMUX_PANE")]
        options = [item for pair in (("-e", value) for value in changed) for item in pair]
        # Tag the pane and keep it after exit *before* the agent runs: an agent that
        # exits at once would otherwise take an untagged pane with it, and its exit
        # would never be reported.
        pane = self.run("new-window", "-d", "-n", name[:24] or "agent", "-P", "-F", "#{pane_id}",
                        "--", "tail", "-f", "/dev/null").strip()
        self.run("set-option", "-p", "-t", pane, KEY, key)
        self.run("set-option", "-p", "-t", pane, "remain-on-exit", "on")
        self.run("respawn-pane", "-k", "-t", pane, "-c", cwd, *options, "--", *argv)
        self.show(pane)
        return pane

    def poll(self) -> tuple[dict[str, Pane], list[Pane]]:
        """What is open, after closing what has exited: one tmux call when nothing
        has exited, which is nearly always."""
        panes = self.panes()
        dead = [pane for pane in panes.values() if pane.dead]
        if dead:
            self.reap(dead)
        return {key: pane for key, pane in panes.items() if not pane.dead}, dead

    def reap(self, dead: list[Pane] | None = None) -> list[Pane]:
        """Close panes whose agent has exited; return them so the panel can say so."""
        if dead is None:
            dead = [pane for pane in self.panes().values() if pane.dead]
        if not dead:
            return []
        stage = self.stage()
        for pane in dead:
            if pane.id == stage:
                placeholder = self._placeholder()
                if placeholder:
                    self.run("swap-pane", "-d", "-s", placeholder, "-t", stage)
                    self.focus_panel()
            self.run("kill-pane", "-t", pane.id)
        return dead

    def detach(self) -> None:
        self.run("detach-client", "-s", SESSION)

    def close_all(self) -> None:
        self.run("kill-server")
