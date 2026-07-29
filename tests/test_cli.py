import io
import unittest
from contextlib import redirect_stdout

from tmuxcue.service import build_parser, entrypoint, print_completion


class CliTests(unittest.TestCase):
    def test_public_commands(self):
        parser = build_parser()
        for command in ("update", "version", "uninstall", "help"):
            self.assertEqual(parser.parse_args([command]).command, command)

    def test_version_and_help(self):
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(entrypoint(["version"]), 0)
        self.assertIn("TmuxCue 0.4.0", output.getvalue())

        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(entrypoint(["help"]), 0)
        help_text = output.getvalue()
        for command in ("update", "version", "uninstall", "help"):
            self.assertIn(command, help_text)
        self.assertNotIn("__service", help_text)

    def test_bash_completion_contains_public_commands_and_title_modes(self):
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(print_completion("bash"), 0)
        completion = output.getvalue()
        for value in ("update", "version", "uninstall", "help", "override", "native"):
            self.assertIn(value, completion)


if __name__ == "__main__":
    unittest.main()
