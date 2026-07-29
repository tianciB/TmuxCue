import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from tmuxcue.vscode import (
    JsoncError,
    install_remote_settings,
    load_jsonc,
    remote_settings_path,
    state_path,
    uninstall_remote_settings,
)


class VSCodeRemoteSettingsTests(unittest.TestCase):
    def environment(self, home: Path):
        return patch.dict(
            os.environ,
            {
                "HOME": str(home),
                "XDG_CONFIG_HOME": str(home / ".config"),
            },
        )

    def test_install_creates_server_settings_and_uninstall_removes_them(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            with self.environment(home):
                self.assertTrue(install_remote_settings(home))
                settings = remote_settings_path(home)
                values = load_jsonc(settings.read_text(encoding="utf-8"))
                self.assertEqual(values["terminal.integrated.tabs.title"], "${sequence}")
                self.assertEqual(values["terminal.integrated.tabs.description"], "")
                self.assertEqual(stat.S_IMODE(settings.stat().st_mode), 0o600)
                self.assertEqual(stat.S_IMODE(state_path().stat().st_mode), 0o600)

                self.assertTrue(uninstall_remote_settings(home))
                self.assertFalse(settings.exists())
                self.assertFalse(state_path().exists())
                self.assertEqual(list(settings.parent.glob("*.backup-tmuxcue-*")), [])

    def test_jsonc_is_backed_up_and_restored_exactly(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            settings = remote_settings_path(home)
            settings.parent.mkdir(parents=True)
            original = (
                "{\n"
                "  // Keep this comment.\n"
                '  "editor.fontSize": 14,\n'
                '  "terminal.integrated.tabs.title": "custom",\n'
                "}\n"
            )
            settings.write_text(original, encoding="utf-8")

            with self.environment(home):
                self.assertTrue(install_remote_settings(home))
                values = load_jsonc(settings.read_text(encoding="utf-8"))
                self.assertEqual(values["editor.fontSize"], 14)
                self.assertEqual(values["terminal.integrated.tabs.title"], "${sequence}")
                self.assertEqual(values["terminal.integrated.tabs.description"], "")
                backups = list(settings.parent.glob("*.backup-tmuxcue-*"))
                self.assertEqual(len(backups), 1)
                self.assertEqual(backups[0].read_text(encoding="utf-8"), original)

                self.assertTrue(uninstall_remote_settings(home))
                self.assertEqual(settings.read_text(encoding="utf-8"), original)

    def test_reinstall_keeps_the_original_restore_point(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            settings = remote_settings_path(home)
            settings.parent.mkdir(parents=True)
            settings.write_text(
                json.dumps({"terminal.integrated.tabs.title": "original"}) + "\n",
                encoding="utf-8",
            )

            with self.environment(home):
                self.assertTrue(install_remote_settings(home))
                self.assertFalse(install_remote_settings(home))
                self.assertTrue(uninstall_remote_settings(home))
                values = load_jsonc(settings.read_text(encoding="utf-8"))
                self.assertEqual(values["terminal.integrated.tabs.title"], "original")

    def test_reinstall_does_not_overwrite_a_later_user_change(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            settings = remote_settings_path(home)
            settings.parent.mkdir(parents=True)
            settings.write_text("{}\n", encoding="utf-8")

            with self.environment(home):
                install_remote_settings(home)
                values = load_jsonc(settings.read_text(encoding="utf-8"))
                values["terminal.integrated.tabs.title"] = "later-user-choice"
                settings.write_text(json.dumps(values) + "\n", encoding="utf-8")
                self.assertFalse(install_remote_settings(home))
                values = load_jsonc(settings.read_text(encoding="utf-8"))
                self.assertEqual(
                    values["terminal.integrated.tabs.title"],
                    "later-user-choice",
                )

    def test_uninstall_does_not_overwrite_a_later_user_change(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            settings = remote_settings_path(home)
            settings.parent.mkdir(parents=True)
            settings.write_text("{}\n", encoding="utf-8")

            with self.environment(home):
                install_remote_settings(home)
                values = load_jsonc(settings.read_text(encoding="utf-8"))
                values["terminal.integrated.tabs.title"] = "later-user-choice"
                settings.write_text(json.dumps(values), encoding="utf-8")
                uninstall_remote_settings(home)
                values = load_jsonc(settings.read_text(encoding="utf-8"))
                self.assertEqual(
                    values["terminal.integrated.tabs.title"],
                    "later-user-choice",
                )
                self.assertNotIn("terminal.integrated.tabs.description", values)

    def test_invalid_jsonc_and_symlinks_are_rejected_without_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            settings = remote_settings_path(home)
            settings.parent.mkdir(parents=True)
            settings.write_text("{ invalid", encoding="utf-8")

            with self.environment(home):
                with self.assertRaises(JsoncError):
                    install_remote_settings(home)
                self.assertEqual(settings.read_text(encoding="utf-8"), "{ invalid")
                self.assertFalse(state_path().exists())

            settings.unlink()
            target = home / "target.json"
            target.write_text("{}\n", encoding="utf-8")
            settings.symlink_to(target)
            with self.environment(home):
                with self.assertRaises(RuntimeError):
                    install_remote_settings(home)
                self.assertEqual(target.read_text(encoding="utf-8"), "{}\n")

            settings.unlink()
            outside = home / "outside"
            outside.mkdir()
            server = home / ".vscode-server"
            for child in sorted(server.rglob("*"), reverse=True):
                if child.is_dir():
                    child.rmdir()
            server.rmdir()
            server.symlink_to(outside, target_is_directory=True)
            with self.environment(home):
                with self.assertRaises(RuntimeError):
                    install_remote_settings(home)
                self.assertFalse((outside / "data").exists())


if __name__ == "__main__":
    unittest.main()
