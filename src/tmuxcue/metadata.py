from __future__ import annotations

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
AgentRow = PaneRow | TerminalRow


def default_command(args: tuple[str, ...], timeout: float = 5) -> str:
    try:
        return subprocess.check_output(
            args,
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
        ).strip()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return ""


class MetadataResolver:
    def __init__(
        self,
        *,
        home: Path | None = None,
        topic_width: int = 38,
        command: Callable[[tuple[str, ...], float], str] = default_command,
    ):
        self.home = Path(home or Path.home())
        self.codex_home = Path(os.environ.get("CODEX_HOME", self.home / ".codex"))
        self.claude_home = Path(os.environ.get("CLAUDE_HOME", self.home / ".claude"))
        self.topic_width = topic_width
        self.command = command

    def run(self, *args: str, timeout: float = 5) -> str:
        return self.command(tuple(args), timeout)

    def foreground_process(self, pane: str, names: set[str]) -> ProcessInfo | None:
        tty = self.run("tmux", "display-message", "-p", "-t", pane, "#{pane_tty}")
        if not tty:
            return None
        output = self.run("ps", "-t", tty, "-o", "pid=,stat=,etimes=,comm=")
        choices: list[tuple[tuple[int, int], ProcessInfo]] = []
        for line in output.splitlines():
            fields = line.split(None, 3)
            if len(fields) != 4 or fields[3] not in names or "T" in fields[1]:
                continue
            try:
                pid, elapsed = int(fields[0]), int(fields[2])
            except ValueError:
                continue
            try:
                cmdline = (
                    Path(f"/proc/{pid}/cmdline")
                    .read_bytes()
                    .replace(b"\0", b" ")
                    .decode("utf-8", "replace")
                    .strip()
                )
            except OSError:
                cmdline = fields[3]
            score = (1 if "+" in fields[1] else 0, pid)
            choices.append((score, (pid, int(time.time()) - elapsed, cmdline)))
        return max(choices, default=((), None))[1]

    def resolve(self, row: AgentRow) -> Metadata:
        if isinstance(row, PaneRow):
            cwd = self.run(
                "tmux", "display-message", "-p", "-t", row.pane, "#{pane_current_path}"
            ) or row.cwd_hint
            names = {"codex"} if row.agent == "codex" else {"claude", "claude-code"}
            process = self.foreground_process(row.pane, names)
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

    def runtime_state(self, metadata: Metadata) -> str | None:
        if not metadata.session_id:
            return None
        if metadata.agent == "codex":
            return self.codex_runtime_state(metadata.session_id)
        if metadata.agent == "claude":
            return self.claude_runtime_state(metadata.session_id)
        return None

    def codex_runtime_state(self, session_id: str) -> str | None:
        database = self.codex_database()
        if database is None:
            return None
        try:
            connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
            row = connection.execute(
                "select rollout_path from threads where id=?", (session_id,)
            ).fetchone()
        except sqlite3.Error:
            return None
        finally:
            try:
                connection.close()
            except (UnboundLocalError, sqlite3.Error):
                pass
        if not row or not row[0]:
            return None

        path = Path(row[0])
        try:
            with path.open("rb") as stream:
                size = stream.seek(0, os.SEEK_END)
                stream.seek(max(0, size - 524_288))
                data = stream.read().decode("utf-8", "replace")
        except OSError:
            return None

        state = None
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
        return state

    def claude_runtime_state(self, session_id: str) -> str | None:
        sessions_dir = self.claude_home / "sessions"
        if not sessions_dir.exists():
            return None
        for path in sessions_dir.glob("*.json"):
            try:
                item = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if item.get("sessionId") != session_id:
                continue
            status = item.get("status")
            if status == "busy":
                return "working"
            if status:
                return "idle"
        return None

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
