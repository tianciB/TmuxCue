from __future__ import annotations

import os
from pathlib import Path
import re
import stat
import time
from typing import Callable

from .core import TerminalRow
from .metadata import default_command

AGENTS = {"codex": "codex", "claude": "claude", "claude-code": "claude"}
CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f-\x9f]")


class TerminalDetector:
    """Find foreground Codex and Claude processes outside tmux panes."""

    def __init__(
        self,
        command: Callable[[tuple[str, ...], float], str] = default_command,
        proc_root: Path = Path("/proc"),
        uid: int | None = None,
    ):
        self.command = command
        self.proc_root = proc_root
        self.uid = os.getuid() if uid is None else uid

    def run(self, *args: str, timeout: float = 5) -> str:
        return self.command(tuple(args), timeout)

    @staticmethod
    def tty_path(value: str) -> str:
        value = value.strip()
        if not value or value == "?":
            return ""
        return value if value.startswith("/dev/") else f"/dev/{value}"

    def scan(self, excluded_ttys: set[str]) -> dict[str, TerminalRow]:
        output = self.run("ps", "-eo", "uid=,pid=,tty=,stat=,etimes=,comm=")
        selected: dict[str, TerminalRow] = {}
        for line in output.splitlines():
            fields = line.split(None, 5)
            if len(fields) != 6:
                continue
            try:
                uid, pid, elapsed = int(fields[0]), int(fields[1]), int(fields[4])
            except ValueError:
                continue
            tty = self.tty_path(fields[2])
            process_state, process_name = fields[3], fields[5].lower()
            agent = AGENTS.get(process_name, "")
            if (
                uid != self.uid
                or not agent
                or not tty
                or tty in excluded_ttys
                or "+" not in process_state
                or "T" in process_state
            ):
                continue

            process_dir = self.proc_root / str(pid)
            try:
                cwd = os.readlink(process_dir / "cwd")
                cmdline = (
                    (process_dir / "cmdline")
                    .read_bytes()
                    .replace(b"\0", b" ")
                    .decode("utf-8", "replace")
                    .strip()
                )
            except OSError:
                continue

            row = TerminalRow(
                pid=pid,
                tty=tty,
                agent=agent,
                state="idle",
                cwd_hint=cwd,
                started_at=int(time.time()) - elapsed,
                cmdline=cmdline or process_name,
            )
            target = f"terminal:{pid}"
            previous = selected.get(tty)
            if previous is None or row.pid > previous.pid:
                selected[tty] = row

        return {f"terminal:{row.pid}": row for row in selected.values()}


class TerminalTitleWriter:
    """Write only a sanitized OSC title to an owned foreground TTY."""

    def __init__(self, proc_root: Path = Path("/proc"), uid: int | None = None):
        self.proc_root = proc_root
        self.uid = os.getuid() if uid is None else uid

    @staticmethod
    def sanitize(title: str, limit: int = 240) -> str:
        title = CONTROL_CHARACTERS.sub(" ", title)
        title = " ".join(title.split())
        encoded = title.encode("utf-8")
        if len(encoded) <= limit:
            return title
        encoded = encoded[:limit]
        while encoded:
            try:
                return encoded.decode("utf-8").rstrip()
            except UnicodeDecodeError:
                encoded = encoded[:-1]
        return ""

    def validate(self, row: TerminalRow) -> Path | None:
        tty = Path(row.tty)
        try:
            resolved = tty.resolve(strict=True)
            device = resolved.stat()
            process = (self.proc_root / str(row.pid)).stat()
            stdin = (self.proc_root / str(row.pid) / "fd/0").resolve(strict=True)
        except OSError:
            return None

        if (
            resolved.parent != Path("/dev/pts")
            or not resolved.name.isdigit()
            or not stat.S_ISCHR(device.st_mode)
            or device.st_uid != self.uid
            or process.st_uid != self.uid
            or stdin != resolved
        ):
            return None
        return resolved

    def write(self, row: TerminalRow, title: str) -> bool:
        tty = self.validate(row)
        safe_title = self.sanitize(title)
        if tty is None or not safe_title:
            return False

        flags = os.O_WRONLY | os.O_NOCTTY | os.O_CLOEXEC | os.O_NONBLOCK
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            before = tty.stat()
            fd = os.open(tty, flags)
            try:
                opened = os.fstat(fd)
                if (
                    not stat.S_ISCHR(opened.st_mode)
                    or opened.st_uid != self.uid
                    or opened.st_dev != before.st_dev
                    or opened.st_ino != before.st_ino
                ):
                    return False
                os.write(fd, b"\x1b]0;" + safe_title.encode("utf-8") + b"\x07")
            finally:
                os.close(fd)
        except OSError:
            return False
        return True
