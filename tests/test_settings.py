import json
import os
from pathlib import Path
import tempfile
import tomllib
import unittest
from unittest.mock import patch

from tmuxcue import settings


class TitleModeTests(unittest.TestCase):
    def environment(self, root: Path):
        return patch.dict(
            os.environ,
            {
                "HOME": str(root),
                "XDG_CONFIG_HOME": str(root / "config"),
                "CODEX_HOME": str(root / "codex"),
                "CLAUDE_HOME": str(root / "claude"),
            },
        )

    def test_native_is_default_and_override_restores_native_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "codex").mkdir()
            (root / "claude").mkdir()
            codex = root / "codex/config.toml"
            claude = root / "claude/settings.json"
            codex.write_text(
                '[tui]\nterminal_title = ["spinner", "project"]\n',
                encoding="utf-8",
            )
            claude.write_text(
                json.dumps({"env": {"EXISTING": "1"}}),
                encoding="utf-8",
            )

            with self.environment(root):
                self.assertEqual(settings.title_mode(), "native")
                settings.apply_title_mode("override")
                self.assertGreater(settings.title_override_since(), 0)
                self.assertIn("terminal_title = [] # managed by TmuxCue", codex.read_text())
                self.assertEqual(tomllib.loads(codex.read_text())["tui"]["terminal_title"], [])
                value = json.loads(claude.read_text())
                self.assertEqual(value["env"]["CLAUDE_CODE_DISABLE_TERMINAL_TITLE"], "1")
                self.assertEqual(value["env"]["EXISTING"], "1")

                settings.apply_title_mode("native")
                self.assertEqual(settings.title_override_since(), 0)
                self.assertIn(
                    'terminal_title = ["spinner", "project"]', codex.read_text()
                )
                self.assertEqual(
                    tomllib.loads(codex.read_text())["tui"]["terminal_title"],
                    ["spinner", "project"],
                )
                value = json.loads(claude.read_text())
                self.assertNotIn("CLAUDE_CODE_DISABLE_TERMINAL_TITLE", value["env"])
                self.assertEqual(value["env"]["EXISTING"], "1")

    def test_source_directory_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            with self.environment(root):
                settings.set_source_dir(source)
                self.assertEqual(settings.source_dir(), source.resolve())

    def test_invalid_title_mode_falls_back_to_native(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config/tmuxcue/config.json"
            config.parent.mkdir(parents=True)
            config.write_text(
                json.dumps({"terminal_title": "invalid"}),
                encoding="utf-8",
            )

            with self.environment(root):
                self.assertEqual(settings.title_mode(), "native")


if __name__ == "__main__":
    unittest.main()
