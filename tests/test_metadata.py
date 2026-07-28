import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from tmuxcue.core import Metadata
from tmuxcue.metadata import MetadataResolver


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
