import os
from pathlib import Path
import tempfile
import unittest

from tmuxcue.processes import discover_agent_processes, is_gemini_cli_command


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

    def test_official_gemini_node_entrypoint_is_detected(self):
        temporary, proc = self.proc_tree()
        with temporary:
            self.add_process(
                proc,
                201,
                comm="node",
                cmdline=[
                    "node",
                    "/opt/app/node_modules/.bin/gemini",
                    "--model",
                    "gemini-test",
                ],
                tty="/dev/pts/7",
                process_group=200,
                foreground_group=200,
            )

            rows = discover_agent_processes(proc, uid=os.getuid(), now=2_000)

            self.assertEqual(rows[("/dev/pts/7", "gemini")][0], 201)
            self.assertIn("gemini-test", rows[("/dev/pts/7", "gemini")][2])

    def test_unrelated_node_command_containing_gemini_is_rejected(self):
        self.assertFalse(is_gemini_cli_command("node /srv/gemini-report/server.js"))
        self.assertFalse(is_gemini_cli_command("node /srv/tools/gemini.js"))

    def test_deepest_official_gemini_node_layer_wins_one_tty(self):
        temporary, proc = self.proc_tree()
        with temporary:
            self.add_process(
                proc,
                201,
                comm="node",
                cmdline=["node", "/opt/node_modules/.bin/gemini"],
                tty="/dev/pts/7",
                process_group=200,
                foreground_group=200,
            )
            self.add_process(
                proc,
                202,
                comm="node",
                cmdline=[
                    "node",
                    "--max-old-space-size=4096",
                    "/opt/node_modules/.bin/gemini",
                ],
                tty="/dev/pts/7",
                process_group=200,
                foreground_group=200,
                start_ticks=510,
            )

            rows = discover_agent_processes(proc, uid=os.getuid(), now=2_000)

            self.assertEqual(rows[("/dev/pts/7", "gemini")][0], 202)


if __name__ == "__main__":
    unittest.main()
