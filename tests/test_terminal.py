import os
from pathlib import Path
import pty
import select
import subprocess
import tempfile
import time
import unittest

from tmuxcue.core import TerminalRow
from tmuxcue.terminal import TerminalDetector, TerminalTitleWriter


class TerminalDetectorTests(unittest.TestCase):
    def test_discovers_only_foreground_agents_outside_tmux(self):
        uid = os.getuid()
        with tempfile.TemporaryDirectory() as directory:
            proc = Path(directory)
            for pid, cwd in ((101, "/tmp/codex"), (102, "/tmp/claude")):
                process = proc / str(pid)
                process.mkdir()
                (process / "cwd").symlink_to(cwd)

            processes = {
                ("/dev/pts/1", "codex"): (101, 10, "codex resume"),
                ("/dev/pts/2", "claude"): (102, 20, "claude"),
            }
            detector = TerminalDetector(
                proc_root=proc,
                uid=uid,
                process_provider=lambda _root, _uid: processes,
            )
            rows = detector.scan({"/dev/pts/1"})
            self.assertEqual(set(rows), {"terminal:102"})
            self.assertEqual(rows["terminal:102"].agent, "claude")
            self.assertEqual(rows["terminal:102"].tty, "/dev/pts/2")


class TerminalTitleWriterTests(unittest.TestCase):
    def test_sanitize_removes_terminal_control_characters(self):
        self.assertEqual(
            TerminalTitleWriter.sanitize("hello\x1b]2;bad\x07\nworld"),
            "hello ]2;bad world",
        )

    def test_writes_only_an_osc_title_to_matching_owned_tty(self):
        master, slave = pty.openpty()
        tty = os.ttyname(slave)
        child = subprocess.Popen(
            ["sleep", "5"],
            stdin=slave,
            stdout=slave,
            stderr=slave,
            start_new_session=True,
        )
        try:
            row = TerminalRow(
                pid=child.pid,
                tty=tty,
                agent="codex",
                state="idle",
                cwd_hint="/tmp",
                started_at=int(time.time()),
                cmdline="codex",
            )
            self.assertTrue(TerminalTitleWriter().write(row, "✅ codex: safe"))
            readable, _, _ = select.select([master], [], [], 1)
            self.assertTrue(readable)
            self.assertEqual(os.read(master, 1024), "\x1b]0;✅ codex: safe\x07".encode())
        finally:
            child.terminate()
            child.wait(timeout=5)
            os.close(master)
            os.close(slave)

    def test_rejects_a_tty_not_owned_by_the_target_process(self):
        master, slave = pty.openpty()
        try:
            row = TerminalRow(
                pid=os.getpid(),
                tty=os.ttyname(slave),
                agent="codex",
                state="idle",
                cwd_hint="/tmp",
                started_at=int(time.time()),
                cmdline="codex",
            )
            self.assertFalse(TerminalTitleWriter().write(row, "unsafe"))
        finally:
            os.close(master)
            os.close(slave)


if __name__ == "__main__":
    unittest.main()
