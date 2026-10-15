import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from tmuxcue import hook
from tmuxcue.events import (
    EVENT_RETENTION_SECONDS,
    EventReader,
    event_directory,
    load_events,
    write_event,
)
from tmuxcue.processes import ProcessBinding


class EventTests(unittest.TestCase):
    def test_atomic_event_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"XDG_RUNTIME_DIR": directory}):
                write_event({"agent": "codex", "session_id": "s1", "state": "running"})
                event = load_events()[("codex", "s1")]
                self.assertEqual(event["state"], "running")

    def test_stale_event_files_are_pruned(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"XDG_RUNTIME_DIR": directory}):
                path = write_event(
                    {"agent": "codex", "session_id": "stale", "state": "done"}
                )
                self.assertIsNotNone(path)
                os.utime(path, (0, 0))
                self.assertEqual(
                    load_events(now=EVENT_RETENTION_SECONDS + 1),
                    {},
                )
                self.assertFalse(path.exists())

    def test_event_reader_reloads_only_after_directory_change(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"XDG_RUNTIME_DIR": directory}):
                write_event({"agent": "codex", "session_id": "s1", "state": "running"})
                reader = EventReader()
                with patch("tmuxcue.events.load_events", wraps=load_events) as loader:
                    reader.poll()
                    reader.poll()
                    self.assertEqual(loader.call_count, 1)

                    write_event({"agent": "codex", "session_id": "s2", "state": "done"})
                    events_dir = event_directory()
                    directory_stat = events_dir.stat()
                    os.utime(
                        events_dir,
                        ns=(directory_stat.st_atime_ns, directory_stat.st_mtime_ns + 1),
                    )
                    reader.poll()
                    self.assertEqual(loader.call_count, 2)

    def test_gemini_hook_records_only_process_binding_metadata(self):
        input_data = {
            "session_id": "11111111-1111-1111-1111-111111111111",
            "transcript_path": "/private/transcript.jsonl",
            "cwd": "/private/project",
            "hook_event_name": "Notification",
            "notification_type": "ToolPermission",
            "message": "private permission message",
            "details": {
                "tool_name": "run_shell_command",
                "path": "/private/file",
            },
        }
        binding = ProcessBinding(
            agent="gemini",
            pid=321,
            started_at=900,
            tty="/dev/pts/7",
            cmdline="node /opt/node_modules/.bin/gemini",
        )
        with tempfile.TemporaryDirectory() as directory:
            with (
                patch.dict(os.environ, {"XDG_RUNTIME_DIR": directory}),
                patch.object(sys, "stdin", io.StringIO(json.dumps(input_data))),
                patch("tmuxcue.hook.find_agent_ancestor", return_value=binding),
                redirect_stdout(io.StringIO()) as output,
            ):
                result = hook.entrypoint(
                    ["--agent", "gemini", "--state", "approval"]
                )
                event = load_events()[("gemini", input_data["session_id"])]

            self.assertEqual(result, 0)
            self.assertEqual(output.getvalue(), "{}\n")
            self.assertEqual(
                set(event),
                {
                    "agent",
                    "session_id",
                    "state",
                    "event",
                    "timestamp",
                    "pid",
                    "started_at",
                    "tty",
                },
            )
            self.assertEqual(event["pid"], 321)
            self.assertEqual(event["started_at"], 900)
            self.assertEqual(event["tty"], "/dev/pts/7")



if __name__ == "__main__":
    unittest.main()
