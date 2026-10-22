import json
import os
from dataclasses import replace
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

from tmuxcue.core import Metadata, PaneRow, TerminalRow
from tmuxcue.metadata import MetadataResolver, discover_agent_processes
from tmuxcue.service import IndicatorDaemon


def create_thread_database(path: Path, rows: list[tuple]) -> None:
    connection = sqlite3.connect(path)
    connection.execute(
        """
        create table threads (
            id text primary key,
            created_at_ms integer,
            updated_at_ms integer,
            model text,
            title text,
            name text,
            cwd text,
            archived integer,
            thread_source text,
            rollout_path text
        )
        """
    )
    connection.executemany(
        "insert into threads values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    connection.commit()
    connection.close()


def create_log_database(
    path: Path, rows: list[tuple[int, int, str | None, str]]
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute(
        """
        create table logs (
            id integer primary key autoincrement,
            ts integer not null,
            ts_nanos integer not null,
            thread_id text,
            process_uuid text not null
        )
        """
    )
    connection.executemany(
        """
        insert into logs (ts, ts_nanos, thread_id, process_uuid)
        values (?, ?, ?, ?)
        """,
        rows,
    )
    connection.commit()
    connection.close()


def create_goal_database(path: Path, rows: list[tuple[str, str, int]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute(
        """
        create table thread_goals (
            thread_id text primary key,
            status text not null,
            updated_at_ms integer not null
        )
        """
    )
    connection.executemany(
        "insert into thread_goals values (?, ?, ?)",
        rows,
    )
    connection.commit()
    connection.close()


class ProcessDiscoveryTests(unittest.TestCase):
    def test_proc_scan_returns_only_owned_foreground_agents(self):
        with tempfile.TemporaryDirectory() as directory:
            proc = Path(directory)
            (proc / "uptime").write_text("1000.0 0.0\n", encoding="utf-8")
            (proc / "stat").write_text("cpu 1 2 3 4\nbtime 1000\n", encoding="utf-8")

            def add_process(
                pid: int, name: str, tty: str, *, state: str = "S", foreground: bool = True
            ) -> None:
                process = proc / str(pid)
                process.mkdir()
                (process / "comm").write_text(name + "\n", encoding="utf-8")
                (process / "cmdline").write_bytes(f"{name}\0resume\0".encode())
                fd = process / "fd"
                fd.mkdir()
                (fd / "0").symlink_to(tty)
                process_group = pid
                foreground_group = pid if foreground else pid + 1
                fields = [
                    state,
                    "1",
                    str(process_group),
                    str(pid),
                    "0",
                    str(foreground_group),
                    "0",
                    "0",
                    "0",
                    "0",
                    "0",
                    "0",
                    "0",
                    "0",
                    "0",
                    "0",
                    "0",
                    "1",
                    "0",
                    "500",
                ]
                (process / "stat").write_text(
                    f"{pid} ({name}) " + " ".join(fields) + "\n",
                    encoding="utf-8",
                )

            add_process(101, "codex", "/dev/pts/1")
            add_process(102, "claude-code", "/dev/pts/2")
            add_process(103, "codex", "/dev/pts/3", state="T")
            add_process(104, "codex", "/dev/pts/4", foreground=False)
            add_process(105, "python", "/dev/pts/5")

            processes = discover_agent_processes(proc, os.getuid(), now=2000)
            self.assertEqual(
                set(processes),
                {("/dev/pts/1", "codex"), ("/dev/pts/2", "claude")},
            )
            self.assertEqual(processes[("/dev/pts/1", "codex")][0], 101)
            self.assertGreaterEqual(processes[("/dev/pts/1", "codex")][1], 1000)
            self.assertIn("resume", processes[("/dev/pts/2", "claude")][2])


class CodexMetadataTests(unittest.TestCase):
    correct = "11111111-1111-1111-1111-111111111111"
    wrong = "22222222-2222-2222-2222-222222222222"
    subagent = "33333333-3333-3333-3333-333333333333"

    def test_open_user_rollout_beats_nearby_created_thread(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / "home"
            codex = home / ".codex"
            proc = root / "proc"
            fd = proc / "123/fd"
            fd.mkdir(parents=True)
            sessions = codex / "sessions/2026/08/19"
            sessions.mkdir(parents=True)
            correct_rollout = sessions / f"rollout-current-{self.correct}.jsonl"
            subagent_rollout = sessions / f"rollout-child-{self.subagent}.jsonl"
            correct_rollout.write_text("", encoding="utf-8")
            subagent_rollout.write_text("", encoding="utf-8")
            (fd / "40").symlink_to(correct_rollout)
            (fd / "41").symlink_to(subagent_rollout)

            create_thread_database(
                codex / "state_1.sqlite",
                [
                    (
                        self.correct,
                        1_000,
                        9_000,
                        "model",
                        "correct title",
                        "correct topic",
                        "/repo",
                        0,
                        "user",
                        str(correct_rollout),
                    ),
                    (
                        self.wrong,
                        5_000,
                        6_000,
                        "model",
                        "wrong title",
                        "wrong topic",
                        "/repo",
                        0,
                        "user",
                        "/tmp/wrong.jsonl",
                    ),
                    (
                        self.subagent,
                        5_100,
                        10_000,
                        "model",
                        "child title",
                        "child topic",
                        "/repo",
                        0,
                        "subagent",
                        str(subagent_rollout),
                    ),
                ],
            )
            create_log_database(
                codex / "logs_2.sqlite",
                [(6, 0, self.wrong, "pid:123:current")],
            )
            resolver = MetadataResolver(home=home, proc_root=proc)

            model, topic, session_id = resolver.codex_metadata(
                "/repo", (123, 5, "codex resume")
            )

            self.assertEqual(model, "model")
            self.assertEqual(topic, "correct topic")
            self.assertEqual(session_id, self.correct)

    def test_rollout_outside_codex_home_is_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / "home"
            codex = home / ".codex"
            codex.mkdir(parents=True)
            proc = root / "proc"
            fd = proc / "123/fd"
            fd.mkdir(parents=True)
            outside = root / f"rollout-current-{self.correct}.jsonl"
            outside.write_text("", encoding="utf-8")
            (fd / "40").symlink_to(outside)
            create_thread_database(
                codex / "state_1.sqlite",
                [
                    (
                        self.correct,
                        1_000,
                        9_000,
                        "model",
                        "correct title",
                        "correct topic",
                        "/repo",
                        0,
                        "user",
                        str(outside),
                    ),
                    (
                        self.wrong,
                        5_000,
                        6_000,
                        "model",
                        "wrong title",
                        "wrong topic",
                        "/repo",
                        0,
                        "user",
                        "/tmp/wrong.jsonl",
                    ),
                ],
            )
            resolver = MetadataResolver(home=home, proc_root=proc)

            _, topic, session_id = resolver.codex_metadata(
                "/repo", (123, 5, "codex resume")
            )

            self.assertEqual(topic, "wrong topic")
            self.assertEqual(session_id, self.wrong)

    def test_process_log_binds_same_cwd_resume_to_user_thread(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / "home"
            codex = home / ".codex"
            codex.mkdir(parents=True)
            proc = root / "proc"
            (proc / "123").mkdir(parents=True)
            create_thread_database(
                codex / "state_1.sqlite",
                [
                    (
                        self.correct,
                        1_000,
                        6_000,
                        "model",
                        "correct title",
                        "correct topic",
                        "/repo",
                        0,
                        "user",
                        "/tmp/correct.jsonl",
                    ),
                    (
                        self.wrong,
                        5_000,
                        9_000,
                        "model",
                        "wrong title",
                        "wrong topic",
                        "/repo",
                        0,
                        "user",
                        "/tmp/wrong.jsonl",
                    ),
                ],
            )
            create_log_database(
                codex / "logs_2.sqlite",
                [
                    (5, 0, None, "pid:123:current"),
                    (6, 0, self.correct, "pid:123:current"),
                    (7, 0, self.wrong, "pid:999:other"),
                ],
            )
            resolver = MetadataResolver(home=home, proc_root=proc)

            model, topic, session_id = resolver.codex_metadata(
                "/repo", (123, 5, "codex resume")
            )

            self.assertEqual(model, "model")
            self.assertEqual(topic, "correct topic")
            self.assertEqual(session_id, self.correct)

    def test_process_log_does_not_reuse_an_old_pid_incarnation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / "home"
            codex = home / ".codex"
            codex.mkdir(parents=True)
            proc = root / "proc"
            (proc / "123").mkdir(parents=True)
            create_log_database(
                codex / "logs_2.sqlite",
                [
                    (90, 0, self.correct, "pid:123:old"),
                    (100, 0, None, "pid:123:current"),
                ],
            )
            resolver = MetadataResolver(home=home, proc_root=proc)

            self.assertEqual(resolver.codex_logged_session_id(123, 100), "")

    def test_process_log_thread_must_be_a_user_candidate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / "home"
            codex = home / ".codex"
            codex.mkdir(parents=True)
            proc = root / "proc"
            (proc / "123").mkdir(parents=True)
            create_thread_database(
                codex / "state_1.sqlite",
                [
                    (
                        self.wrong,
                        5_000,
                        9_000,
                        "model",
                        "wrong title",
                        "wrong topic",
                        "/repo",
                        0,
                        "user",
                        "/tmp/wrong.jsonl",
                    ),
                    (
                        self.subagent,
                        1_000,
                        10_000,
                        "model",
                        "child title",
                        "child topic",
                        "/repo",
                        0,
                        "subagent",
                        "/tmp/child.jsonl",
                    ),
                ],
            )
            create_log_database(
                codex / "logs_2.sqlite",
                [(6, 0, self.subagent, "pid:123:current")],
            )
            resolver = MetadataResolver(home=home, proc_root=proc)

            _, topic, session_id = resolver.codex_metadata(
                "/repo", (123, 5, "codex resume")
            )

            self.assertEqual(topic, "wrong topic")
            self.assertEqual(session_id, self.wrong)

    def test_native_title_hint_binds_after_process_log_rotation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / "home"
            codex = home / ".codex"
            codex.mkdir(parents=True)
            proc = root / "proc"
            (proc / "123").mkdir(parents=True)
            create_thread_database(
                codex / "state_1.sqlite",
                [
                    (
                        self.correct,
                        1_000,
                        6_000,
                        "model",
                        "correct title",
                        "correct topic",
                        "/repo",
                        0,
                        "user",
                        "/tmp/correct.jsonl",
                    ),
                    (
                        self.wrong,
                        5_000,
                        9_000,
                        "model",
                        "wrong title",
                        "wrong topic",
                        "/repo",
                        0,
                        "user",
                        "/tmp/wrong.jsonl",
                    ),
                ],
            )
            create_log_database(
                codex / "logs_2.sqlite",
                [(6, 0, None, "pid:123:current")],
            )
            resolver = MetadataResolver(home=home, proc_root=proc)

            _, topic, session_id = resolver.codex_metadata(
                "/repo",
                (123, 5, "codex resume"),
                "correct topic",
            )

            self.assertEqual(topic, "correct topic")
            self.assertEqual(session_id, self.correct)

    def test_duplicate_native_title_hint_uses_existing_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / "home"
            codex = home / ".codex"
            codex.mkdir(parents=True)
            proc = root / "proc"
            (proc / "123").mkdir(parents=True)
            create_thread_database(
                codex / "state_1.sqlite",
                [
                    (
                        self.correct,
                        1_000,
                        6_000,
                        "model",
                        "correct title",
                        "shared topic",
                        "/repo",
                        0,
                        "user",
                        "/tmp/correct.jsonl",
                    ),
                    (
                        self.wrong,
                        5_000,
                        9_000,
                        "model",
                        "wrong title",
                        "shared topic",
                        "/repo",
                        0,
                        "user",
                        "/tmp/wrong.jsonl",
                    ),
                ],
            )
            resolver = MetadataResolver(home=home, proc_root=proc)

            _, _, session_id = resolver.codex_metadata(
                "/repo",
                (123, 5, "codex resume"),
                "shared topic",
            )

            self.assertEqual(session_id, self.wrong)

    def test_malformed_process_log_database_uses_existing_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / "home"
            codex = home / ".codex"
            codex.mkdir(parents=True)
            proc = root / "proc"
            (proc / "123").mkdir(parents=True)
            create_thread_database(
                codex / "state_1.sqlite",
                [
                    (
                        self.wrong,
                        5_000,
                        9_000,
                        "model",
                        "wrong title",
                        "wrong topic",
                        "/repo",
                        0,
                        "user",
                        "/tmp/wrong.jsonl",
                    ),
                ],
            )
            (codex / "logs_2.sqlite").write_text("not sqlite", encoding="utf-8")
            resolver = MetadataResolver(home=home, proc_root=proc)

            _, topic, session_id = resolver.codex_metadata(
                "/repo", (123, 5, "codex resume")
            )

            self.assertEqual(topic, "wrong topic")
            self.assertEqual(session_id, self.wrong)

    def test_open_writer_lock_identifies_user_thread(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / "home"
            codex = home / ".codex"
            locks = codex / "thread-writer-locks"
            locks.mkdir(parents=True)
            proc = root / "proc"
            fd = proc / "123/fd"
            fd.mkdir(parents=True)
            lock = locks / f"{self.correct}.lock"
            lock.write_text("", encoding="utf-8")
            (fd / "40").symlink_to(lock)
            create_thread_database(
                codex / "state_1.sqlite",
                [
                    (
                        self.correct,
                        1_000,
                        9_000,
                        "model",
                        "correct title",
                        "correct topic",
                        "/repo",
                        0,
                        "user",
                        "/tmp/correct.jsonl",
                    ),
                    (
                        self.wrong,
                        5_000,
                        6_000,
                        "model",
                        "wrong title",
                        "wrong topic",
                        "/repo",
                        0,
                        "user",
                        "/tmp/wrong.jsonl",
                    ),
                ],
            )
            resolver = MetadataResolver(home=home, proc_root=proc)

            _, topic, session_id = resolver.codex_metadata(
                "/repo", (123, 5, "codex resume")
            )

            self.assertEqual(topic, "correct topic")
            self.assertEqual(session_id, self.correct)


class RuntimeStateTests(unittest.TestCase):
    def test_codex_goal_snapshot_maps_exact_durable_states(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            codex = home / ".codex"
            codex.mkdir()
            create_goal_database(
                codex / "goals_1.sqlite",
                [
                    ("active-thread", "active", 123_000),
                    ("blocked-thread", "blocked", 124_000),
                    ("paused-thread", "paused", 125_000),
                    ("complete-thread", "complete", 126_000),
                    ("usage-thread", "usage_limited", 127_000),
                    ("budget-thread", "budget_limited", 128_000),
                ],
            )
            resolver = MetadataResolver(home=home)

            self.assertEqual(
                resolver.codex_goal_snapshot("active-thread"),
                ("goal_active", 123.0),
            )
            self.assertEqual(
                resolver.codex_goal_snapshot("blocked-thread"),
                ("goal_blocked", 124.0),
            )
            for session_id, timestamp in (
                ("paused-thread", 125.0),
                ("complete-thread", 126.0),
                ("usage-thread", 127.0),
                ("budget-thread", 128.0),
            ):
                self.assertEqual(
                    resolver.codex_goal_snapshot(session_id),
                    ("idle", timestamp),
                )

    def test_codex_goal_lookup_is_exact_and_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            codex = home / ".codex"
            codex.mkdir()
            create_goal_database(
                codex / "goals_1.sqlite",
                [
                    ("other-thread", "active", 123_000),
                    ("unknown-thread", "future_status", 124_000),
                ],
            )
            resolver = MetadataResolver(home=home)

            self.assertEqual(
                resolver.codex_goal_snapshot("missing-thread"),
                (None, 0.0),
            )
            self.assertEqual(
                resolver.codex_goal_snapshot("unknown-thread"),
                (None, 0.0),
            )

            (codex / "goals_1.sqlite").unlink()
            (codex / "goals_1.sqlite").write_text(
                "not a sqlite database",
                encoding="utf-8",
            )
            self.assertEqual(
                resolver.codex_goal_snapshot("other-thread"),
                (None, 0.0),
            )

    def test_runtime_snapshot_prefers_goal_and_falls_back_without_one(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            codex = home / ".codex"
            codex.mkdir()
            create_goal_database(
                codex / "goals_1.sqlite",
                [("goal-thread", "active", 123_000)],
            )
            rollout = codex / "rollout.jsonl"
            rollout.write_text(
                json.dumps(
                    {
                        "timestamp": "2026-10-16T06:00:00Z",
                        "type": "event_msg",
                        "payload": {"type": "task_started"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            database = codex / "state_1.sqlite"
            connection = sqlite3.connect(database)
            connection.execute(
                "create table threads (id text primary key, rollout_path text)"
            )
            connection.executemany(
                "insert into threads values (?, ?)",
                [
                    ("goal-thread", str(rollout)),
                    ("plain-thread", str(rollout)),
                ],
            )
            connection.commit()
            connection.close()
            resolver = MetadataResolver(home=home)

            self.assertEqual(
                resolver.runtime_snapshot(
                    Metadata("codex", "model", "topic", "goal-thread")
                ),
                ("goal_active", 123.0),
            )
            state, timestamp = resolver.runtime_snapshot(
                Metadata("codex", "model", "topic", "plain-thread")
            )
            self.assertEqual(state, "working")
            self.assertGreater(timestamp, 0)

    def test_codex_runtime_state_follows_latest_task_event(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            codex = home / ".codex"
            codex.mkdir()
            rollout = codex / "rollout.jsonl"
            rollout.write_text(
                json.dumps({"type": "event_msg", "payload": {"type": "task_started"}})
                + "\n",
                encoding="utf-8",
            )
            database = codex / "state_1.sqlite"
            connection = sqlite3.connect(database)
            connection.execute("create table threads (id text primary key, rollout_path text)")
            connection.execute("insert into threads values (?, ?)", ("session-1", str(rollout)))
            connection.commit()
            connection.close()

            resolver = MetadataResolver(home=home)
            metadata = Metadata("codex", "model", "topic", "session-1")
            self.assertEqual(resolver.runtime_state(metadata), "working")

            with rollout.open("a", encoding="utf-8") as stream:
                stream.write(
                    json.dumps(
                        {
                            "timestamp": "2026-07-31T11:42:16.576Z",
                            "type": "event_msg",
                            "payload": {"type": "task_complete"},
                        }
                    )
                    + "\n"
                )
            self.assertEqual(resolver.runtime_state(metadata), "idle")

            with rollout.open("a", encoding="utf-8") as stream:
                stream.write(
                    json.dumps(
                        {
                            "timestamp": "2026-07-31T11:47:18.375Z",
                            "type": "event_msg",
                            "payload": {"type": "turn_aborted"},
                        }
                    )
                    + "\n"
                )
            state, event_time = resolver.runtime_snapshot(metadata)
            self.assertEqual(state, "aborted")
            self.assertGreater(event_time, 0)

    def test_claude_runtime_state_uses_live_session_status(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            sessions = home / ".claude/sessions"
            sessions.mkdir(parents=True)
            path = sessions / "session.json"
            path.write_text(
                json.dumps({"sessionId": "session-2", "status": "busy"}),
                encoding="utf-8",
            )
            resolver = MetadataResolver(home=home)
            metadata = Metadata("claude", "model", "topic", "session-2")
            self.assertEqual(resolver.runtime_state(metadata), "working")

            path.write_text(
                json.dumps({"sessionId": "session-2", "status": "idle"}),
                encoding="utf-8",
            )
            self.assertEqual(resolver.runtime_state(metadata), "idle")


class CodexPaneIsolationTests(unittest.TestCase):
    old = "11111111-1111-1111-1111-111111111111"
    current = "22222222-2222-2222-2222-222222222222"

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name) / "home"
        self.codex = self.home / ".codex"
        self.proc = Path(temporary.name) / "proc"
        self.fd = self.proc / "123/fd"
        self.fd.mkdir(parents=True)
        (self.codex / "sessions").mkdir(parents=True)
        environment = patch.dict(os.environ, {"CODEX_HOME": str(self.codex)})
        environment.start()
        self.addCleanup(environment.stop)
        self.database = self.codex / "state_1.sqlite"
        create_thread_database(self.database, [])
        self.row = PaneRow(
            "%1", "s:0", "codex", "idle", "/repo", "",
            "/dev/pts/1", 123, 10_000, "codex",
        )
        self.resolver = MetadataResolver(home=self.home, proc_root=self.proc)
        self.old_rollout = self.add_thread(self.old)
        self.old_metadata = Metadata("codex", "old-model", "Old thread", self.old)

    def add_thread(
        self, session_id, *, created=1_000, updated=2_000,
        model="old-model", topic="Old thread",
    ):
        rollout = self.codex / "sessions" / f"rollout-{session_id}.jsonl"
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                "insert into threads values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    session_id, created, updated, model, topic, topic,
                    "/repo", 0, "user", str(rollout),
                ),
            )
        return rollout

    def make_daemon(self):
        daemon = IndicatorDaemon()
        daemon.metadata_resolver = self.resolver
        daemon.tmux = Mock()
        daemon.tmux.set_many.return_value = True
        daemon.last_scan = 10_010.0
        return daemon

    def test_unbound_pane_rejects_one_old_same_cwd_thread(self):
        self.assertEqual(
            self.resolver.resolve(self.row),
            Metadata("codex", "", "repo", ""),
        )

    def test_unbound_pane_rejects_latest_of_multiple_old_threads(self):
        self.add_thread(
            self.current, updated=10_020_000,
            model="other-model", topic="Other thread",
        )
        row = replace(self.row, subject="Current UI")
        self.assertEqual(
            self.resolver.resolve(row),
            Metadata("codex", "", "Current UI", ""),
        )

    def test_unbound_pane_does_not_select_a_future_thread(self):
        with sqlite3.connect(self.database) as connection:
            connection.execute("delete from threads")
        self.add_thread(
            self.current, created=11_000_000, updated=11_000_000,
            model="other-model", topic="Future thread",
        )
        self.assertEqual(self.resolver.resolve(self.row).session_id, "")

    def test_cwd_fallback_remains_optional_for_direct_callers(self):
        process = (self.row.pid, self.row.started_at, self.row.cmdline)
        expected = ("old-model", "Old thread", self.old)
        self.assertEqual(
            self.resolver.codex_metadata("/repo", process), expected,
        )
        self.assertEqual(
            self.resolver.codex_metadata(
                "/repo", process, allow_cwd_fallback=True,
            ),
            expected,
        )
        self.assertEqual(
            self.resolver.codex_metadata(
                "/repo", process, allow_cwd_fallback=False,
            ),
            ("", "", ""),
        )

    def test_strict_lookup_without_process_stays_unbound(self):
        self.assertEqual(
            self.resolver.codex_metadata(
                "/repo", None, allow_cwd_fallback=False,
            ),
            ("", "", ""),
        )

    def test_direct_terminal_keeps_old_cwd_fallback(self):
        row = TerminalRow(
            123, "/dev/pts/1", "codex", "idle", "/repo", 10_000, "codex",
        )
        self.assertEqual(self.resolver.resolve(row), self.old_metadata)

    def test_nearby_creation_still_binds_current_pane(self):
        self.add_thread(
            self.current, created=10_001_000, updated=10_002_000,
            model="current-model", topic="Current thread",
        )
        self.assertEqual(
            self.resolver.resolve(self.row),
            Metadata("codex", "current-model", "Current thread", self.current),
        )

    def test_old_explicit_bindings_beat_a_new_same_cwd_thread(self):
        self.add_thread(
            self.current, created=10_001_000, updated=10_002_000,
            model="current-model", topic="Current thread",
        )
        for binding in ("command", "rollout", "writer_lock", "log", "title"):
            with self.subTest(binding=binding):
                row = self.row
                link = self.fd / "40"
                log = self.codex / "logs_2.sqlite"
                if binding == "command":
                    row = replace(row, cmdline=f"codex resume {self.old}")
                elif binding == "rollout":
                    self.old_rollout.touch()
                    link.symlink_to(self.old_rollout)
                elif binding == "writer_lock":
                    lock = self.codex / "thread-writer-locks" / f"{self.old}.lock"
                    lock.parent.mkdir()
                    lock.touch()
                    link.symlink_to(lock)
                elif binding == "log":
                    create_log_database(
                        log,
                        [
                            (10_001, 0, self.old, "pid:123:current"),
                            (10_002, 0, self.current, "pid:999:other"),
                        ],
                    )
                elif binding == "title":
                    row = replace(row, subject="Old thread")
                try:
                    self.assertEqual(self.resolver.resolve(row), self.old_metadata)
                finally:
                    link.unlink(missing_ok=True)
                    log.unlink(missing_ok=True)

    def test_missing_or_malformed_database_stays_unbound(self):
        self.database.unlink()
        self.assertEqual(self.resolver.resolve(self.row).session_id, "")
        self.database.write_text("not a database", encoding="utf-8")
        self.assertEqual(self.resolver.resolve(self.row).session_id, "")

    def test_old_goals_and_hooks_cannot_change_unbound_pane(self):
        goals = self.codex / "goals_1.sqlite"
        create_goal_database(goals, [(self.old, "blocked", 10_000_000)])
        bound = replace(
            self.row, pane="%2", window="s:1", tty="/dev/pts/2",
            pid=124, subject="Old thread",
        )
        for goal in ("blocked", "active"):
            with sqlite3.connect(goals) as connection:
                connection.execute("update thread_goals set status=?", (goal,))
            for hook in (None, "approval", "running", "ended"):
                with self.subTest(goal=goal, hook=hook):
                    daemon = self.make_daemon()
                    daemon.rows = {self.row.window: self.row, bound.window: bound}
                    daemon.refresh_metadata_targets(
                        set(daemon.rows), daemon.last_scan,
                    )
                    events = {}
                    if hook is not None:
                        events[("codex", self.old)] = {
                            "state": hook, "timestamp": daemon.last_scan + 0.5,
                        }
                    with patch("builtins.print"):
                        daemon.apply_targets(
                            set(daemon.rows), events, daemon.last_scan + 1,
                        )
                    self.assertEqual(daemon.applied[self.row.window][0], "done")
                    self.assertEqual(daemon.metadata[self.row.window].session_id, "")
                    self.assertNotIn("Old thread", daemon.applied[self.row.window][2])
                    self.assertEqual(daemon.metadata[bound.window], self.old_metadata)
                    if hook is None:
                        self.assertEqual(
                            daemon.applied[bound.window][0],
                            "approval" if goal == "blocked" else "running",
                        )

    def test_unbound_pane_keeps_native_state(self):
        for native, visual in (
            ("idle", "done"), ("working", "running"), ("blocked", "approval"),
        ):
            with self.subTest(native=native):
                row = replace(self.row, state=native)
                daemon = self.make_daemon()
                daemon.rows = {row.window: row}
                daemon.refresh_metadata(row.window, row, daemon.last_scan)
                with patch("builtins.print"):
                    daemon.apply_targets({row.window}, {}, daemon.last_scan + 1)
                self.assertEqual(daemon.applied[row.window][0], visual)
                self.assertEqual(daemon.metadata[row.window].session_id, "")

    def test_normal_refresh_binds_new_thread_and_invalidates_runtime_cache(self):
        daemon = self.make_daemon()
        target = self.row.window
        initial = daemon.refresh_metadata(target, self.row, daemon.last_scan)
        self.assertEqual(initial.session_id, "")
        daemon.raw_state(target, self.row, initial, daemon.last_scan)
        self.assertIn(target, daemon.runtime_states)
        self.add_thread(
            self.current, created=10_001_000, updated=10_002_000,
            model="current-model", topic="Current thread",
        )
        current = daemon.refresh_metadata(target, self.row, daemon.last_scan + 1)
        self.assertEqual(
            current,
            Metadata("codex", "current-model", "Current thread", self.current),
        )
        self.assertNotIn(target, daemon.runtime_states)
        create_goal_database(
            self.codex / "goals_1.sqlite",
            [(self.current, "active", 10_010_000)],
        )
        self.assertEqual(
            daemon.raw_state(target, self.row, current, daemon.last_scan + 1),
            "working",
        )


if __name__ == "__main__":
    unittest.main()
