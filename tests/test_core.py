import json
from pathlib import Path
import tempfile
import time
import unittest

from tmuxcue.core import (
    JsonlTail,
    PaneRow,
    is_generic_input,
    shorten,
    visual_state,
)


class CoreTests(unittest.TestCase):
    def test_pane_row_side_active_defaults_false(self):
        row = PaneRow("%1", "s:0", "codex", "idle", "/tmp", "")
        self.assertFalse(row.side_active)

    def test_jsonl_tail_reads_only_new_records(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.jsonl"
            path.write_text(json.dumps({"old": True}) + "\n", encoding="utf-8")
            tail = JsonlTail(path)
            with path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({"session_id": "s1", "text": "\u65b0\u8f93\u5165"}, ensure_ascii=False) + "\n")
            self.assertEqual(tail.poll(), [{"session_id": "s1", "text": "\u65b0\u8f93\u5165"}])
            self.assertEqual(tail.poll(), [])

    def test_prompt_submission_forces_running_until_detector_catches_up(self):
        now = time.time()
        prompt = {"at": now, "saw_working": False}
        self.assertEqual(visual_state("idle", prompt, now, 8), "running")
        self.assertEqual(visual_state("working", prompt, now + 1, 8), "running")
        self.assertTrue(prompt["saw_working"])
        self.assertEqual(visual_state("idle", prompt, now + 2, 8), "done")

    def test_blocked_always_wins(self):
        prompt = {"at": time.time(), "saw_working": False}
        self.assertEqual(visual_state("blocked", prompt, time.time(), 8), "approval")

    def test_shortening_uses_display_width_and_ellipsis(self):
        value = shorten("\u8fd9\u662f\u4e00\u4e2a\u5f88\u957f\u7684\u4e2d\u6587\u7a97\u53e3\u6807\u9898\uff0c\u7528\u4e8e\u9a8c\u8bc1\u622a\u65ad\u6548\u679c", width=16)
        self.assertTrue(value.endswith("..."))
        self.assertLessEqual(len(value), 12)

    def test_generic_acknowledgements_are_not_topics(self):
        for value in ("\u7ee7\u7eed", "\u597d\u7684", "/t", "OK"):
            self.assertTrue(is_generic_input(value))
        self.assertFalse(is_generic_input("\u8bf7\u7ee7\u7eed\u6267\u884c\u5206\u6790\u5e76\u8bb0\u5f55\u7ed3\u679c"))


if __name__ == "__main__":
    unittest.main()
