from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from .core import JsonlTail, Metadata, PaneRow, icon_for, visual_state
from .detector import TmuxDetector
from .events import load_events
from .metadata import MetadataResolver, default_command

HOME = Path.home()
STOP = False


def request_stop(*_args):
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
        self.loop_interval = float(os.environ.get("TMUX_AGENT_LOOP_INTERVAL", "1"))
        self.scan_interval = float(os.environ.get("TMUX_AGENT_SCAN_INTERVAL", "3"))
        self.metadata_interval = float(os.environ.get("TMUX_AGENT_METADATA_INTERVAL", "10"))
        self.prompt_grace = float(os.environ.get("TMUX_AGENT_PROMPT_GRACE", "8"))
        self.topic_width = int(os.environ.get("TMUX_AGENT_TOPIC_WIDTH", "38"))
        self.tmux = TmuxClient()
        self.detector = TmuxDetector()
        self.metadata_resolver = MetadataResolver(topic_width=self.topic_width)
        codex_home = Path(os.environ.get("CODEX_HOME", HOME / ".codex"))
        self.history = JsonlTail(codex_home / "history.jsonl")

        self.applied: dict[str, tuple[str, str, str]] = {}
        self.metadata: dict[str, Metadata] = {}
        self.metadata_times: dict[str, float] = {}
        self.prompts: dict[str, dict] = {}
        self.recent_prompt_events: dict[str, float] = {}
        self.semantic: dict[str, tuple[str, str]] = {}
        self.last_agents: dict[str, PaneRow] = {}
        self.last_windows: set[str] = set()
        self.last_scan = 0.0

    def scan(self) -> None:
        self.last_agents = self.detector.scan()
        self.last_windows = self.tmux.windows()
        self.last_scan = time.time()

    def refresh_metadata(self, target: str, row: PaneRow, now: float) -> Metadata:
        metadata = self.metadata_resolver.resolve(row)
        self.metadata[target] = metadata
        self.metadata_times[target] = now
        if metadata.session_id and self.recent_prompt_events.get(metadata.session_id, 0) > now - 5:
            self.prompts[target] = {"at": self.recent_prompt_events[metadata.session_id], "saw_working": False}
        return metadata

    def poll_prompt_events(self, now: float) -> set[str]:
        affected: set[str] = set()
        for item in self.history.poll():
            session_id = item.get("session_id")
            text = item.get("text")
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

    def update_once(self, *, force_metadata: bool = False) -> bool:
        now = time.time()
        if now - self.last_scan >= self.scan_interval:
            self.scan()
        if not self.last_windows:
            self.applied.clear()
            return False

        for target, row in self.last_agents.items():
            if target not in self.last_windows:
                continue
            if (
                force_metadata
                or target not in self.metadata
                or now - self.metadata_times.get(target, 0) >= self.metadata_interval
            ):
                self.refresh_metadata(target, row, now)

        affected = self.poll_prompt_events(now)
        for target in affected:
            row = self.last_agents.get(target)
            if row is not None:
                self.refresh_metadata(target, row, now)

        hook_events = load_events()
        changed = False
        for target, row in self.last_agents.items():
            if target not in self.last_windows:
                continue
            metadata = self.metadata.get(target) or Metadata(row.agent, "", row.subject, "")
            prompt = self.prompts.get(target)
            state = visual_state(row.state, prompt, now, self.prompt_grace)

            hook_event = hook_events.get((row.agent, metadata.session_id)) if metadata.session_id else None
            if hook_event:
                hook_state = hook_event.get("state")
                hook_timestamp = float(hook_event.get("timestamp", 0))
                hook_age = now - hook_timestamp
                prompt_is_newer = prompt is not None and prompt.get("at", 0) > hook_timestamp
                if row.state == "blocked":
                    state = "approval"
                elif prompt_is_newer:
                    # History tailing is the fallback for sessions whose newly
                    # installed hooks have not been trusted/reloaded yet.
                    pass
                elif hook_state == "approval" and row.state != "working":
                    state = "approval"
                elif hook_state == "running":
                    state = "running" if row.state == "working" or hook_age < self.prompt_grace else state
                elif hook_state == "done":
                    state = "running" if row.state == "working" else "done"
                elif hook_state == "ended":
                    state = ""

            if prompt is not None and state in {"done", "approval"}:
                self.prompts.pop(target, None)
            icon = icon_for(state, now)
            title = f"{icon} {metadata.label}".strip()
            values = (state, icon, title)
            if self.applied.get(target) != values:
                self.tmux.set_values(target, state, icon, title)
                self.applied[target] = values
                changed = True

            semantic_value = (state, metadata.label)
            if self.semantic.get(target) != semantic_value:
                print(f"{target}: {title}", flush=True)
                self.semantic[target] = semantic_value

        for target in self.last_windows - set(self.last_agents):
            if target in self.applied:
                self.tmux.clear(target)
                changed = True
            self.drop_target(target)

        for target in list(self.applied):
            if target not in self.last_windows:
                self.drop_target(target)

        if changed:
            self.tmux.refresh()
        return True

    def drop_target(self, target: str) -> None:
        self.applied.pop(target, None)
        self.metadata.pop(target, None)
        self.metadata_times.pop(target, None)
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
    checks = {
        "tmux": bool(default_command(("tmux", "-V"))),
        "systemd-user": subprocess.run(
            ("systemctl", "--user", "is-system-running"),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        ).returncode
        in {0, 1},
    }
    for name, ok in checks.items():
        print(f"{name}: {'ok' if ok else 'missing'}")
    return 0 if all(checks.values()) else 1


def entrypoint(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Safe tmux AI-agent title updater")
    parser.add_argument("--once", action="store_true", help="update tmux options once and exit")
    parser.add_argument("--check", action="store_true", help="check runtime dependencies")
    args = parser.parse_args(argv)
    if args.check:
        return check_dependencies()
    return IndicatorDaemon().run(once=args.once)


if __name__ == "__main__":
    raise SystemExit(entrypoint())
