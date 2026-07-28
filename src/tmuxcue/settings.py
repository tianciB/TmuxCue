from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil

DEFAULTS = {"terminal_title": "native", "override_enabled_at": 0, "source_dir": ""}
MANAGED_CODEX_LINE = "terminal_title = [] # managed by TmuxCue"


def config_dir() -> Path:
    root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return root / "tmuxcue"


def config_path() -> Path:
    return config_dir() / "config.json"


def state_path() -> Path:
    return config_dir() / "native-title-state.json"


def atomic_write(path: Path, text: str, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    temporary = path.with_name(f".{path.name}.tmuxcue-{os.getpid()}")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
        os.replace(temporary, path)
        os.chmod(path, mode)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def backup(path: Path) -> None:
    if not path.exists():
        return
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    target = path.with_name(f"{path.name}.backup-tmuxcue-{stamp}")
    shutil.copy2(path, target)
    os.chmod(target, 0o600)


def load() -> dict:
    data = dict(DEFAULTS)
    try:
        stored = json.loads(config_path().read_text(encoding="utf-8"))
        if isinstance(stored, dict):
            data.update(stored)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    if data.get("terminal_title") not in {"override", "native"}:
        data["terminal_title"] = DEFAULTS["terminal_title"]
    return data


def save(data: dict) -> None:
    clean = dict(DEFAULTS)
    clean.update(data)
    atomic_write(config_path(), json.dumps(clean, ensure_ascii=False, indent=2) + "\n")


def set_source_dir(path: Path) -> None:
    data = load()
    data["source_dir"] = str(path.resolve())
    save(data)


def source_dir() -> Path | None:
    value = str(load().get("source_dir") or "")
    return Path(value) if value else None


def title_mode() -> str:
    return str(load()["terminal_title"])


def title_override_since() -> int:
    try:
        return int(load().get("override_enabled_at") or 0)
    except (TypeError, ValueError):
        return 0


def _load_state() -> dict:
    try:
        value = json.loads(state_path().read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _save_state(state: dict) -> None:
    atomic_write(state_path(), json.dumps(state, ensure_ascii=False, indent=2) + "\n")


def _codex_path() -> Path:
    return Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "config.toml"


def _claude_path() -> Path:
    return Path(os.environ.get("CLAUDE_HOME", Path.home() / ".claude")) / "settings.json"


def _disable_codex_title(state: dict) -> None:
    path = _codex_path()
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    lines = text.splitlines()
    original = state.get("codex")
    if original is None:
        original = {"line": None, "kind": "section", "section_added": False}
        dotted = next(
            (i for i, line in enumerate(lines) if re.match(r"^\s*tui\.terminal_title\s*=", line)),
            None,
        )
        if dotted is not None:
            original.update({"line": lines[dotted], "kind": "dotted"})
        else:
            section = next(
                (i for i, line in enumerate(lines) if re.match(r"^\s*\[tui\]\s*$", line)),
                None,
            )
            if section is None:
                original["section_added"] = True
            else:
                end = next(
                    (i for i in range(section + 1, len(lines)) if re.match(r"^\s*\[", lines[i])),
                    len(lines),
                )
                key = next(
                    (i for i in range(section + 1, end) if re.match(r"^\s*terminal_title\s*=", lines[i])),
                    None,
                )
                if key is not None:
                    original["line"] = lines[key]
        state["codex"] = original

    dotted = next(
        (i for i, line in enumerate(lines) if re.match(r"^\s*tui\.terminal_title\s*=", line)),
        None,
    )
    if dotted is not None:
        lines[dotted] = f"tui.{MANAGED_CODEX_LINE}"
    else:
        section = next(
            (i for i, line in enumerate(lines) if re.match(r"^\s*\[tui\]\s*$", line)),
            None,
        )
        if section is None:
            if lines and lines[-1].strip():
                lines.append("")
            lines.extend(("[tui]", MANAGED_CODEX_LINE))
        else:
            end = next(
                (i for i in range(section + 1, len(lines)) if re.match(r"^\s*\[", lines[i])),
                len(lines),
            )
            key = next(
                (i for i in range(section + 1, end) if re.match(r"^\s*terminal_title\s*=", lines[i])),
                None,
            )
            if key is None:
                lines.insert(end, MANAGED_CODEX_LINE)
            else:
                lines[key] = MANAGED_CODEX_LINE

    backup(path)
    atomic_write(path, "\n".join(lines).rstrip() + "\n")


def _restore_codex_title(state: dict) -> None:
    original = state.get("codex")
    path = _codex_path()
    if original is None or not path.exists():
        return
    lines = path.read_text(encoding="utf-8").splitlines()
    managed = next((i for i, line in enumerate(lines) if "managed by TmuxCue" in line), None)
    if managed is None:
        return

    if original.get("line") is not None:
        lines[managed] = original["line"]
    else:
        lines.pop(managed)
        if original.get("section_added"):
            section = next(
                (i for i, line in enumerate(lines) if re.match(r"^\s*\[tui\]\s*$", line)),
                None,
            )
            if section is not None:
                end = next(
                    (i for i in range(section + 1, len(lines)) if re.match(r"^\s*\[", lines[i])),
                    len(lines),
                )
                if not any(line.strip() and not line.lstrip().startswith("#") for line in lines[section + 1:end]):
                    lines.pop(section)
    backup(path)
    atomic_write(path, "\n".join(lines).rstrip() + "\n")


def _disable_claude_title(state: dict) -> None:
    path = _claude_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except json.JSONDecodeError as error:
        raise RuntimeError(f"Invalid Claude settings JSON: {path}") from error
    env = data.setdefault("env", {})
    if "claude" not in state:
        state["claude"] = {
            "present": "CLAUDE_CODE_DISABLE_TERMINAL_TITLE" in env,
            "value": env.get("CLAUDE_CODE_DISABLE_TERMINAL_TITLE"),
        }
    env["CLAUDE_CODE_DISABLE_TERMINAL_TITLE"] = "1"
    backup(path)
    atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def _restore_claude_title(state: dict) -> None:
    original = state.get("claude")
    path = _claude_path()
    if original is None or not path.exists():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise RuntimeError(f"Invalid Claude settings JSON: {path}") from error
    env = data.get("env") or {}
    if env.get("CLAUDE_CODE_DISABLE_TERMINAL_TITLE") != "1":
        return
    if original.get("present"):
        env["CLAUDE_CODE_DISABLE_TERMINAL_TITLE"] = original.get("value")
    else:
        env.pop("CLAUDE_CODE_DISABLE_TERMINAL_TITLE", None)
    if env:
        data["env"] = env
    else:
        data.pop("env", None)
    backup(path)
    atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def apply_title_mode(mode: str) -> None:
    if mode not in {"override", "native"}:
        raise ValueError("terminal title mode must be 'override' or 'native'")
    data = load()
    state = _load_state()
    if mode == "override":
        if data.get("terminal_title") != "override" or not data.get("override_enabled_at"):
            data["override_enabled_at"] = int(datetime.now(timezone.utc).timestamp())
        _disable_codex_title(state)
        _save_state(state)
        _disable_claude_title(state)
        _save_state(state)
    else:
        _restore_codex_title(state)
        _restore_claude_title(state)
        try:
            state_path().unlink()
        except FileNotFoundError:
            pass
    data["terminal_title"] = mode
    if mode == "native":
        data["override_enabled_at"] = 0
    save(data)
