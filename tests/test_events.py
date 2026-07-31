import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tmuxcue.events import (
    EVENT_RETENTION_SECONDS,
    EventReader,
    event_directory,
    load_events,
    write_event,
)


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



if __name__ == "__main__":
    unittest.main()
