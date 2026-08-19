from __future__ import annotations

import argparse
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from . import __version__
from .core import JsonlTail, Metadata, PaneRow, TerminalRow, icon_for, visual_state
from .detector import TmuxDetector
from .events import EventReader
from .metadata import AgentRow, MetadataResolver, ProcessSnapshot, default_command
from .settings import apply_title_mode, source_dir, title_mode, title_override_since
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

    def managed_windows(self) -> set[str]:
        output = self.command(
            "list-windows",
            "-a",
            "-F",
            "#{session_name}:#{window_index}\t#{@agent_state}\t#{@agent_icon}\t#{@agent_title}",
        )
        managed: set[str] = set()
        for line in output.splitlines():
            fields = line.split("\t", 3)
            if len(fields) == 4 and any(fields[1:]):
                managed.add(fields[0])
        return managed

    @staticmethod
    def _append_command(arguments: list[str], command: tuple[str, ...]) -> None:
        if arguments:
            arguments.append(";")
        arguments.extend(command)

    @staticmethod
    def _run(arguments: list[str], timeout: float = 5) -> bool:
        if not arguments:
            return True
        try:
            result = subprocess.run(
                ("tmux", *arguments),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=timeout,
                check=False,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return False
        return result.returncode == 0

    def set_many(self, updates: list[tuple[str, str, str, str]]) -> bool:
        arguments: list[str] = []
        for target, state, icon, title in updates:
            for option, value in (
                ("@agent_state", state),
                ("@agent_icon", icon),
                ("@agent_title", title),
            ):
                self._append_command(
                    arguments, ("set-option", "-w", "-t", target, option, value)
                )
        return self._run(arguments)

    def set_values(self, target: str, state: str, icon: str, title: str) -> bool:
        return self.set_many([(target, state, icon, title)])

    def clear_many(self, targets: list[str]) -> bool:
        arguments: list[str] = []
        for target in targets:
            for option in ("@agent_state", "@agent_icon", "@agent_title"):
                self._append_command(
                    arguments, ("set-option", "-wqu", "-t", target, option)
                )
        return self._run(arguments)

    def clear(self, target: str) -> bool:
        return self.clear_many([target])

    def refresh(self) -> None:
        self.command("refresh-client", "-S")


class IndicatorDaemon:
    def __init__(self) -> None:
        self.loop_interval = max(
            0.05, float(os.environ.get("TMUXCUE_LOOP_INTERVAL", "1"))
        )
        self.scan_interval = max(
            0.05, float(os.environ.get("TMUXCUE_SCAN_INTERVAL", "3"))
        )
        self.metadata_interval = max(
            0.05, float(os.environ.get("TMUXCUE_METADATA_INTERVAL", "10"))
        )
        self.metadata_retry_interval = max(
            0.05, float(os.environ.get("TMUXCUE_METADATA_RETRY_INTERVAL", "1"))
        )
        self.runtime_interval = max(
            0.05, float(os.environ.get("TMUXCUE_RUNTIME_INTERVAL", "1"))
        )
        self.prompt_grace = max(
            0.0, float(os.environ.get("TMUXCUE_PROMPT_GRACE", "8"))
        )
        self.topic_width = int(os.environ.get("TMUXCUE_TOPIC_WIDTH", "38"))
        self.terminal_titles = title_mode() == "override"
        self.terminal_override_since = title_override_since()
        self.terminal_refresh_interval = max(
            0.05, float(os.environ.get("TMUXCUE_TERMINAL_REFRESH_INTERVAL", "5"))
        )

        self.tmux = TmuxClient()
        self.tmux_detector = TmuxDetector()
        self.terminal_detector = TerminalDetector()
        self.terminal_writer = TerminalTitleWriter()
        self.metadata_resolver = MetadataResolver(topic_width=self.topic_width)
        self.event_reader = EventReader()
        codex_home = Path(os.environ.get("CODEX_HOME", HOME / ".codex"))
        self.history = JsonlTail(codex_home / "history.jsonl")

        self.rows: dict[str, AgentRow] = {}
        self.row_identities: dict[str, tuple] = {}
        self.tmux_windows: set[str] = set()
        self.managed_tmux_windows: set[str] = set()
        self.applied: dict[str, tuple[str, str, str]] = {}
        self.metadata: dict[str, Metadata] = {}
        self.metadata_times: dict[str, float] = {}
        self.runtime_states: dict[str, str | None] = {}
        self.runtime_times: dict[str, float] = {}
        self.runtime_event_times: dict[str, float] = {}
        self.raw_observation_times: dict[str, float] = {}
        self.terminal_write_times: dict[str, float] = {}
        self.prompts: dict[str, dict] = {}
        self.recent_prompt_events: dict[str, float] = {}
        self.semantic: dict[str, tuple[str, str]] = {}
        self.last_scan = 0.0

    @staticmethod
    def row_identity(row: AgentRow) -> tuple:
        if isinstance(row, PaneRow):
            return (
                "tmux", row.pane, row.agent, row.cwd_hint, row.tty, row.pid, row.started_at
            )
        return ("terminal", row.pid, row.agent, row.started_at, row.tty)

    @staticmethod
    def metadata_source_key(row: AgentRow) -> tuple:
        if isinstance(row, PaneRow):
            return (
                "tmux", row.pane, row.agent, row.cwd_hint, row.tty, row.pid, row.started_at
            )
        return ("terminal", row.pid, row.agent, row.started_at)

    def scan(self) -> None:
        scan_started = time.time()
        self.terminal_titles = title_mode() == "override"
        self.terminal_override_since = title_override_since()
        tmux_rows = self.tmux_detector.scan()
        terminal_rows = (
            self.terminal_detector.scan(
                self.tmux_detector.pane_ttys, self.tmux_detector.processes
            )
            if self.terminal_titles
            else {}
        )
        if self.terminal_override_since:
            terminal_rows = {
                target: row
                for target, row in terminal_rows.items()
                if row.started_at >= self.terminal_override_since
            }
        rows = {**tmux_rows, **terminal_rows}
        identities = {target: self.row_identity(row) for target, row in rows.items()}
        for target, identity in identities.items():
            previous = self.row_identities.get(target)
            if previous is not None and previous != identity:
                self.drop_target(target)
        self.rows = rows
        self.row_identities = identities
        self.tmux_windows = self.tmux.windows()
        self.managed_tmux_windows = self.tmux.managed_windows()
        self.last_scan = scan_started

    def _store_metadata(
        self, target: str, metadata: Metadata, now: float
    ) -> Metadata:
        previous = self.metadata.get(target)
        if previous is not None and previous.session_id != metadata.session_id:
            self.runtime_states.pop(target, None)
            self.runtime_times.pop(target, None)
            self.runtime_event_times.pop(target, None)
            self.raw_observation_times.pop(target, None)
        self.metadata[target] = metadata
        self.metadata_times[target] = now
        if (
            metadata.session_id
            and self.recent_prompt_events.get(metadata.session_id, 0)
            > now - max(5.0, self.prompt_grace)
        ):
            self.prompts[target] = {
                "at": self.recent_prompt_events[metadata.session_id],
                "saw_working": False,
            }
        return metadata

    def refresh_metadata(
        self,
        target: str,
        row: AgentRow,
        now: float,
        processes: ProcessSnapshot | None = None,
    ) -> Metadata:
        return self._store_metadata(
            target, self.metadata_resolver.resolve(row, processes=processes), now
        )

    def refresh_metadata_targets(self, targets: set[str], now: float) -> set[str]:
        valid = [target for target in targets if target in self.rows]
        processes = (
            self.metadata_resolver.process_snapshot()
            if any(
                isinstance(self.rows[target], PaneRow) and not self.rows[target].pid
                for target in valid
            )
            else None
        )
        resolved: dict[tuple, Metadata] = {}
        refreshed: set[str] = set()
        for target in valid:
            row = self.rows[target]
            key = self.metadata_source_key(row)
            metadata = resolved.get(key)
            if metadata is None:
                metadata = self.metadata_resolver.resolve(row, processes=processes)
                resolved[key] = metadata
            self._store_metadata(target, metadata, now)
            refreshed.add(target)
        return refreshed

    def poll_prompt_events(self, now: float) -> set[str]:
        affected: set[str] = set()
        for item in self.history.poll():
            session_id, text = item.get("session_id"), item.get("text")
            if not session_id or not text:
                continue
            self.recent_prompt_events[session_id] = now
            matched = False
            for target, metadata in self.metadata.items():
                if metadata.session_id != session_id:
                    continue
                self.prompts[target] = {"at": now, "saw_working": False}
                self.metadata_times[target] = 0.0
                affected.add(target)
                matched = True
            if not matched:
                for target, row in self.rows.items():
                    if row.agent == "codex":
                        self.metadata_times[target] = 0.0
                        affected.add(target)
            print("codex: prompt submitted", flush=True)
        self.recent_prompt_events = {
            session_id: timestamp
            for session_id, timestamp in self.recent_prompt_events.items()
            if timestamp > now - 30
        }
        return affected

    @staticmethod
    def event_timestamp(event: dict | None) -> float:
        try:
            return float((event or {}).get("timestamp", 0))
        except (TypeError, ValueError):
            return 0.0

    def recent_unmatched_hook_targets(
        self, hook_events: dict[tuple[str, str], dict], now: float
    ) -> set[str]:
        represented = {
            (metadata.agent, metadata.session_id)
            for metadata in self.metadata.values()
            if metadata.session_id
        }
        agents = {
            agent
            for (agent, session_id), event in hook_events.items()
            if (agent, session_id) not in represented
            and self.event_timestamp(event) > now - max(5.0, self.prompt_grace)
        }
        return {target for target, row in self.rows.items() if row.agent in agents}

    def raw_state(self, target: str, row: AgentRow, metadata: Metadata, now: float) -> str:
        if isinstance(row, PaneRow) and row.state != "working":
            self.raw_observation_times[target] = self.last_scan
            return row.state

        if now - self.runtime_times.get(target, 0) >= self.runtime_interval:
            runtime_state, event_time = self.metadata_resolver.runtime_snapshot(metadata)
            self.runtime_states[target] = runtime_state
            self.runtime_times[target] = now
            self.runtime_event_times[target] = event_time

        runtime_state = self.runtime_states.get(target)
        event_time = self.runtime_event_times.get(target, 0.0)
        if runtime_state is not None:
            if (
                isinstance(row, PaneRow)
                and row.state == "working"
                and runtime_state in {"idle", "aborted"}
                and event_time > 0
                and row.started_at > 0
                and event_time < row.started_at
            ):
                self.raw_observation_times[target] = self.last_scan
                return "working"
            observation_time = event_time or self.runtime_times.get(target, now)
            if (
                runtime_state == "working"
                and isinstance(row, PaneRow)
                and row.state == "working"
            ):
                observation_time = max(observation_time, self.last_scan)
            self.raw_observation_times[target] = observation_time
            if runtime_state == "working":
                return "working"
            if runtime_state == "aborted":
                return "done"
            if runtime_state == "idle":
                return "idle"

        self.raw_observation_times[target] = (
            self.last_scan
            if isinstance(row, PaneRow)
            else self.runtime_times.get(target, self.last_scan)
        )
        return row.state

    def observation_time(self, row: AgentRow) -> float:
        target = row.window if isinstance(row, PaneRow) else f"terminal:{row.pid}"
        return self.raw_observation_times.get(target, self.last_scan)

    def resolve_state(
        self,
        row: AgentRow,
        raw_state: str,
        prompt: dict | None,
        hook_event: dict | None,
        now: float,
    ) -> str:
        state = visual_state(raw_state, prompt, now, self.prompt_grace)
        hook_timestamp = self.event_timestamp(hook_event)
        hook_state = (hook_event or {}).get("state")
        prompt_timestamp = float((prompt or {}).get("at", 0))

        event_state = "running" if prompt is not None else ""
        event_timestamp = prompt_timestamp
        if hook_event and hook_timestamp >= event_timestamp:
            event_state = str(hook_state or "")
            event_timestamp = hook_timestamp
        if event_timestamp > self.observation_time(row):
            if event_state == "approval":
                return "approval"
            if event_state == "running":
                return "running"
            if event_state == "done":
                return "done"
            if event_state == "ended":
                return ""

        target = row.window if isinstance(row, PaneRow) else f"terminal:{row.pid}"
        runtime_state = self.runtime_states.get(target)
        runtime_event_time = self.runtime_event_times.get(target, 0.0)
        if (
            raw_state != "blocked"
            and runtime_state in {"idle", "aborted"}
            and runtime_event_time > 0
            and runtime_event_time >= event_timestamp
        ):
            return "done"

        if raw_state == "blocked":
            return "approval"
        if not hook_event:
            return state

        hook_age = max(0.0, now - hook_timestamp)
        prompt_is_newer = prompt is not None and prompt_timestamp > hook_timestamp
        if prompt_is_newer:
            return state
        if hook_state == "approval":
            if (
                isinstance(row, PaneRow)
                and self.observation_time(row) >= hook_timestamp
            ):
                return state
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

    @staticmethod
    def format_title(row: AgentRow, state: str, metadata: Metadata, now: float) -> tuple[str, str]:
        icon = "🌒" if isinstance(row, TerminalRow) and state == "running" else icon_for(state, now)
        source = "[term]" if isinstance(row, TerminalRow) else "[tmux]"
        return icon, f"{icon} {source} {metadata.label}".strip()

    def apply(self, target: str, row: AgentRow, values: tuple[str, str, str]) -> bool:
        state, icon, title = values
        if isinstance(row, TerminalRow):
            return self.terminal_writer.write(row, title)
        return self.tmux.set_values(target, state, icon, title)

    def apply_targets(
        self,
        targets: set[str],
        hook_events: dict[tuple[str, str], dict],
        now: float,
    ) -> bool:
        tmux_updates: list[tuple[str, str, str, str]] = []
        pending_values: dict[str, tuple[str, str, str]] = {}
        for target in targets:
            row = self.rows.get(target)
            if row is None:
                continue
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

            icon, title = self.format_title(row, state, metadata, now)
            values = (state, icon, title)
            terminal_refresh = (
                isinstance(row, TerminalRow)
                and now - self.terminal_write_times.get(target, 0) >= self.terminal_refresh_interval
            )
            if self.applied.get(target) != values or terminal_refresh:
                if isinstance(row, TerminalRow):
                    if self.terminal_writer.write(row, title):
                        self.applied[target] = values
                        self.terminal_write_times[target] = now
                else:
                    tmux_updates.append((target, state, icon, title))
                    pending_values[target] = values

            semantic_value = (state, metadata.label)
            if self.semantic.get(target) != semantic_value:
                print(f"{target}: {title}", flush=True)
                self.semantic[target] = semantic_value

        if tmux_updates and self.tmux.set_many(tmux_updates):
            for target, values in pending_values.items():
                self.applied[target] = values
                self.managed_tmux_windows.add(target)
            return True
        return False

    def cleanup_missing_targets(self) -> bool:
        active_tmux = {
            target for target, row in self.rows.items() if isinstance(row, PaneRow)
        }
        tracked_tmux = self.managed_tmux_windows | {
            target for target in self.applied if not target.startswith("terminal:")
        }
        stale_tmux = tracked_tmux - active_tmux
        existing = stale_tmux & self.tmux_windows
        missing = stale_tmux - self.tmux_windows
        for target in missing:
            self.managed_tmux_windows.discard(target)
            self.drop_target(target)

        pending = set(existing)
        changed = False
        if existing and self.tmux.clear_many(sorted(existing)):
            changed = True
            pending.clear()
            for target in existing:
                self.managed_tmux_windows.discard(target)
                self.drop_target(target)

        known_targets = (
            set(self.applied)
            | set(self.metadata)
            | set(self.row_identities)
            | set(self.prompts)
        )
        for target in known_targets - set(self.rows):
            if target not in pending:
                self.drop_target(target)
        return changed

    def metadata_due_targets(self, force_metadata: bool) -> set[str]:
        now = time.time()
        represented_sessions = {
            metadata.session_id for metadata in self.metadata.values() if metadata.session_id
        }
        unmatched_prompt = any(
            session_id not in represented_sessions
            and timestamp > now - max(5.0, self.prompt_grace)
            for session_id, timestamp in self.recent_prompt_events.items()
        )
        due: set[str] = set()
        for target, row in self.rows.items():
            metadata = self.metadata.get(target)
            interval = (
                self.metadata_interval
                if metadata is not None and metadata.session_id
                else min(self.metadata_interval, self.metadata_retry_interval)
            )
            if (
                force_metadata
                or metadata is None
                or (unmatched_prompt and row.agent == "codex")
                or now - self.metadata_times.get(target, 0) >= interval
            ):
                due.add(target)
        return due

    def update_once(self, *, force_metadata: bool = False) -> bool:
        now = time.time()
        if now - self.last_scan >= self.scan_interval:
            self.scan()
            now = time.time()

        if self.cleanup_missing_targets():
            self.tmux.refresh()
        if not self.rows:
            return False

        prompt_targets = self.poll_prompt_events(now)
        hook_events = self.event_reader.poll(now)

        if self.apply_targets(set(self.rows), hook_events, now):
            self.tmux.refresh()

        metadata_targets = (
            self.metadata_due_targets(force_metadata)
            | prompt_targets
            | self.recent_unmatched_hook_targets(hook_events, now)
        )
        refreshed = self.refresh_metadata_targets(metadata_targets, time.time())
        if refreshed:
            refreshed_at = time.time()
            hook_events = self.event_reader.poll(refreshed_at)
            if self.apply_targets(refreshed, hook_events, refreshed_at):
                self.tmux.refresh()
        return True

    def drop_target(self, target: str) -> None:
        self.applied.pop(target, None)
        self.metadata.pop(target, None)
        self.metadata_times.pop(target, None)
        self.runtime_states.pop(target, None)
        self.runtime_times.pop(target, None)
        self.runtime_event_times.pop(target, None)
        self.raw_observation_times.pop(target, None)
        self.terminal_write_times.pop(target, None)
        self.prompts.pop(target, None)
        self.semantic.pop(target, None)
        self.row_identities.pop(target, None)

    def run(self, once: bool = False) -> int:
        next_tick = time.monotonic()
        while not STOP:
            available = self.update_once(force_metadata=once)
            if once:
                return 0 if available else 1
            next_tick += self.loop_interval
            delay = next_tick - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            else:
                next_tick = time.monotonic()
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


def run_script(path: Path) -> int:
    if not path.is_file():
        print(f"Required script not found: {path}", file=sys.stderr)
        return 1
    os.execv(str(path), [str(path)])
    return 1


def print_completion(shell: str) -> int:
    commands = "update version uninstall help config"
    if shell == "bash":
        print(
            "_tmuxcue() {\n"
            "  local cur prev\n"
            "  COMPREPLY=()\n"
            "  cur=\"${COMP_WORDS[COMP_CWORD]}\"\n"
            "  prev=\"${COMP_WORDS[COMP_CWORD-1]}\"\n"
            f"  if [[ ${{COMP_CWORD}} -eq 1 ]]; then COMPREPLY=( $(compgen -W \"{commands}\" -- \"$cur\") ); fi\n"
            "  if [[ $prev == config ]]; then COMPREPLY=( $(compgen -W \"terminal-title\" -- \"$cur\") ); fi\n"
            "  if [[ ${COMP_WORDS[1]} == config && $prev == terminal-title ]]; then COMPREPLY=( $(compgen -W \"override native\" -- \"$cur\") ); fi\n"
            "}\ncomplete -F _tmuxcue tmuxcue"
        )
    elif shell == "zsh":
        print(
            "#compdef tmuxcue\n"
            "_arguments '1:command:(update version uninstall help config)' "
            "'2:option:(terminal-title)' '3:mode:(override native)'"
        )
    elif shell == "fish":
        for command in commands.split():
            print(f"complete -c tmuxcue -f -n '__fish_use_subcommand' -a {command}")
        print("complete -c tmuxcue -f -n '__fish_seen_subcommand_from config' -a terminal-title")
        print("complete -c tmuxcue -f -n '__fish_seen_subcommand_from terminal-title' -a 'override native'")
    return 0



def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tmuxcue",
        description="Safe status titles for Codex and Claude terminals",
    )
    subcommands = parser.add_subparsers(dest="command")
    subcommands.add_parser("update", help="update TmuxCue from its Git clone")
    subcommands.add_parser("version", help="print the installed version")
    subcommands.add_parser("uninstall", help="remove TmuxCue safely")
    subcommands.add_parser("help", help="show this help message")

    config = subcommands.add_parser("config", help="change TmuxCue options")
    config_subcommands = config.add_subparsers(dest="config_command")
    terminal_title = config_subcommands.add_parser(
        "terminal-title", help="choose whether TmuxCue overrides native terminal titles"
    )
    terminal_title.add_argument("mode", choices=("override", "native"))
    return parser


def entrypoint(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] == "__service":
        once = len(arguments) > 1 and arguments[1] == "--once"
        return IndicatorDaemon().run(once=once)
    if arguments and arguments[0] == "__check":
        return check_dependencies()
    if len(arguments) == 2 and arguments[0] == "__completion":
        return print_completion(arguments[1])

    parser = build_parser()
    args = parser.parse_args(arguments)
    if args.command in {None, "help"}:
        parser.print_help()
        return 0
    if args.command == "version":
        print(f"TmuxCue {__version__}")
        return 0
    if args.command == "config":
        if args.config_command != "terminal-title":
            config_parser = build_parser()
            config_parser.parse_args(["config", "--help"])
            return 1
        apply_title_mode(args.mode)
        print(f"terminal-title: {args.mode}")
        if args.mode == "override":
            print("Native title generation is disabled for newly started Codex and Claude sessions.")
        return 0
    if args.command == "update":
        source = source_dir()
        if not source:
            print("TmuxCue source directory is not configured.", file=sys.stderr)
            return 1
        return run_script(source / "update.sh")
    if args.command == "uninstall":
        current = Path.home() / ".local/share/tmuxcue/current/scripts/uninstall.sh"
        return run_script(current)
    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(entrypoint())
