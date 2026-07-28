import io
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from tmuxcue.core import Metadata, TerminalRow
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

    def test_terminal_hook_state_is_resolved_without_process_control(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"CODEX_HOME": directory}):
                daemon = IndicatorDaemon()
                row = TerminalRow(
                    pid=123,
                    tty="/dev/pts/9",
                    agent="codex",
                    state="idle",
                    cwd_hint="/tmp",
                    started_at=1,
                    cmdline="codex",
                )
                now = time.time()
                running = {"state": "running", "timestamp": now - 30}
                daemon.runtime_states["terminal:123"] = None
                self.assertEqual(
                    daemon.resolve_state(row, "idle", None, running, now),
                    "running",
                )
                self.assertEqual(
                    daemon.resolve_state(
                        row, "idle", None, {"state": "done", "timestamp": now}, now
                    ),
                    "done",
                )
                self.assertEqual(
                    daemon.resolve_state(
                        row, "idle", None, {"state": "approval", "timestamp": now}, now
                    ),
                    "approval",
                )

    def test_tmux_snippet_never_executes_shell_commands(self):
        snippet = (
            Path(__file__).parents[1] / "tmux" / "tmuxcue.conf"
        ).read_text(encoding="utf-8")
        self.assertNotIn("#(", snippet)


if __name__ == "__main__":
    unittest.main()
