from __future__ import annotations

import os
from pathlib import Path
import re
from typing import Callable

from .core import PaneRow, PRIORITY
from .metadata import default_command
from .processes import ProcessSnapshot, discover_agent_processes

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

GEMINI_APPROVAL_PATTERNS = (
    r"allow execution\?",
    r"allow once",
    r"allow for this session",
    r"action required",
    r"waiting for approval",
    r"permission (?:required|requested)",
)

WORKING_PATTERNS = (
    r"esc to interrupt",
    r"ctrl\+c to interrupt",
)

GEMINI_WORKING_PATTERNS = WORKING_PATTERNS + (
    r"esc to cancel",
    r"\bworking\b",
)

CLAUDE_ACTIVITY = re.compile(
    r"^[·✢✳✶✻✽]\s+(?:"
    r"Waiting for [1-9]\d* background agents? to finish"
    r"|[^\r\n]+(?:…|\.{3})\s+"
    r"\(\d+[hms](?:\s+\d+[hms])*\s+·\s+"
    r"[↑↓]\s*\d+(?:[.,]\d+)*[kKmM]?\s+tokens"
    r"(?:\s+·\s+[^)\r\n]+)?\))$",
    re.I,
)
CLAUDE_PROMPT = re.compile(r"^\s*❯(?:\s|$)")
CLAUDE_SEPARATOR = re.compile(r"^[─━]{3,}$")

SIDE_CONTEXT = re.compile(
    r"^\s*Side from (?:main thread|parent thread(?:\s*\([^)]*\))?)"
    r"(?:\s|·|$)",
    re.I,
)


class TmuxDetector:
    """Detect Codex, Claude, and Gemini panes from native tmux state."""

    def __init__(
        self,
        command: Callable[[tuple[str, ...], float], str] = default_command,
        capture_lines: int = 50,
        proc_root: Path = Path("/proc"),
        uid: int | None = None,
        process_provider: Callable[[Path, int], ProcessSnapshot] = discover_agent_processes,
    ):
        self.command = command
        self.capture_lines = capture_lines
        self.proc_root = Path(proc_root)
        self.uid = os.getuid() if uid is None else uid
        self.process_provider = process_provider
        self.processes: ProcessSnapshot = {}
        self.pane_ttys: set[str] = set()

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
        self.processes = self.process_provider(self.proc_root, self.uid)
        selected: dict[str, PaneRow] = {}
        self.pane_ttys = set()
        for line in output.splitlines():
            fields = line.split("\t", 5)
            if len(fields) != 6:
                continue
            pane, location, current_command, cwd, title, tty = fields
            if tty:
                self.pane_ttys.add(tty)
            agent = self.identify_agent(current_command, tty, self.processes)
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
            process = self.processes.get((tty, agent))
            pid, started_at, cmdline = process or (0, 0, "")
            row = PaneRow(
                pane,
                window,
                agent,
                state,
                cwd,
                self.title_subject(title),
                tty,
                pid,
                started_at,
                cmdline,
                side_active=self.side_conversation_active(screen),
            )
            previous = selected.get(window)
            if previous is None or PRIORITY.get(state, 0) > PRIORITY.get(previous.state, 0):
                selected[window] = row
        return selected

    def identify_agent(
        self, current_command: str, tty: str, processes: ProcessSnapshot | None = None
    ) -> str:
        command = current_command.lower()
        if command in {"codex", "claude", "claude-code", "gemini"}:
            if command.startswith("claude"):
                return "claude"
            return command
        if not tty:
            return ""
        processes = self.processes if processes is None else processes
        candidates = [
            (process[0], agent)
            for (process_tty, agent), process in processes.items()
            if process_tty == tty
        ]
        return max(candidates, default=(0, ""))[1]

    @staticmethod
    def title_is_working(title: str) -> bool:
        stripped = title.lstrip()
        return bool(stripped) and BRAILLE_START <= ord(stripped[0]) <= BRAILLE_END

    @classmethod
    def title_subject(cls, title: str) -> str:
        value = (title or "").strip()
        if value.startswith(("✦", "✋", "◇", "⏲")):
            return ""
        if cls.title_is_working(value):
            value = value[1:].lstrip()
        return value.rsplit(" | ", 1)[0].strip()

    @staticmethod
    def matches_any(patterns: tuple[str, ...], text: str) -> bool:
        return any(re.search(pattern, text, flags=re.I | re.S) for pattern in patterns)

    @staticmethod
    def side_conversation_active(screen: str) -> bool:
        return any(
            SIDE_CONTEXT.search(line)
            for line in (screen or "").splitlines()[-20:]
        )

    @staticmethod
    def claude_footer_is_working(screen: str) -> bool:
        """Read only the status immediately above the current bounded composer."""
        lines = (screen or "").splitlines()[-20:]
        for index in range(len(lines) - 1, -1, -1):
            if not CLAUDE_PROMPT.match(lines[index]):
                continue
            if index < 2 or not CLAUDE_SEPARATOR.fullmatch(lines[index - 1].strip()):
                return False
            if not any(
                CLAUDE_SEPARATOR.fullmatch(line.strip())
                for line in lines[index + 1 :]
            ):
                return False
            candidate = index - 2
            while candidate >= 0 and not lines[candidate].strip():
                candidate -= 1
            return candidate >= 0 and bool(
                CLAUDE_ACTIVITY.fullmatch(lines[candidate].strip())
            )
        return False

    def detect_state(self, agent: str, title: str, screen: str) -> str:
        title_text = title or ""
        screen_lines = (screen or "").splitlines()

        if agent == "codex":
            approval_patterns = CODEX_APPROVAL_PATTERNS
            working_patterns = WORKING_PATTERNS
        elif agent == "claude":
            approval_patterns = CLAUDE_APPROVAL_PATTERNS
            working_patterns = WORKING_PATTERNS
        elif agent == "gemini":
            approval_patterns = GEMINI_APPROVAL_PATTERNS
            working_patterns = GEMINI_WORKING_PATTERNS
        else:
            approval_patterns = ()
            working_patterns = WORKING_PATTERNS

        stripped_title = title_text.lstrip()
        lowered_title = stripped_title.lower()
        approval_tail = "\n".join(screen_lines[-12:])
        has_approval_dialog = self.matches_any(approval_patterns, approval_tail)
        if agent == "gemini":
            if has_approval_dialog:
                return "blocked"
            if (
                stripped_title.startswith(("✦", "✋", "⏲"))
                or "action required" in lowered_title
                or "working" in lowered_title
            ):
                return "working"
        else:
            if "action required" in lowered_title:
                return "blocked"
            if self.title_is_working(title_text):
                return "working"
            if has_approval_dialog:
                return "blocked"
        if agent == "claude" and self.claude_footer_is_working(screen):
            return "working"
        working_tail = "\n".join(screen_lines[-12:])
        if self.matches_any(working_patterns, working_tail):
            return "working"
        return "idle"
