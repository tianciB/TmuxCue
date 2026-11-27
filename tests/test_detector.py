import unittest

from tmuxcue.detector import TmuxDetector


class DetectorTests(unittest.TestCase):
    def setUp(self):
        self.detector = TmuxDetector(command=lambda _args, _timeout=5: "")

    def test_side_conversation_active_matches_main_thread_label(self):
        self.assertTrue(
            self.detector.side_conversation_active(
                "output\nSide from main thread · ctrl+c to close"
            )
        )

    def test_side_conversation_active_matches_named_parent_label(self):
        self.assertTrue(
            self.detector.side_conversation_active(
                "Side from parent thread (task) · ctrl+c to close"
            )
        )

    def test_side_conversation_active_ignores_parent_view_hint(self):
        self.assertFalse(
            self.detector.side_conversation_active("ctrl+x for side")
        )

    def test_side_conversation_active_is_bounded_to_last_twenty_lines(self):
        screen = "Side from main thread\n" + "\n".join(
            f"output {index}" for index in range(20)
        )
        self.assertFalse(self.detector.side_conversation_active(screen))

    def test_scan_propagates_active_side_context(self):
        def command(args, _timeout=5):
            if args[:3] == ("tmux", "list-panes", "-a"):
                return "%1\ts:0.0\tcodex\t/tmp\tCodex\t/dev/pts/1"
            if len(args) > 1 and args[1] == "capture-pane":
                return "Side from main thread · ctrl+c to close"
            return ""

        detector = TmuxDetector(
            command=command,
            process_provider=lambda _root, _uid: {},
        )

        rows = detector.scan()

        self.assertTrue(rows["s:0"].side_active)

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

    def test_scan_recognizes_wrapped_agy_process(self):
        def command(args, _timeout=5):
            if args[:3] == ("tmux", "list-panes", "-a"):
                return "%2\ts:0.0\tbash\t/tmp\thostname\t/dev/pts/4"
            if len(args) > 1 and args[1] == "capture-pane":
                return (
                    "⣷  Running command... 18s\n"
                    "────────\n"
                    ">\n"
                    "────────\n"
                    "esc to cancel          Gemini 4 Neon · xhigh\n"
                    + "\n" * 40
                )
            return ""

        processes = {
            ("/dev/pts/4", "agy"): (
                202,
                100,
                "/opt/agy/bin/agy --model neon-xhigh --effort xhigh",
            )
        }
        detector = TmuxDetector(
            command=command,
            process_provider=lambda _root, _uid: processes,
        )

        rows = detector.scan()

        self.assertEqual(rows["s:0"].pid, 202)
        self.assertEqual(rows["s:0"].agent, "agy")
        self.assertEqual(rows["s:0"].state, "working")

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

    def test_static_claude_session_title_is_not_working_evidence(self):
        self.assertEqual(
            self.detector.detect_state(
                "claude",
                "✳ Derived session title",
                "",
            ),
            "idle",
        )

    def test_agy_states_handle_trailing_blank_lines(self):
        working = (
            "⣷  Running command... 18s\n"
            "────────\n"
            ">\n"
            "────────\n"
            "esc to cancel\n"
            + "\n" * 40
        )
        self.assertEqual(
            self.detector.detect_state("agy", "build-host", working),
            "working",
        )

        spinner_only = (
            "⣯  Thinking... 5s\n"
            "────────\n"
            ">\n"
            "────────\n"
            + "\n" * 40
        )
        self.assertEqual(
            self.detector.detect_state("agy", "build-host", spinner_only),
            "working",
        )

        approval = (
            "Run this command?\n"
            "Yes, allow tool call\n"
            "No, deny tool call\n"
            + "\n" * 40
        )
        self.assertEqual(
            self.detector.detect_state("agy", "build-host", approval),
            "blocked",
        )

        idle = (
            "Analysis finished.\n"
            "────────\n"
            ">\n"
            "────────\n"
            "Gemini 4 Neon · xhigh\n"
            + "\n" * 40
        )
        self.assertEqual(
            self.detector.detect_state("agy", "build-host", idle),
            "idle",
        )

    def test_agy_old_approval_outside_nonblank_tail_is_ignored(self):
        screen = (
            "Run this command?\n"
            "Yes, allow tool call\n"
            + "\n".join(f"output {index}" for index in range(13))
            + "\n" * 30
        )
        self.assertEqual(
            self.detector.detect_state("agy", "build-host", screen),
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
