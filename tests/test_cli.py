import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from tmuxcue.service import build_parser, entrypoint, print_completion
from tmuxcue.processes import ProcessBinding


class CliTests(unittest.TestCase):
    def test_public_commands(self):
        parser = build_parser()
        for command in ("update", "version", "uninstall", "help"):
            self.assertEqual(parser.parse_args([command]).command, command)

    def test_version_and_help(self):
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(entrypoint(["version"]), 0)
        self.assertIn("TmuxCue 0.5.5", output.getvalue())

        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(entrypoint(["help"]), 0)
        help_text = output.getvalue()
        for command in ("update", "version", "uninstall", "help"):
            self.assertIn(command, help_text)
        self.assertIn("Codex, Claude, and Agy", help_text)
        self.assertNotIn("__service", help_text)

    def test_bash_completion_contains_public_commands_only(self):
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(print_completion("bash"), 0)
        completion = output.getvalue()
        for value in ("update", "version", "uninstall", "help"):
            self.assertIn(value, completion)
        for removed in ("config", "terminal-title", "override", "native"):
            self.assertNotIn(removed, completion)

    def test_hidden_process_snapshot_includes_agy(self):
        bindings = [
            ProcessBinding(
                agent="agy",
                pid=202,
                started_at=900,
                tty="/dev/pts/4",
                cmdline="/opt/agy/bin/agy --model neon-xhigh",
            ),
            ProcessBinding(
                agent="codex",
                pid=101,
                started_at=800,
                tty="/dev/pts/1",
                cmdline="codex",
            ),
        ]
        output = io.StringIO()
        with (
            patch(
                "tmuxcue.service.discover_all_agent_processes",
                return_value=bindings,
            ),
            redirect_stdout(output),
        ):
            self.assertEqual(entrypoint(["__process-snapshot"]), 0)

        self.assertEqual(
            output.getvalue().splitlines(),
            [
                "agy:202:900:/dev/pts/4",
                "codex:101:800:/dev/pts/1",
            ],
        )


if __name__ == "__main__":
    unittest.main()
