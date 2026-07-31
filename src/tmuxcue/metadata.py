from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import time
from typing import Callable

from .core import Metadata, PaneRow, TerminalRow, clean_text, is_generic_input, shorten

UUID_RE = re.compile(r"\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b", re.I)
ProcessInfo = tuple[int, int, str]
ProcessSnapshot = dict[tuple[str, str], ProcessInfo]
RuntimeSnapshot = tuple[str | None, float]
AgentRow = PaneRow | TerminalRow
AGENT_PROCESS_NAMES = {"codex": "codex", "claude": "claude", "claude-code": "claude"}


def event_timestamp(value: object) -> float:
    if isinstance(value, (int, float)):
        timestamp = float(value)
        return timestamp / 1000 if timestamp > 10_000_000_000 else timestamp
    if not isinstance(value, str) or not value:
        return 0.0
    try:
        timestamp = float(value)
        return timestamp / 1000 if timestamp > 10_000_000_000 else timestamp
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _read_process_stat(path: Path) -> tuple[str, int, int, int] | None:
    try:
        text = path.read_text(encoding="utf-8")
        closing = text.rfind(")")
        if closing < 0:
            return None
        fields = text[closing + 2 :].split()
        if len(fields) <= 19:
            return None
        return fields[0], int(fields[2]), int(fields[5]), int(fields[19])
    except (OSError, ValueError):
        return None


def discover_agent_processes(
    proc_root: Path = Path("/proc"),
    uid: int | None = None,
    now: float | None = None,
) -> ProcessSnapshot:
    """Find foreground Codex and Claude processes with one bounded /proc scan."""
    proc_root = Path(proc_root)
    uid = os.getuid() if uid is None else uid
    now = time.time() if now is None else now
    booted_at: float | None = None
    try:
        for line in (proc_root / "stat").read_text(encoding="utf-8").splitlines():
            if line.startswith("btime "):
                booted_at = float(line.split()[1])
                break
    except (OSError, ValueError, IndexError):
        pass
    if booted_at is None:
        try:
            uptime = float((proc_root / "uptime").read_text(encoding="utf-8").split()[0])
            booted_at = now - uptime
        except (OSError, ValueError, IndexError):
            booted_at = now
    try:
        clock_ticks = int(os.sysconf("SC_CLK_TCK"))
    except (OSError, ValueError):
        clock_ticks = 100

    selected: dict[tuple[str, str], tuple[int, ProcessInfo]] = {}
    try:
        process_dirs = proc_root.iterdir()
    except OSError:
        return {}
    for process_dir in process_dirs:
        if not process_dir.name.isdigit():
            continue
        try:
            if process_dir.stat().st_uid != uid:
                continue
            process_name = (process_dir / "comm").read_text(encoding="utf-8").strip().lower()
        except OSError:
            continue
        agent = AGENT_PROCESS_NAMES.get(process_name, "")
        if not agent:
            continue

        process_stat = _read_process_stat(process_dir / "stat")
        if process_stat is None:
            continue
        state, process_group, foreground_group, start_ticks = process_stat
        if state in {"T", "t", "Z", "X"} or foreground_group <= 0 or process_group != foreground_group:
            continue
        try:
            tty = os.readlink(process_dir / "fd/0").removesuffix(" (deleted)")
        except OSError:
            continue
        if not tty.startswith("/dev/"):
            continue
        try:
            cmdline = (
                (process_dir / "cmdline")
                .read_bytes()
                .replace(b"\0", b" ")
                .decode("utf-8", "replace")
                .strip()
            )
        except OSError:
            cmdline = process_name

        pid = int(process_dir.name)
        started_at = int(booted_at + (start_ticks / max(clock_ticks, 1)))
        key = (tty, agent)
        previous = selected.get(key)
        if previous is None or pid > previous[0]:
            selected[key] = (pid, (pid, started_at, cmdline or process_name))
    return {key: value for key, (_pid, value) in selected.items()}


def default_command(args: tuple[str, ...], timeout: float = 5) -> str:
    try:
        return subprocess.check_output(
            args,
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
        ).strip()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return ""


