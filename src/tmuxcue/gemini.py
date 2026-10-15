from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import re
import shlex

from .core import clean_text, is_generic_input
from .processes import ProcessInfo, read_process_environment_value

UUID_RE = re.compile(r"^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$", re.I)
DEFAULT_MAX_TRANSCRIPT_BYTES = 2 * 1024 * 1024
DEFAULT_SESSION_LIMIT = 200


@dataclass(frozen=True)
class GeminiSession:
    path: Path
    session_id: str
    project_root: str
    started_at: float
    updated_at: float
    model: str
    topic: str


def timestamp(value: object) -> float:
    if isinstance(value, (int, float)):
        number = float(value)
        return number / 1000 if number > 10_000_000_000 else number
    if not isinstance(value, str) or not value:
        return 0.0
    try:
        number = float(value)
        return number / 1000 if number > 10_000_000_000 else number
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def gemini_state_dir(
    home: Path,
    pid: int,
    *,
    proc_root: Path = Path("/proc"),
    uid: int | None = None,
) -> Path:
    uid = os.getuid() if uid is None else uid
    value = read_process_environment_value(
        pid,
        "GEMINI_CLI_HOME",
        proc_root=proc_root,
        uid=uid,
    )
    if value:
        candidate = Path(value)
        try:
            if candidate.is_absolute() and candidate.stat().st_uid == uid:
                return candidate / ".gemini"
        except OSError:
            pass
    return Path(home) / ".gemini"


def _project_root(path: Path) -> str:
    try:
        return (path.parent.parent / ".project_root").read_text(
            encoding="utf-8"
        ).strip()
    except OSError:
        return ""


def _user_text(record: dict) -> str:
    content = record.get("content")
    if not isinstance(content, list):
        return ""
    pieces = [
        item.get("text", "")
        for item in content
        if isinstance(item, dict) and isinstance(item.get("text"), str)
    ]
    return " ".join(piece for piece in pieces if piece).strip()


def _load_session(path: Path, max_bytes: int) -> GeminiSession | None:
    try:
        stat_result = path.stat()
        if stat_result.st_size > max_bytes:
            return None
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return None

    header: dict | None = None
    records: list[dict] = []
    for line in lines:
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            return None
        if not isinstance(item, dict):
            continue
        if header is None and item.get("sessionId"):
            header = item
        records.append(item)
    if header is None:
        return None

    session_id = str(header.get("sessionId") or "")
    if not UUID_RE.fullmatch(session_id):
        return None

    started_at = timestamp(header.get("startTime"))
    updated_at = timestamp(header.get("lastUpdated")) or stat_result.st_mtime
    model = ""
    topic = ""
    for item in records:
        update = item.get("$set")
        if isinstance(update, dict):
            updated_at = max(updated_at, timestamp(update.get("lastUpdated")))
        updated_at = max(updated_at, timestamp(item.get("timestamp")))
        if item.get("type") == "gemini" and isinstance(item.get("model"), str):
            candidate_model = item["model"].strip()
            if candidate_model:
                model = candidate_model
        if item.get("type") == "user":
            candidate = _user_text(item)
            if candidate and not is_generic_input(candidate):
                topic = candidate

    return GeminiSession(
        path=path,
        session_id=session_id,
        project_root=_project_root(path),
        started_at=started_at,
        updated_at=updated_at,
        model=model,
        topic=clean_text(topic),
    )


def load_gemini_sessions(
    state_dir: Path,
    *,
    limit: int = DEFAULT_SESSION_LIMIT,
    max_bytes: int = DEFAULT_MAX_TRANSCRIPT_BYTES,
) -> list[GeminiSession]:
    try:
        paths = sorted(
            Path(state_dir).glob("tmp/*/chats/session-*.jsonl"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )[:limit]
    except OSError:
        return []
    sessions: list[GeminiSession] = []
    for path in paths:
        session = _load_session(path, max_bytes)
        if session is not None:
            sessions.append(session)
    return sessions


def select_gemini_session(
    sessions: list[GeminiSession],
    *,
    session_id: str,
    project_root: str,
    process_started_at: int,
) -> GeminiSession | None:
    project_sessions = [
        session for session in sessions if session.project_root == project_root
    ]
    if session_id:
        return next(
            (
                session
                for session in project_sessions
                if session.session_id == session_id
            ),
            None,
        )
    compatible = [
        session
        for session in project_sessions
        if session.updated_at >= process_started_at - 5
    ]
    return compatible[0] if len(compatible) == 1 else None


def gemini_model_from_command(command: str) -> str:
    try:
        arguments = shlex.split(command)
    except ValueError:
        return ""
    for index, value in enumerate(arguments):
        if value in {"--model", "-m"} and index + 1 < len(arguments):
            return arguments[index + 1]
        if value.startswith("--model="):
            return value.split("=", 1)[1]
    return ""


def gemini_configured_model(state_dir: Path) -> str:
    path = Path(state_dir) / "settings.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return ""
    model = data.get("model") if isinstance(data, dict) else None
    if not isinstance(model, dict):
        return ""
    value = model.get("name")
    return str(value).strip() if isinstance(value, str) else ""


def gemini_bound_session_id(
    events: dict[tuple[str, str], dict],
    process: ProcessInfo,
    tty: str,
) -> str:
    pid, started_at, _command = process
    selected_id = ""
    selected_timestamp = 0.0
    for (agent, session_id), event in events.items():
        if agent != "gemini":
            continue
        try:
            event_pid = int(event.get("pid", 0))
            event_started_at = int(event.get("started_at", 0))
            event_timestamp = float(event.get("timestamp", 0))
        except (TypeError, ValueError):
            continue
        if (
            event_pid != pid
            or event_started_at != started_at
            or str(event.get("tty") or "") != tty
            or event_timestamp < started_at
        ):
            continue
        if event_timestamp >= selected_timestamp:
            selected_id = session_id
            selected_timestamp = event_timestamp
    return selected_id
