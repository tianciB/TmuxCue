from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import time

ProcessInfo = tuple[int, int, str]
ProcessSnapshot = dict[tuple[str, str], ProcessInfo]

AGENT_PROCESS_NAMES = {
    "codex": "codex",
    "claude": "claude",
    "claude-code": "claude",
}
GEMINI_ENTRY = re.compile(
    r"(?:^|\s)(?:\S*/)?node_modules/\.bin/gemini(?:\s|$)"
    r"|(?:^|\s)\S*/@google/gemini-cli(?:/|\s|$)"
)
STOPPED_STATES = {"T", "t", "Z", "X"}


@dataclass(frozen=True)
class ProcessBinding:
    agent: str
    pid: int
    started_at: int
    tty: str
    cmdline: str


def is_gemini_cli_command(command: str) -> bool:
    return bool(GEMINI_ENTRY.search(" ".join((command or "").split())))


def classify_agent(process_name: str, command: str) -> str:
    agent = AGENT_PROCESS_NAMES.get((process_name or "").lower(), "")
    if agent:
        return agent
    if (process_name or "").lower() == "node" and is_gemini_cli_command(command):
        return "gemini"
    return ""


def _read_process_stat(path: Path) -> tuple[str, int, int, int, int] | None:
    try:
        text = path.read_text(encoding="utf-8")
        closing = text.rfind(")")
        if closing < 0:
            return None
        fields = text[closing + 2 :].split()
        if len(fields) <= 19:
            return None
        return (
            fields[0],
            int(fields[1]),
            int(fields[2]),
            int(fields[5]),
            int(fields[19]),
        )
    except (OSError, ValueError):
        return None


def _booted_at(proc_root: Path, now: float) -> float:
    try:
        for line in (proc_root / "stat").read_text(encoding="utf-8").splitlines():
            if line.startswith("btime "):
                return float(line.split()[1])
    except (OSError, ValueError, IndexError):
        pass
    try:
        uptime = float((proc_root / "uptime").read_text(encoding="utf-8").split()[0])
        return now - uptime
    except (OSError, ValueError, IndexError):
        return now


def _clock_ticks() -> int:
    try:
        return max(1, int(os.sysconf("SC_CLK_TCK")))
    except (OSError, ValueError):
        return 100


def _read_command(process_dir: Path, process_name: str) -> str:
    try:
        return (
            (process_dir / "cmdline")
            .read_bytes()
            .replace(b"\0", b" ")
            .decode("utf-8", "replace")
            .strip()
        ) or process_name
    except OSError:
        return process_name


def _binding_for_process(
    process_dir: Path,
    *,
    uid: int,
    booted_at: float,
    clock_ticks: int,
    require_foreground: bool,
) -> tuple[ProcessBinding, int] | None:
    try:
        if process_dir.stat().st_uid != uid:
            return None
        process_name = (
            (process_dir / "comm").read_text(encoding="utf-8").strip().lower()
        )
    except OSError:
        return None

    process_stat = _read_process_stat(process_dir / "stat")
    if process_stat is None:
        return None
    state, parent_pid, process_group, foreground_group, start_ticks = process_stat
    if state in STOPPED_STATES:
        return None
    if require_foreground and (
        foreground_group <= 0 or process_group != foreground_group
    ):
        return None

    command = _read_command(process_dir, process_name)
    agent = classify_agent(process_name, command)
    if not agent:
        return None
    try:
        tty = os.readlink(process_dir / "fd/0").removesuffix(" (deleted)")
    except OSError:
        return None
    if not tty.startswith("/dev/"):
        return None

    pid = int(process_dir.name)
    started_at = int(booted_at + (start_ticks / clock_ticks))
    return ProcessBinding(agent, pid, started_at, tty, command), parent_pid


def discover_all_agent_processes(
    proc_root: Path = Path("/proc"),
    uid: int | None = None,
    now: float | None = None,
) -> list[ProcessBinding]:
    proc_root = Path(proc_root)
    uid = os.getuid() if uid is None else uid
    now = time.time() if now is None else now
    booted_at = _booted_at(proc_root, now)
    clock_ticks = _clock_ticks()
    try:
        process_dirs = list(proc_root.iterdir())
    except OSError:
        return []

    bindings: list[ProcessBinding] = []
    for process_dir in process_dirs:
        if not process_dir.name.isdigit():
            continue
        value = _binding_for_process(
            process_dir,
            uid=uid,
            booted_at=booted_at,
            clock_ticks=clock_ticks,
            require_foreground=True,
        )
        if value is not None:
            bindings.append(value[0])
    return bindings


def discover_agent_processes(
    proc_root: Path = Path("/proc"),
    uid: int | None = None,
    now: float | None = None,
) -> ProcessSnapshot:
    """Find foreground Codex, Claude, and Gemini processes in one /proc scan."""
    selected: dict[tuple[str, str], ProcessBinding] = {}
    for binding in discover_all_agent_processes(proc_root, uid, now):
        key = (binding.tty, binding.agent)
        previous = selected.get(key)
        if previous is None or binding.pid > previous.pid:
            selected[key] = binding
    return {
        key: (binding.pid, binding.started_at, binding.cmdline)
        for key, binding in selected.items()
    }


def find_agent_ancestor(
    agent: str,
    *,
    start_pid: int | None = None,
    proc_root: Path = Path("/proc"),
    uid: int | None = None,
) -> ProcessBinding | None:
    proc_root = Path(proc_root)
    uid = os.getuid() if uid is None else uid
    pid = os.getppid() if start_pid is None else start_pid
    booted_at = _booted_at(proc_root, time.time())
    clock_ticks = _clock_ticks()
    seen: set[int] = set()
    while pid > 1 and pid not in seen:
        seen.add(pid)
        process_dir = proc_root / str(pid)
        value = _binding_for_process(
            process_dir,
            uid=uid,
            booted_at=booted_at,
            clock_ticks=clock_ticks,
            require_foreground=False,
        )
        if value is not None:
            binding, parent_pid = value
            if binding.agent == agent:
                return binding
        else:
            process_stat = _read_process_stat(process_dir / "stat")
            if process_stat is None:
                return None
            parent_pid = process_stat[1]
        pid = parent_pid
    return None


def read_process_environment_value(
    pid: int,
    name: str,
    *,
    proc_root: Path = Path("/proc"),
    uid: int | None = None,
) -> str:
    uid = os.getuid() if uid is None else uid
    process = Path(proc_root) / str(pid)
    try:
        if process.stat().st_uid != uid:
            return ""
        entries = (process / "environ").read_bytes().split(b"\0")
    except OSError:
        return ""
    prefix = name.encode("utf-8") + b"="
    for entry in entries:
        if entry.startswith(prefix):
            return entry[len(prefix) :].decode("utf-8", "replace")
    return ""
