import os
from pathlib import Path
import tempfile
import unittest

from tmuxcue.processes import (
    classify_agent,
    discover_agent_processes,
    find_agent_ancestor,
)


class ProcessDiscoveryTests(unittest.TestCase):
    def add_process(
        self,
        proc: Path,
        pid: int,
        *,
        comm: str,
        cmdline: list[str],
        tty: str,
        parent: int = 1,
        process_group: int | None = None,
        foreground_group: int | None = None,
        start_ticks: int = 500,
        state: str = "S",
    ) -> None:
        process = proc / str(pid)
        process.mkdir()
        (process / "comm").write_text(comm + "\n", encoding="utf-8")
        (process / "cmdline").write_bytes(
            b"\0".join(part.encode() for part in cmdline) + b"\0"
        )
        (process / "environ").write_bytes(b"")
        fd = process / "fd"
        fd.mkdir()
        (fd / "0").symlink_to(tty)
        process_group = pid if process_group is None else process_group
        foreground_group = (
            process_group if foreground_group is None else foreground_group
        )
        fields = [
            state,
            str(parent),
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
            str(start_ticks),
        ]
        (process / "stat").write_text(
            f"{pid} ({comm}) " + " ".join(fields) + "\n",
            encoding="utf-8",
        )

    def proc_tree(self) -> tuple[tempfile.TemporaryDirectory, Path]:
        temporary = tempfile.TemporaryDirectory()
        proc = Path(temporary.name)
        (proc / "uptime").write_text("1000.0 0.0\n", encoding="utf-8")
        (proc / "stat").write_text("cpu 1 2 3 4\nbtime 1000\n", encoding="utf-8")
        return temporary, proc

    def test_agy_foreground_process_is_detected_under_wrapper_group(self):
        temporary, proc = self.proc_tree()
        with temporary:
            self.add_process(
                proc,
                200,
                comm="bash",
                cmdline=["bash", "/opt/bin/agy"],
                tty="/dev/pts/4",
                process_group=200,
                foreground_group=200,
            )
            self.add_process(
                proc,
                201,
                comm="node",
                cmdline=["node", "/opt/agy/bridge.mjs"],
                tty="/dev/pts/4",
                parent=200,
                process_group=200,
                foreground_group=200,
            )
            self.add_process(
                proc,
                202,
                comm="agy",
                cmdline=[
                    "/opt/agy/bin/agy",
                    "--model",
                    "neon-xhigh",
                    "--effort",
                    "xhigh",
                ],
                tty="/dev/pts/4",
                parent=200,
                process_group=200,
                foreground_group=200,
                start_ticks=510,
            )

            rows = discover_agent_processes(proc, uid=os.getuid(), now=2_000)

            self.assertEqual(set(rows), {("/dev/pts/4", "agy")})
            self.assertEqual(rows[("/dev/pts/4", "agy")][0], 202)
            self.assertIn("neon-xhigh", rows[("/dev/pts/4", "agy")][2])

    def test_legacy_gemini_node_process_is_not_classified(self):
        self.assertEqual(
            classify_agent("node", "node /opt/app/node_modules/.bin/gemini"),
            "",
        )
        self.assertEqual(
            classify_agent("gemini", "gemini"),
            "",
        )

    def test_hook_parent_walk_finds_nearest_agy_process(self):
        temporary, proc = self.proc_tree()
        with temporary:
            self.add_process(
                proc,
                202,
                comm="agy",
                cmdline=["/opt/agy/bin/agy", "--model", "neon-xhigh"],
                tty="/dev/pts/4",
                parent=200,
                process_group=200,
                foreground_group=200,
                start_ticks=510,
            )
            self.add_process(
                proc,
                300,
                comm="sh",
                cmdline=["sh", "-c", "tmuxcue-hook"],
                tty="/dev/pts/4",
                parent=202,
                process_group=200,
                foreground_group=200,
                start_ticks=520,
            )

            binding = find_agent_ancestor(
                "agy",
                start_pid=300,
                proc_root=proc,
                uid=os.getuid(),
            )

            self.assertIsNotNone(binding)
            self.assertEqual(binding.pid, 202)
            self.assertEqual(binding.tty, "/dev/pts/4")


if __name__ == "__main__":
    unittest.main()
