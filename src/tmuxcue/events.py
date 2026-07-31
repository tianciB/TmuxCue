from __future__ import annotations

import json
import os
from pathlib import Path
import re
import time

EVENT_RETENTION_SECONDS = 7 * 24 * 60 * 60


def event_directory() -> Path:
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR") or os.environ.get("TMPDIR") or "/tmp")
    return runtime / "tmuxcue" / "events"


def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", value)


def write_event(payload: dict) -> Path | None:
    session_id = str(payload.get("session_id") or "").strip()
    agent = str(payload.get("agent") or "agent").strip()
    if not session_id:
        return None

    directory = event_directory()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(directory, 0o700)
    path = directory / f"{safe_name(agent)}-{safe_name(session_id)}.json"
    temporary = path.with_suffix(f".tmp-{os.getpid()}")

    data = dict(payload)
    data.setdefault("timestamp", time.time())
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False)
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return path


def load_events(
    now: float | None = None, retention: float = EVENT_RETENTION_SECONDS
) -> dict[tuple[str, str], dict]:
    directory = event_directory()
    events: dict[tuple[str, str], dict] = {}
    now = time.time() if now is None else now
    if not directory.exists():
        return events
    for path in directory.glob("*.json"):
        try:
            if retention > 0 and path.stat().st_mtime < now - retention:
                path.unlink()
                continue
            item = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        key = (str(item.get("agent") or ""), str(item.get("session_id") or ""))
        if not all(key):
            continue
        try:
            timestamp = float(item.get("timestamp", 0))
            previous_timestamp = float(events.get(key, {}).get("timestamp", 0))
        except (TypeError, ValueError):
            continue
        if key not in events or timestamp > previous_timestamp:
            events[key] = item
    return events


class EventReader:
    """Reload hook events only when the atomic event directory changes."""

    def __init__(self) -> None:
        self.directory: Path | None = None
        self.signature: tuple[int, int] | None = None
        self.events: dict[tuple[str, str], dict] = {}

    @staticmethod
    def _signature(directory: Path) -> tuple[int, int] | None:
        try:
            stat_result = directory.stat()
            return stat_result.st_mtime_ns, stat_result.st_ctime_ns
        except OSError:
            return None

    def poll(self, now: float | None = None) -> dict[tuple[str, str], dict]:
        directory = event_directory()
        signature = self._signature(directory)
        if directory != self.directory or signature != self.signature:
            self.events = load_events(now=now)
            self.directory = directory
            self.signature = self._signature(directory)
        return self.events
