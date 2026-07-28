import io
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from tmuxcue.core import Metadata
from tmuxcue.service import IndicatorDaemon


class DaemonTests(unittest.TestCase):
    def test_codex_history_event_marks_matching_window_running(self):
        with tempfile.TemporaryDirectory() as directory:
            codex_home = Path(directory)
            history = codex_home / "history.jsonl"
            history.write_text("", encoding="utf-8")
            with patch.dict(os.environ, {"CODEX_HOME": str(codex_home)}):
                daemon = IndicatorDaemon()
                daemon.metadata["codex:0"] = Metadata(
                    "codex", "gpt-test", "topic", "session-1"
                )
                with history.open("a", encoding="utf-8") as stream:
                    stream.write(
                        json.dumps(
                            {"session_id": "session-1", "ts": int(time.time()), "text": "run"}
                        )
                        + "\n"
                    )
                with redirect_stdout(io.StringIO()):
                    affected = daemon.poll_prompt_events(time.time())
                self.assertEqual(affected, {"codex:0"})
                self.assertIn("codex:0", daemon.prompts)

    def test_tmux_snippet_never_executes_shell_commands(self):
        snippet = (
            Path(__file__).parents[1] / "tmux" / "tmuxcue.conf"
        ).read_text(encoding="utf-8")
        self.assertNotIn("#(", snippet)


if __name__ == "__main__":
    unittest.main()
