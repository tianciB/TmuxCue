import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from tmuxcue.core import Metadata
from tmuxcue.metadata import MetadataResolver, discover_agent_processes


class ProcessDiscoveryTests(unittest.TestCase):
    def test_proc_scan_returns_only_owned_foreground_agents(self):
        with tempfile.TemporaryDirectory() as directory:
            proc = Path(directory)
            (proc / "uptime").write_text("1000.0 0.0\n", encoding="utf-8")
            (proc / "stat").write_text("cpu 1 2 3 4\nbtime 1000\n", encoding="utf-8")

            def add_process(
                pid: int, name: str, tty: str, *, state: str = "S", foreground: bool = True
            ) -> None:
                process = proc / str(pid)
                process.mkdir()
                (process / "comm").write_text(name + "\n", encoding="utf-8")
                (process / "cmdline").write_bytes(f"{name}\0resume\0".encode())
                fd = process / "fd"
                fd.mkdir()
                (fd / "0").symlink_to(tty)
                process_group = pid
                foreground_group = pid if foreground else pid + 1
                fields = [
                    state,
                    "1",
                    str(process_group),
                    str(pid),
                    "0",
                    str(foreground_group),
                    "0",
                    "0",
                    "0",
                    "0",
                    "0",
                    "0",
                    "0",
                    "0",
                    "0",
                    "0",
                    "0",
                    "1",
                    "0",
                    "500",
                ]
                (process / "stat").write_text(
                    f"{pid} ({name}) " + " ".join(fields) + "\n",
                    encoding="utf-8",
                )

            add_process(101, "codex", "/dev/pts/1")
            add_process(102, "claude-code", "/dev/pts/2")
            add_process(103, "codex", "/dev/pts/3", state="T")
            add_process(104, "codex", "/dev/pts/4", foreground=False)
            add_process(105, "python", "/dev/pts/5")

            processes = discover_agent_processes(proc, os.getuid(), now=2000)
            self.assertEqual(
                set(processes),
                {("/dev/pts/1", "codex"), ("/dev/pts/2", "claude")},
            )
            self.assertEqual(processes[("/dev/pts/1", "codex")][0], 101)
            self.assertGreaterEqual(processes[("/dev/pts/1", "codex")][1], 1000)
            self.assertIn("resume", processes[("/dev/pts/2", "claude")][2])


class RuntimeStateTests(unittest.TestCase):
    def test_codex_runtime_state_follows_latest_task_event(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            codex = home / ".codex"
            codex.mkdir()
            rollout = codex / "rollout.jsonl"
            rollout.write_text(
                json.dumps({"type": "event_msg", "payload": {"type": "task_started"}})
                + "\n",
                encoding="utf-8",
            )
            database = codex / "state_1.sqlite"
            connection = sqlite3.connect(database)
            connection.execute("create table threads (id text primary key, rollout_path text)")
            connection.execute("insert into threads values (?, ?)", ("session-1", str(rollout)))
            connection.commit()
            connection.close()

            resolver = MetadataResolver(home=home)
            metadata = Metadata("codex", "model", "topic", "session-1")
            self.assertEqual(resolver.runtime_state(metadata), "working")

            with rollout.open("a", encoding="utf-8") as stream:
                stream.write(
                    json.dumps({"type": "event_msg", "payload": {"type": "task_complete"}})
                    + "\n"
                )
            self.assertEqual(resolver.runtime_state(metadata), "idle")

    def test_claude_runtime_state_uses_live_session_status(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            sessions = home / ".claude/sessions"
            sessions.mkdir(parents=True)
            path = sessions / "session.json"
            path.write_text(
                json.dumps({"sessionId": "session-2", "status": "busy"}),
                encoding="utf-8",
            )
            resolver = MetadataResolver(home=home)
            metadata = Metadata("claude", "model", "topic", "session-2")
            self.assertEqual(resolver.runtime_state(metadata), "working")

            path.write_text(
                json.dumps({"sessionId": "session-2", "status": "idle"}),
                encoding="utf-8",
            )
            self.assertEqual(resolver.runtime_state(metadata), "idle")


if __name__ == "__main__":
    unittest.main()
