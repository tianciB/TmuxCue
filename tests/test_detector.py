import unittest

from tmuxcue.detector import TmuxDetector


class DetectorTests(unittest.TestCase):
    def setUp(self):
        self.detector = TmuxDetector(command=lambda _args, _timeout=5: "")

    def test_scan_reuses_process_snapshot_for_wrapped_agent(self):
        commands = []

        def command(args, _timeout=5):
            commands.append(args)
            if args[:3] == ("tmux", "list-panes", "-a"):
                return "%1\ts:0.0\tbash\t/tmp\t⠙ Codex\t/dev/pts/1"
            if len(args) > 1 and args[1] == "capture-pane":
                return ""
            return ""

        processes = {("/dev/pts/1", "codex"): (123, 100, "codex resume")}
        detector = TmuxDetector(
            command=command,
            process_provider=lambda _root, _uid: processes,
        )
        rows = detector.scan()
        self.assertEqual(set(rows), {"s:0"})
        self.assertEqual(rows["s:0"].pid, 123)
        self.assertEqual(rows["s:0"].agent, "codex")
        self.assertEqual(rows["s:0"].state, "working")
        self.assertFalse(any(args[0] == "ps" for args in commands))

    def test_scan_recognizes_wrapped_gemini_process(self):
        def command(args, _timeout=5):
            if args[:3] == ("tmux", "list-panes", "-a"):
                return "%1\ts:0.0\tbash\t/tmp\t◇  Ready (tmp)\t/dev/pts/7"
            if len(args) > 1 and args[1] == "capture-pane":
                return ""
            return ""

        processes = {
            ("/dev/pts/7", "gemini"): (
                202,
                100,
                "node /opt/node_modules/.bin/gemini",
            )
        }
        detector = TmuxDetector(
            command=command,
            process_provider=lambda _root, _uid: processes,
        )

        rows = detector.scan()

        self.assertEqual(rows["s:0"].pid, 202)
        self.assertEqual(rows["s:0"].agent, "gemini")

    def test_braille_title_is_working(self):
        self.assertEqual(self.detector.detect_state("codex", "⠙ Codex", ""), "working")

    def test_native_title_subject_strips_spinner_and_repository(self):
        self.assertEqual(
            self.detector.title_subject("⠙ Exact thread name | repository"),
            "Exact thread name",
        )
        self.assertEqual(
            self.detector.title_subject("Exact thread name | repository"),
            "Exact thread name",
        )

    def test_codex_approval_prompt_is_blocked(self):
        screen = "Press Enter to confirm or Esc to cancel"
        self.assertEqual(self.detector.detect_state("codex", "Codex", screen), "blocked")

    def test_claude_question_dialog_is_blocked(self):
        screen = "Enter to select · Esc to cancel"
        self.assertEqual(self.detector.detect_state("claude", "Claude Code", screen), "blocked")

    def test_gemini_native_titles_map_to_shared_states(self):
        self.assertEqual(
            self.detector.detect_state("gemini", "✦ Working (repo)", ""),
            "working",
        )
        self.assertEqual(
            self.detector.detect_state("gemini", "⏲ Working… (repo)", ""),
            "working",
        )
        self.assertEqual(
            self.detector.detect_state("gemini", "✋ Action Required (repo)", ""),
            "working",
        )
        self.assertEqual(
            self.detector.detect_state("gemini", "◇ Ready (repo)", ""),
            "idle",
        )

    def test_gemini_action_title_with_confirmation_dialog_is_blocked(self):
        screen = "Action Required\nAllow once\nAllow for this session"
        self.assertEqual(
            self.detector.detect_state(
                "gemini",
                "✋ Action Required (repo)",
                screen,
            ),
            "blocked",
        )

    def test_old_gemini_confirmation_text_outside_tail_does_not_block(self):
        screen = "Action Required\nAllow once\n" + "\n".join(
            f"output {index}" for index in range(13)
        )
        self.assertEqual(
            self.detector.detect_state(
                "gemini",
                "✋ Action Required (repo)",
                screen,
            ),
            "working",
        )

    def test_gemini_native_status_is_not_used_as_a_topic(self):
        self.assertEqual(self.detector.title_subject("◇ Ready (repo)"), "")
        self.assertEqual(self.detector.title_subject("✦ Working (repo)"), "")
        self.assertEqual(
            self.detector.title_subject("✦ Reviewing changes (repo)"),
            "",
        )
        self.assertEqual(
            self.detector.title_subject("⏲ Working… (repo)"),
            "",
        )

    def test_gemini_old_permission_text_outside_tail_is_ignored(self):
        screen = "Allow execution?\n" + "\n".join(
            f"output {index}" for index in range(13)
        )
        self.assertEqual(
            self.detector.detect_state("gemini", "◇ Ready (repo)", screen),
            "idle",
        )

    def test_current_working_title_beats_old_approval_text(self):
        screen = "Press Enter to confirm or Esc to cancel"
        self.assertEqual(self.detector.detect_state("codex", "⠙ Codex", screen), "working")

    def test_old_approval_text_outside_dialog_tail_is_ignored(self):
        screen = "Press Enter to confirm or Esc to cancel\n" + "\n".join(
            f"output {index}" for index in range(13)
        )
        self.assertEqual(self.detector.detect_state("codex", "Codex", screen), "idle")

    def test_old_interrupt_text_outside_status_tail_is_ignored(self):
        screen = "Esc to interrupt\n" + "\n".join(
            f"output {index}" for index in range(13)
        )
        self.assertEqual(self.detector.detect_state("codex", "Codex", screen), "idle")

    def test_plain_prompt_is_idle(self):
        self.assertEqual(self.detector.detect_state("codex", "repo", "› next prompt"), "idle")


if __name__ == "__main__":
    unittest.main()
