from __future__ import annotations

import json
import os
from pathlib import Path
import re
import time


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


def load_events() -> dict[tuple[str, str], dict]:
    directory = event_directory()
    events: dict[tuple[str, str], dict] = {}
    if not directory.exists():
        return events
    for path in directory.glob("*.json"):
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        key = (str(item.get("agent") or ""), str(item.get("session_id") or ""))
        if not all(key):
            continue
        if key not in events or item.get("timestamp", 0) > events[key].get("timestamp", 0):
            events[key] = item
    return events
