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

    def test_restore_native_titles_recovers_legacy_override_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "codex").mkdir()
            (root / "claude").mkdir()
            codex = root / "codex/config.toml"
            claude = root / "claude/settings.json"
            gemini = root / "gemini-home/.gemini/settings.json"
            gemini.parent.mkdir(parents=True)
            codex.write_text(
                "[tui]\nterminal_title = [] # managed by TmuxCue\n",
                encoding="utf-8",
            )
            claude.write_text(
                json.dumps(
                    {
                        "env": {
                            "EXISTING": "1",
                            "CLAUDE_CODE_DISABLE_TERMINAL_TITLE": "1",
                        }
                    }
                ),
                encoding="utf-8",
            )
            gemini.write_text(
                json.dumps(
                    {
                        "ui": {
                            "dynamicWindowTitle": False,
                            "showStatusInTitle": False,
                            "theme": "Default",
                        }
                    }
                ),
                encoding="utf-8",
            )

            with self.environment(root):
                settings.state_path().parent.mkdir(parents=True)
                settings.state_path().write_text(
                    json.dumps(
                        {
                            "codex": {
                                "line": 'terminal_title = ["spinner", "project"]',
                                "kind": "section",
                                "section_added": False,
                            },
                            "claude": {"present": False, "value": None},
                            "gemini": {
                                str(gemini): {
                                    "dynamicWindowTitle": {
                                        "present": True,
                                        "value": True,
                                    },
                                    "showStatusInTitle": {
                                        "present": True,
                                        "value": True,
                                    },
                                }
                            },
                        }
                    ),
                    encoding="utf-8",
                )
                settings.config_path().write_text(
                    json.dumps(
                        {
                            "terminal_title": "override",
                            "override_enabled_at": 123,
                            "source_dir": str(root / "source"),
                        }
                    ),
                    encoding="utf-8",
                )

                settings.restore_native_titles()

                self.assertFalse(settings.state_path().exists())
                self.assertEqual(
                    json.loads(settings.config_path().read_text(encoding="utf-8")),
                    {"source_dir": str(root / "source")},
                )
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
                value = json.loads(gemini.read_text())
                self.assertTrue(value["ui"]["dynamicWindowTitle"])
                self.assertTrue(value["ui"]["showStatusInTitle"])
                self.assertEqual(value["ui"]["theme"], "Default")

    def test_gemini_title_restore_removes_keys_added_by_tmuxcue(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "codex").mkdir()
            (root / "claude").mkdir()
            (root / "codex/config.toml").write_text(
                "[tui]\nterminal_title = [] # managed by TmuxCue\n",
                encoding="utf-8",
            )
            (root / "claude/settings.json").write_text(
                json.dumps({"env": {"CLAUDE_CODE_DISABLE_TERMINAL_TITLE": "1"}}),
                encoding="utf-8",
            )
            gemini = root / "gemini-home/.gemini/settings.json"
            gemini.parent.mkdir(parents=True)
            gemini.write_text(
                json.dumps(
                    {
                        "ui": {
                            "dynamicWindowTitle": False,
                            "showStatusInTitle": False,
                            "theme": "Default",
                        }
                    }
                ),
                encoding="utf-8",
            )

            with self.environment(root):
                settings.state_path().parent.mkdir(parents=True)
                settings.state_path().write_text(
                    json.dumps(
                        {
                            "codex": {
                                "line": None,
                                "kind": "section",
                                "section_added": True,
                            },
                            "claude": {"present": False, "value": None},
                            "gemini": {
                                str(gemini): {
                                    "dynamicWindowTitle": {
                                        "present": False,
                                        "value": None,
                                    },
                                    "showStatusInTitle": {
                                        "present": False,
                                        "value": None,
                                    },
                                }
                            },
                        }
                    ),
                    encoding="utf-8",
                )
                settings.restore_native_titles()

            self.assertEqual(
                (root / "codex/config.toml").read_text(encoding="utf-8").strip(),
                "",
            )
            self.assertEqual(
                json.loads((root / "claude/settings.json").read_text(encoding="utf-8")),
                {},
            )
            value = json.loads(gemini.read_text())
            self.assertEqual(value, {"ui": {"theme": "Default"}})

    def test_source_directory_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            with self.environment(root):
                settings.set_source_dir(source)
                self.assertEqual(settings.source_dir(), source.resolve())


if __name__ == "__main__":
    unittest.main()
