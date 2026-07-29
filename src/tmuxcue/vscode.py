from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
from typing import Any

from .settings import config_dir

MANAGED_SETTINGS = {
    "terminal.integrated.tabs.title": "${sequence}",
    "terminal.integrated.tabs.description": "",
}
STATE_VERSION = 1
MAX_SETTINGS_BYTES = 1024 * 1024


class JsoncError(ValueError):
    pass


def remote_settings_path(home: Path | None = None) -> Path:
    return (home or Path.home()) / ".vscode-server/data/Machine/settings.json"


def state_path() -> Path:
    return config_dir() / "vscode-remote-state.json"


def _clean_jsonc(text: str) -> str:
    output = list(text)
    position = 0
    in_string = False
    escaped = False

    while position < len(output):
        character = output[position]
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            position += 1
            continue
        if character == '"':
            in_string = True
            position += 1
            continue
        if character == "/" and position + 1 < len(output):
            if output[position + 1] == "/":
                end = position + 2
                while end < len(output) and output[end] not in "\r\n":
                    output[end] = " "
                    end += 1
                output[position] = output[position + 1] = " "
                position = end
                continue
            if output[position + 1] == "*":
                end = position + 2
                while end + 1 < len(output) and output[end:end + 2] != ["*", "/"]:
                    if output[end] not in "\r\n":
                        output[end] = " "
                    end += 1
                if end + 1 >= len(output):
                    raise JsoncError("unterminated block comment")
                output[position] = output[position + 1] = " "
                output[end] = output[end + 1] = " "
                position = end + 2
                continue
        position += 1

    position = 0
    in_string = False
    escaped = False
    while position < len(output):
        character = output[position]
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
        elif character == '"':
            in_string = True
        elif character == ",":
            lookahead = position + 1
            while lookahead < len(output) and output[lookahead].isspace():
                lookahead += 1
            if lookahead < len(output) and output[lookahead] in "}]":
                output[position] = " "
        position += 1
    return "".join(output)


def load_jsonc(text: str) -> dict[str, Any]:
    cleaned = _clean_jsonc(text)
    if not cleaned.strip():
        return {}

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise JsoncError(f"duplicate JSON key: {key}")
            value[key] = item
        return value

    try:
        value = json.loads(cleaned, object_pairs_hook=unique_object)
    except json.JSONDecodeError as error:
        raise JsoncError(str(error)) from error
    if not isinstance(value, dict):
        raise JsoncError("VS Code settings must be a JSON object")
    return value


def dump_json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def _validate_target(path: Path, home: Path, *, create_parent: bool) -> bool:
    home = home.resolve()
    try:
        parts = path.parent.relative_to(home).parts
    except ValueError as error:
        raise RuntimeError("refusing to edit VS Code settings outside the home directory") from error

    parent = home
    for part in parts:
        parent /= part
        if parent.is_symlink():
            raise RuntimeError("refusing to follow a VS Code settings directory symlink")
        if not parent.exists():
            if not create_parent:
                return False
            parent.mkdir(mode=0o700)
        metadata = parent.stat()
        if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid():
            raise RuntimeError("VS Code settings directory is not user-owned")

    if path.is_symlink():
        raise RuntimeError("refusing to follow a VS Code settings symlink")
    if path.exists():
        metadata = path.stat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid():
            raise RuntimeError("VS Code settings file is not a user-owned regular file")
        if metadata.st_size > MAX_SETTINGS_BYTES:
            raise RuntimeError("VS Code settings file is unexpectedly large")
    return True


def _atomic_write(path: Path, text: str, mode: int) -> None:
    if path.is_symlink():
        raise RuntimeError(f"refusing to replace symlink: {path}")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.tmuxcue-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _backup(path: Path) -> None:
    if not path.exists():
        return
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    target = path.with_name(f"{path.name}.backup-tmuxcue-{stamp}")
    suffix = 1
    while target.exists() or target.is_symlink():
        target = path.with_name(f"{path.name}.backup-tmuxcue-{stamp}-{suffix}")
        suffix += 1
    shutil.copy2(path, target)
    os.chmod(target, 0o600)
    print(f"backup: {target}")


def _load_state() -> dict[str, Any]:
    path = state_path()
    if not path.exists():
        return {"version": STATE_VERSION, "paths": {}}
    if path.is_symlink():
        raise RuntimeError("refusing to follow the VS Code state symlink")
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise RuntimeError("invalid TmuxCue VS Code state file") from error
    if state.get("version") != STATE_VERSION or not isinstance(state.get("paths"), dict):
        raise RuntimeError("unsupported TmuxCue VS Code state file")
    return state


def _save_state(state: dict[str, Any]) -> None:
    _atomic_write(state_path(), dump_json(state), 0o600)


def install_remote_settings(home: Path | None = None) -> bool:
    home = (home or Path.home()).resolve()
    path = remote_settings_path(home)
    _validate_target(path, home, create_parent=True)
    existed = path.exists()
    original_text = path.read_text(encoding="utf-8") if existed else None
    data = load_jsonc(original_text or "{}")

    state = _load_state()
    key = str(path)
    first_install = key not in state["paths"]
    if first_install:
        state["paths"][key] = {"original_text": original_text}
        _save_state(state)

    updated = dict(data)
    for name, value in MANAGED_SETTINGS.items():
        if first_install or data.get(name) == value:
            updated[name] = value
    if updated == data:
        return False
    rendered = dump_json(updated)
    if original_text == rendered:
        return False
    if existed:
        _backup(path)
        mode = stat.S_IMODE(path.stat().st_mode)
    else:
        mode = 0o600
    _atomic_write(path, rendered, mode)
    print(f"VS Code Remote titles configured: {path}")
    return True


def uninstall_remote_settings(home: Path | None = None) -> bool:
    home = (home or Path.home()).resolve()
    state = _load_state()
    path = remote_settings_path(home)
    entry = state["paths"].get(str(path))
    changed = False

    if entry is not None and _validate_target(path, home, create_parent=False) and path.exists():
        current_text = path.read_text(encoding="utf-8")
        current = load_jsonc(current_text)
        original_text = entry.get("original_text")
        original = load_jsonc(original_text or "{}")
        installed = dict(original)
        installed.update(MANAGED_SETTINGS)
        expected_text = dump_json(installed)

        if current_text == expected_text:
            if original_text is None:
                path.unlink()
            else:
                _backup(path)
                _atomic_write(path, original_text, stat.S_IMODE(path.stat().st_mode))
            changed = True
        else:
            restored = dict(current)
            for key, managed_value in MANAGED_SETTINGS.items():
                if current.get(key) != managed_value:
                    continue
                if key in original:
                    restored[key] = original[key]
                else:
                    restored.pop(key, None)
            if restored != current:
                _backup(path)
                _atomic_write(path, dump_json(restored), stat.S_IMODE(path.stat().st_mode))
                changed = True
        if changed:
            print(f"VS Code Remote titles restored: {path}")

    path = state_path()
    if path.exists():
        if path.is_symlink():
            raise RuntimeError("refusing to unlink the VS Code state symlink")
        path.unlink()
    return changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manage TmuxCue VS Code Remote title settings")
    parser.add_argument("action", choices=("install", "uninstall"))
    arguments = parser.parse_args(argv)
    if arguments.action == "install":
        install_remote_settings()
    else:
        uninstall_remote_settings()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
