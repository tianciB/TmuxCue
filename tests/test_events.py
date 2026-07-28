import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tmuxcue.events import load_events, write_event


class EventTests(unittest.TestCase):
    def test_atomic_event_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"XDG_RUNTIME_DIR": directory}):
                write_event({"agent": "codex", "session_id": "s1", "state": "running"})
                event = load_events()[("codex", "s1")]
                self.assertEqual(event["state"], "running")


if __name__ == "__main__":
    unittest.main()
