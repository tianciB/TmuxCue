from __future__ import annotations

import re
import subprocess
from typing import Callable

from .core import PaneRow, PRIORITY
from .metadata import default_command

BRAILLE_START = 0x2800
BRAILLE_END = 0x28FF

CODEX_APPROVAL_PATTERNS = (
    r"press enter to confirm or esc to cancel",
    r"enter to submit (?:answer|all)",
    r"allow (?:this )?command\?",
    r"approval (?:is )?required",
    r"requires approval",
    r"do you want to (?:allow|run|execute|continue)",
    r"\[y/n\]",
    r"yes \(y\)",
)

CLAUDE_APPROVAL_PATTERNS = (
    r"do you want to proceed\?",
    r"allow (?:this )?(?:tool|command|action)",
    r"permission (?:required|requested)",
    r"enter to select.*esc to cancel",
    r"elicitation",
)

WORKING_PATTERNS = (
    r"esc to interrupt",
    r"ctrl\+c to interrupt",
)


class TmuxDetector:
    """Independently detect Codex and Claude panes from native tmux state."""

    def __init__(
        self,
        command: Callable[[tuple[str, ...], float], str] = default_command,
        capture_lines: int = 50,
    ):
        self.command = command
        self.capture_lines = capture_lines

    def run(self, *args: str, timeout: float = 5) -> str:
        return self.command(tuple(args), timeout)

    def scan(self) -> dict[str, PaneRow]:
        format_string = "\t".join(
            (
                "#{pane_id}",
                "#{session_name}:#{window_index}.#{pane_index}",
                "#{pane_current_command}",
                "#{pane_current_path}",
                "#{pane_title}",
                "#{pane_tty}",
            )
        )
        output = self.run("tmux", "list-panes", "-a", "-F", format_string)
        selected: dict[str, PaneRow] = {}
        for line in output.splitlines():
            fields = line.split("\t", 5)
            if len(fields) != 6:
                continue
            pane, location, current_command, cwd, title, tty = fields
            agent = self.identify_agent(current_command, tty)
            if not agent:
                continue
            screen = self.run(
                "tmux",
                "capture-pane",
                "-p",
                "-J",
                "-t",
                pane,
                "-S",
                f"-{self.capture_lines}",
                timeout=5,
            )
            state = self.detect_state(agent, title, screen)
            window = location.rsplit(".", 1)[0]
            row = PaneRow(pane, window, agent, state, cwd, "")
            previous = selected.get(window)
            if previous is None or PRIORITY.get(state, 0) > PRIORITY.get(previous.state, 0):
                selected[window] = row
        return selected

    def identify_agent(self, current_command: str, tty: str) -> str:
        command = current_command.lower()
        if command in {"codex", "claude", "claude-code"}:
            return "claude" if command.startswith("claude") else "codex"
        if not tty:
            return ""
        output = self.run("ps", "-t", tty, "-o", "stat=,comm=")
        candidates = []
        for line in output.splitlines():
            fields = line.split(None, 1)
            if len(fields) != 2 or "T" in fields[0]:
                continue
            process = fields[1].lower()
            if process in {"codex", "claude", "claude-code"}:
                score = 1 if "+" in fields[0] else 0
                candidates.append((score, process))
        if not candidates:
            return ""
        process = max(candidates)[1]
        return "claude" if process.startswith("claude") else "codex"

    @staticmethod
    def title_is_working(title: str) -> bool:
        stripped = title.lstrip()
        return bool(stripped) and BRAILLE_START <= ord(stripped[0]) <= BRAILLE_END

    @staticmethod
    def matches_any(patterns: tuple[str, ...], text: str) -> bool:
        return any(re.search(pattern, text, flags=re.I | re.S) for pattern in patterns)

    def detect_state(self, agent: str, title: str, screen: str) -> str:
        title_text = title or ""
        screen_tail = "\n".join((screen or "").splitlines()[-35:])
        combined = f"{title_text}\n{screen_tail}"

        approval_patterns = (
            CODEX_APPROVAL_PATTERNS if agent == "codex" else CLAUDE_APPROVAL_PATTERNS
        )
        if "action required" in title_text.lower() or self.matches_any(approval_patterns, combined):
            return "blocked"
        if self.title_is_working(title_text) or self.matches_any(WORKING_PATTERNS, screen_tail):
            return "working"
        return "idle"
