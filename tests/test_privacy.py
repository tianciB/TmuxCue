import re
from pathlib import Path
import unittest

ROOT = Path(__file__).parents[1]
AUTHOR_FILES = {ROOT / "LICENSE", ROOT / "pyproject.toml"}


class PrivacyTests(unittest.TestCase):
    def test_repository_contains_no_cjk_characters(self):
        cjk = re.compile(r"[\u3400-\u9fff]")
        for path in ROOT.rglob("*"):
            if not path.is_file() or ".git" in path.parts or path.suffix == ".pyc":
                continue
            try:
                content = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            self.assertIsNone(cjk.search(content), f"CJK character found in {path}")

    def test_repository_contains_no_personal_runtime_data(self):
        forbidden = (
            re.compile(r"/home/"),
            re.compile(r"msraima", re.I),
            re.compile(r"Rep-Diffusion", re.I),
            re.compile(r"Efficient-Visual", re.I),
            re.compile(r"\b019fa[0-9a-f-]+\b", re.I),
            re.compile(r"\bca062128[0-9a-f-]*\b", re.I),
        )
        for path in ROOT.rglob("*"):
            if path.resolve() == Path(__file__).resolve():
                continue
            if not path.is_file() or ".git" in path.parts or path.suffix == ".pyc":
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            for pattern in forbidden:
                self.assertIsNone(pattern.search(text), f"{pattern.pattern} found in {path}")
            if path not in AUTHOR_FILES:
                self.assertNotIn("v-bitianci", text, f"author identifier found in {path}")


if __name__ == "__main__":
    unittest.main()
