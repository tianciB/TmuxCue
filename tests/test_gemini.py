import json
import os
from pathlib import Path
import tempfile
import unittest

from tmuxcue.core import PaneRow
from tmuxcue.gemini import (
    gemini_bound_session_id,
    gemini_model_from_command,
    gemini_state_dir,
    load_gemini_sessions,
    select_gemini_session,
)
from tmuxcue.metadata import MetadataResolver


SESSION_ONE = "11111111-1111-1111-1111-111111111111"
SESSION_TWO = "22222222-2222-2222-2222-222222222222"


def write_session(
    state_dir: Path,
    slug: str,
    session_id: str,
    project_root: str,
    *,
    started: str = "2026-10-15T07:00:00Z",
    updated: str = "2026-10-15T07:05:00Z",
    user_messages: tuple[str, ...] = ("Implement Gemini supervision",),
    model: str = "gemini-test",
) -> Path:
    project = state_dir / "tmp" / slug
    chats = project / "chats"
    chats.mkdir(parents=True, exist_ok=True)
    (project / ".project_root").write_text(project_root, encoding="utf-8")
    path = chats / f"session-2026-10-15T07-00-{session_id[:8]}.jsonl"
    records: list[dict] = [
        {
            "sessionId": session_id,
            "projectHash": slug,
            "startTime": started,
            "lastUpdated": updated,
            "kind": "main",
        }
    ]
    for index, text in enumerate(user_messages):
        records.append(
            {
                "id": f"u{index}",
                "timestamp": updated,
                "type": "user",
                "content": [{"text": text}],
            }
        )
    records.append(
        {
            "id": "g1",
            "timestamp": updated,
            "type": "gemini",
            "model": model,
            "content": "done",
        }
    )
    path.write_text(
        "".join(json.dumps(item) + "\n" for item in records),
        encoding="utf-8",
    )
    return path


