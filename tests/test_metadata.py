import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from tmuxcue.core import Metadata
from tmuxcue.metadata import MetadataResolver, discover_agent_processes


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


if __name__ == "__main__":
    unittest.main()
