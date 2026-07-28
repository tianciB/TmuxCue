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

    def test_override_ignores_terminals_started_before_title_ownership(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"CODEX_HOME": directory}), \
                 patch("tmuxcue.service.title_mode", return_value="override"), \
                 patch("tmuxcue.service.title_override_since", return_value=100):
                daemon = IndicatorDaemon()
                daemon.tmux_detector.scan = lambda: {}
                daemon.tmux_detector.pane_ttys = set()
                old = TerminalRow(1, "/dev/pts/8", "codex", "idle", "/tmp", 99, "codex")
                new = TerminalRow(2, "/dev/pts/9", "codex", "idle", "/tmp", 100, "codex")
                daemon.terminal_detector.scan = lambda _excluded: {
                    "terminal:1": old,
                    "terminal:2": new,
                }
                daemon.tmux.windows = lambda: set()
                daemon.scan()
                self.assertEqual(set(daemon.rows), {"terminal:2"})

    def test_titles_identify_tmux_and_regular_terminals(self):
        metadata = Metadata("codex", "gpt-test", "topic", "session")
        terminal = TerminalRow(123, "/dev/pts/9", "codex", "idle", "/tmp", 1, "codex")
        term_icon, term_title = IndicatorDaemon.format_title(terminal, "running", metadata, 2)
        self.assertEqual(term_icon, "🌒")
        self.assertIn("[term]", term_title)

        from tmuxcue.core import PaneRow
        pane = PaneRow("%1", "s:0", "codex", "working", "/tmp", "")
        _, tmux_title = IndicatorDaemon.format_title(pane, "running", metadata, 2)
        self.assertIn("[tmux]", tmux_title)

    def test_tmux_snippet_never_executes_shell_commands(self):
        snippet = (
            Path(__file__).parents[1] / "tmux" / "tmuxcue.conf"
        ).read_text(encoding="utf-8")
        self.assertNotIn("#(", snippet)


if __name__ == "__main__":
    unittest.main()