class GeminiSessionTests(unittest.TestCase):
    def test_loads_model_and_latest_meaningful_topic(self):
        with tempfile.TemporaryDirectory() as directory:
            state_dir = Path(directory) / ".gemini"
            write_session(
                state_dir,
                "repo",
                SESSION_ONE,
                "/repo",
                user_messages=("Implement Gemini supervision", "OK"),
            )

            sessions = load_gemini_sessions(state_dir)

            self.assertEqual(len(sessions), 1)
            self.assertEqual(sessions[0].session_id, SESSION_ONE)
            self.assertEqual(sessions[0].model, "gemini-test")
            self.assertEqual(sessions[0].topic, "Implement Gemini supervision")

    def test_exact_session_and_unique_legacy_fallback_are_conservative(self):
        with tempfile.TemporaryDirectory() as directory:
            state_dir = Path(directory) / ".gemini"
            write_session(state_dir, "repo", SESSION_ONE, "/repo")
            sessions = load_gemini_sessions(state_dir)

            exact = select_gemini_session(
                sessions,
                session_id=SESSION_ONE,
                project_root="/repo",
                process_started_at=1,
            )
            unique = select_gemini_session(
                sessions,
                session_id="",
                project_root="/repo",
                process_started_at=1,
            )

            self.assertEqual(exact.session_id, SESSION_ONE)
            self.assertEqual(unique.session_id, SESSION_ONE)

            write_session(state_dir, "repo", SESSION_TWO, "/repo")
            ambiguous = select_gemini_session(
                load_gemini_sessions(state_dir),
                session_id="",
                project_root="/repo",
                process_started_at=1,
            )
            self.assertIsNone(ambiguous)

    def test_oversized_and_malformed_transcripts_are_skipped(self):
        with tempfile.TemporaryDirectory() as directory:
            state_dir = Path(directory) / ".gemini"
            valid = write_session(state_dir, "valid", SESSION_ONE, "/repo")
            malformed = write_session(state_dir, "bad", SESSION_TWO, "/repo2")
            malformed.write_text("{not json}\n", encoding="utf-8")
            oversized = write_session(
                state_dir,
                "large",
                "33333333-3333-3333-3333-333333333333",
                "/repo3",
            )
            with oversized.open("ab") as stream:
                stream.truncate(2 * 1024 * 1024 + 1)

            sessions = load_gemini_sessions(state_dir)

            self.assertEqual([item.path for item in sessions], [valid])

    def test_custom_home_uses_only_gemini_cli_home(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            proc = root / "proc"
            process = proc / "321"
            process.mkdir(parents=True)
            custom = root / "custom-home"
            custom.mkdir()
            (process / "environ").write_bytes(
                (
                    f"SECRET=do-not-read\0GEMINI_CLI_HOME={custom}\0"
                    "OTHER=value\0"
                ).encode()
            )

            state_dir = gemini_state_dir(
                root / "default-home",
                321,
                proc_root=proc,
                uid=os.getuid(),
            )

            self.assertEqual(state_dir, custom / ".gemini")

    def test_command_model_parser_accepts_both_long_forms(self):
        self.assertEqual(
            gemini_model_from_command("gemini --model gemini-command"),
            "gemini-command",
        )
        self.assertEqual(
            gemini_model_from_command("gemini --model=gemini-inline"),
            "gemini-inline",
        )


class GeminiBindingTests(unittest.TestCase):
    def test_hook_binding_requires_pid_start_time_and_tty(self):
        process = (321, 900, "node /opt/node_modules/.bin/gemini")
        base = {
            "agent": "gemini",
            "session_id": SESSION_ONE,
            "state": "running",
            "timestamp": 901,
            "pid": 321,
            "started_at": 900,
            "tty": "/dev/pts/7",
        }
        events = {("gemini", SESSION_ONE): base}

        self.assertEqual(
            gemini_bound_session_id(events, process, "/dev/pts/7"),
            SESSION_ONE,
        )
        self.assertEqual(
            gemini_bound_session_id(
                {("gemini", SESSION_ONE): {**base, "pid": 322}},
                process,
                "/dev/pts/7",
            ),
            "",
        )
        self.assertEqual(
            gemini_bound_session_id(
                {("gemini", SESSION_ONE): {**base, "started_at": 899}},
                process,
                "/dev/pts/7",
            ),
            "",
        )
        self.assertEqual(
            gemini_bound_session_id(events, process, "/dev/pts/8"),
            "",
        )

    def test_metadata_uses_exact_event_for_same_directory_sessions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / "home"
            state_dir = home / ".gemini"
            write_session(
                state_dir,
                "repo",
                SESSION_ONE,
                "/repo",
                user_messages=("First topic",),
                model="gemini-one",
            )
            write_session(
                state_dir,
                "repo",
                SESSION_TWO,
                "/repo",
                user_messages=("Second topic",),
                model="gemini-two",
            )
            resolver = MetadataResolver(home=home, proc_root=root / "proc")
            row = PaneRow(
                "%1",
                "s:0",
                "gemini",
                "idle",
                "/repo",
                "",
                "/dev/pts/7",
                321,
                900,
                "node /opt/node_modules/.bin/gemini --model fallback",
            )
            events = {
                ("gemini", SESSION_TWO): {
                    "agent": "gemini",
                    "session_id": SESSION_TWO,
                    "state": "done",
                    "timestamp": 901,
                    "pid": 321,
                    "started_at": 900,
                    "tty": "/dev/pts/7",
                }
            }

            metadata = resolver.resolve(row, hook_events=events)

            self.assertEqual(metadata.session_id, SESSION_TWO)
            self.assertEqual(metadata.model, "gemini-two")
            self.assertEqual(metadata.topic, "Second topic")

    def test_metadata_refuses_ambiguous_same_directory_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / "home"
            state_dir = home / ".gemini"
            write_session(state_dir, "repo", SESSION_ONE, "/repo")
            write_session(state_dir, "repo", SESSION_TWO, "/repo")
            resolver = MetadataResolver(home=home, proc_root=root / "proc")
            row = PaneRow(
                "%1",
                "s:0",
                "gemini",
                "idle",
                "/repo",
                "",
                "/dev/pts/7",
                321,
                1,
                "node /opt/node_modules/.bin/gemini --model fallback",
            )

            metadata = resolver.resolve(row, hook_events={})

            self.assertEqual(metadata.session_id, "")
            self.assertEqual(metadata.model, "fallback")


if __name__ == "__main__":
    unittest.main()
