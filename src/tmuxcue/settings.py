from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil

DEFAULTS = {"source_dir": ""}


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
    return data


def save(data: dict) -> None:
    clean = {"source_dir": str(data.get("source_dir") or "")}
    atomic_write(config_path(), json.dumps(clean, ensure_ascii=False, indent=2) + "\n")


def set_source_dir(path: Path) -> None:
    data = load()
    data["source_dir"] = str(path.resolve())
    save(data)


def source_dir() -> Path | None:
    value = str(load().get("source_dir") or "")
    return Path(value) if value else None


def _load_state() -> dict:
    try:
        value = json.loads(state_path().read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _codex_path() -> Path:
    return Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "config.toml"


def _claude_path() -> Path:
    return Path(os.environ.get("CLAUDE_HOME", Path.home() / ".claude")) / "settings.json"


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


def _load_json_settings(path: Path, label: str) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except json.JSONDecodeError as error:
        raise RuntimeError(f"Invalid {label} settings JSON: {path}") from error
    if not isinstance(value, dict):
        raise RuntimeError(f"Invalid {label} settings JSON: {path}")
    return value


def _restore_gemini_titles(state: dict) -> None:
    gemini_state = state.get("gemini")
    if not isinstance(gemini_state, dict):
        return
    for value, original in gemini_state.items():
        path = Path(value)
        if (
            not path.is_absolute()
            or path.name != "settings.json"
            or path.parent.name != ".gemini"
            or not path.exists()
            or not isinstance(original, dict)
        ):
            continue
        data = _load_json_settings(path, "Gemini")
        ui = data.get("ui")
        if not isinstance(ui, dict):
            continue
        changed = False
        for key in ("dynamicWindowTitle", "showStatusInTitle"):
            saved = original.get(key)
            if not isinstance(saved, dict) or ui.get(key) is not False:
                continue
            if saved.get("present"):
                ui[key] = saved.get("value")
            else:
                ui.pop(key, None)
            changed = True
        if not changed:
            continue
        if ui:
            data["ui"] = ui
        else:
            data.pop("ui", None)
        backup(path)
        atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def restore_native_titles() -> None:
    """Restore any native terminal-title settings changed by older TmuxCue versions."""
    state = _load_state()
    if state:
        _restore_codex_title(state)
        _restore_claude_title(state)
        _restore_gemini_titles(state)
    try:
        state_path().unlink()
    except FileNotFoundError:
        pass
    if config_path().exists():
        save(load())
