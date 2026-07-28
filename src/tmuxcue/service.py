from __future__ import annotations

import argparse
import os
from pathlib import Path
import signal
import subprocess
import time

from . import __version__
from .core import JsonlTail, Metadata, PaneRow, TerminalRow, icon_for, visual_state
from .detector import TmuxDetector
from .events import load_events
from .metadata import AgentRow, MetadataResolver, default_command
from .terminal import TerminalDetector, TerminalTitleWriter

HOME = Path.home()
STOP = False


def request_stop(*_args) -> None:
    global STOP
    STOP = True


signal.signal(signal.SIGTERM, request_stop)
signal.signal(signal.SIGINT, request_stop)


class TmuxClient:
    def command(self, *args: str, timeout: float = 5) -> str:
        return default_command(("tmux",) + args, timeout)

    def windows(self) -> set[str]:
        output = self.command("list-windows", "-a", "-F", "#{session_name}:#{window_index}")
        return {line for line in output.splitlines() if line}

    def set_values(self, target: str, state: str, icon: str, title: str) -> None:
        subprocess.run(
            (
                "tmux",
                "set-option", "-w", "-t", target, "@agent_state", state,
                ";", "set-option", "-w", "-t", target, "@agent_icon", icon,
                ";", "set-option", "-w", "-t", target, "@agent_title", title,
            ),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )

    def clear(self, target: str) -> None:
        for option in ("@agent_state", "@agent_icon", "@agent_title"):
            subprocess.run(
                ("tmux", "set-option", "-wu", "-t", target, option),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
            )

    def refresh(self) -> None:
        self.command("refresh-client", "-S")


