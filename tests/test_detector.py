import unittest

from tmuxcue.detector import TmuxDetector


class DetectorTests(unittest.TestCase):
    def setUp(self):
        self.detector = TmuxDetector(command=lambda _args, _timeout=5: "")

    def test_braille_title_is_working(self):
        self.assertEqual(self.detector.detect_state("codex", "⠙ Codex", ""), "working")

    def test_codex_approval_prompt_is_blocked(self):
        screen = "Press Enter to confirm or Esc to cancel"
        self.assertEqual(self.detector.detect_state("codex", "Codex", screen), "blocked")

    def test_claude_question_dialog_is_blocked(self):
        screen = "Enter to select · Esc to cancel"
        self.assertEqual(self.detector.detect_state("claude", "Claude Code", screen), "blocked")

    def test_plain_prompt_is_idle(self):
        self.assertEqual(self.detector.detect_state("codex", "repo", "› next prompt"), "idle")


if __name__ == "__main__":
    unittest.main()
