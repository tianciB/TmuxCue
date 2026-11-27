from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import re
import shlex
import sqlite3
from urllib.parse import unquote, urlparse

from .core import clean_text, is_generic_input
from .processes import ProcessInfo, read_process_environment_value

UUID_RE = re.compile(r"\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b", re.I)
FULL_UUID_RE = re.compile(r"^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$", re.I)

ACTIVE_STEP_STATUSES = {1, 2, 8, 11}  # PENDING, RUNNING, GENERATING, QUEUED
WAITING_STEP_STATUS = 9  # WAITING (for user approval)
ACTIVE_RUN_STATUSES = {
    "CASCADE_RUN_STATUS_RUNNING",
    "CASCADE_RUN_STATUS_BUSY",
    "CASCADE_RUN_STATUS_CANCELING",
}
IDLE_RUN_STATUSES = {
    "CASCADE_RUN_STATUS_IDLE",
}


@dataclass(frozen=True)
class AgyConversation:
    conversation_id: str
    title: str
    preview: str
    workspace_paths: tuple[str, ...]
    status: str
    not_fully_idle: bool
    killed: bool
    last_modified: float
    last_user_input: float


def parse_timestamp(value: object) -> float:
    if isinstance(value, (int, float)):
        number = float(value)
        return number / 1000 if number > 10_000_000_000 else number
    if not isinstance(value, str) or not value:
        return 0.0
    text = value.strip()
    try:
        number = float(text)
        return number / 1000 if number > 10_000_000_000 else number
    except ValueError:
        pass
    normalized = text.replace(" ", "T", 1)
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    # Python datetime.fromisoformat supports up to 6 fractional digits; agy writes 9.
    normalized = re.sub(r"(\.\d{6})\d+", r"\1", normalized)
    try:
        return datetime.fromisoformat(normalized).timestamp()
    except ValueError:
        return 0.0


def _normalize_workspace_uri(uri: str) -> str:
    if not uri:
        return ""
    if uri.startswith("file://"):
        parsed = urlparse(uri)
        path = unquote(parsed.path)
    else:
        path = uri
    return path.rstrip("/") or "/"


def _parse_workspace_uris(raw: str) -> tuple[str, ...]:
    if not raw:
        return ()
    try:
        items = json.loads(raw)
    except json.JSONDecodeError:
        return ()
    if not isinstance(items, list):
        return ()
    paths: list[str] = []
    for item in items:
        if isinstance(item, str) and item:
            paths.append(_normalize_workspace_uri(item))
    return tuple(paths)


def agy_state_dir(
    home: Path,
    pid: int = 0,
    *,
    proc_root: Path = Path("/proc"),
    uid: int | None = None,
) -> Path:
    uid = os.getuid() if uid is None else uid
    if pid > 0:
        custom_dir = read_process_environment_value(
            pid,
            "ANTIGRAVITY_APP_DATA_DIR",
            proc_root=proc_root,
            uid=uid,
        )
        if custom_dir:
            candidate = Path(custom_dir)
            try:
                if candidate.is_absolute() and candidate.stat().st_uid == uid:
                    return candidate
            except OSError:
                pass
        rel_dir = read_process_environment_value(
            pid,
            "ANTIGRAVITY_CONFIG_REL_DIR",
            proc_root=proc_root,
            uid=uid,
        )
        if rel_dir and not Path(rel_dir).is_absolute() and ".." not in Path(rel_dir).parts:
            candidate = Path(home) / rel_dir
            try:
                if candidate.exists() and candidate.stat().st_uid == uid:
                    return candidate
            except OSError:
                pass
    env_override = os.environ.get("ANTIGRAVITY_APP_DATA_DIR", "")
    if env_override:
        candidate = Path(env_override)
        if candidate.is_absolute():
            return candidate
    return Path(home) / ".gemini" / "antigravity-cli"


def agy_open_conversation_ids(
    state_dir: Path,
    pid: int,
    *,
    proc_root: Path = Path("/proc"),
    uid: int | None = None,
) -> set[str]:
    """Return conversation IDs actively owned by this Agy process via presence locks.

    Agy holds an exclusive lock under ``presence/<conversation_id>.lock`` only
    for the conversation active in that process. Files under ``conversations/``
    may also be opened transiently for other conversations during startup list
    or background reconciliation, so they must not bind an unrelated pane.
    """
    if pid <= 0:
        return set()
    uid = os.getuid() if uid is None else uid
    process = Path(proc_root) / str(pid)
    try:
        if process.stat().st_uid != uid:
            return set()
        entries = list((process / "fd").iterdir())
    except OSError:
        return set()
    try:
        resolved_root = Path(state_dir).resolve()
    except OSError:
        resolved_root = Path(state_dir)

    conversation_ids: set[str] = set()
    for entry in entries:
        try:
            target = Path(os.readlink(entry).removesuffix(" (deleted)")).resolve()
            relative = target.relative_to(resolved_root)
        except (OSError, RuntimeError, ValueError):
            continue
        if len(relative.parts) >= 2 and relative.parts[0] == "presence" and target.suffix == ".lock":
            match = FULL_UUID_RE.fullmatch(target.stem)
            if match:
                conversation_ids.add(match.group(0))
    return conversation_ids


def agy_conversation_from_command(command: str) -> str:
    try:
        arguments = shlex.split(command)
    except ValueError:
        arguments = (command or "").split()
    for index, value in enumerate(arguments):
        if value in {"--conversation", "--resume", "-r"} and index + 1 < len(arguments):
            candidate = arguments[index + 1]
            if FULL_UUID_RE.fullmatch(candidate):
                return candidate
        for prefix in ("--conversation=", "--resume="):
            if value.startswith(prefix):
                candidate = value.split("=", 1)[1]
                if FULL_UUID_RE.fullmatch(candidate):
                    return candidate
    return ""


