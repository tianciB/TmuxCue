import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest

from tmuxcue.agy import (
    agy_annotation_title,
    agy_bound_conversation_id,
    agy_configured_model,
    agy_conversation_from_command,
    agy_model_from_command,
    agy_open_conversation_ids,
    agy_runtime_snapshot,
    agy_state_dir,
    latest_agy_input,
    load_agy_conversation,
    parse_timestamp,
)
from tmuxcue.core import PaneRow
from tmuxcue.metadata import MetadataResolver


CONV_ONE = "11111111-1111-1111-1111-111111111111"
CONV_TWO = "22222222-2222-2222-2222-222222222222"


def create_summaries_db(path: Path, rows: list[tuple]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute(
        """
        create table conversation_summaries (
            conversation_id text primary key,
            title text not null default '',
            preview text not null default '',
            workspace_uris text not null default '[]',
            status text not null default '',
            not_fully_idle numeric not null default 0,
            killed numeric not null default 0,
            last_modified_time text not null default '',
            last_user_input_time text not null default ''
        )
        """
    )
    connection.executemany(
        """
        insert into conversation_summaries values (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    connection.commit()
    connection.close()


def create_conversation_db(path: Path, step_statuses: list[int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    connection = sqlite3.connect(path)
    connection.execute(
        """
        create table steps (
            idx integer primary key,
            step_type integer not null default 0,
            status integer not null default 0
        )
        """
    )
    connection.executemany(
        "insert into steps (idx, step_type, status) values (?, 132, ?)",
        list(enumerate(step_statuses)),
    )
    connection.commit()
    connection.close()


class AgySessionTests(unittest.TestCase):
    def test_parses_nanosecond_iso_timestamps(self):
        ts = parse_timestamp("2026-11-27 06:04:52.388461265+00:00")
        self.assertGreater(ts, 1_795_000_000)

    def test_command_model_and_conversation_parsing(self):
        command = (
            "/opt/agy/bin/agy --settings '{\"modelProvider\":\"gemini\"}' "
            "--model gemini-4-neon-xhigh --effort xhigh "
            f"--conversation {CONV_ONE}"
        )
        self.assertEqual(agy_model_from_command(command), "gemini-4-neon-xhigh")
        self.assertEqual(agy_conversation_from_command(command), CONV_ONE)
        self.assertEqual(
            agy_model_from_command("agy --model=agy-inline"),
            "agy-inline",
        )
        self.assertEqual(
            agy_conversation_from_command(f"agy --resume={CONV_TWO}"),
            CONV_TWO,
        )

    def test_custom_app_data_dir_from_process_environ(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            proc = root / "proc"
            process = proc / "321"
            process.mkdir(parents=True)
            custom = root / "custom-agy"
            custom.mkdir()
            (process / "environ").write_bytes(
                f"SECRET=ignore\0ANTIGRAVITY_APP_DATA_DIR={custom}\0".encode()
            )

            resolved = agy_state_dir(
                root / "home",
                321,
                proc_root=proc,
                uid=os.getuid(),
            )
            self.assertEqual(resolved, custom)

    def test_open_conversation_ids_reads_presence_lock_and_db_fds(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_dir = root / "home/.gemini/antigravity-cli"
            (state_dir / "presence").mkdir(parents=True)
            (state_dir / "conversations").mkdir(parents=True)
            lock = state_dir / "presence" / f"{CONV_ONE}.lock"
            lock.touch()
            db_wal = state_dir / "conversations" / f"{CONV_TWO}.db-wal"
            db_wal.touch()
            outside = root / f"{CONV_ONE}.lock"
            outside.touch()

            proc = root / "proc"
            fd = proc / "404/fd"
            fd.mkdir(parents=True)
            (fd / "10").symlink_to(lock)
            (fd / "11").symlink_to(db_wal)
            (fd / "12").symlink_to(outside)

            found = agy_open_conversation_ids(
                state_dir,
                404,
                proc_root=proc,
                uid=os.getuid(),
            )
            self.assertEqual(found, {CONV_ONE, CONV_TWO})

    def test_runtime_snapshot_reports_waiting_running_and_idle(self):
        with tempfile.TemporaryDirectory() as directory:
            state_dir = Path(directory) / ".gemini/antigravity-cli"
            create_summaries_db(
                state_dir / "conversation_summaries.db",
                [
                    (
                        CONV_ONE,
                        "Cluster analysis",
                        "Cluster analysis",
                        '["file:///repo"]',
                        "CASCADE_RUN_STATUS_RUNNING",
                        1,
                        0,
                        "2026-11-27 06:04:52.388461265+00:00",
                        "2026-11-27 05:57:07.510587677+00:00",
                    ),
                    (
                        CONV_TWO,
                        "Finished task",
                        "Finished task",
                        '["file:///repo"]',
                        "CASCADE_RUN_STATUS_IDLE",
                        0,
                        0,
                        "2026-11-27 06:05:00.000000000+00:00",
                        "2026-11-27 05:58:00.000000000+00:00",
                    ),
                ],
            )
            create_conversation_db(
                state_dir / "conversations" / f"{CONV_ONE}.db",
                [3, 3, 2],
            )
            create_conversation_db(
                state_dir / "conversations" / f"{CONV_TWO}.db",
                [3, 3],
            )

            state, ts = agy_runtime_snapshot(state_dir, CONV_ONE)
            self.assertEqual(state, "working")
            self.assertGreater(ts, 0)

            state, ts = agy_runtime_snapshot(state_dir, CONV_TWO)
            self.assertEqual(state, "idle")
            self.assertGreater(ts, 0)

            # A step waiting for user approval (status 9) overrides running.
            create_conversation_db(
                state_dir / "conversations" / f"{CONV_ONE}.db",
                [3, 9],
            )
            state, _ = agy_runtime_snapshot(state_dir, CONV_ONE)
            self.assertEqual(state, "blocked")

    def test_annotation_and_history_fallback_topics(self):
        with tempfile.TemporaryDirectory() as directory:
            state_dir = Path(directory) / ".gemini/antigravity-cli"
            (state_dir / "annotations").mkdir(parents=True)
            (state_dir / "annotations" / f"{CONV_ONE}.pbtxt").write_text(
                'title:"Analyze repository status"\n',
                encoding="utf-8",
            )
            self.assertEqual(
                agy_annotation_title(state_dir, CONV_ONE),
                "Analyze repository status",
            )

            (state_dir / "history.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "display": "/permissions",
                                "workspace": "/repo",
                                "type": "slash_command",
                            }
                        ),
                        json.dumps(
                            {
                                "display": "Check cluster capacity",
                                "workspace": "/repo",
                            }
                        ),
                        json.dumps(
                            {
                                "display": "Other workspace task",
                                "workspace": "/other",
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            self.assertEqual(
                latest_agy_input(state_dir, "/repo"),
                "Check cluster capacity",
            )

    def test_configured_model_from_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            state_dir = Path(directory)
            (state_dir / "settings.json").write_text(
                json.dumps({"defaultModel": "agy-default-model"}),
                encoding="utf-8",
            )
            self.assertEqual(agy_configured_model(state_dir), "agy-default-model")


class AgyBindingTests(unittest.TestCase):
    def test_hook_binding_requires_pid_start_time_and_tty(self):
        process = (321, 900, "/opt/agy/bin/agy --model agy-test")
        base = {
            "agent": "agy",
            "session_id": CONV_ONE,
            "state": "running",
            "timestamp": 901,
            "pid": 321,
            "started_at": 900,
            "tty": "/dev/pts/4",
        }
        events = {("agy", CONV_ONE): base}

        self.assertEqual(
            agy_bound_conversation_id(events, process, "/dev/pts/4"),
            CONV_ONE,
        )
        self.assertEqual(
            agy_bound_conversation_id(
                {("agy", CONV_ONE): {**base, "pid": 322}},
                process,
                "/dev/pts/4",
            ),
            "",
        )
        self.assertEqual(
            agy_bound_conversation_id(
                {("agy", CONV_ONE): {**base, "started_at": 899}},
                process,
                "/dev/pts/4",
            ),
            "",
        )
        self.assertEqual(
            agy_bound_conversation_id(events, process, "/dev/pts/8"),
            "",
        )

    def test_metadata_binds_from_open_presence_lock_without_borrowing_host_title(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / "home"
            state_dir = home / ".gemini/antigravity-cli"
            (state_dir / "presence").mkdir(parents=True)
            lock = state_dir / "presence" / f"{CONV_ONE}.lock"
            lock.touch()
            create_summaries_db(
                state_dir / "conversation_summaries.db",
                [
                    (
                        CONV_ONE,
                        "Repository and cluster review",
                        "Repository and cluster review",
                        '["file:///repo"]',
                        "CASCADE_RUN_STATUS_RUNNING",
                        1,
                        0,
                        "2026-11-27 06:04:52.388461265+00:00",
                        "2026-11-27 05:57:07.510587677+00:00",
                    )
                ],
            )
            proc = root / "proc"
            fd = proc / "321/fd"
            fd.mkdir(parents=True)
            (fd / "18").symlink_to(lock)

            resolver = MetadataResolver(home=home, proc_root=proc)
            row = PaneRow(
                "%2",
                "t2i-agy:0",
                "agy",
                "working",
                "/repo",
                "build-host-01",
                "/dev/pts/4",
                321,
                900,
                "/opt/agy/bin/agy --model neon-xhigh --effort xhigh",
            )

            metadata = resolver.resolve(row, hook_events={})

            self.assertEqual(metadata.agent, "agy")
            self.assertEqual(metadata.session_id, CONV_ONE)
            self.assertEqual(metadata.model, "neon-xhigh")
            self.assertEqual(metadata.topic, "Repository and cluster review")

            state, ts = resolver.runtime_snapshot(metadata)
            self.assertEqual(state, "working")
            self.assertGreater(ts, 0)

    def test_metadata_falls_back_to_workspace_name_not_hostname_when_unbound(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / "home"
            resolver = MetadataResolver(home=home, proc_root=root / "proc")
            row = PaneRow(
                "%2",
                "t2i-agy:0",
                "agy",
                "idle",
                "/work/my-project",
                "build-host-01",
                "/dev/pts/4",
                321,
                900,
                "/opt/agy/bin/agy --model neon-xhigh",
            )

            metadata = resolver.resolve(row, hook_events={})

            self.assertEqual(metadata.session_id, "")
            self.assertEqual(metadata.model, "neon-xhigh")
            self.assertEqual(metadata.topic, "my-project")


if __name__ == "__main__":
    unittest.main()
