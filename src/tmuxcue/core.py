from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
import unicodedata

PHASES = ("🌑", "🌒", "🌓", "🌔", "🌕", "🌖", "🌗", "🌘")
PRIORITY = {"blocked": 4, "working": 3, "done": 2, "idle": 1}


@dataclass(frozen=True)
class PaneRow:
    pane: str
    window: str
    agent: str
    state: str
    cwd_hint: str
    subject: str


@dataclass(frozen=True)
class Metadata:
    agent: str
    model: str
    topic: str
    session_id: str = ""

    @property
    def label(self) -> str:
        prefix = f"{self.agent}/{self.model}" if self.model else self.agent
        return f"{prefix}: {self.topic}" if self.topic else prefix


class JsonlTail:
    """Incrementally read complete JSONL records appended after construction."""

    def __init__(self, path: Path):
        self.path = Path(path)
        try:
            self.offset = self.path.stat().st_size
        except FileNotFoundError:
            self.offset = 0
        self.partial = b""

    def poll(self) -> list[dict]:
        try:
            size = self.path.stat().st_size
            if size < self.offset:
                self.offset = 0
                self.partial = b""
            with self.path.open("rb") as stream:
                stream.seek(self.offset)
                chunk = stream.read()
                self.offset = stream.tell()
        except (FileNotFoundError, OSError):
            return []
        if not chunk:
            return []

        data = self.partial + chunk
        lines = data.split(b"\n")
        self.partial = lines.pop()
        records: list[dict] = []
        for line in lines:
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
        return records


def clean_text(text: str) -> str:
    text = text or ""
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"^\s{0,3}#{1,6}\s*", "", text)
    text = re.sub(r"[*_`~]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def is_generic_input(text: str) -> bool:
    candidate = clean_text(text)
    if not candidate:
        return True
    if candidate.startswith("/") and len(candidate.split()) <= 2:
        return True
    return bool(
        re.fullmatch(
            (
                "(?:"
                "\u7ee7\u7eed\u6267\u884c|"
                "\u7ee7\u7eed|"
                "\u597d\u7684?|"
                "\u53ef\u4ee5|"
                "\u786e\u8ba4|"
                "\u540c\u610f|"
                "\u5f00\u59cb|"
                "\u6267\u884c|"
                "ok|okay|yes|y"
                ")[\u3002.!\uff01]?"
            ),
            candidate,
            flags=re.I,
        )
    )


def display_width(character: str) -> int:
    return 2 if unicodedata.east_asian_width(character) in {"W", "F"} else 1


def shorten(text: str, width: int = 38) -> str:
    text = clean_text(text)
    if not text:
        return ""
    if sum(display_width(ch) for ch in text) <= width:
        return text

    limit = max(1, width - 3)
    used = 0
    output: list[str] = []
    for character in text:
        char_width = display_width(character)
        if used + char_width > limit:
            break
        output.append(character)
        used += char_width
    return "".join(output).rstrip(" ，。；：,.;:") + "..."


def visual_state(raw_state: str, prompt: dict | None, now: float, prompt_grace: float) -> str:
    if raw_state == "blocked":
        return "approval"
    if raw_state == "working":
        if prompt is not None:
            prompt["saw_working"] = True
        return "running"
    if prompt is not None:
        if prompt.get("saw_working"):
            return "done"
        if now - prompt["at"] < prompt_grace:
            return "running"
    if raw_state in {"idle", "done"}:
        return "done"
    return ""


def icon_for(state: str, now: float) -> str:
    if state == "approval":
        return "❓"
    if state == "running":
        return PHASES[int(now) % len(PHASES)]
    if state == "done":
        return "✅"
    return ""