def agy_model_from_command(command: str) -> str:
    try:
        arguments = shlex.split(command)
    except ValueError:
        return ""
    for index, value in enumerate(arguments):
        if value == "--model" and index + 1 < len(arguments):
            return arguments[index + 1].strip()
        if value.startswith("--model="):
            return value.split("=", 1)[1].strip()
    return ""


def agy_configured_model(state_dir: Path) -> str:
    path = Path(state_dir) / "settings.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return ""
    if not isinstance(data, dict):
        return ""
    for key in ("model", "defaultModel"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict) and isinstance(value.get("name"), str):
            candidate = value["name"].strip()
            if candidate:
                return candidate
    return ""


def agy_bound_conversation_id(
    events: dict[tuple[str, str], dict],
    process: ProcessInfo,
    tty: str,
) -> str:
    pid, started_at, _command = process
    selected_id = ""
    selected_timestamp = 0.0
    for (agent, session_id), event in events.items():
        if agent != "agy":
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


def load_agy_conversation(
    state_dir: Path,
    conversation_id: str,
) -> AgyConversation | None:
    if not conversation_id or not FULL_UUID_RE.fullmatch(conversation_id):
        return None
    database = Path(state_dir) / "conversation_summaries.db"
    if not database.is_file():
        return None
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(
            f"file:{database}?mode=ro",
            uri=True,
            timeout=0.05,
        )
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            """
            select conversation_id, title, preview, workspace_uris, status,
                   not_fully_idle, killed, last_modified_time, last_user_input_time
              from conversation_summaries
             where conversation_id=?
            """,
            (conversation_id,),
        ).fetchone()
    except sqlite3.Error:
        return None
    finally:
        if connection is not None:
            try:
                connection.close()
            except sqlite3.Error:
                pass
    if row is None:
        return None
    return AgyConversation(
        conversation_id=str(row["conversation_id"] or ""),
        title=clean_text(str(row["title"] or "")),
        preview=clean_text(str(row["preview"] or "")),
        workspace_paths=_parse_workspace_uris(str(row["workspace_uris"] or "")),
        status=str(row["status"] or ""),
        not_fully_idle=bool(row["not_fully_idle"]),
        killed=bool(row["killed"]),
        last_modified=parse_timestamp(row["last_modified_time"]),
        last_user_input=parse_timestamp(row["last_user_input_time"]),
    )


def agy_annotation_title(state_dir: Path, conversation_id: str) -> str:
    if not conversation_id or not FULL_UUID_RE.fullmatch(conversation_id):
        return ""
    path = Path(state_dir) / "annotations" / f"{conversation_id}.pbtxt"
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return ""
    match = re.search(r'^\s*title\s*:\s*"((?:[^"\\]|\\.)*)"', text, flags=re.M)
    if not match:
        return ""
    raw = match.group(1)
    try:
        decoded = json.loads(f'"{raw}"')
    except json.JSONDecodeError:
        decoded = raw
    return clean_text(decoded)


def latest_agy_input(state_dir: Path, cwd: str) -> str:
    history = Path(state_dir) / "history.jsonl"
    if not history.is_file():
        return ""
    normalized_cwd = _normalize_workspace_uri(cwd) if cwd else ""
    topic = ""
    try:
        with history.open(encoding="utf-8") as stream:
            for line in stream:
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "slash_command":
                    continue
                workspace = item.get("workspace")
                if normalized_cwd and isinstance(workspace, str) and workspace:
                    if _normalize_workspace_uri(workspace) != normalized_cwd:
                        continue
                display = item.get("display")
                if not isinstance(display, str):
                    continue
                candidate = clean_text(display)
                if candidate and not is_generic_input(candidate):
                    topic = candidate
    except OSError:
        return ""
    return topic


def agy_runtime_snapshot(
    state_dir: Path,
    conversation_id: str,
) -> tuple[str | None, float]:
    if not conversation_id or not FULL_UUID_RE.fullmatch(conversation_id):
        return None, 0.0

    # Check per-conversation steps DB first for live WAITING/RUNNING step states
    # because conversation_summaries.db may lag behind in-flight step updates.
    conv_db = Path(state_dir) / "conversations" / f"{conversation_id}.db"
    step_state: str | None = None
    step_mtime = 0.0
    if conv_db.is_file():
        for suffix in ("", "-wal"):
            candidate = conv_db if not suffix else conv_db.with_name(conv_db.name + suffix)
            try:
                step_mtime = max(step_mtime, candidate.stat().st_mtime)
            except OSError:
                pass
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(
                f"file:{conv_db}?mode=ro",
                uri=True,
                timeout=0.05,
            )
            statuses = {
                int(row[0])
                for row in connection.execute("select distinct status from steps")
                if row and row[0] is not None
            }
            if WAITING_STEP_STATUS in statuses:
                step_state = "blocked"
            elif statuses & ACTIVE_STEP_STATUSES:
                step_state = "working"
        except (sqlite3.Error, ValueError, TypeError):
            pass
        finally:
            if connection is not None:
                try:
                    connection.close()
                except sqlite3.Error:
                    pass

    summary = load_agy_conversation(state_dir, conversation_id)
    summary_time = summary.last_modified if summary is not None else 0.0
    event_time = max(step_mtime, summary_time)

    if step_state == "blocked":
        return "blocked", event_time
    if step_state == "working":
        return "working", event_time
    if summary is None:
        return None, 0.0
    if summary.status in ACTIVE_RUN_STATUSES or summary.not_fully_idle:
        return "working", event_time
    if summary.status in IDLE_RUN_STATUSES:
        return "idle", event_time
    return None, 0.0