class IndicatorDaemon:
    def __init__(self) -> None:
        self.loop_interval = float(os.environ.get("TMUXCUE_LOOP_INTERVAL", "1"))
        self.scan_interval = float(os.environ.get("TMUXCUE_SCAN_INTERVAL", "3"))
        self.metadata_interval = float(os.environ.get("TMUXCUE_METADATA_INTERVAL", "10"))
        self.runtime_interval = float(os.environ.get("TMUXCUE_RUNTIME_INTERVAL", "3"))
        self.prompt_grace = float(os.environ.get("TMUXCUE_PROMPT_GRACE", "8"))
        self.topic_width = int(os.environ.get("TMUXCUE_TOPIC_WIDTH", "38"))
        self.terminal_titles = os.environ.get("TMUXCUE_TERMINAL_TITLES", "1") != "0"

        self.tmux = TmuxClient()
        self.tmux_detector = TmuxDetector()
        self.terminal_detector = TerminalDetector()
        self.terminal_writer = TerminalTitleWriter()
        self.metadata_resolver = MetadataResolver(topic_width=self.topic_width)
        codex_home = Path(os.environ.get("CODEX_HOME", HOME / ".codex"))
        self.history = JsonlTail(codex_home / "history.jsonl")

        self.rows: dict[str, AgentRow] = {}
        self.tmux_windows: set[str] = set()
        self.applied: dict[str, tuple[str, str, str]] = {}
        self.metadata: dict[str, Metadata] = {}
        self.metadata_times: dict[str, float] = {}
        self.runtime_states: dict[str, str | None] = {}
        self.runtime_times: dict[str, float] = {}
        self.prompts: dict[str, dict] = {}
        self.recent_prompt_events: dict[str, float] = {}
        self.semantic: dict[str, tuple[str, str]] = {}
        self.last_scan = 0.0

    def scan(self) -> None:
        tmux_rows = self.tmux_detector.scan()
        terminal_rows = (
            self.terminal_detector.scan(self.tmux_detector.pane_ttys)
            if self.terminal_titles
            else {}
        )
        self.rows = {**tmux_rows, **terminal_rows}
        self.tmux_windows = self.tmux.windows()
        self.last_scan = time.time()

    def refresh_metadata(self, target: str, row: AgentRow, now: float) -> Metadata:
        metadata = self.metadata_resolver.resolve(row)
        self.metadata[target] = metadata
        self.metadata_times[target] = now
        if metadata.session_id and self.recent_prompt_events.get(metadata.session_id, 0) > now - 5:
            self.prompts[target] = {
                "at": self.recent_prompt_events[metadata.session_id],
                "saw_working": False,
            }
        return metadata

    def poll_prompt_events(self, now: float) -> set[str]:
        affected: set[str] = set()
        for item in self.history.poll():
            session_id, text = item.get("session_id"), item.get("text")
            if not session_id or not text:
                continue
            self.recent_prompt_events[session_id] = now
            for target, metadata in self.metadata.items():
                if metadata.session_id == session_id:
                    self.prompts[target] = {"at": now, "saw_working": False}
                    self.metadata_times[target] = 0.0
                    affected.add(target)
                    print(f"{target}: prompt submitted", flush=True)
        self.recent_prompt_events = {
            session_id: timestamp
            for session_id, timestamp in self.recent_prompt_events.items()
            if timestamp > now - 30
        }
        return affected

    def raw_state(self, target: str, row: AgentRow, metadata: Metadata, now: float) -> str:
        if isinstance(row, PaneRow):
            return row.state
        if now - self.runtime_times.get(target, 0) >= self.runtime_interval:
            self.runtime_states[target] = self.metadata_resolver.runtime_state(metadata)
            self.runtime_times[target] = now
        return self.runtime_states.get(target) or row.state

    def resolve_state(
        self,
        row: AgentRow,
        raw_state: str,
        prompt: dict | None,
        hook_event: dict | None,
        now: float,
    ) -> str:
        state = visual_state(raw_state, prompt, now, self.prompt_grace)
        if not hook_event:
            return state

        hook_state = hook_event.get("state")
        hook_timestamp = float(hook_event.get("timestamp", 0))
        hook_age = now - hook_timestamp
        prompt_is_newer = prompt is not None and prompt.get("at", 0) > hook_timestamp
        if raw_state == "blocked":
            return "approval"
        if prompt_is_newer:
            return state
        if hook_state == "approval":
            return "approval" if raw_state != "working" else state
        if hook_state == "running":
            if raw_state == "working":
                return "running"
            if isinstance(row, TerminalRow) and self.runtime_states.get(f"terminal:{row.pid}") is None:
                return "running"
            return "running" if hook_age < self.prompt_grace else state
        if hook_state == "done":
            return "running" if raw_state == "working" else "done"
        if hook_state == "ended":
            return ""
        return state

    def apply(self, target: str, row: AgentRow, values: tuple[str, str, str]) -> bool:
        state, icon, title = values
        if isinstance(row, TerminalRow):
            return self.terminal_writer.write(row, title)
        self.tmux.set_values(target, state, icon, title)
        return True

    def update_once(self, *, force_metadata: bool = False) -> bool:
        now = time.time()
        if now - self.last_scan >= self.scan_interval:
            self.scan()
        if not self.rows:
            self.applied.clear()
            return False

        for target, row in self.rows.items():
            if (
                force_metadata
                or target not in self.metadata
                or now - self.metadata_times.get(target, 0) >= self.metadata_interval
            ):
                self.refresh_metadata(target, row, now)

        for target in self.poll_prompt_events(now):
            row = self.rows.get(target)
            if row is not None:
                self.refresh_metadata(target, row, now)

        hook_events = load_events()
        tmux_changed = False
        for target, row in self.rows.items():
            metadata = self.metadata.get(target) or Metadata(row.agent, "", row.subject, "")
            prompt = self.prompts.get(target)
            raw_state = self.raw_state(target, row, metadata, now)
            hook_event = (
                hook_events.get((row.agent, metadata.session_id))
                if metadata.session_id
                else None
            )
            state = self.resolve_state(row, raw_state, prompt, hook_event, now)
            if prompt is not None and state in {"done", "approval"}:
                self.prompts.pop(target, None)

            icon = icon_for(state, now)
            title = f"{icon} {metadata.label}".strip()
            values = (state, icon, title)
            if self.applied.get(target) != values and self.apply(target, row, values):
                self.applied[target] = values
                tmux_changed |= isinstance(row, PaneRow)

            semantic_value = (state, metadata.label)
            if self.semantic.get(target) != semantic_value:
                print(f"{target}: {title}", flush=True)
                self.semantic[target] = semantic_value

        for target in self.tmux_windows - set(self.rows):
            if target in self.applied:
                self.tmux.clear(target)
                tmux_changed = True
            self.drop_target(target)

        for target in list(self.applied):
            if target not in self.rows:
                self.drop_target(target)

        if tmux_changed:
            self.tmux.refresh()
        return True

    def drop_target(self, target: str) -> None:
        self.applied.pop(target, None)
        self.metadata.pop(target, None)
        self.metadata_times.pop(target, None)
        self.runtime_states.pop(target, None)
        self.runtime_times.pop(target, None)
        self.prompts.pop(target, None)
        self.semantic.pop(target, None)

    def run(self, once: bool = False) -> int:
        while not STOP:
            available = self.update_once(force_metadata=once)
            if once:
                return 0 if available else 1
            time.sleep(self.loop_interval)
        return 0


def check_dependencies() -> int:
    systemd_ok = subprocess.run(
        ("systemctl", "--user", "is-system-running"),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    ).returncode in {0, 1}
    tmux_ok = bool(default_command(("tmux", "-V")))
    print(f"systemd-user: {'ok' if systemd_ok else 'missing'}")
    print(f"tmux: {'ok' if tmux_ok else 'optional (not installed)'}")
    return 0 if systemd_ok else 1


def entrypoint(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Safe status titles for Codex and Claude terminals"
    )
    parser.add_argument("--once", action="store_true", help="update titles once and exit")
    parser.add_argument("--check", action="store_true", help="check runtime dependencies")
    parser.add_argument("--version", action="version", version=f"TmuxCue {__version__}")
    args = parser.parse_args(argv)
    if args.check:
        return check_dependencies()
    return IndicatorDaemon().run(once=args.once)


if __name__ == "__main__":
    raise SystemExit(entrypoint())