class MetadataResolver:
    def __init__(
        self,
        *,
        home: Path | None = None,
        topic_width: int = 38,
        command: Callable[[tuple[str, ...], float], str] = default_command,
        proc_root: Path = Path("/proc"),
        uid: int | None = None,
    ):
        self.home = Path(home or Path.home())
        self.codex_home = Path(os.environ.get("CODEX_HOME", self.home / ".codex"))
        self.claude_home = Path(os.environ.get("CLAUDE_HOME", self.home / ".claude"))
        self.topic_width = topic_width
        self.command = command
        self.proc_root = Path(proc_root)
        self.uid = os.getuid() if uid is None else uid
        self._process_snapshot: ProcessSnapshot = {}
        self._process_snapshot_at = 0.0

    def run(self, *args: str, timeout: float = 5) -> str:
        return self.command(tuple(args), timeout)

    def process_snapshot(self, max_age: float = 1.0) -> ProcessSnapshot:
        now = time.monotonic()
        if now - self._process_snapshot_at >= max_age:
            self._process_snapshot = discover_agent_processes(
                self.proc_root, self.uid
            )
            self._process_snapshot_at = now
        return self._process_snapshot

    def foreground_process(self, pane: str, agent: str, tty: str = "") -> ProcessInfo | None:
        tty = tty or self.run("tmux", "display-message", "-p", "-t", pane, "#{pane_tty}")
        if not tty:
            return None
        return self.process_snapshot().get((tty, agent))

    def resolve(
        self, row: AgentRow, processes: ProcessSnapshot | None = None
    ) -> Metadata:
        if isinstance(row, PaneRow):
            cwd = row.cwd_hint or self.run(
                "tmux", "display-message", "-p", "-t", row.pane, "#{pane_current_path}"
            )
            if row.pid:
                process = (row.pid, row.started_at, row.cmdline)
            elif processes is not None and row.tty:
                process = processes.get((row.tty, row.agent))
            else:
                process = self.foreground_process(row.pane, row.agent, row.tty)
        else:
            cwd = row.cwd_hint
            process = (row.pid, row.started_at, row.cmdline)

        if row.agent == "codex":
            model, topic, session_id = self.codex_metadata(cwd, process)
        elif row.agent == "claude":
            model, topic, session_id = self.claude_metadata(
                cwd, process[0] if process else None
            )
        else:
            model, topic, session_id = "", row.subject, ""

        topic = shorten(topic or row.subject or Path(cwd).name, self.topic_width)
        return Metadata(row.agent, model, topic, session_id)

    def codex_database(self) -> Path | None:
        databases = sorted(
            self.codex_home.glob("state_*.sqlite"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        return databases[0] if databases else None

    def codex_metadata(
        self, cwd: str, process: ProcessInfo | None
    ) -> tuple[str, str, str]:
        database = self.codex_database()
        if database is None:
            return "", "", ""

        started_ms = process[1] * 1000 if process else int(time.time() * 1000)
        match = UUID_RE.search(process[2]) if process else None
        resumed_id = match.group(0) if match else ""

        try:
            connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
            connection.row_factory = sqlite3.Row
            rows = list(
                connection.execute(
                    """
                    select id, created_at_ms, updated_at_ms, model, title, name
                      from threads
                     where cwd=? and archived=0 and thread_source='user'
                    """,
                    (cwd,),
                )
            )
        except sqlite3.Error:
            return "", "", ""
        finally:
            try:
                connection.close()
            except (UnboundLocalError, sqlite3.Error):
                pass

        if not rows:
            return "", "", ""
        exact = next((row for row in rows if row["id"] == resumed_id), None)
        if exact is not None:
            thread = exact
        else:
            nearby = [
                row for row in rows
                if abs((row["created_at_ms"] or 0) - started_ms) <= 180_000
            ]
            if nearby:
                thread = min(
                    nearby,
                    key=lambda row: abs((row["created_at_ms"] or 0) - started_ms),
                )
            else:
                eligible = [
                    row for row in rows
                    if (row["created_at_ms"] or 0) <= started_ms + 120_000
                ]
                thread = max(eligible or rows, key=lambda row: row["updated_at_ms"] or 0)

        topic = (thread["name"] or "").strip()
        if not topic:
            topic = self.latest_codex_input(thread["id"])
        if not topic:
            topic = thread["title"] or ""
        return thread["model"] or "", topic, thread["id"]

    def runtime_snapshot(self, metadata: Metadata) -> RuntimeSnapshot:
        if not metadata.session_id:
            return None, 0.0
        if metadata.agent == "codex":
            return self.codex_runtime_snapshot(metadata.session_id)
        if metadata.agent == "claude":
            return self.claude_runtime_snapshot(metadata.session_id)
        return None, 0.0

    def runtime_state(self, metadata: Metadata) -> str | None:
        return self.runtime_snapshot(metadata)[0]

    def codex_runtime_snapshot(self, session_id: str) -> RuntimeSnapshot:
        database = self.codex_database()
        if database is None:
            return None, 0.0
        try:
            connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
            row = connection.execute(
                "select rollout_path from threads where id=?", (session_id,)
            ).fetchone()
        except sqlite3.Error:
            return None, 0.0
        finally:
            try:
                connection.close()
            except (UnboundLocalError, sqlite3.Error):
                pass
        if not row or not row[0]:
            return None, 0.0

        path = Path(row[0])
        try:
            with path.open("rb") as stream:
                size = stream.seek(0, os.SEEK_END)
                stream.seek(max(0, size - 524_288))
                data = stream.read().decode("utf-8", "replace")
        except OSError:
            return None, 0.0

        state = None
        state_timestamp = 0.0
        for line in data.splitlines():
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if item.get("type") != "event_msg":
                continue
            event = (item.get("payload") or {}).get("type")
            if event == "task_started":
                state = "working"
            elif event == "task_complete":
                state = "idle"
            elif event == "turn_aborted":
                state = "aborted"
            else:
                continue
            state_timestamp = event_timestamp(item.get("timestamp"))
        return state, state_timestamp

    def codex_runtime_state(self, session_id: str) -> str | None:
        return self.codex_runtime_snapshot(session_id)[0]

    def claude_runtime_snapshot(self, session_id: str) -> RuntimeSnapshot:
        sessions_dir = self.claude_home / "sessions"
        if not sessions_dir.exists():
            return None, 0.0
        for path in sessions_dir.glob("*.json"):
            try:
                item = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if item.get("sessionId") != session_id:
                continue
            status = item.get("status")
            timestamp = event_timestamp(item.get("updatedAt"))
            if status == "busy":
                return "working", timestamp
            if status:
                return "idle", timestamp
        return None, 0.0

    def claude_runtime_state(self, session_id: str) -> str | None:
        return self.claude_runtime_snapshot(session_id)[0]

    def latest_codex_input(self, session_id: str) -> str:
        history = self.codex_home / "history.jsonl"
        if not history.exists():
            return ""
        topic = ""
        try:
            with history.open(encoding="utf-8") as stream:
                for line in stream:
                    try:
                        item = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if item.get("session_id") != session_id or not item.get("text"):
                        continue
                    candidate = clean_text(item["text"])
                    if candidate and not is_generic_input(candidate):
                        topic = item["text"]
        except OSError:
            return ""
        return topic

    def claude_metadata(self, cwd: str, pid: int | None) -> tuple[str, str, str]:
        session = None
        sessions_dir = self.claude_home / "sessions"
        if sessions_dir.exists():
            for path in sessions_dir.glob("*.json"):
                try:
                    item = json.loads(path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    continue
                if pid and item.get("pid") == pid:
                    session = item
                    break
                if item.get("cwd") == cwd and (
                    session is None or item.get("updatedAt", 0) > session.get("updatedAt", 0)
                ):
                    session = item
        if not session:
            return "", "", ""

        session_id = session.get("sessionId", "")
        topic = ""
        if session.get("name") and session.get("nameSource") not in (None, "derived"):
            topic = session["name"]
        if not topic:
            topic = self.latest_claude_input(session_id)
        return self.claude_model(session_id), topic, session_id

    def latest_claude_input(self, session_id: str) -> str:
        history = self.claude_home / "history.jsonl"
        if not history.exists():
            return ""
        topic = ""
        try:
            with history.open(encoding="utf-8") as stream:
                for line in stream:
                    try:
                        item = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if item.get("sessionId") != session_id:
                        continue
                    pasted = item.get("pastedContents") or {}
                    pieces = [
                        value["content"]
                        for value in pasted.values()
                        if isinstance(value, dict) and value.get("content")
                    ]
                    candidate = " ".join(pieces) if pieces else item.get("display", "")
                    if (
                        candidate
                        and not candidate.startswith("[Pasted text #")
                        and not is_generic_input(candidate)
                    ):
                        topic = candidate
        except OSError:
            return ""
        return topic

    def claude_model(self, session_id: str) -> str:
        projects = self.claude_home / "projects"
        if not projects.exists() or not session_id:
            return ""
        candidates = list(projects.glob(f"*/{session_id}.jsonl"))
        if not candidates:
            return ""
        model = ""
        try:
            with candidates[0].open(encoding="utf-8") as stream:
                for line in stream:
                    try:
                        item = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    message = item.get("message") or {}
                    if isinstance(message, dict) and message.get("model"):
                        model = message["model"]
        except OSError:
            return ""
        return model
