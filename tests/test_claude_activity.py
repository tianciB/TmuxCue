import os
import tempfile
import unittest
from unittest.mock import Mock, patch

from tmuxcue.core import Metadata, PaneRow
from tmuxcue.detector import TmuxDetector
from tmuxcue.service import IndicatorDaemon


RULE = "─" * 40
PROGRESS = "✽ Thinking… (1m 58s · ↓ 1.5k tokens)"
WAITING = "✻ Waiting for 1 background agent to finish"
COMPLETED = "✻ Baked for 2m 10s · done 9:00 AM"


def frame(status, *, history="", prompt="❯ ", footer="  Model · high\n  auto mode on"):
    return f"{history}\n{status}\n\n{RULE}\n{prompt}\n{RULE}\n{footer}\n"


class ClaudeActivityDetectorTests(unittest.TestCase):
    def setUp(self):
        self.detector = TmuxDetector(command=lambda _args, _timeout=5: "")

    def state(self, screen, agent="claude"):
        return self.detector.detect_state(agent, "✳ Session title", screen)

    def test_progress_without_interrupt_hint_is_working(self):
        for glyph in "·✢✳✶✻✽":
            for meter in (
                "8s · ↓ 42 tokens",
                "1m 58s · ↑ 1.5k tokens",
                "2h 1m 8s · ↓ 1,024 tokens · thinking",
            ):
                with self.subTest(glyph=glyph, meter=meter):
                    self.assertEqual(
                        self.state(frame(f"{glyph} Thinking… ({meter})")),
                        "working",
                    )

    def test_ascii_ellipsis_is_working(self):
        self.assertEqual(
            self.state(frame("✳ Thinking... (8s · ↑ 12 tokens)")), "working",
        )

    def test_positive_background_agent_wait_is_working(self):
        for count, noun in ((1, "agent"), (2, "agents"), (12, "agents")):
            with self.subTest(count=count):
                self.assertEqual(
                    self.state(frame(f"✻ Waiting for {count} background {noun} to finish")),
                    "working",
                )

    def test_input_whitespace_and_multiline_composer_are_supported(self):
        self.assertEqual(
            self.state(frame("  " + PROGRESS, prompt="  ❯\u00a0queued input\n  second line")),
            "working",
        )

    def test_completed_and_non_activity_statuses_remain_idle(self):
        for status in (
            COMPLETED,
            "✻ Interrupted",
            "✻ Waiting for 0 background agents to finish",
            "✻ Waiting for -1 background agents to finish",
            "✻ Waiting for 1 background terminal to finish",
            "✻ Waiting for background agents",
            "Thinking… (8s · ↓ 42 tokens)",
            "✻ Thinking (8s · ↓ 42 tokens)",
            "✻ Thinking… (8s)",
            "✻ Thinking… (8s · 42 tokens)",
            "✻ Thinking… (↓ 42 tokens)",
            "Finished.",
            "",
        ):
            with self.subTest(status=status):
                self.assertEqual(self.state(frame(status)), "idle")

    def test_static_native_title_is_not_activity(self):
        self.assertEqual(self.state(""), "idle")
        self.assertEqual(self.state(frame("Ready.")), "idle")

    def test_old_output_does_not_override_current_completion(self):
        for old in (PROGRESS, WAITING):
            with self.subTest(old=old):
                self.assertEqual(
                    self.state(frame(COMPLETED, history=old)), "idle",
                )

    def test_earlier_composer_does_not_override_current_composer(self):
        self.assertEqual(
            self.state(frame(COMPLETED, history=frame(WAITING))), "idle",
        )

    def test_quoted_and_code_fenced_activity_is_not_current_status(self):
        for status in ("> " + WAITING, "`" + PROGRESS + "`", PROGRESS + "\n```"):
            with self.subTest(status=status):
                self.assertEqual(self.state(frame(status)), "idle")

    def test_activity_requires_a_complete_composer(self):
        for screen in (
            PROGRESS,
            WAITING,
            f"{PROGRESS}\n❯ \n{RULE}",
            f"{PROGRESS}\n{RULE}\n❯ ",
            f"{PROGRESS}\n{RULE}\nnot a prompt\n{RULE}",
        ):
            with self.subTest(screen=screen):
                self.assertEqual(self.state(screen), "idle")

    def test_detection_is_bounded_to_the_last_twenty_lines(self):
        screen = frame(WAITING) + "\n".join(f"output {i}" for i in range(20))
        self.assertEqual(self.state(screen), "idle")

    def test_visible_approval_wins_over_progress_and_waiting(self):
        for status in (PROGRESS, WAITING):
            with self.subTest(status=status):
                self.assertEqual(
                    self.state(frame(status, footer="Enter to select · Esc to cancel")),
                    "blocked",
                )

    def test_claude_footer_does_not_change_other_agents(self):
        for agent in ("codex", "gemini", "unknown"):
            for status in (PROGRESS, WAITING):
                with self.subTest(agent=agent, status=status):
                    self.assertEqual(self.state(frame(status), agent), "idle")

    def test_existing_interrupt_hints_still_work_without_a_composer(self):
        for hint in ("Esc to interrupt", "Ctrl+C to interrupt"):
            with self.subTest(hint=hint):
                self.assertEqual(self.state(hint), "working")

    def test_scan_propagates_current_claude_activity(self):
        def command(args, _timeout=5):
            if args[:3] == ("tmux", "list-panes", "-a"):
                return "%1\ts:0.0\tclaude\t/repo\t✳ Session title\t/dev/pts/1"
            if args[:2] == ("tmux", "capture-pane"):
                return frame(WAITING)
            return ""

        detector = TmuxDetector(
            command=command, process_provider=lambda _root, _uid: {},
        )
        self.assertEqual(detector.scan()["s:0"].state, "working")


class ClaudeActivityDaemonTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        environment = patch.dict(os.environ, {"CODEX_HOME": temporary.name})
        environment.start()
        self.addCleanup(environment.stop)
        self.now = 10_000.0
        self.detector = TmuxDetector(command=lambda _args, _timeout=5: "")
        self.daemon = IndicatorDaemon()
        self.daemon.last_scan = self.now
        self.daemon.metadata_resolver.runtime_snapshot = Mock(
            return_value=("idle", self.now - 60),
        )
        self.metadata = Metadata("claude", "test-model", "topic", "test-session")

    def row(self, status, **kwargs):
        screen = frame(status, **kwargs)
        native = self.detector.detect_state("claude", "✳ Session title", screen)
        return PaneRow(
            "%1", "s:0", "claude", native, "/repo", "", "/dev/pts/1", 123, 1, "claude",
        )

    def resolve(self, row, hook=None, now=None):
        now = self.now if now is None else now
        raw = self.daemon.raw_state(row.window, row, self.metadata, now)
        return self.daemon.resolve_state(row, raw, None, hook, now)

    def test_live_progress_and_waiting_supersede_old_idle_and_stop(self):
        for status in (PROGRESS, WAITING):
            with self.subTest(status=status):
                row = self.row(status)
                self.assertEqual(row.state, "working")
                self.assertEqual(
                    self.resolve(row, {"state": "done", "timestamp": self.now - 5}),
                    "running",
                )
                self.assertEqual(self.daemon.observation_time(row), self.now)

    def test_live_footer_works_when_runtime_is_missing(self):
        self.daemon.metadata_resolver.runtime_snapshot.return_value = (None, 0)
        self.assertEqual(self.resolve(self.row(WAITING)), "running")

    def test_existing_busy_runtime_still_promotes_unrecognized_footer(self):
        self.daemon.metadata_resolver.runtime_snapshot.return_value = (
            "working", self.now - 60,
        )
        self.assertEqual(
            self.resolve(
                self.row("Ready."),
                {"state": "done", "timestamp": self.now - 5},
            ),
            "running",
        )

    def test_visible_approval_stays_authoritative(self):
        self.daemon.metadata_resolver.runtime_snapshot.return_value = (
            "working", self.now - 60,
        )
        row = self.row(PROGRESS, footer="Enter to select · Esc to cancel")
        self.assertEqual(self.resolve(row), "approval")
        self.daemon.metadata_resolver.runtime_snapshot.assert_not_called()

    def test_completed_footer_clears_previous_wait_without_extra_grace(self):
        self.assertEqual(self.resolve(self.row(WAITING)), "running")
        self.daemon.last_scan = self.now + 3
        self.daemon.metadata_resolver.runtime_snapshot.return_value = (
            "idle", self.now + 2,
        )
        self.assertEqual(
            self.resolve(
                self.row(COMPLETED),
                {"state": "done", "timestamp": self.now + 2},
                now=self.now + 3,
            ),
            "done",
        )

    def test_newer_completion_still_supersedes_stale_working_scan(self):
        self.daemon.last_scan = self.now - 5
        self.daemon.metadata_resolver.runtime_snapshot.return_value = (
            "idle", self.now - 1,
        )
        self.assertEqual(self.resolve(self.row(PROGRESS)), "done")

    def test_newer_approval_still_supersedes_stale_working_scan(self):
        self.daemon.last_scan = self.now - 5
        self.assertEqual(
            self.resolve(
                self.row(WAITING),
                {"state": "approval", "timestamp": self.now - 1},
            ),
            "approval",
        )


if __name__ == "__main__":
    unittest.main()
