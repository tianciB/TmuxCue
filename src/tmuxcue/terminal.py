from __future__ import annotations

import os
from pathlib import Path
import re
import stat
from typing import Callable

from .core import TerminalRow
from .metadata import ProcessSnapshot, default_command, discover_agent_processes

AGENTS = {"codex": "codex", "claude": "claude", "claude-code": "claude"}
CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f-\x9f]")


class TerminalDetector:
    """Find foreground Codex and Claude processes outside tmux panes."""

    def __init__(
        self,
        command: Callable[[tuple[str, ...], float], str] = default_command,
        proc_root: Path = Path("/proc"),
        uid: int | None = None,
        process_provider: Callable[[Path, int], ProcessSnapshot] = discover_agent_processes,
    ):
        self.command = command
        self.proc_root = proc_root
        self.uid = os.getuid() if uid is None else uid
        self.process_provider = process_provider

    def run(self, *args: str, timeout: float = 5) -> str:
        return self.command(tuple(args), timeout)

    @staticmethod
    def tty_path(value: str) -> str:
        value = value.strip()
        if not value or value == "?":
            return ""
        return value if value.startswith("/dev/") else f"/dev/{value}"

    def scan(
        self, excluded_ttys: set[str], processes: ProcessSnapshot | None = None
    ) -> dict[str, TerminalRow]:
        selected: dict[str, TerminalRow] = {}
        processes = (
            self.process_provider(self.proc_root, self.uid)
            if processes is None
            else processes
        )
        for (tty, agent), process in processes.items():
            if tty in excluded_ttys:
                continue
            pid, started_at, cmdline = process
            row = TerminalRow(
                pid=pid,
                tty=tty,
                agent=agent,
                state="idle",
                cwd_hint=self._cwd(pid),
                started_at=started_at,
                cmdline=cmdline,
            )
            previous = selected.get(tty)
            if previous is None or row.pid > previous.pid:
                selected[tty] = row
        return {f"terminal:{row.pid}": row for row in selected.values()}

    def _cwd(self, pid: int) -> str:
        try:
            return os.readlink(self.proc_root / str(pid) / "cwd")
        except OSError:
            return ""


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
