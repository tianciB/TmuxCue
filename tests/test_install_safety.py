import importlib.machinery
import importlib.util
import io
import json
from pathlib import Path
from contextlib import redirect_stdout
import tempfile
import unittest

ROOT = Path(__file__).parents[1]


class InstallSafetyTests(unittest.TestCase):
    def test_install_and_uninstall_never_control_panes(self):
        scripts = [
            (ROOT / "scripts/install.sh").read_text(encoding="utf-8"),
            (ROOT / "scripts/update.sh").read_text(encoding="utf-8"),
            (ROOT / "scripts/uninstall.sh").read_text(encoding="utf-8"),
        ]
        for script in scripts:
            commands = "\n".join(
                line.strip() for line in script.splitlines()
                if line.strip() and not line.lstrip().startswith("#")
            )
            for forbidden in (
                "tmux kill-",
                "tmux new-session",
                "tmux send-keys",
                "tmux attach",
                "tmux respawn",
                "tmux source-file",
            ):
                self.assertNotIn(forbidden, commands)
        self.assertIn("PANES_BEFORE", scripts[0])
        self.assertIn("PANES_AFTER", scripts[0])
        self.assertIn("AI_BEFORE", scripts[0])
        self.assertIn("AI_AFTER", scripts[0])
        self.assertIn("git pull --ff-only", scripts[1])
        self.assertIn("Refusing to update a dirty worktree", scripts[1])
        self.assertIn('"$HOME/.local/bin/tmuxcue" version', scripts[1])
        self.assertNotIn('"$HOME/.local/bin/tmuxcue" --version', scripts[1])
        self.assertIn("__service", scripts[0])
        self.assertIn("bash-completion", scripts[0])
        self.assertIn("site-functions", scripts[0])
        self.assertIn("fish/completions", scripts[0])

    def test_codex_hook_template_and_readme_match_minimum_trust_set(self):
        template = json.loads((ROOT / "hooks/codex-hooks.json.in").read_text())
        self.assertEqual(set(template), {"UserPromptSubmit", "PermissionRequest", "Stop"})

        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        expected = {
            "UserPromptSubmit": "--agent codex --state running",
            "PermissionRequest": "--agent codex --state approval",
            "Stop": "--agent codex --state done",
        }
        for event, command in expected.items():
            self.assertIn(event, readme)
            self.assertIn(command, readme)
        self.assertIn("Installed 0", readme)
        self.assertIn("Installed 1", readme)
        self.assertIn("git clone https://github.com/tianciB/TmuxCue.git", readme)
        self.assertIn("cd TmuxCue", readme)
        self.assertIn("tmuxcue update", readme)
        self.assertIn("regular terminal", readme)

    def test_systemd_unit_has_basic_hardening(self):
        unit = (ROOT / "systemd/tmuxcue.service").read_text(encoding="utf-8")
        for setting in (
            "NoNewPrivileges=true",
            "UMask=0077",
        ):
            self.assertIn(setting, unit)

    def test_config_merge_is_reversible_and_preserves_other_hooks(self):
        loader = importlib.machinery.SourceFileLoader(
            "configure", str(ROOT / "scripts/configure.py")
        )
        spec = importlib.util.spec_from_loader(loader.name, loader)
        module = importlib.util.module_from_spec(spec)
        loader.exec_module(module)

        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            (home / ".codex").mkdir()
            (home / ".claude").mkdir()
            (home / ".tmux.conf").write_text("set -g mouse off\n", encoding="utf-8")
            other = {
                "hooks": {
                    "Stop": [
                        {"hooks": [{"type": "command", "command": "/usr/bin/true"}]}
                    ]
                }
            }
            (home / ".codex/hooks.json").write_text(json.dumps(other), encoding="utf-8")
            (home / ".claude/settings.json").write_text("{}", encoding="utf-8")

            with redirect_stdout(io.StringIO()):
                module.install(ROOT, home)
            tmux_conf = (home / ".tmux.conf").read_text(encoding="utf-8")
            self.assertIn("# BEGIN tmuxcue", tmux_conf)
            codex = json.loads((home / ".codex/hooks.json").read_text(encoding="utf-8"))
            commands = [
                hook["command"]
                for group in codex["hooks"]["Stop"]
                for hook in group.get("hooks", [])
            ]
            self.assertIn("/usr/bin/true", commands)
            self.assertTrue(any("$HOME/.local/bin/tmuxcue-hook" in command for command in commands))

            with redirect_stdout(io.StringIO()):
                module.uninstall(home)
            self.assertNotIn("# BEGIN tmuxcue", (home / ".tmux.conf").read_text())
            codex = json.loads((home / ".codex/hooks.json").read_text())
            commands = [
                hook["command"]
                for group in codex["hooks"]["Stop"]
                for hook in group.get("hooks", [])
            ]
            self.assertEqual(commands, ["/usr/bin/true"])


if __name__ == "__main__":
    unittest.main()
