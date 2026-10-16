import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from unittest.mock import Mock, patch

from tmuxcue.core import Metadata, PaneRow, TerminalRow
from tmuxcue.service import IndicatorDaemon, TmuxClient


class DaemonTests(unittest.TestCase):
    def test_codex_history_event_marks_matching_window_running(self):
        with tempfile.TemporaryDirectory() as directory:
            codex_home = Path(directory)
            history = codex_home / "history.jsonl"
            history.write_text("", encoding="utf-8")
            with patch.dict(os.environ, {"CODEX_HOME": str(codex_home)}):
                daemon = IndicatorDaemon()
                daemon.metadata["codex:0"] = Metadata(
                    "codex", "gpt-test", "topic", "session-1"
                )
                with history.open("a", encoding="utf-8") as stream:
                    stream.write(
                        json.dumps(
                            {"session_id": "session-1", "ts": int(time.time()), "text": "run"}
                        )
                        + "\n"
                    )
                with redirect_stdout(io.StringIO()):
                    affected = daemon.poll_prompt_events(time.time())
                self.assertEqual(affected, {"codex:0"})
                self.assertIn("codex:0", daemon.prompts)

    def test_terminal_hook_state_is_resolved_without_process_control(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"CODEX_HOME": directory}):
                daemon = IndicatorDaemon()
                row = TerminalRow(
                    pid=123,
                    tty="/dev/pts/9",
                    agent="codex",
                    state="idle",
                    cwd_hint="/tmp",
                    started_at=1,
                    cmdline="codex",
                )
                now = time.time()
                running = {"state": "running", "timestamp": now - 30}
                daemon.runtime_states["terminal:123"] = None
                self.assertEqual(
                    daemon.resolve_state(row, "idle", None, running, now),
                    "running",
                )
                self.assertEqual(
                    daemon.resolve_state(
                        row, "idle", None, {"state": "done", "timestamp": now}, now
                    ),
                    "done",
                )
                self.assertEqual(
                    daemon.resolve_state(
                        row, "idle", None, {"state": "approval", "timestamp": now}, now
                    ),
                    "approval",
                )

    def test_fresh_hook_events_override_a_cached_working_scan(self):
        daemon = IndicatorDaemon()
        row = PaneRow("%1", "s:0", "codex", "working", "/tmp", "", "/dev/pts/1")
        now = time.time()
        daemon.last_scan = now - 1
        self.assertEqual(
            daemon.resolve_state(
                row, "working", None, {"state": "approval", "timestamp": now}, now
            ),
            "approval",
        )
        self.assertEqual(
            daemon.resolve_state(
                row, "working", None, {"state": "done", "timestamp": now}, now
            ),
            "done",
        )

        daemon.last_scan = now + 1
        self.assertEqual(
            daemon.resolve_state(
                row, "working", None, {"state": "approval", "timestamp": now}, now
            ),
            "running",
        )

    def test_fresh_working_scan_supersedes_older_approval_hook(self):
        daemon = IndicatorDaemon()
        now = time.time()
        row = PaneRow(
            "%1", "s:0", "codex", "working", "/tmp", "", "/dev/pts/1"
        )
        metadata = Metadata("codex", "model", "topic", "session")
        daemon.last_scan = now
        daemon.metadata_resolver.runtime_snapshot = Mock(
            return_value=("working", now - 20)
        )

        raw_state = daemon.raw_state("s:0", row, metadata, now)

        self.assertEqual(raw_state, "working")
        self.assertEqual(daemon.observation_time(row), now)
        self.assertEqual(
            daemon.resolve_state(
                row,
                raw_state,
                None,
                {
                    "state": "approval",
                    "event": "PermissionRequest",
                    "timestamp": now - 5,
                },
                now,
            ),
            "running",
        )

    def test_gemini_working_scan_supersedes_older_permission_hook(self):
        daemon = IndicatorDaemon()
        now = time.time()
        row = PaneRow(
            "%7",
            "g:0",
            "gemini",
            "working",
            "/repo",
            "",
            "/dev/pts/7",
            321,
            int(now - 100),
            "node /opt/node_modules/.bin/gemini",
        )
        metadata = Metadata("gemini", "model", "topic", "session")
        daemon.last_scan = now
        daemon.metadata_resolver.runtime_snapshot = Mock(return_value=(None, 0.0))

        raw_state = daemon.raw_state("g:0", row, metadata, now)

        self.assertEqual(raw_state, "working")
        self.assertEqual(
            daemon.resolve_state(
                row,
                raw_state,
                None,
                {
                    "state": "approval",
                    "event": "Notification",
                    "timestamp": now - 5,
                },
                now,
            ),
            "running",
        )

    def test_claude_busy_runtime_promotes_an_idle_native_pane(self):
        daemon = IndicatorDaemon()
        now = time.time()
        daemon.last_scan = now
        row = PaneRow(
            "%8",
            "g:0",
            "claude",
            "idle",
            "/repo",
            "",
            "/dev/pts/8",
            401,
            int(now - 100),
            "claude",
        )
        metadata = Metadata("claude", "gemini-test", "topic", "session")
        daemon.metadata_resolver.runtime_snapshot = Mock(
            return_value=("working", now - 30)
        )

        raw_state = daemon.raw_state("g:0", row, metadata, now)

        self.assertEqual(raw_state, "working")
        self.assertEqual(daemon.observation_time(row), now)
        self.assertEqual(
            daemon.resolve_state(
                row,
                raw_state,
                None,
                {
                    "state": "done",
                    "event": "Stop",
                    "timestamp": now - 5,
                },
                now,
            ),
            "running",
        )

    def test_claude_blocked_pane_beats_busy_runtime(self):
        daemon = IndicatorDaemon()
        now = time.time()
        daemon.last_scan = now
        row = PaneRow(
            "%8",
            "g:0",
            "claude",
            "blocked",
            "/repo",
            "",
            "/dev/pts/8",
        )
        metadata = Metadata("claude", "model", "topic", "session")
        daemon.metadata_resolver.runtime_snapshot = Mock(
            return_value=("working", now)
        )

        raw_state = daemon.raw_state("g:0", row, metadata, now)

        self.assertEqual(raw_state, "blocked")
        daemon.metadata_resolver.runtime_snapshot.assert_not_called()
        self.assertEqual(
            daemon.resolve_state(row, raw_state, None, None, now),
            "approval",
        )

    def test_claude_idle_runtime_and_missing_runtime_remain_idle(self):
        daemon = IndicatorDaemon()
        now = time.time()
        daemon.last_scan = now
        row = PaneRow(
            "%8",
            "g:0",
            "claude",
            "idle",
            "/repo",
            "",
            "/dev/pts/8",
        )
        metadata = Metadata("claude", "model", "topic", "session")
        daemon.metadata_resolver.runtime_snapshot = Mock(
            return_value=("idle", now)
        )

        self.assertEqual(daemon.raw_state("g:0", row, metadata, now), "idle")
        daemon.metadata_resolver.runtime_snapshot.assert_called_once()

        daemon.runtime_times["g:0"] = 0
        daemon.metadata_resolver.runtime_snapshot = Mock(
            return_value=(None, 0.0)
        )
        self.assertEqual(
            daemon.raw_state("g:0", row, metadata, now + 2),
            "idle",
        )
        daemon.metadata_resolver.runtime_snapshot.assert_called_once()

    def test_codex_active_goal_promotes_idle_pane_and_supersedes_done(self):
        daemon = IndicatorDaemon()
        now = time.time()
        daemon.last_scan = now
        row = PaneRow(
            "%1",
            "s:0",
            "codex",
            "idle",
            "/repo",
            "",
            "/dev/pts/1",
        )
        metadata = Metadata("codex", "model", "topic", "session")
        daemon.metadata_resolver.runtime_snapshot = Mock(
            return_value=("goal_active", now - 30)
        )

        raw_state = daemon.raw_state("s:0", row, metadata, now)

        self.assertEqual(raw_state, "working")
        self.assertEqual(daemon.observation_time(row), now)
        self.assertEqual(
            daemon.resolve_state(
                row,
                raw_state,
                None,
                {
                    "state": "done",
                    "event": "Stop",
                    "timestamp": now - 5,
                },
                now,
            ),
            "running",
        )

    def test_active_side_idle_ignores_parent_goal(self):
        daemon = IndicatorDaemon()
        now = time.time()
        daemon.last_scan = now
        row = PaneRow(
            "%1",
            "s:0",
            "codex",
            "idle",
            "/repo",
            "",
            "/dev/pts/1",
            side_active=True,
        )
        metadata = Metadata("codex", "model", "topic", "session")
        daemon.metadata_resolver.runtime_snapshot = Mock(
            return_value=("goal_active", now - 30)
        )

        raw_state = daemon.raw_state("s:0", row, metadata, now)

        self.assertEqual(raw_state, "idle")
        self.assertEqual(
            daemon.resolve_state(row, raw_state, None, None, now),
            "done",
        )
        daemon.metadata_resolver.runtime_snapshot.assert_not_called()

    def test_active_side_working_ignores_parent_done_hook(self):
        daemon = IndicatorDaemon()
        now = time.time()
        daemon.last_scan = now
        row = PaneRow(
            "%1",
            "s:0",
            "codex",
            "working",
            "/repo",
            "",
            "/dev/pts/1",
            side_active=True,
        )
        daemon.rows = {"s:0": row}
        daemon.metadata = {
            "s:0": Metadata("codex", "model", "topic", "session")
        }
        daemon.tmux.set_many = Mock(return_value=True)
        daemon.metadata_resolver.runtime_snapshot = Mock(
            return_value=("idle", now)
        )

        changed = daemon.apply_targets(
            {"s:0"},
            {
                ("codex", "session"): {
                    "state": "done",
                    "timestamp": now + 1,
                }
            },
            now,
        )

        self.assertTrue(changed)
        self.assertEqual(daemon.applied["s:0"][0], "running")
        daemon.metadata_resolver.runtime_snapshot.assert_not_called()

    def test_active_side_blocked_ignores_parent_goal(self):
        daemon = IndicatorDaemon()
        now = time.time()
        daemon.last_scan = now
        row = PaneRow(
            "%1",
            "s:0",
            "codex",
            "blocked",
            "/repo",
            "",
            "/dev/pts/1",
            side_active=True,
        )
        metadata = Metadata("codex", "model", "topic", "session")
        daemon.metadata_resolver.runtime_snapshot = Mock(
            return_value=("goal_active", now - 30)
        )

        raw_state = daemon.raw_state("s:0", row, metadata, now)

        self.assertEqual(raw_state, "blocked")
        self.assertEqual(
            daemon.resolve_state(row, raw_state, None, None, now),
            "approval",
        )
        daemon.metadata_resolver.runtime_snapshot.assert_not_called()

    def test_active_side_ignores_parent_prompt_grace(self):
        daemon = IndicatorDaemon()
        now = time.time()
        daemon.last_scan = now
        row = PaneRow(
            "%1",
            "s:0",
            "codex",
            "idle",
            "/repo",
            "",
            "/dev/pts/1",
            side_active=True,
        )
        daemon.rows = {"s:0": row}
        daemon.metadata = {
            "s:0": Metadata("codex", "model", "topic", "session")
        }
        daemon.prompts = {
            "s:0": {"at": now, "saw_working": False}
        }
        daemon.tmux.set_many = Mock(return_value=True)

        changed = daemon.apply_targets({"s:0"}, {}, now)

        self.assertTrue(changed)
        self.assertEqual(daemon.applied["s:0"][0], "done")

    def test_side_context_changes_tmux_identity_and_metadata_source(self):
        daemon = IndicatorDaemon()
        parent = PaneRow(
            "%1", "s:0", "codex", "idle", "/repo", "", "/dev/pts/1"
        )
        side = PaneRow(
            "%1",
            "s:0",
            "codex",
            "idle",
            "/repo",
            "",
            "/dev/pts/1",
            side_active=True,
        )

        self.assertNotEqual(
            daemon.row_identity(parent),
            daemon.row_identity(side),
        )
        self.assertNotEqual(
            daemon.metadata_source_key(parent),
            daemon.metadata_source_key(side),
        )

    def test_codex_blocked_goal_promotes_idle_pane_to_approval(self):
        daemon = IndicatorDaemon()
        now = time.time()
        daemon.last_scan = now
        row = PaneRow(
            "%1",
            "s:0",
            "codex",
            "idle",
            "/repo",
            "",
            "/dev/pts/1",
        )
        metadata = Metadata("codex", "model", "topic", "session")
        daemon.metadata_resolver.runtime_snapshot = Mock(
            return_value=("goal_blocked", now - 30)
        )

        raw_state = daemon.raw_state("s:0", row, metadata, now)

        self.assertEqual(raw_state, "blocked")
        self.assertEqual(daemon.observation_time(row), now)
        self.assertEqual(
            daemon.resolve_state(row, raw_state, None, None, now),
            "approval",
        )

    def test_codex_idle_pane_ignores_ordinary_runtime_working(self):
        daemon = IndicatorDaemon()
        now = time.time()
        daemon.last_scan = now
        row = PaneRow(
            "%1",
            "s:0",
            "codex",
            "idle",
            "/repo",
            "",
            "/dev/pts/1",
        )
        metadata = Metadata("codex", "model", "topic", "session")
        daemon.metadata_resolver.runtime_snapshot = Mock(
            return_value=("working", now)
        )

        self.assertEqual(daemon.raw_state("s:0", row, metadata, now), "idle")
        daemon.metadata_resolver.runtime_snapshot.assert_called_once()

    def test_codex_terminal_goal_status_remains_done_when_idle(self):
        daemon = IndicatorDaemon()
        now = time.time()
        daemon.last_scan = now
        row = PaneRow(
            "%1",
            "s:0",
            "codex",
            "idle",
            "/repo",
            "",
            "/dev/pts/1",
        )
        metadata = Metadata("codex", "model", "topic", "session")
        daemon.metadata_resolver.runtime_snapshot = Mock(
            return_value=("idle", now)
        )

        raw_state = daemon.raw_state("s:0", row, metadata, now)

        self.assertEqual(raw_state, "idle")
        self.assertEqual(
            daemon.resolve_state(row, raw_state, None, None, now),
            "done",
        )

    def test_direct_terminal_codex_active_goal_is_running(self):
        daemon = IndicatorDaemon()
        now = time.time()
        row = TerminalRow(
            pid=123,
            tty="/dev/pts/9",
            agent="codex",
            state="idle",
            cwd_hint="/repo",
            started_at=int(now - 100),
            cmdline="codex",
        )
        metadata = Metadata("codex", "model", "topic", "session")
        daemon.metadata_resolver.runtime_snapshot = Mock(
            return_value=("goal_active", now - 30)
        )

        raw_state = daemon.raw_state("terminal:123", row, metadata, now)

        self.assertEqual(raw_state, "working")
        self.assertEqual(
            daemon.resolve_state(row, raw_state, None, None, now),
            "running",
        )

    def test_fresh_working_scan_supersedes_older_task_complete_and_stop(self):
        daemon = IndicatorDaemon()
        now = time.time()
        completed_at = now - 5
        daemon.last_scan = now
        row = PaneRow(
            "%1",
            "s:0",
            "codex",
            "working",
            "/tmp",
            "",
            "/dev/pts/1",
            123,
            int(now - 100),
            "codex resume",
        )
        metadata = Metadata("codex", "model", "topic", "session")
        daemon.metadata_resolver.runtime_snapshot = Mock(
            return_value=("idle", completed_at)
        )

        raw_state = daemon.raw_state("s:0", row, metadata, now)

        self.assertEqual(raw_state, "working")
        self.assertEqual(daemon.observation_time(row), now)
        self.assertEqual(
            daemon.resolve_state(
                row,
                raw_state,
                None,
                {"state": "done", "timestamp": completed_at},
                now,
            ),
            "running",
        )

    def test_newer_task_complete_still_beats_a_cached_working_scan(self):
        daemon = IndicatorDaemon()
        now = time.time()
        completed_at = now - 1
        daemon.last_scan = now - 5
        row = PaneRow(
            "%1",
            "s:0",
            "codex",
            "working",
            "/tmp",
            "",
            "/dev/pts/1",
            123,
            int(now - 100),
            "codex resume",
        )
        metadata = Metadata("codex", "model", "topic", "session")
        daemon.metadata_resolver.runtime_snapshot = Mock(
            return_value=("idle", completed_at)
        )

        raw_state = daemon.raw_state("s:0", row, metadata, now)

        self.assertEqual(raw_state, "idle")
        self.assertEqual(
            daemon.resolve_state(
                row,
                raw_state,
                None,
                {"state": "done", "timestamp": completed_at},
                now,
            ),
            "done",
        )

    def test_fresh_idle_scan_supersedes_older_approval_hook(self):
        daemon = IndicatorDaemon()
        now = time.time()
        metadata = Metadata("codex", "model", "topic", "session")
        row = PaneRow(
            "%1", "s:0", "codex", "idle", "/tmp", "", "/dev/pts/1"
        )
        daemon.last_scan = now
        daemon.runtime_states["s:0"] = "working"
        daemon.runtime_event_times["s:0"] = now - 20

        raw_state = daemon.raw_state("s:0", row, metadata, now)

        self.assertEqual(raw_state, "idle")
        self.assertEqual(daemon.observation_time(row), now)
        self.assertEqual(
            daemon.resolve_state(
                row,
                raw_state,
                None,
                {
                    "state": "approval",
                    "event": "PermissionRequest",
                    "timestamp": now - 5,
                },
                now,
            ),
            "done",
        )

        blocked = PaneRow(
            "%2", "s:1", "codex", "blocked", "/tmp", "", "/dev/pts/2"
        )
        self.assertEqual(
            daemon.resolve_state(
                blocked,
                daemon.raw_state("s:1", blocked, metadata, now),
                None,
                {
                    "state": "approval",
                    "event": "PermissionRequest",
                    "timestamp": now - 5,
                },
                now,
            ),
            "approval",
        )

    def test_turn_aborted_overrides_stale_running_ui_and_hook(self):
        daemon = IndicatorDaemon()
        now = time.time()
        daemon.last_scan = now
        row = PaneRow(
            "%1", "s:0", "codex", "working", "/tmp", "", "/dev/pts/1", 123, 1, "codex"
        )
        metadata = Metadata("codex", "model", "topic", "session")
        aborted_at = now - 1
        daemon.metadata_resolver.runtime_snapshot = Mock(
            return_value=("aborted", aborted_at)
        )
        raw_state = daemon.raw_state("s:0", row, metadata, now)
        self.assertEqual(raw_state, "done")
        self.assertEqual(
            daemon.resolve_state(
                row,
                raw_state,
                {"at": aborted_at - 5, "saw_working": False},
                {"state": "running", "timestamp": aborted_at - 5},
                now,
            ),
            "done",
        )
        self.assertEqual(
            daemon.resolve_state(
                row,
                raw_state,
                None,
                {"state": "running", "timestamp": aborted_at + 0.5},
                now,
            ),
            "running",
        )

    def test_runtime_event_before_process_start_is_ignored(self):
        daemon = IndicatorDaemon()
        daemon.last_scan = 110
        row = PaneRow(
            "%1",
            "s:0",
            "codex",
            "working",
            "/tmp",
            "",
            "/dev/pts/1",
            123,
            100,
            "codex resume",
        )
        metadata = Metadata("codex", "model", "topic", "session")
        daemon.metadata_resolver.runtime_snapshot = Mock(
            return_value=("aborted", 90)
        )

        self.assertEqual(
            daemon.raw_state("s:0", row, metadata, 110),
            "working",
        )
        self.assertEqual(daemon.observation_time(row), 110)

    def test_last_agent_exit_clears_managed_tmux_values(self):
        daemon = IndicatorDaemon()
        daemon.last_scan = time.time()
        daemon.rows = {}
        daemon.tmux_windows = {"s:0"}
        daemon.managed_tmux_windows = {"s:0"}
        daemon.applied = {"s:0": ("running", "🌒", "title")}
        daemon.metadata = {"s:0": Metadata("codex", "model", "topic", "session")}
        daemon.tmux.clear_many = Mock(return_value=True)
        daemon.tmux.refresh = Mock()

        self.assertFalse(daemon.update_once())
        daemon.tmux.clear_many.assert_called_once_with(["s:0"])
        daemon.tmux.refresh.assert_called_once_with()
        self.assertNotIn("s:0", daemon.applied)
        self.assertNotIn("s:0", daemon.metadata)
        self.assertNotIn("s:0", daemon.managed_tmux_windows)

    def test_startup_clears_managed_values_without_in_memory_state(self):
        daemon = IndicatorDaemon()
        daemon.last_scan = time.time()
        daemon.rows = {}
        daemon.tmux_windows = {"s:0"}
        daemon.managed_tmux_windows = {"s:0"}
        daemon.tmux.clear_many = Mock(return_value=True)
        daemon.tmux.refresh = Mock()

        self.assertFalse(daemon.update_once())
        daemon.tmux.clear_many.assert_called_once_with(["s:0"])
        self.assertNotIn("s:0", daemon.managed_tmux_windows)

    def test_scan_invalidates_metadata_when_foreground_process_changes(self):
        daemon = IndicatorDaemon()
        old = PaneRow(
            "%1", "s:0", "codex", "idle", "/repo", "", "/dev/pts/1", 101, 10, "codex"
        )
        new = PaneRow(
            "%1", "s:0", "codex", "idle", "/repo", "", "/dev/pts/1", 202, 20, "codex"
        )
        daemon.rows = {"s:0": old}
        daemon.row_identities = {"s:0": daemon.row_identity(old)}
        daemon.applied = {"s:0": ("done", "✅", "old")}
        daemon.metadata = {"s:0": Metadata("codex", "old", "old", "old-session")}
        daemon.semantic = {"s:0": ("done", "codex/old: old")}
        daemon.tmux_detector.scan = lambda: {"s:0": new}
        daemon.tmux_detector.pane_ttys = {new.tty}
        daemon.tmux.windows = lambda: {"s:0"}
        daemon.tmux.managed_windows = lambda: {"s:0"}

        with patch("tmuxcue.service.title_mode", return_value="native"), patch(
            "tmuxcue.service.title_override_since", return_value=0
        ):
            daemon.scan()

        self.assertNotIn("s:0", daemon.metadata)
        self.assertNotIn("s:0", daemon.applied)
        self.assertNotIn("s:0", daemon.semantic)
        self.assertEqual(daemon.row_identities["s:0"], daemon.row_identity(new))

    def test_linked_tmux_windows_share_one_metadata_resolution(self):
        daemon = IndicatorDaemon()
        first = PaneRow("%1", "a:0", "codex", "idle", "/tmp", "", "/dev/pts/1")
        second = PaneRow("%1", "b:0", "codex", "idle", "/tmp", "", "/dev/pts/1")
        daemon.rows = {"a:0": first, "b:0": second}
        daemon.metadata_resolver.process_snapshot = Mock(return_value={})
        daemon.metadata_resolver.resolve = Mock(
            return_value=Metadata("codex", "model", "topic", "session")
        )

        refreshed = daemon.refresh_metadata_targets({"a:0", "b:0"}, time.time())
        self.assertEqual(refreshed, {"a:0", "b:0"})
        self.assertEqual(daemon.metadata_resolver.resolve.call_count, 1)

    def test_gemini_hook_events_are_passed_to_metadata_resolution(self):
        daemon = IndicatorDaemon()
        row = PaneRow(
            "%7",
            "g:0",
            "gemini",
            "idle",
            "/repo",
            "",
            "/dev/pts/7",
            321,
            900,
            "node /opt/node_modules/.bin/gemini",
        )
        events = {
            ("gemini", "session-1"): {
                "agent": "gemini",
                "session_id": "session-1",
                "state": "running",
                "timestamp": 901,
                "pid": 321,
                "started_at": 900,
                "tty": "/dev/pts/7",
            }
        }
        daemon.rows = {"g:0": row}
        daemon.metadata_resolver.resolve = Mock(
            return_value=Metadata("gemini", "model", "topic", "session-1")
        )

        refreshed = daemon.refresh_metadata_targets(
            {"g:0"},
            time.time(),
            hook_events=events,
        )

        self.assertEqual(refreshed, {"g:0"})
        daemon.metadata_resolver.resolve.assert_called_once_with(
            row,
            processes=None,
            hook_events=events,
        )

    def test_unmatched_recent_prompt_retries_metadata_immediately(self):
        daemon = IndicatorDaemon()
        row = PaneRow("%1", "s:0", "codex", "idle", "/tmp", "", "/dev/pts/1")
        now = time.time()
        daemon.rows = {"s:0": row}
        daemon.metadata = {"s:0": Metadata("codex", "old", "old", "old-session")}
        daemon.metadata_times = {"s:0": now}
        daemon.recent_prompt_events = {"new-session": now}
        self.assertEqual(daemon.metadata_due_targets(False), {"s:0"})

    def test_state_is_applied_before_scheduled_metadata_refresh(self):
        daemon = IndicatorDaemon()
        daemon.last_scan = time.time()
        row = PaneRow("%1", "s:0", "codex", "working", "/tmp", "", "/dev/pts/1")
        daemon.rows = {"s:0": row}
        daemon.row_identities = {"s:0": daemon.row_identity(row)}
        daemon.tmux_windows = {"s:0"}
        order = []
        daemon.poll_prompt_events = lambda _now: set()
        daemon.event_reader.poll = lambda _now: {}
        daemon.apply_targets = lambda *_args: order.append("apply") or False
        daemon.metadata_due_targets = lambda _force: {"s:0"}
        daemon.refresh_metadata_targets = (
            lambda _targets, _now, _events=None: order.append("metadata") or set()
        )

        self.assertTrue(daemon.update_once())
        self.assertEqual(order, ["apply", "metadata"])

    def test_tmux_write_timeout_is_recoverable(self):
        with patch(
            "tmuxcue.service.subprocess.run",
            side_effect=subprocess.TimeoutExpired("tmux", 5),
        ):
            client = TmuxClient()
            self.assertFalse(client.set_values("s:0", "running", "🌒", "title"))
            self.assertFalse(client.clear("s:0"))

    def test_override_ignores_terminals_started_before_title_ownership(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"CODEX_HOME": directory}), \
                 patch("tmuxcue.service.title_mode", return_value="override"), \
                 patch("tmuxcue.service.title_override_since", return_value=100):
                daemon = IndicatorDaemon()
                daemon.tmux_detector.scan = lambda: {}
                daemon.tmux_detector.pane_ttys = set()
                old = TerminalRow(1, "/dev/pts/8", "codex", "idle", "/tmp", 99, "codex")
                new = TerminalRow(2, "/dev/pts/9", "codex", "idle", "/tmp", 100, "codex")
                daemon.terminal_detector.scan = lambda _excluded, _processes=None: {
                    "terminal:1": old,
                    "terminal:2": new,
                }
                daemon.tmux.windows = lambda: set()
                daemon.tmux.managed_windows = lambda: set()
                daemon.scan()
                self.assertEqual(set(daemon.rows), {"terminal:2"})

    def test_titles_identify_tmux_and_regular_terminals(self):
        metadata = Metadata("codex", "gpt-test", "topic", "session")
        terminal = TerminalRow(123, "/dev/pts/9", "codex", "idle", "/tmp", 1, "codex")
        term_icon, term_title = IndicatorDaemon.format_title(terminal, "running", metadata, 2)
        self.assertEqual(term_icon, "🌒")
        self.assertIn("[term]", term_title)

        pane = PaneRow("%1", "s:0", "codex", "working", "/tmp", "")
        _, tmux_title = IndicatorDaemon.format_title(pane, "running", metadata, 2)
        self.assertIn("[tmux]", tmux_title)

    def test_gemini_uses_the_shared_tmux_title_format(self):
        row = PaneRow(
            "%3",
            "gemini:0",
            "gemini",
            "working",
            "/repo",
            "",
            "/dev/pts/7",
        )
        metadata = Metadata(
            "gemini",
            "gemini-test",
            "Fix session binding",
            "session-1",
        )

        icon, title = IndicatorDaemon.format_title(row, "running", metadata, 0)

        self.assertEqual(icon, "🌑")
        self.assertEqual(
            title,
            "🌑 [tmux] gemini/gemini-test: Fix session binding",
        )

    def test_tmux_snippet_never_executes_shell_commands(self):
        snippet = (
            Path(__file__).parents[1] / "tmux" / "tmuxcue.conf"
        ).read_text(encoding="utf-8")
        self.assertNotIn("#(", snippet)


if __name__ == "__main__":
    unittest.main()
